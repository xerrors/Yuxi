"""真实 TCP、PostgreSQL 和 Redis 验证身份提供及用途隔离。"""

import asyncio
import base64
import hashlib
import os
import secrets
import socket
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.api.dependencies.auth import get_db
from yuxi.api.routers.identity.provider import router
from yuxi.modules.identity.models import Department, RevokedAccessToken, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.identity.services import provider
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def scenario(monkeypatch, tmp_path):
    """独立 schema 与随机 namespace，不修改现有用户或部署配置。"""
    schema = "pytest_provider_" + uuid4().hex
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(os.environ["REDIS_URL"])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "provider.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    for name, value in {
        "ENABLED": "true",
        "CLIENT_ID": "test-client",
        "CLIENT_SECRET": secrets.token_urlsafe(32),
        "REDIRECT_URI": origin + "/client/callback",
        "PRIVATE_KEY_FILE": str(pem),
        "ISSUER": origin + "/api/auth/provider",
    }.items():
        monkeypatch.setenv("YUXI_OIDC_PROVIDER_" + name, value)
    monkeypatch.setenv("YUXI_PUBLIC_URL", origin)

    async def get_redis():
        """协议用真实 Redis 的原子操作。"""
        return redis

    monkeypatch.setattr(provider, "get_async_redis_client", get_redis)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def request_db():
        """每个 HTTP 请求拥有独立数据库事务。"""
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False))
    task = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            for model in (Department, User, RevokedAccessToken):
                await conn.run_sync(lambda sync, model=model: model.__table__.create(sync))
        async with factory() as db:
            department = Department(name="Provider test")
            db.add(department)
            await db.flush()
            user = User(uid="stable-person", username="Person", password_hash="!disabled", department_id=department.id)
            db.add(user)
            await db.commit()
            token = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=origin, trust_env=False, follow_redirects=False) as http:
            yield {"http": http, "app": app, "db": factory, "key": key, "redis": redis, "origin": origin, "token": token, "id": user.id}
    finally:
        if task:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
        sock.close()
        prefix = provider.ProviderService().prefix
        keys = [item async for item in redis.scan_iter(prefix + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def authorize(scenario, **overrides):
    """经过真实 GET 和登录 POST 取得授权码，独立固定预期声明。"""
    verifier = secrets.token_urlsafe(48)
    params = {
        "client_id": "test-client",
        "redirect_uri": scenario["origin"] + "/client/callback",
        "response_type": "code",
        "scope": "openid profile",
        "state": "client-state",
        "nonce": "client-nonce",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
    } | overrides
    start = await scenario["http"].get("/api/auth/provider/authorize", params=params)
    assert start.status_code == 302, start.text
    assert "httponly" in start.headers["set-cookie"].lower()
    response = await scenario["http"].post(
        "/api/auth/provider/authorize", json=params, headers={"Authorization": "Bearer " + scenario["token"]}
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert query["state"] == ["client-state"]
    return query["code"][0], verifier, params


def token_form(scenario, code, verifier):
    """构造标准 OAuth 表单，不从服务返回值生成 expected claims。"""
    return {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": scenario["origin"] + "/client/callback",
        "client_id": "test-client",
        "client_secret": os.environ["YUXI_OIDC_PROVIDER_CLIENT_SECRET"],
    }


async def test_real_protocol_claims_and_code_single_consumption(scenario):
    """公开公钥独立验证身份，重复与并发兑换只发布一份结果。"""
    http = scenario["http"]
    metadata = (await http.get("/api/auth/provider/.well-known/openid-configuration")).json()
    assert metadata["issuer"] == scenario["origin"] + "/api/auth/provider"
    public = (await http.get(metadata["jwks_uri"])).json()["keys"][0]
    assert "d" not in public and public["alg"] == "RS256"
    code, verifier, _ = await authorize(scenario)
    responses = await asyncio.gather(*[http.post("/api/auth/provider/token", data=token_form(scenario, code, verifier)) for _ in range(2)])
    assert sorted(r.status_code for r in responses) == [200, 400]
    result = next(r.json() for r in responses if r.status_code == 200)
    claims = jwt.decode(
        result["id_token"], jwt.PyJWK.from_dict(public).key, algorithms=["RS256"], audience="test-client", issuer=metadata["issuer"]
    )
    assert claims["sub"] == "stable-person" and claims["nonce"] == "client-nonce" and claims["name"] == "Person"
    assert claims["exp"] - claims["iat"] == 300
    info = await http.get("/api/auth/provider/userinfo", headers={"Authorization": "Bearer " + result["access_token"]})
    assert info.json() == {"sub": "stable-person", "name": "Person", "preferred_username": "Person"}
    wrong = await http.get("/api/auth/provider/userinfo", headers={"Authorization": "Bearer " + result["id_token"]})
    assert wrong.status_code == 401 and wrong.json()["error"] == "invalid_token"
    async with scenario["db"]() as db:
        await db.execute(update(User).where(User.id == scenario["id"]).values(is_deleted=1))
        await db.commit()
    rejected = await http.get("/api/auth/provider/userinfo", headers={"Authorization": "Bearer " + result["access_token"]})
    assert rejected.status_code == 403 and rejected.json()["error"] == "access_denied"


@pytest.mark.parametrize(
    "changed,error",
    [
        ({"client_secret": "wrong"}, "invalid_client"),
        ({"client_id": "用户"}, "invalid_client"),
        ({"code_verifier": "x" * 64}, "invalid_grant"),
        ({"redirect_uri": "https://attacker.invalid/callback"}, "invalid_grant"),
        ({"grant_type": "refresh_token"}, "unsupported_grant_type"),
    ],
)
async def test_invalid_exchange_does_not_consume_legitimate_code(scenario, changed, error):
    """错误凭据、PKCE 和回调不能消费其他客户端的合法授权码。"""
    code, verifier, _ = await authorize(scenario)
    form = token_form(scenario, code, verifier)
    response = await scenario["http"].post("/api/auth/provider/token", data=form | changed)
    assert response.json()["error"] == error
    valid = await scenario["http"].post("/api/auth/provider/token", data=form)
    assert valid.status_code == 200, valid.text


@pytest.mark.parametrize(
    "changed,error",
    [
        ({"redirect_uri": "https://attacker.invalid/callback"}, "invalid_request"),
        ({"client_id": "other"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"scope": "openid admin"}, "invalid_scope"),
        ({"response_type": "token"}, "unsupported_response_type"),
    ],
)
async def test_unsafe_authorization_never_redirects(scenario, changed, error):
    """不可信请求只返回站内错误，不能形成开放重定向。"""
    _, _, params = await authorize(scenario)
    response = await scenario["http"].get("/api/auth/provider/authorize", params=params | changed)
    assert response.status_code == 400 and response.json()["error"] == error
    assert "location" not in response.headers


async def test_browser_binding_and_locked_identity(scenario):
    """同登录用户仍不能更换 GET 绑定的授权参数，锁定后也不能兑换。"""
    code, verifier, params = await authorize(scenario)
    altered = await scenario["http"].post(
        "/api/auth/provider/authorize", json=params | {"nonce": "different"}, headers={"Authorization": "Bearer " + scenario["token"]}
    )
    assert altered.status_code == 400 and altered.json()["error"] == "invalid_request"
    async with scenario["db"]() as db:
        await db.execute(update(User).where(User.id == scenario["id"]).values(login_locked_until=utc_now() + timedelta(minutes=2)))
        await db.commit()
    rejected = await scenario["http"].post("/api/auth/provider/token", data=token_form(scenario, code, verifier))
    assert rejected.status_code == 403 and rejected.json()["error"] == "access_denied"


async def test_duplicate_and_non_string_protocol_parameters_are_rejected(scenario):
    """HTTP 参数污染与上传对象不能进入字符串凭证校验。"""
    code, verifier, params = await authorize(scenario)
    repeated = await scenario["http"].get("/api/auth/provider/authorize", params=list(params.items()) + [("state", "second")])
    assert repeated.status_code == 400 and repeated.json()["error"] == "invalid_request"
    response = await scenario["http"].post("/api/auth/provider/token", files={"code": ("code.txt", b"upload")})
    assert response.status_code == 400 and response.json()["error"] == "invalid_request"
    from urllib.parse import urlencode

    duplicated = urlencode(token_form(scenario, code, verifier)) + "&code=other"
    response = await scenario["http"].post(
        "/api/auth/provider/token", content=duplicated, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_request"


async def test_basic_authentication_and_expired_code(scenario):
    """静态机密客户端支持 Basic，过期授权码不能换取身份。"""
    code, verifier, _ = await authorize(scenario)
    form = token_form(scenario, code, verifier)
    secret = form.pop("client_secret")
    form.pop("client_id")
    response = await scenario["http"].post("/api/auth/provider/token", data=form, auth=("test-client", secret))
    assert response.status_code == 200 and response.json()["token_type"] == "Bearer"
    code, verifier, _ = await authorize(scenario)
    await scenario["redis"].delete(provider.ProviderService().code_key(code))
    expired = await scenario["http"].post("/api/auth/provider/token", data=token_form(scenario, code, verifier))
    assert expired.status_code == 400 and expired.json()["error"] == "invalid_grant"
