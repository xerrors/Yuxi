"""Yuxi 身份提供、一次性授权码与内部签名。"""

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization

from yuxi.infrastructure.redis import get_async_redis_client
from yuxi.modules.identity.provider import AuthorizationRequest, ProviderConfig, ProviderError
from yuxi.modules.identity.repositories.users import UserRepository

_CONSUME_CODE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


class ProviderService:
    """协议会话存在短期 Redis 中，当前人类身份始终回读 PostgreSQL。"""

    def __init__(self, db=None):
        """固定本次操作所用的静态部署信任根。"""
        self.config = ProviderConfig.from_env()
        self.db = db
        public = self.config.private_key.public_key()
        der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.kid = hashlib.sha256(der).hexdigest()[:32]
        revision = "\0".join((self.config.issuer, self.config.client_id, self.config.client_secret, self.config.redirect_uri, self.kid))
        self.prefix = "yuxi:provider:" + hashlib.sha256(revision.encode()).hexdigest() + ":"

    def metadata(self):
        """公开唯一发行者的已实现能力，不宣告刷新或动态登记。"""
        issuer = self.config.issuer
        return {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token",
            "userinfo_endpoint": issuer + "/userinfo",
            "jwks_uri": issuer + "/jwks",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["openid", "profile"],
            "claims_supported": ["iss", "sub", "aud", "iat", "exp", "nonce", "name", "preferred_username"],
        }

    def jwks(self):
        """只发布公钥，kid 绑定实际密钥内容。"""
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.config.private_key.public_key()))
        key.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [key]}

    async def start_browser(self, request: AuthorizationRequest):
        """绑定浏览器与完整请求，登录后不能替换成另一笔授权。"""
        request.validate_client(self.config)
        browser = secrets.token_urlsafe(32)
        redis = await get_async_redis_client()
        await redis.set(self.prefix + "browser:" + hashlib.sha256(browser.encode()).hexdigest(), request.model_dump_json(), ex=300)
        return browser

    async def authorize(self, request: AuthorizationRequest, uid: str, browser: str):
        """校验同浏览器请求与当前身份后，为静态客户端签发一次性授权码。"""
        request.validate_client(self.config)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser):
            raise ProviderError("invalid_request", "授权浏览器会话无效")
        redis = await get_async_redis_client()
        raw = await redis.get(self.prefix + "browser:" + hashlib.sha256(browser.encode()).hexdigest())
        if not raw or json.loads(raw) != request.model_dump():
            raise ProviderError("invalid_request", "授权浏览器会话已过期或请求已改变")
        await self.active_user(uid)
        code = secrets.token_urlsafe(32)
        record = {**request.model_dump(), "uid": uid}
        await redis.set(self.code_key(code), json.dumps(record), ex=300, nx=True)
        return self.config.redirect_uri + "?" + urlencode({"code": code, "state": request.state})

    async def exchange(self, form: dict, authorization: str = ""):
        """先验证客户端、PKCE 和当前用户，签名成功后原子消费授权码。"""
        self.authenticate_client(form, authorization)
        if form.get("grant_type") != "authorization_code":
            raise ProviderError("unsupported_grant_type", "只支持 authorization_code")
        code, verifier = form.get("code", ""), form.get("code_verifier", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
            raise ProviderError("invalid_grant", "授权码或 PKCE 无效")
        redis = await get_async_redis_client()
        raw = await redis.get(self.code_key(code))
        if not raw:
            raise ProviderError("invalid_grant", "授权码已消费或过期")
        record = json.loads(raw)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if form.get("redirect_uri") != record["redirect_uri"] or not secrets.compare_digest(challenge, record["code_challenge"]):
            raise ProviderError("invalid_grant", "回调或 PKCE 不匹配")
        user = await self.active_user(record["uid"])
        profile = {"name": user.username, "preferred_username": user.username} if "profile" in record["scope"].split() else {}
        access = self.sign(user.uid, self.config.issuer + "/userinfo", "oidc-userinfo", 300, scope=record["scope"])
        identity = self.sign(user.uid, self.config.client_id, "oidc-id", 300, nonce=record["nonce"], **profile)
        if not await redis.eval(_CONSUME_CODE, 1, self.code_key(code), raw):
            raise ProviderError("invalid_grant", "授权码已消费或过期")
        return {"access_token": access, "token_type": "Bearer", "expires_in": 300, "id_token": identity, "scope": record["scope"]}

    async def userinfo(self, token: str):
        """ID Token 和委托 Token 都不能用作 userinfo 访问凭据。"""
        try:
            claims = jwt.decode(
                token,
                self.config.private_key.public_key(),
                algorithms=["RS256"],
                issuer=self.config.issuer,
                audience=self.config.issuer + "/userinfo",
                options={"require": ["iss", "sub", "aud", "exp", "iat", "purpose"]},
            )
            if claims["purpose"] != "oidc-userinfo":
                raise jwt.InvalidTokenError("wrong purpose")
        except jwt.PyJWTError as exc:
            raise ProviderError("invalid_token", "访问令牌无效", 401) from exc
        user = await self.active_user(claims["sub"])
        result = {"sub": user.uid}
        if "profile" in claims.get("scope", "").split():
            result.update({"name": user.username, "preferred_username": user.username})
        return result

    async def active_user(self, uid: str):
        """共享锁定当前人类身份，拒绝软删除、锁定及外部终端用户。"""
        user = await UserRepository(self.db).lock_active_human(uid)
        if user is None or user.is_login_locked():
            raise ProviderError("access_denied", "用户已失效或被锁定", 403)
        return user

    def sign(self, uid: str, audience: str, purpose: str, lifetime: int, **extra):
        """不同用途共享发行者，严格分离受众与 purpose。"""
        now = int(time.time())
        claims = {"iss": self.config.issuer, "sub": uid, "aud": audience, "iat": now, "exp": now + lifetime, "purpose": purpose, **extra}
        return jwt.encode(claims, self.config.private_key, algorithm="RS256", headers={"kid": self.kid})

    def code_key(self, code: str):
        """Redis key 不包含原始授权码。"""
        return self.prefix + hashlib.sha256(code.encode()).hexdigest()

    def authenticate_client(self, form: dict, authorization: str):
        """支持标准 Basic 或 POST 客户端认证，拒绝混用。"""
        from urllib.parse import unquote_plus

        client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
        if authorization:
            try:
                method, value = authorization.split(" ", 1)
                if method.lower() != "basic" or "client_secret" in form or "client_id" in form:
                    raise ValueError("ambiguous authentication")
                client_id, secret = base64.b64decode(value, validate=True).decode().split(":", 1)
                client_id, secret = unquote_plus(client_id), unquote_plus(secret)
            except (ValueError, UnicodeError) as exc:
                raise ProviderError("invalid_client", "客户端认证失败", 401) from exc
        if not secrets.compare_digest(client_id.encode(), self.config.client_id.encode()) or not secrets.compare_digest(
            secret.encode(), self.config.client_secret.encode()
        ):
            raise ProviderError("invalid_client", "客户端认证失败", 401)
