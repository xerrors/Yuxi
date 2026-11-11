"""个人 MCP 授权事务、断开与跨 worker 刷新。"""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from yuxi.modules.extensions.mcp.models import MCPOAuthAttempt
from yuxi.modules.extensions.mcp.oauth_config import (
    PERSONAL_AUTH_MODES,
    STATIC_AUTH_MODES,
    MCPOAuthConfig,
    cipher,
    config_revision,
    decrypt,
    encrypt,
    origin,
    redirect_uri,
)
from yuxi.modules.extensions.mcp.oauth_protocol import (
    OAuthTokenRejected,
    discover,
    request_json,
    token_request,
)
from yuxi.modules.extensions.mcp.repository import (
    active_mcp_user,
    consume_oauth_attempt,
    delete_expired_oauth_attempts,
    get_mcp_connection_by_id,
    get_mcp_server,
    get_mcp_server_by_id,
    mcp_connection,
    runtime_mcp_policy,
)
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, PermissionDeniedError, ResourceNotFoundError, StateConflictError, UpstreamError


async def cleanup_expired_mcp_oauth_attempts(_context: dict) -> None:
    """由 worker 定期提交过期授权事务清理，跳过正在使用的记录。"""
    from yuxi.infrastructure.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        await delete_expired_oauth_attempts(db)


def fingerprint(value):
    """短期 state 和浏览器标识仅保存摘要。"""
    return hashlib.sha256(value.encode()).hexdigest()


def connection_status(server, connection):
    """状态投影只读取本人的授权事实，不暴露凭据。"""
    if server.auth_mode not in PERSONAL_AUTH_MODES:
        return "not_required"
    if not connection:
        return "disconnected"
    if connection.config_revision != config_revision(server) and connection.status == "connected":
        return "reconnect_required"
    return connection.status


