"""仅用于模型前诊断的协程执行切片和嵌套 I/O 时间线。"""

import asyncio
import contextvars
import functools
import fcntl
import gc
import importlib
import inspect
import itertools
import os
import sys
import threading
import time
from collections.abc import Coroutine

parent_span = contextvars.ContextVar("stage_parent", default=None)
_installed = False
span_ids = itertools.count()


class SlicedCoroutine(Coroutine):
    """只累计当前协程被恢复执行的切片，不累计等待期间其他任务的 CPU。"""

    def __init__(self, coroutine, span, state=None):
        self.coroutine = coroutine
        self.span = span
        self.state = state

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        return self._step(self.coroutine.send, value)

    def throw(self, *args):
        return self._step(self.coroutine.throw, *args)

    def close(self):
        return self.coroutine.close()

    def _step(self, method, *args):
        """计时覆盖一次 send/throw，保留取消与异常传播。"""
        start, cpu = time.perf_counter_ns(), time.thread_time_ns()
        active = self.state is None or not self.state["sent"]
        try:
            return method(*args)
        finally:
            if active:
                end, end_cpu = time.perf_counter_ns(), time.thread_time_ns()
                if self.state and self.state.get("stop_perf_ns"):
                    end, end_cpu = self.state["stop_perf_ns"], self.state["stop_cpu_ns"]
                self.span["active_ms"] += (end - start) / 1e6
                self.span["cpu_ms"] += (end_cpu - cpu) / 1e6
                self.span["resumes"] += 1


def new_span(state, name, *, kind="async"):
    """记录开始时点与父跨度，以便区分嵌套和并行区间。"""
    span = {
        "id": next(span_ids),
        "parent": parent_span.get(),
        "name": name,
        "kind": kind,
        "start_ns": time.time_ns(),
        "end_ns": None,
        "active_ms": None if kind in {"thread", "gc"} else 0.0,
        "cpu_ms": 0.0,
        "resumes": 0,
        "thread": threading.get_ident(),
    }
    state["spans"].append(span)
    return span


def finish_span(span):
    """总跨度包含 I/O 与恢复等待，执行切片独立记录。"""
    span["end_ns"] = time.time_ns()
    span["ms"] = (span["end_ns"] - span["start_ns"]) / 1e6


def measured(original, trace, *, name=None, greenlet=False):
    """包装普通函数或协程，不改变参数、结果与异常。"""
    if getattr(original, "_stage_measured", False):
        return original
    label = name or original.__module__ + "." + original.__qualname__

    @functools.wraps(original)
    async def async_wrapped(*args, **kwargs):
        state = trace.get()
        if state is None or state["sent"]:
            return await original(*args, **kwargs)
        span = new_span(state, label)
        token = parent_span.set(span["id"])
        try:
            return await SlicedCoroutine(original(*args, **kwargs), span, state)
        finally:
            parent_span.reset(token)
            finish_span(span)

    @functools.wraps(original)
    def sync_wrapped(*args, **kwargs):
        state = trace.get()
        if state is None or state["sent"]:
            return original(*args, **kwargs)
        span = new_span(state, label, kind="greenlet" if greenlet else "sync")
        token = parent_span.set(span["id"])
        cpu = time.thread_time_ns()
        try:
            return original(*args, **kwargs)
        finally:
            finish_span(span)
            # SQLAlchemy 同步 greenlet 会让出执行权，其线程 CPU 差值不能归属本请求。
            span["cpu_ms"] = None if greenlet else (time.thread_time_ns() - cpu) / 1e6
            span["active_ms"] = None if greenlet else span["ms"]
            parent_span.reset(token)

    wrapped = async_wrapped if inspect.iscoroutinefunction(original) else sync_wrapped
    wrapped._stage_measured = True
    return wrapped


def emit_spans(probe, state):
    """请求结束后分块输出，避免大量序列化阻塞首次 HTTP 发送。"""
    identity = {key: state[key] for key in ("request_id", "run_id") if key in state}
    for offset in range(0, len(state["spans"]), 30):
        probe.emit({"event": "stage_spans", **identity, "spans": state["spans"][offset : offset + 30]})
    probe.emit({"event": "stages_done", **identity})


