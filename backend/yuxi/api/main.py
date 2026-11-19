# ruff: noqa: E402  # 入口必须先加载环境和事件循环策略。
"""API 进程入口：装配路由、CORS、访问日志与登录限流中间件。"""

import asyncio
import os
import sys

from yuxi.bootstrap.environment import load_environment

load_environment()

# Windows 下 psycopg 异步模式不支持 ProactorEventLoop；必须先于 FastAPI 与数据库导入设置策略。
if sys.platform == "win32":
    # 使直接运行本文件时能导入仓库根目录下的 yuxi 包
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from yuxi.api.errors import domain_error_handler
from yuxi.api.lifespan import lifespan
from yuxi.api.middleware.access_log import AccessLogMiddleware, extract_client_ip
from yuxi.api.routers import router
from yuxi.bootstrap.capabilities import knowledge_enabled
from yuxi.infrastructure.observability.logging import setup_logging
from yuxi.shared.errors import DomainError

setup_logging()

RATE_LIMIT_MAX_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ENDPOINTS = {("/api/auth/token", "POST")}
DEFAULT_DEVELOPMENT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
EXPLICIT_CORS_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")
EXPLICIT_CORS_HEADERS = (
    "Accept",
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "If-None-Match",
    "Last-Event-ID",
    "X-End-User-Id",
    "X-Requested-With",
)

# 进程内登录尝试记录，收窄单个 worker 的暴力破解窗口。
_login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempt_lock = asyncio.Lock()


def parse_cors_origins() -> list[str]:
    """读取 CORS 来源配置；生产环境未配置时默认拒绝跨域。"""
    value = os.getenv("YUXI_CORS_ORIGINS")
    origins = [origin.strip() for origin in (value or "").split(",") if origin.strip()]
    if origins:
        return origins

    environment = (os.getenv("YUXI_ENV") or "development").strip().lower()
    if environment in {"production", "prod"}:
        return []

    return list(DEFAULT_DEVELOPMENT_CORS_ORIGINS)


def build_cors_options(origins: list[str] | None = None) -> dict[str, object]:
    """组装 CORSMiddleware 参数；通配符来源必须关闭凭据。"""
    allow_origins = parse_cors_origins() if origins is None else origins
    if "*" in allow_origins:
        return {
            "allow_origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
            "expose_headers": ["ETag"],
        }

    return {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        "allow_methods": list(EXPLICIT_CORS_METHODS),
        "allow_headers": list(EXPLICIT_CORS_HEADERS),
        "expose_headers": ["Content-Disposition", "ETag", "X-Lock-Remaining", "X-App-Id"],
    }


app = FastAPI(
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "agents-public-v1",
            "description": (
                "对外智能体 Session API。JWT 或 Bearer API Key 鉴权；APP Key 用 X-End-User-Id 区分终端用户。"
                "创建和事件提交必须携带 Idempotency-Key。支持 OpenAI Agents 核心会话协议子集，Yuxi 扩展有明确说明。"
                "SSE 为长期订阅，调用方按 Turn 终态回读结果并主动关闭。"
            ),
            "externalDocs": {
                "description": "接入与差异说明",
                "url": "https://github.com/xerrors/Yuxi/blob/main/docs/advanced/agents-public-api.md",
            },
        }
    ],
)
# 所有业务接口统一挂载到 /api，具体分组在 yuxi.api.routers 中集中注册。
app.add_exception_handler(DomainError, domain_error_handler)
app.include_router(router, prefix="/api")
if knowledge_enabled():
    from yuxi.api.routers.public_v1.knowledge_mcp import create_knowledge_mcp

    app.state.knowledge_mcp = create_knowledge_mcp()
    app.router.routes.append(app.state.knowledge_mcp.route)

app.add_middleware(
    CORSMiddleware,
    **build_cors_options(),
)


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """限制登录接口的进程内尝试频率。"""

    async def dispatch(self, request: Request, call_next):
        normalized_path = request.url.path.rstrip("/") or "/"
        request_signature = (normalized_path, request.method.upper())

        if request_signature not in RATE_LIMIT_ENDPOINTS:
            return await call_next(request)

        client_ip = extract_client_ip(request)
        now = time.monotonic()

        async with _attempt_lock:
            attempt_history = _login_attempts[client_ip]

            while attempt_history and now - attempt_history[0] > RATE_LIMIT_WINDOW_SECONDS:
                attempt_history.popleft()

            if len(attempt_history) >= RATE_LIMIT_MAX_ATTEMPTS:
                retry_after = int(max(1, RATE_LIMIT_WINDOW_SECONDS - (now - attempt_history[0])))
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "登录尝试过于频繁，请稍后再试"},
                    headers={"Retry-After": str(retry_after)},
                )

            attempt_history.append(now)

        response = await call_next(request)

        if response.status_code < 400:
            async with _attempt_lock:
                _login_attempts.pop(client_ip, None)

        return response


# 访问日志记录请求处理时间；登录限流在外层先拦截，避免被限流请求进入业务日志。
app.add_middleware(AccessLogMiddleware)
app.add_middleware(LoginRateLimitMiddleware)


@app.middleware("http")
async def add_app_source_header(request: Request, call_next):
    """回显 API Key 绑定的调用来源，并禁止 OIDC 回调路径被缓存。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/oidc/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    app_id = getattr(request.state, "app_id", None)
    if app_id:
        response.headers["X-App-Id"] = app_id
    return response


if __name__ == "__main__":
    uvicorn.run(
        "yuxi.api.main:app",
        host="0.0.0.0",
        port=5050,
        access_log=False,
        reload=True,
        reload_dirs=["yuxi"],
    )
