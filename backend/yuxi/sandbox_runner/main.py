"""Sandbox runner HTTP 入口：唯一持有 Docker socket 的进程。"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from yuxi.sandbox_runner.engine import EngineError
from yuxi.sandbox_runner.manager import (
    ContainerManager,
    SandboxError,
)
from yuxi.sandbox_runner.settings import RunnerSettings

logger = logging.getLogger("yuxi.sandbox_runner")

_settings_lock = threading.Lock()
_manager_lock = threading.Lock()
_executor_lock = threading.Lock()
_settings: RunnerSettings | None = None


class ExecRequest(BaseModel):
    """执行一条归属于 Run 与 attempt 的命令。"""

    uid: str
    owner: str
    command_id: str
    command: str
    cwd: str
    workdir: str | None = None
    timeout_seconds: int
    output_budget: int
    stdin_b64: str | None = None
    python: bool = False
    envs: dict[str, str] = Field(default_factory=dict)


class CancelRequest(BaseModel):
    """取消指定命令的进程组。"""

    uid: str
    owner: str


class SkillFetchRequest(BaseModel):
    """可信应用发起的固定 Skills CLI 参数，不接收用户环境和命令文本。"""

    uid: str
    args: list[str] = Field(..., min_length=5, max_length=205)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动兜底 TTL 清理；runner 不持久化任何 Run 状态。"""
    task = asyncio.create_task(_expiry_loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            await asyncio.to_thread(get_manager().remove_all)
        except Exception:
            logger.exception("Sandbox container shutdown cleanup failed")
        if app.state.executor is not None:
            app.state.executor.shutdown(wait=False, cancel_futures=True)


async def _expiry_loop() -> None:
    """每 60 秒清理超过最大存活时间的受管容器。"""
    while True:
        try:
            removed = await asyncio.to_thread(get_manager().remove_expired)
            if removed:
                logger.warning("Removed expired sandbox containers: count=%s", removed)
        except Exception:
            logger.exception("Sandbox container expiry sweep failed")
        await asyncio.sleep(60)


app = FastAPI(
    title="Yuxi sandbox runner",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.state.manager = None
app.state.executor = None


def get_settings() -> RunnerSettings:
    """首次请求时读取并校验部署配置。"""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = RunnerSettings.from_env()
    return _settings


def get_manager() -> ContainerManager:
    """懒初始化 Docker 与镜像，失败后允许下一次请求重试。"""
    if app.state.manager is None:
        with _manager_lock:
            if app.state.manager is None:
                app.state.manager = ContainerManager(get_settings())
    return app.state.manager


def get_executor() -> ThreadPoolExecutor:
    """长命令使用独立线程池，避免占满 ASGI 的短请求线程池。"""
    if app.state.executor is None:
        with _executor_lock:
            if app.state.executor is None:
                settings = get_settings()
                # 线程数按容器与单容器并发 exec 的乘积推导，并夹在固定上下界内
                workers = max(8, min(256, settings.max_containers * settings.max_execs))
                app.state.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sandbox-exec")
    return app.state.executor


def require_token(authorization: str | None = Header(default=None)) -> None:
    """只接受共享 Bearer token，比较使用常量时间。"""
    expected = f"Bearer {get_settings().token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(dependencies=[Depends(require_token)])


@app.get("/healthz")
def health() -> JSONResponse:
    """健康检查同时证明 Docker、镜像和挂载根可用。"""
    try:
        return JSONResponse(get_manager().health())
    except Exception:
        logger.exception("Sandbox runner health check failed")
        return JSONResponse(status_code=503, content={"ok": False, "error": "sandbox_unavailable"})


@router.get("/v1/health")
def authenticated_health() -> dict:
    """认证后的就绪检查，证明 API/worker 与 runner 共享同一 token。"""
    return get_manager().health()


@router.post("/v1/runs/{run_id}/exec")
async def execute(run_id: str, body: ExecRequest) -> dict:
    """执行命令；长任务使用专用线程池，健康检查和取消不被饿死。"""
    loop = asyncio.get_running_loop()

    def operation() -> dict:
        return get_manager().execute(run_id=run_id, **body.model_dump())

    return await loop.run_in_executor(get_executor(), operation)


@router.post("/v1/skill-fetches/{request_id}")
async def fetch_skills(request_id: str, body: SkillFetchRequest) -> dict:
    """在独立一次性容器获取包，结束后确认容器清理再返回。"""
    loop = asyncio.get_running_loop()

    def operation() -> dict:
        """专用线程持有完整获取与清理生命周期。"""
        return get_manager().fetch_skills(uid=body.uid, request_id=request_id, args=body.args)

    return await loop.run_in_executor(get_executor(), operation)


@router.post("/v1/runs/{run_id}/exec/{command_id}/cancel")
def cancel_exec(run_id: str, command_id: str, body: CancelRequest) -> dict:
    """只终止指定执行标识的进程组。"""
    return {
        "cancelled": get_manager().cancel_exec(
            uid=body.uid,
            run_id=run_id,
            owner=body.owner,
            command_id=command_id,
        )
    }


@router.delete("/v1/runs/{run_id}")
def remove_run(run_id: str, uid: str = Query(...), owner: str | None = Query(default=None)) -> dict:
    """删除 Run 容器；owner 缺省表示终态清理。"""
    return get_manager().remove_run(uid=uid, run_id=run_id, owner=owner)


@router.delete("/v1/containers/{container_id}")
def remove_container(container_id: str) -> dict:
    """按容器 ID 清理 label 异常的受管容器。"""
    return get_manager().remove_container(container_id)


@router.get("/v1/runs")
def list_runs() -> dict:
    """列出全部受管容器与 label。"""
    return get_manager().list_containers()


@router.get("/v1/stats")
def stats() -> dict:
    """读取运行中与全部受管容器数量。"""
    return get_manager().stats()


@app.exception_handler(SandboxError)
def sandbox_error_handler(_, exc: SandboxError) -> JSONResponse:
    """把 runner 的受控错误映射为稳定 HTTP 状态。"""
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(EngineError)
def engine_error_handler(_, exc: EngineError) -> JSONResponse:
    """Docker/Podman 故障对外只暴露不可用，不泄露宿主路径。"""
    logger.error("Sandbox engine request failed: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "容器运行时不可用"})


app.include_router(router)