def install(probe, app=None):
    """装配细粒度探针；仅实验进程调用，不进入业务源码。"""
    global _installed
    if _installed:
        return
    _installed = True
    modules = (
        "yuxi.services.agent_request_service",
        "yuxi.services.agent_request_queue_service",
        "yuxi.services.agent_run_manifest_service",
        "yuxi.services.workdir_service",
        "yuxi.services.memory_service",
        "yuxi.services.run_queue_service",
        "yuxi.agents.context",
        "yuxi.agents.skills.runtime",
        "yuxi.agents.skills.service",
        "yuxi.agents.backends.composite",
        "yuxi.agents.buildin.chatbot.graph",
        "server.utils.auth_middleware",
    )
    for name in modules:
        importlib.import_module(name)

    replacements = {}
    for module_name, module in list(sys.modules.items()):
        if module is None or not (module_name in modules or module_name.startswith("yuxi.repositories.")):
            continue
        for value in vars(module).copy().values():
            if inspect.isfunction(value) and value.__module__ == module_name:
                if not inspect.isgeneratorfunction(value) and not inspect.isasyncgenfunction(value):
                    replacements[value] = measured(value, probe.trace)
            elif inspect.isclass(value) and value.__module__ == module_name:
                for method_name, method in vars(value).copy().items():
                    if inspect.iscoroutinefunction(method):
                        setattr(value, method_name, measured(method, probe.trace))
    # 同一函数可能以 from import 绑定到多个 consumer，均替换以覆盖实际装配路径。
    for module_name, module in list(sys.modules.items()):
        if module is not None and module_name.startswith(("yuxi.", "server.")):
            for name, value in vars(module).copy().items():
                if inspect.isfunction(value) and value in replacements:
                    setattr(module, name, replacements[value])
    if app is not None:

        def patch_dependency(dependant):
            """FastAPI 已解析的依赖保留原签名，仅替换执行 callable。"""
            if inspect.isfunction(dependant.call) and dependant.call in replacements:
                dependant.call = replacements[dependant.call]
            for child in dependant.dependencies:
                patch_dependency(child)

        for route in app.routes:
            if hasattr(route, "dependant"):
                patch_dependency(route.dependant)

    from redis.asyncio import ConnectionPool, Redis
    from redis.asyncio.client import Pipeline
    from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection, AsyncAdapt_asyncpg_cursor
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.pool import QueuePool
    from psycopg_pool import AsyncConnectionPool
    from psycopg import AsyncConnection, AsyncCursor, AsyncPipeline
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.pregel._loop import AsyncPregelLoop
    from langchain_core.callbacks.manager import AsyncCallbackManager
    from langchain_openai import ChatOpenAI
    from openai import AsyncOpenAI

    for owner, methods in (
        (AsyncSession, ("execute", "scalar", "scalars", "flush", "commit", "rollback")),
        (AsyncAdapt_asyncpg_cursor, ("_prepare_and_execute",)),
        (AsyncAdapt_asyncpg_connection, ("_prepare", "_commit_and_discard")),
        (Redis, ("execute_command",)),
        (Pipeline, ("execute",)),
        (ConnectionPool, ("get_connection",)),
        (AsyncConnectionPool, ("getconn", "putconn")),
        (AsyncConnection, ("execute", "commit")),
        (AsyncCursor, ("execute", "executemany", "fetchone")),
        (AsyncPipeline, ("__aenter__", "__aexit__", "sync")),
        (AsyncPostgresSaver, ("aget_tuple", "aput", "aput_writes")),
        (AsyncPregelLoop, ("__aenter__", "tick", "after_tick")),
        (AsyncCallbackManager, ("on_chat_model_start",)),
        (ChatOpenAI, ("_get_request_payload", "bind_tools", "_convert_input")),
        (AsyncOpenAI, ("request", "_build_request", "_prepare_request")),
    ):
        for name in methods:
            setattr(owner, name, measured(getattr(owner, name), probe.trace))
    QueuePool._do_get = measured(QueuePool._do_get, probe.trace, greenlet=True)
    original_saver_init = AsyncPostgresSaver.__init__

    @functools.wraps(original_saver_init)
    def saver_init(self, *args, **kwargs):
        """直接度量 checkpoint 自己的互斥锁，不把 SQL 时间猜成锁等待。"""
        original_saver_init(self, *args, **kwargs)
        self.lock.acquire = measured(self.lock.acquire, probe.trace, name="checkpoint.lock.acquire")

    AsyncPostgresSaver.__init__ = saver_init
    for owner, methods in (
        (os, ("open", "read", "write", "stat", "fstat", "mkdir", "rename", "unlink")),
        (fcntl, ("flock",)),
    ):
        for name in methods:
            setattr(owner, name, measured(getattr(owner, name), probe.trace))

    original_to_thread = asyncio.to_thread

    async def to_thread(func, /, *args, **kwargs):
        """拆开线程排队、实际工作和完成后的事件循环恢复延迟。"""
        state = probe.trace.get()
        if state is None or state["sent"]:
            return await original_to_thread(func, *args, **kwargs)
        span = new_span(state, "thread:" + getattr(func, "__qualname__", type(func).__name__), kind="thread")

        def work():
            """在真实文件线程内记录开始和完成，不记录路径或内容。"""
            span["work_start_ns"] = time.time_ns()
            cpu = time.thread_time_ns()
            try:
                return func(*args, **kwargs)
            finally:
                span["work_end_ns"] = time.time_ns()
                span["cpu_ms"] = (time.thread_time_ns() - cpu) / 1e6

        try:
            return await original_to_thread(work)
        finally:
            finish_span(span)
            if "work_end_ns" in span:
                span["queue_ms"] = (span["work_start_ns"] - span["start_ns"]) / 1e6
                span["work_ms"] = (span["work_end_ns"] - span["work_start_ns"]) / 1e6
                span["resume_ms"] = (span["end_ns"] - span["work_end_ns"]) / 1e6

    asyncio.to_thread = to_thread

    gc_started = {}

    def gc_event(phase, info):
        """定位 GC 暂停；无请求上下文的周期不计入请求跨度。"""
        state = probe.trace.get()
        if phase == "start" and state is not None and not state["sent"]:
            gc_started[info["generation"]] = new_span(state, f"gc.generation{info['generation']}", kind="gc")
        elif phase == "stop":
            span = gc_started.pop(info["generation"], None)
            if span is not None:
                finish_span(span)
                span["cpu_ms"] = None

    gc.callbacks.append(gc_event)
