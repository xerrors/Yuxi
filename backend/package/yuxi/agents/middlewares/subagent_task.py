from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware, ContextT, ModelRequest, ModelResponse, ResponseT
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel

from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

# 四个内建工具的签名固定；仅复用参数类型，闭包与父 Run 仍逐次创建。
_TOOL_INPUT_SCHEMAS: dict[str, type[BaseModel]] = {}


def _subagent_run_service_module():
    from yuxi.services import subagent_run_service

    return subagent_run_service


def _async_only_tool(*, name: str, coroutine: Callable[..., Awaitable[Any]], description: str) -> StructuredTool:
    """后台子智能体工具只在异步链路执行；仅声明 coroutine，同步调用由 LangChain 直接报错。"""
    tool = StructuredTool.from_function(
        name=name,
        coroutine=coroutine,
        description=description,
        args_schema=_TOOL_INPUT_SCHEMAS.get(name),
        infer_schema=True,
    )
    _TOOL_INPUT_SCHEMAS.setdefault(name, tool.args_schema)
    return tool


SUBAGENT_SYSTEM_PROMPT = """## 子智能体

使用 `subagent_start` 把复杂、独立、需要隔离上下文的任务交给可用子智能体。
它立即返回 run_id 和 thread_id；先派发互不依赖的任务，再继续你自己的工作。
需要结果时调用 `subagent_await(run_id)`；需要查看进度或取消时使用
`subagent_status` 或 `subagent_cancel`。短任务也遵循先 start、后 await 的顺序。

- description 写清目标、上下文和期望输出，subagent_slug 必须选择下方可用项。
- 新任务不填 thread_id；继续既有子线程时使用之前返回的 thread_id。
- 同一 thread_id 不可并发写入；忙碌时返回 busy，不隐藏排队。
- 父运行结束会取消仍活跃的子 Run，给出最终答复前应 await 所需结果或明确取消。
- 简单问题或少量直接工具调用不要委派。
- 不通过 shell、curl、HTTP API 或命令行间接调用子智能体。

Available subagent slugs:

{available_agents}"""

SUBAGENT_START_DESCRIPTION = """Start a configured Yuxi subagent asynchronously.

Returns a child thread ID for future continuation and a run ID for status/cancel/result checks.
Use this for all subagent work; call subagent_await when the result is needed.
If `thread_id` is provided, continue that thread when no active run is currently writing to it."""

SUBAGENT_STATUS_DESCRIPTION = """Check a subagent run status by run_id.

Returns the current run status, a compact progress summary with the latest 3 readable messages, and the final result
when the run has reached a terminal status."""

SUBAGENT_CANCEL_DESCRIPTION = """Cancel a running subagent run by run_id."""

SUBAGENT_AWAIT_DESCRIPTION = """Wait for a subagent run to finish and return its final result."""

SUBAGENT_DESCRIPTION_ARG = "需要子智能体独立完成的任务描述，包含必要上下文和期望输出。"
SUBAGENT_SLUG_ARG = "要调用的子智能体 slug，必须是工具描述中列出的可用项之一。"
ASYNC_THREAD_ID_ARG = "可选。要继续的后台子智能体线程 ID，来自之前 subagent_start 返回的 thread_id；新任务不要填写。"
SUBAGENT_RUN_ID_ARG = "子智能体运行 ID，由 subagent_start 返回。"


async def create_subagent_task_middleware(parent_context) -> YuxiSubAgentMiddleware | None:
    """根据父智能体上下文加载可用子智能体，并在存在可调用项时创建子智能体中间件。"""
    selected_slugs = [
        str(slug).strip() for slug in (getattr(parent_context, "subagents", None) or []) if str(slug).strip()
    ]
    uid = str(getattr(parent_context, "uid", "") or "").strip()
    if not uid:
        return None

    async with pg_manager.get_async_session_context() as db:
        user = await UserRepository().get_by_uid_with_db(db, uid)
        if user is None:
            return None
        repo = AgentRepository(db)
        if selected_slugs:
            subagents: list[Agent] = []
            seen: set[str] = set()
            for slug in selected_slugs:
                if slug in seen:
                    continue
                seen.add(slug)
                agent = await repo.get_visible_by_slug(slug=slug, user=user, kind="subagent")
                if agent:
                    subagents.append(agent)
        else:
            subagents = await repo.list_visible_subagents(user=user)

    if not subagents:
        return None
    return YuxiSubAgentMiddleware(parent_context=parent_context, subagents=subagents)


class YuxiSubAgentMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    def __init__(self, *, parent_context, subagents: list[Agent]) -> None:
        super().__init__()
        self.parent_context = parent_context
        self.subagents = {agent.slug: agent for agent in subagents}
        available_agents = "\n".join(f"- {agent.slug}: {agent.description or agent.name}" for agent in subagents)
        self.system_prompt = SUBAGENT_SYSTEM_PROMPT.format(available_agents=available_agents)
        self.tools = self._build_subagent_tools(available_agents)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(
            request.override(system_message=append_to_system_message(request.system_message, self.system_prompt))
        )

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(
            request.override(system_message=append_to_system_message(request.system_message, self.system_prompt))
        )

    def _build_subagent_tools(self, available_agents: str) -> list[StructuredTool]:
        """构建后台子智能体生命周期工具：start/status/cancel/await。"""

        async def asubagent_start(
            description: Annotated[str, SUBAGENT_DESCRIPTION_ARG],
            subagent_slug: Annotated[str, SUBAGENT_SLUG_ARG],
            runtime: ToolRuntime,
            thread_id: Annotated[str | None, ASYNC_THREAD_ID_ARG] = None,
        ) -> str | Command:
            if subagent_slug not in self.subagents:
                allowed = ", ".join(f"`{slug}`" for slug in self.subagents)
                return f"无法调用子智能体 {subagent_slug}，可用子智能体只有：{allowed}"
            if not runtime.tool_call_id:
                raise ValueError("Tool call ID is required for subagent invocation")

            parent_runtime, runtime_error = self._require_parent_runtime("无法启动子智能体")
            if runtime_error:
                return runtime_error

            agent_item = self.subagents[subagent_slug]
            input_message = build_chat_input_message(description)
            subagent_service = _subagent_run_service_module()
            try:
                async with pg_manager.get_async_session_context() as db:
                    result = await subagent_service.SubagentRunService(db).start(
                        uid=parent_runtime.uid,
                        created_by_run_id=parent_runtime.created_by_run_id,
                        agent_item=agent_item,
                        input_message=input_message,
                        tool_call_id=runtime.tool_call_id,
                        requested_thread_id=thread_id,
                    )
            except subagent_service.SubagentRunBusy as exc:
                payload = exc.to_payload()
                return _json_tool_command(payload, runtime.tool_call_id)
            except ValueError as exc:
                return str(exc)
            payload = {
                "status": "started" if result.created else "existing",
                "run_id": result.run.id,
                "thread_id": result.relation.child_thread_id,
                "subagent_slug": subagent_slug,
                "subagent_name": agent_item.name,
                "created_by_run_id": result.run.created_by_run_id,
                "run_status": result.run.status,
                "continuing": result.continuing,
                "subagent_thread_relation_id": result.relation.id,
                **subagent_service.subagent_run_urls(result.run.id),
            }
            subagent_run = subagent_service.serialize_subagent_run_state(result.run)
            return _json_tool_command(payload, runtime.tool_call_id, subagent_run=subagent_run)

        async def asubagent_status(
            run_id: Annotated[str, SUBAGENT_RUN_ID_ARG],
            runtime: ToolRuntime,
        ) -> str | Command:
            from yuxi.services.agent_run_service import get_agent_run_progress, get_agent_run_result

            parent_runtime, runtime_error = self._require_parent_runtime("无法查询子智能体")
            if runtime_error:
                return runtime_error
            try:
                run = await self._get_verified_subagent_run(
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                    run_id=run_id,
                )

                # 如果 run 已经终结，则尝试读取最终结果；否则 result 保持 None
                result = None
                if run.status in TERMINAL_RUN_STATUSES:
                    async with pg_manager.get_async_session_context() as db:
                        result = await get_agent_run_result(run_id=run.id, current_uid=parent_runtime.uid, db=db)

            except ValueError as exc:
                return str(exc)

            subagent_service = _subagent_run_service_module()
            payload = {
                "status": run.status,
                "run_id": run.id,
                "thread_id": run.conversation_thread_id,
                "subagent_slug": run.agent_slug,
                "error": run.error_message,
                "progress": await get_agent_run_progress(run.id),
                **subagent_service.subagent_run_urls(run.id),
            }
            if result:
                payload["result"] = result
            subagent_run = subagent_service.serialize_subagent_run_state(run)
            return _json_tool_command(payload, runtime.tool_call_id, subagent_run=subagent_run)

        async def asubagent_cancel(
            run_id: Annotated[str, SUBAGENT_RUN_ID_ARG],
            runtime: ToolRuntime,
        ) -> str | Command:
            from yuxi.services.agent_run_service import request_cancel_agent_run

            parent_runtime, runtime_error = self._require_parent_runtime("无法取消子智能体")
            if runtime_error:
                return runtime_error
            try:
                await self._get_verified_subagent_run(
                    run_id=run_id,
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                )  # 校验子智能体归属

                # 取消子智能体运行，返回最新 run 状态
                async with pg_manager.get_async_session_context() as db:
                    run = await request_cancel_agent_run(run_id=run_id, current_uid=parent_runtime.uid, db=db)

            except ValueError as exc:
                return str(exc)

            subagent_service = _subagent_run_service_module()
            payload = {
                "status": run.status,
                "run_id": run.id,
                "thread_id": run.conversation_thread_id,
                **subagent_service.subagent_run_urls(run.id),
            }
            subagent_run = subagent_service.serialize_subagent_run_state(run)
            return _json_tool_command(payload, runtime.tool_call_id, subagent_run=subagent_run)

        async def asubagent_await(
            run_id: Annotated[str, SUBAGENT_RUN_ID_ARG],
            runtime: ToolRuntime,
        ) -> str | Command:
            from yuxi.services.agent_run_service import AgentRunWaitTimeout, await_agent_run_result

            parent_runtime, runtime_error = self._require_parent_runtime("无法等待子智能体")
            if runtime_error:
                return runtime_error
            wait_timed_out = False
            try:
                # 等待前校验 run 归属，避免越权等待其它子任务
                await self._get_verified_subagent_run(
                    run_id=run_id,
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                )
                try:
                    result = await await_agent_run_result(run_id=run_id, current_uid=parent_runtime.uid)
                except AgentRunWaitTimeout as exc:
                    wait_timed_out = True
                    result = exc.result

                # 正常返回和等待超时都重新读取最新状态。
                run = await self._get_verified_subagent_run(
                    run_id=run_id,
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                )
            except ValueError as exc:
                return str(exc)

            subagent_service = _subagent_run_service_module()
            payload = {
                "status": run.status,
                "run_id": run.id,
                "thread_id": run.conversation_thread_id,
                "result": result,
            }
            if wait_timed_out:
                payload["wait_timed_out"] = True
                payload["message"] = "子智能体仍在运行，等待最终结果超时；请稍后继续查询。"
            subagent_run = subagent_service.serialize_subagent_run_state(run)
            return _json_tool_command(payload, runtime.tool_call_id, subagent_run=subagent_run)

        return [
            _async_only_tool(
                name="subagent_start",
                coroutine=asubagent_start,
                description=SUBAGENT_START_DESCRIPTION + "\n\nAvailable subagent slugs:\n" + available_agents,
            ),
            _async_only_tool(
                name="subagent_status",
                coroutine=asubagent_status,
                description=SUBAGENT_STATUS_DESCRIPTION,
            ),
            _async_only_tool(
                name="subagent_cancel",
                coroutine=asubagent_cancel,
                description=SUBAGENT_CANCEL_DESCRIPTION,
            ),
            _async_only_tool(
                name="subagent_await",
                coroutine=asubagent_await,
                description=SUBAGENT_AWAIT_DESCRIPTION,
            ),
        ]

    def _require_parent_runtime(self, error_prefix: str) -> tuple[_ParentRuntime, str | None]:
        """读取并校验子智能体工具依赖的父运行上下文。"""
        parent_runtime = _ParentRuntime(
            uid=str(getattr(self.parent_context, "uid", "") or "").strip(),
            created_by_run_id=str(getattr(self.parent_context, "run_id", "") or "").strip(),
        )
        if not parent_runtime.uid:
            return parent_runtime, f"{error_prefix}：当前运行时缺少 uid"
        if not parent_runtime.created_by_run_id:
            return parent_runtime, f"{error_prefix}：当前运行时缺少父运行 ID"
        return parent_runtime, None

    async def _get_verified_subagent_run(self, *, run_id: str, uid: str, created_by_run_id: str):
        """在工具调用前按父 run 作用域校验子 run 归属。"""
        subagent_service = _subagent_run_service_module()
        async with pg_manager.get_async_session_context() as db:
            return await subagent_service.SubagentRunService(db).get_run_for_creator(
                uid=uid,
                created_by_run_id=created_by_run_id,
                run_id=run_id,
            )


@dataclass(frozen=True)
class _ParentRuntime:
    uid: str
    created_by_run_id: str


def _json_tool_command(
    payload: dict[str, Any],
    tool_call_id: str,
    *,
    subagent_run: dict[str, Any] | None = None,
) -> Command:
    """把后台子智能体工具的结构化结果包装成 ToolMessage。"""
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    update: dict[str, Any] = {"messages": [ToolMessage(content, tool_call_id=tool_call_id)]}
    if subagent_run is not None:
        update["subagent_runs"] = [subagent_run]
    return Command(update=update)
