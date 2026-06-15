import asyncio
import os
import sys

# ==============================================================================
# 解决 Windows 下 psycopg 异步模式不支持 ProactorEventLoop 的问题
# 注意：这段代码必须放在应用的极早期，最好在导入 FastAPI 或初始化数据库之前
# ==============================================================================
if sys.platform == "win32":
    # 把当前文件 (main.py) 的上一级的上一级 (即根目录 Yuxi) 加入到 sys.path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.routers import router
from server.utils.lifespan import lifespan
from server.utils.common_utils import setup_logging
from server.utils.access_log_middleware import AccessLogMiddleware

# 设置日志配置
setup_logging()

RATE_LIMIT_MAX_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ENDPOINTS = {("/api/auth/token", "POST")}

# In-memory login attempt tracker to reduce brute-force exposure per worker
_login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempt_lock = asyncio.Lock()

app = FastAPI(lifespan=lifespan)
# 所有业务接口统一挂载到 /api，具体分组在 server.routers 中集中注册。
app.include_router(router, prefix="/api")

# CORS 设置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _extract_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        normalized_path = request.url.path.rstrip("/") or "/"
        request_signature = (normalized_path, request.method.upper())

        if request_signature in RATE_LIMIT_ENDPOINTS:
            client_ip = _extract_client_ip(request)
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

        return await call_next(request)


# 添加访问日志中间件（记录请求处理时间）
app.add_middleware(AccessLogMiddleware)

# 添加登录限流中间件
app.add_middleware(LoginRateLimitMiddleware)

if __name__ == "__main__":
    # uvicorn.run(app, host="0.0.0.0", port=5050, threads=10, workers=10, reload=True)

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=5050,
        reload=True,
        # 与 docker-compose 开发环境保持一致，避免 package 下代码变更不触发热重载。
        reload_dirs=["server", "package"],
    )
