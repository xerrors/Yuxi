"""仅由压测 Compose 入口装配的 API/模型发送时点与阶段探针。"""

import asyncio
import contextvars
import functools
import inspect
import json
import os
import resource
import sys
import time

import httpx

trace = contextvars.ContextVar("scheduling_probe", default=None)
FINE = os.getenv("MATRIX_FINE_TIMING") == "1"


def emit(event):
    """输出无凭据、无消息正文的结构化测量事实。"""
    print("MATRIX_TIMING " + json.dumps({"pid": os.getpid(), **event}), flush=True)


class ApiProbe:
    """在鉴权和请求体解析前记录应用入口时间。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        """只测量明确携带实验标记的 Run 提交请求。"""
        received_ns = time.time_ns()
        state = None
        if scope["type"] == "http" and scope.get("method") == "POST" and scope.get("path") == "/api/agent/runs":
            marker = dict(scope["headers"]).get(b"x-load-test-id", b"").decode("ascii", errors="ignore")
            if marker.startswith("matrix-") and len(marker) <= 64:
                emit({"event": "api_received", "request_id": marker, "time_ns": received_ns})
                if FINE:
                    state = {"request_id": marker, "start_ns": received_ns, "spans": [], "sent": False}
        token = trace.set(state)
        try:
            await self.app(scope, receive, send)
        finally:
            trace.reset(token)
            if state is not None:
                state["sent"] = True
                from .stage_probe import emit_spans

                emit_spans(sys.modules[__name__], state)


def create_app():
    """包裹实际 FastAPI 应用，不改变其依赖和路由。"""
    from server.main import app

    if FINE:
        from .stage_probe import install

        install(sys.modules[__name__], app)

    return ApiProbe(app)


def wrap(owner, name):
    """低开销语义跨度；异步等待不错误计入本请求 CPU。"""
    original = getattr(owner, name)
    if FINE:
        from .stage_probe import measured

        setattr(owner, name, measured(original, trace))
        return
    label = original.__module__ + "." + original.__qualname__
    synchronous = not inspect.iscoroutinefunction(original)

    def record(state, start, cpu):
        """在首次发送前保留跨度，避免采样生成阶段。"""
        if state is not None and not state["sent"]:
            end = time.time_ns()
            state["spans"].append(
                {
                    "name": label,
                    "start_ns": start,
                    "end_ns": end,
                    "ms": (end - start) / 1e6,
                    "cpu_ms": (time.thread_time() - cpu) * 1000 if synchronous else None,
                    "kind": "sync" if synchronous else "async",
                }
            )

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            state, start, cpu = trace.get(), time.time_ns(), time.thread_time()
            try:
                return await original(*args, **kwargs)
            finally:
                record(state, start, cpu)
    else:

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            state, start, cpu = trace.get(), time.time_ns(), time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                record(state, start, cpu)

    setattr(owner, name, wrapped)


def run():
    """使用实际 ARQ worker，仅在进程入口安装诊断包装。"""
    from yuxi.services.arq_worker import run_worker
    from yuxi.agents import BaseAgent
    from yuxi.agents.buildin.chatbot import graph
    from yuxi.agents.skills import service
    from yuxi.services import agent_run_manifest_service, chat_service, run_worker as worker
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    if FINE:
        from .stage_probe import install

        install(sys.modules[__name__])

    for name in (
        "_get_run",
        "mark_run_running",
        "_load_input_message",
        "_load_user",
        "_validate_run_workdir_binding",
        "prepare_and_record_run_execution",
        "_append_run_event_best_effort",
    ):
        wrap(worker, name)
    for name in ("_resolve_agent_runtime", "_persist_agent_run_langfuse_trace"):
        wrap(chat_service, name)
    wrap(agent_run_manifest_service, "prepare_agent_runtime_context")
    for name in (
        "sync_agent_context_skills",
        "load_chat_model",
        "resolve_configured_runtime_tools",
        "_build_middlewares",
        "create_agent",
        "create_memory_middleware",
        "create_subagent_task_middleware",
    ):
        wrap(graph, name)
    wrap(BaseAgent, "_get_checkpointer")
    wrap(graph.ChatbotAgent, "get_graph")
    wrap(AsyncPostgresSaver, "aget_tuple")
    wrap(service, "sync_user_accessible_skills")
    original_process = worker.process_agent_run

    @functools.wraps(original_process)
    async def process(ctx, run_id):
        """将任务 Run 绑定到异步上下文，避免相邻任务串绑。"""
        state = {
            "run_id": run_id,
            "start_ns": time.time_ns(),
            "start_cpu_ns": time.thread_time_ns(),
            "spans": [],
            "sent": False,
        }
        token = trace.set(state)
        try:
            return await original_process(ctx, run_id)
        finally:
            trace.reset(token)
            if FINE:
                from .stage_probe import emit_spans

                emit_spans(sys.modules[__name__], state)

    original_send = httpx.AsyncClient.send

    async def send(self, request, *args, **kwargs):
        """记录首个 Chat Completions HTTP 发送入口，不等待首 token。"""
        state = trace.get()
        if state is not None and not state["sent"] and request.url.path.endswith("/chat/completions"):
            observed_ns = time.time_ns()
            state["stop_perf_ns"] = time.perf_counter_ns()
            state["stop_cpu_ns"] = time.thread_time_ns()
            state["sent"] = True
            emit({"event": "model_send", **state, "spans": [] if FINE else state["spans"], "time_ns": observed_ns})
        return await original_send(self, request, *args, **kwargs)

    original_startup = worker.WorkerSettings.on_startup

    async def monitor():
        """采样循环迟到量和进程 CPU；CPU 百分比以一个核心为 100%。"""
        last_sample = time.monotonic()
        while True:
            start = time.perf_counter()
            await asyncio.sleep(0.02)
            lag = (time.perf_counter() - start) * 1000 - 20
            if lag > 30:
                emit({"event": "loop_lag", "time_ns": time.time_ns(), "ms": lag})
            if time.monotonic() - last_sample >= 1:
                emit(
                    {
                        "event": "process_sample",
                        "time_ns": time.time_ns(),
                        "cpu_seconds": time.process_time(),
                        "loop_cpu_seconds": time.thread_time(),
                        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                    }
                )
                last_sample = time.monotonic()

    async def startup(ctx):
        """保留原启动流程并开启轻量迟到采样。"""
        await original_startup(ctx)
        ctx["matrix_monitor"] = asyncio.create_task(monitor())
        emit({"event": "worker_ready", "time_ns": time.time_ns()})

    httpx.AsyncClient.send = send
    worker.WorkerSettings.functions = [process, *worker.WorkerSettings.functions[1:]]
    worker.WorkerSettings.on_startup = startup
    run_worker(worker.WorkerSettings)


if __name__ == "__main__":
    run()