class MCPOAuthService:
    """个人连接复用行锁与世代；OAuth 协议只在授权和刷新时执行。"""

    def __init__(self, db):
        self.db = db

    async def save_credentials(self, slug, user_id, secret, revision):
        """按表单修订发布本人的静态凭证，与配置变更串行提交。"""
        server = await get_mcp_server(self.db, slug, lock=True)
        if not server or not server.enabled:
            raise ResourceNotFoundError("连接器不存在或已停用")
        if server.auth_mode not in STATIC_AUTH_MODES or config_revision(server) != revision:
            raise StateConflictError("连接器配置已改变，请重新打开凭证表单")
        await active_mcp_user(self.db, user_id=user_id)
        connection = await mcp_connection(self.db, server.id, user_id, lock=True, create=True)
        connection.generation += 1
        connection.credentials = encrypt({"secret": secret})
        connection.config_revision = revision
        connection.status, connection.expires_at = "connected", None
        await self.db.commit()
        return connection.status

    async def static_credentials(self, slug, uid, *, tool_name=None, expected_revision=None, catalog=False):
        """每次出站回读本人的静态认证头及其连接世代。"""

        async def policy(tool):
            """目录读取允许停用服务；运行调用继续检查启停。"""
            if catalog:
                return await self.require_catalog_policy(slug, uid, expected_revision)
            return await self.require_runtime_policy(slug, uid, tool, expected_revision)

        user, server = await policy(None)
        connection = await mcp_connection(self.db, server.id, user.id, lock=True)
        _, server = await policy(tool_name)
        if server.auth_mode not in STATIC_AUTH_MODES or not connection or connection_status(server, connection) != "connected":
            raise StateConflictError("请在扩展 → 连接器中配置个人凭证", code="mcp_reconnect_required")
        secret = decrypt(connection.credentials)["secret"]
        header = "Authorization" if server.auth_mode == "bearer" else server.api_key_header
        value = "Bearer " + secret if server.auth_mode == "bearer" else secret
        generation = connection.generation
        await self.db.commit()
        return header, value, generation

    async def reject_credentials(self, slug, uid, generation):
        """迟到的旧请求不能撤销用户后来替换的静态凭证。"""
        user = await active_mcp_user(self.db, uid=uid)
        server = await get_mcp_server(self.db, slug)
        if not server:
            return
        connection = await mcp_connection(self.db, server.id, user.id, lock=True)
        if connection and connection.generation == generation and connection.status == "connected":
            connection.status, connection.credentials, connection.expires_at = "reconnect_required", None, None
            await self.db.commit()

    async def start(self, slug, user_id, browser):
        """在远端注册后创建限时事务，浏览器回调不依赖前端 JWT。"""
        cipher()
        callback = redirect_uri()
        user = await active_mcp_user(self.db, user_id=user_id)
        server = await self.require_server(slug)
        config = MCPOAuthConfig.model_validate(server.oauth_config)
        revision = config_revision(server)
        connection = await mcp_connection(self.db, server.id, user.id, lock=True, create=True)
        connection.generation += 1
        connection_id, generation = connection.id, connection.generation
        await self.db.commit()
        metadata = await discover(server)
        client = await self.register_client(server, config, metadata, callback, revision)
        connection = await get_mcp_connection_by_id(self.db, connection_id, lock=True)
        server = await get_mcp_server_by_id(self.db, server.id)
        self.require_current(server, connection, revision, generation)
        await active_mcp_user(self.db, user_id=user_id)
        state, verifier = (secrets.token_urlsafe(32) for _ in range(2))
        await delete_expired_oauth_attempts(self.db)
        self.db.add(
            MCPOAuthAttempt(
                state_hash=fingerprint(state),
                browser_hash=fingerprint(browser),
                connection_id=connection.id,
                generation=connection.generation,
                config_revision=revision,
                expires_at=utc_now() + timedelta(minutes=5),
                payload=encrypt({"verifier": verifier, "client": client, "metadata": metadata, "redirect_uri": callback}),
            )
        )
        await self.db.commit()
        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": callback,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
            "resource": server.url,
        }
        if config.scopes:
            params["scope"] = config.scopes
        endpoint = urlsplit(metadata["authorization_endpoint"])
        query = urlencode([*parse_qsl(endpoint.query, keep_blank_values=True), *params.items()])
        return urlunsplit(endpoint._replace(query=query))

    async def callback(self, code, state, browser, *, denied=False):
        """先消费事务再兑换授权码，提交前锁定并重新核对连接世代。"""
        attempt = await consume_oauth_attempt(self.db, fingerprint(state), fingerprint(browser))
        if not attempt:
            raise StateConflictError("连接器授权已失效，请重新连接")
        connection_id, generation, revision = attempt.connection_id, attempt.generation, attempt.config_revision
        payload = decrypt(attempt.payload)
        await self.db.commit()
        if denied or not code:
            raise PermissionDeniedError("连接器授权已取消")
        connection = await get_mcp_connection_by_id(self.db, connection_id)
        if not connection:
            raise StateConflictError("连接器连接已移除")
        await active_mcp_user(self.db, user_id=connection.user_id)
        server = await get_mcp_server_by_id(self.db, connection.server_id)
        self.require_current(server, connection, revision, generation)
        config = MCPOAuthConfig.model_validate(server.oauth_config)
        try:
            tokens = await token_request(
                payload["metadata"],
                config,
                payload["client"],
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": payload["verifier"],
                    "redirect_uri": payload["redirect_uri"],
                    "resource": server.url,
                },
            )
        except OAuthTokenRejected as exc:
            raise PermissionDeniedError("连接器授权码已失效，请重新连接") from exc
        # 网络调用结束后重新读取，避免断开、重连或配置修改发布旧授权。
        await self.db.rollback()
        connection = await get_mcp_connection_by_id(self.db, connection_id, lock=True)
        if not connection:
            raise StateConflictError("连接器连接已移除")
        server = await get_mcp_server_by_id(self.db, connection.server_id)
        self.require_current(server, connection, revision, generation)
        await active_mcp_user(self.db, user_id=connection.user_id)
        # 回调不携带前端 JWT；先保存一分钟结果，当前登录用户确认后才发布连接。
        result = secrets.token_urlsafe(32)
        self.db.add(
            MCPOAuthAttempt(
                state_hash=fingerprint(result),
                browser_hash=fingerprint(browser),
                connection_id=connection_id,
                generation=generation,
                config_revision=revision,
                expires_at=utc_now() + timedelta(minutes=1),
                payload=encrypt({"phase": "complete", "tokens": tokens, "client": payload["client"], "metadata": payload["metadata"]}),
            )
        )
        await self.db.commit()
        return result

    async def complete(self, result, browser, user_id):
        """由当前 JWT 身份确认回调归属，切换账号不能完成旧账号授权。"""
        from yuxi.modules.extensions.mcp.repository import get_oauth_attempt

        attempt = await get_oauth_attempt(self.db, fingerprint(result), fingerprint(browser), user_id)
        if not attempt:
            raise StateConflictError("连接器授权结果已失效或不属于当前账号")
        connection = await get_mcp_connection_by_id(self.db, attempt.connection_id, lock=True)
        server = await get_mcp_server_by_id(self.db, connection.server_id)
        self.require_current(server, connection, attempt.config_revision, attempt.generation)
        await active_mcp_user(self.db, user_id=user_id)
        payload = decrypt(attempt.payload)
        if payload.get("phase") != "complete":
            raise StateConflictError("连接器授权结果无效")
        self.store_tokens(connection, payload["tokens"], payload["client"], payload["metadata"])
        connection.config_revision = attempt.config_revision
        await self.db.delete(attempt)
        await self.db.commit()
        return server.slug

    async def disconnect(self, slug, user_id):
        """保留断开墓碑；已发起的回调和刷新均不能重新连接。"""
        server = await get_mcp_server(self.db, slug)
        if not server:
            raise ResourceNotFoundError("连接器不存在")
        connection = await mcp_connection(self.db, server.id, user_id, lock=True, create=True)
        connection.generation += 1
        connection.status, connection.credentials, connection.expires_at = "disconnected", None, None
        await self.db.commit()

    async def access_token(self, slug, uid, *, tool_name=None, expected_revision=None, catalog=False):
        """每次出站前读取有效用户与授权，锁内刷新并原子保存轮换令牌。"""

        async def policy(tool):
            """目录读取允许停用服务；运行调用继续检查启停。"""
            if catalog:
                return await self.require_catalog_policy(slug, uid, expected_revision)
            return await self.require_runtime_policy(slug, uid, tool, expected_revision)

        user, server = await policy(None)
        if server.auth_mode != "oauth":
            raise StateConflictError("此连接器不使用 OAuth")
        connection = await mcp_connection(self.db, server.id, user.id, lock=True)
        # 等待另一个 worker 刷新时策略可能改变；获锁后重新回读。
        _, server = await policy(tool_name)
        if not connection or connection_status(server, connection) != "connected":
            raise StateConflictError(f"请在扩展 → 连接器中连接 {server.name}", code="mcp_reconnect_required")
        credentials = decrypt(connection.credentials)
        if connection.expires_at and connection.expires_at <= utc_now() + timedelta(seconds=60):
            if not credentials.get("refresh_token"):
                await self.mark_reconnect(connection)
            config = MCPOAuthConfig.model_validate(server.oauth_config)
            try:
                tokens = await token_request(
                    credentials["metadata"],
                    config,
                    credentials["client"],
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": credentials["refresh_token"],
                        "resource": server.url,
                    },
                )
            except OAuthTokenRejected:
                await self.mark_reconnect(connection)
            tokens.setdefault("refresh_token", credentials.get("refresh_token"))
            self.store_tokens(connection, tokens, credentials["client"], credentials["metadata"])
            credentials = decrypt(connection.credentials)
            try:
                await policy(tool_name)
            except DomainError:
                # 已轮换的令牌必须落库，即使远端调用期间授权策略已改变。
                await self.db.commit()
                raise
        token = credentials["access_token"]
        await self.db.commit()
        return token

    async def reject_access_token(self, slug, uid, rejected_token):
        """远端 401 只撤销实际被拒绝的当前 token，不覆盖其他 worker 的新授权。"""
        user = await active_mcp_user(self.db, uid=uid)
        server = await get_mcp_server(self.db, slug)
        if not server:
            return
        connection = await mcp_connection(self.db, server.id, user.id, lock=True)
        if connection and connection.credentials and decrypt(connection.credentials).get("access_token") == rejected_token:
            connection.status, connection.credentials = "reconnect_required", None
            await self.db.commit()

    async def require_catalog_policy(self, slug, uid, expected_revision):
        """管理目录允许停用服务，仍只绑定调用者本人的当前配置。"""
        policy = await runtime_mcp_policy(self.db, slug, uid)
        if policy is None:
            raise PermissionDeniedError("连接器用户不可用")
        user, server = policy
        if server is None:
            raise ResourceNotFoundError("连接器不存在")
        if server.auth_mode not in PERSONAL_AUTH_MODES:
            raise StateConflictError("此连接器不需要个人授权")
        if expected_revision is not None and config_revision(server) != expected_revision:
            raise StateConflictError("连接器配置已改变，请重新运行", code="mcp_reconnect_required")
        return user, server

    async def require_runtime_policy(self, slug, uid, tool_name, expected_revision):
        """连接锁等待及远端刷新结束后重新检查当前执行策略。"""
        policy = await runtime_mcp_policy(self.db, slug, uid)
        if policy is None:
            raise PermissionDeniedError("连接器用户不可用")
        user, server = policy
        if not server or not server.enabled:
            raise ResourceNotFoundError("连接器不存在或已停用")
        if server.auth_mode not in PERSONAL_AUTH_MODES:
            raise StateConflictError("此连接器不需要个人授权")
        if expected_revision is not None and config_revision(server) != expected_revision:
            raise StateConflictError("连接器配置已改变，请重新运行", code="mcp_reconnect_required")
        if tool_name and tool_name in (server.disabled_tools or []):
            raise PermissionDeniedError("连接器工具已停用")
        return user, server

    async def require_server(self, slug):
        """个人授权只能用于已启用并明确配置 OAuth 的服务器。"""
        server = await get_mcp_server(self.db, slug)
        if not server or not server.enabled:
            raise ResourceNotFoundError("连接器不存在或已停用")
        if server.auth_mode != "oauth":
            raise StateConflictError("此连接器不需要个人授权")
        return server

    async def register_client(self, server, config, metadata, callback, revision):
        """动态客户端注册在服务器行锁内持久化，同一配置只注册一次。"""
        if config.registration == "static":
            client = {"client_id": config.client_id}
            if server.oauth_secret:
                client.update(decrypt(server.oauth_secret))
            return client
        server = await get_mcp_server_by_id(self.db, server.id, lock=True)
        if not server or not server.enabled or config_revision(server) != revision:
            raise StateConflictError("连接器配置已改变，请重试")
        if server.oauth_registration:
            saved = decrypt(server.oauth_registration)
            if saved.get("revision") == revision and saved.get("redirect_uri") == callback:
                return saved["client"]
        client = await request_json(
            "POST",
            metadata["registration_endpoint"],
            origins={origin(config.issuer_url)},
            json={
                "client_name": "Yuxi",
                "redirect_uris": [callback],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        if not isinstance(client.get("client_id"), str) or not client["client_id"]:
            raise UpstreamError("MCP 动态注册缺少 client_id")
        client = {key: client[key] for key in ("client_id", "client_secret") if key in client}
        server.oauth_registration = encrypt({"revision": revision, "redirect_uri": callback, "client": client})
        return client

    @staticmethod
    def require_current(server, connection, revision, generation):
        """状态变化发生后，旧授权响应不能成为新的授权事实。"""
        if not server or not server.enabled or not connection or config_revision(server) != revision or connection.generation != generation:
            raise StateConflictError("连接器配置或连接已改变，请重新连接")

    @staticmethod
    def store_tokens(connection, tokens, client, metadata):
        """只持久化经过结构验证的 Bearer，响应不写入日志和前端。"""
        token = tokens.get("access_token")
        expires = tokens.get("expires_in")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 32768
            or any(not 33 <= ord(c) <= 126 for c in token)
            or str(tokens.get("token_type", "")).lower() != "bearer"
            or (expires is not None and (type(expires) is not int or not 0 < expires <= 315360000))
        ):
            raise UpstreamError("MCP token 响应无效")
        connection.credentials = encrypt(
            {
                "access_token": token,
                "refresh_token": tokens.get("refresh_token"),
                "client": client,
                "metadata": metadata,
            }
        )
        connection.expires_at = utc_now() + timedelta(seconds=expires) if expires else None
        connection.status = "connected"

    async def mark_reconnect(self, connection):
        """失效令牌必须形成可观察状态，禁止退回共享凭据。"""
        connection.status, connection.credentials = "reconnect_required", None
        await self.db.commit()
        raise StateConflictError("连接器授权已失效，请重新连接", code="mcp_reconnect_required")
