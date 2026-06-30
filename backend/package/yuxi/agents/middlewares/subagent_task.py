from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from deepagents import SubagentTransformer as DeepAgentsSubagentTransformer
from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware, ContextT, ModelRequest, ModelResponse, ResponseT
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command

from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

YUXI_SUBAGENTS_STREAM_KEY = "yuxi_subagents"


def _subagent_run_service_module():
    from yuxi.services import subagent_run_service

    return subagent_run_service


def _async_only_tool(*, name: str, coroutine: Callable[..., Awaitable[Any]], description: str) -> StructuredTool:
    """后台子智能体工具只在异步链路执行；仅声明 coroutine，同步调用由 LangChain 直接报错。"""
    return StructuredTool.from_function(name=name, coroutine=coroutine, description=description, infer_schema=True)


class YuxiSubagentTransformer(DeepAgentsSubagentTransformer):
    def init(self) -> dict[str, Any]:
        return {YUXI_SUBAGENTS_STREAM_KEY: self._log}


TASK_SYSTEM_PROMPT = """## `task`（子智能体任务工具）

你可以使用 `task` 工具把复杂、独立的子任务交给已配置的子智能体处理。子智能体只返回最终结果，你看不到它的中间步骤。
工具结果会包含子智能体线程 ID，后续需要继续同一个子任务时，把该 ID 作为 `thread_id` 传回 `task`。

使用原则：
- 任务足够复杂、可以独立完成、或需要隔离上下文时使用。
- 多个互不依赖的子任务可以并行调用多个 `task`。
- 继续既有子智能体任务时传入之前结果中的 `thread_id`；新任务不要填写 `thread_id`。
- 不要并行调用同一个 `thread_id`，避免多个续跑请求同时写入同一子线程。
- 简单问题或少量直接工具调用不要委派。
- 调用时必须选择下方可用的 `subagent_slug`，并在 `description` 中写清目标、上下文和期望输出。
- 不要通过 shell、curl、HTTP API 或命令行间接调用子智能体；需要子智能体时必须使用 `task` 工具。

后台子智能体：
- 长任务或多个可并行任务优先使用 `subagent_start`，它会立即返回 `run_id` 和 `thread_id`，父智能体可以继续工作。
- 后续用 `subagent_status` 查询状态，`subagent_events` 读取增量事件，
  `subagent_cancel` 取消，`subagent_await` 在明确需要结果时等待。
- `thread_id` 是子智能体长期上下文 ID；同一个 `thread_id` 完成后可以继续创建新的 run。
  若同线程已有运行中 run，会返回 busy，不会隐藏排队。
- 短任务且父智能体必须立刻依赖结果时继续使用 `task`。

Available subagent slugs:

{available_agents}"""

TASK_TOOL_DESCRIPTION = """Launch a configured Yuxi subagent to handle an isolated task.

Available subagent slugs:
{available_agents}

Use `subagent_slug` to select one available subagent and put the full task brief in `description`.
Omit `thread_id` for a new task. To continue a previous subagent task, pass the child thread ID returned by
that prior task result as `thread_id`.
Do not call subagents through shell, curl, HTTP APIs, or command-line indirection."""

SUBAGENT_START_DESCRIPTION = """Start a configured Yuxi subagent asynchronously.

Returns a child thread ID for future continuation and a run ID for status/events/cancel/result checks.
Use this for long-running or parallelizable subagent work. If `thread_id` is provided, it continues that subagent
thread when no active run is currently writing to it."""

SUBAGENT_STATUS_DESCRIPTION = """Check a subagent run status by run_id."""

SUBAGENT_EVENTS_DESCRIPTION = """Read recent events for a subagent run by run_id and Redis stream cursor."""

SUBAGENT_CANCEL_DESCRIPTION = """Cancel a running subagent run by run_id."""

SUBAGENT_AWAIT_DESCRIPTION = """Wait for a subagent run to finish and return its final result."""

TASK_DESCRIPTION_ARG = "需要子智能体独立完成的任务描述，包含必要上下文和期望输出。"
SUBAGENT_SLUG_ARG = "要调用的子智能体 slug，必须是工具描述中列出的可用项之一。"
TASK_THREAD_ID_ARG = "可选。要继续的既有子智能体线程 ID，通常来自之前 task 工具结果；新任务不要填写。"
ASYNC_THREAD_ID_ARG = "可选。要继续的后台子智能体线程 ID，来自之前 subagent_start 返回的 thread_id；新任务不要填写。"
SUBAGENT_RUN_ID_ARG = "子智能体运行 ID，由 subagent_start 返回。"
SUBAGENT_AFTER_SEQ_ARG = "可选。事件流游标，首次读取传 0-0；后续传上次返回的 last_seq。"
SUBAGENT_EVENT_LIMIT_ARG = "可选。读取事件数量，范围 1-50。"


async def create_subagent_task_middleware(parent_context) -> YuxiSubAgentMiddleware | None:
    """根据父智能体上下文加载可用子智能体，并在存在可调用项时创建 task 中间件。"""
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
        self.system_prompt = TASK_SYSTEM_PROMPT.format(available_agents=available_agents)
        self.tools = [self._build_task_tool(available_agents), *self._build_async_subagent_tools(available_agents)]
        self.subagent_names = frozenset(self.subagents)
        self.transformers = [lambda scope: YuxiSubagentTransformer(scope, subagent_names=self.subagent_names)]

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

    def _build_task_tool(self, available_agents: str) -> StructuredTool:
        """构建 task 工具：启动子智能体后阻塞等待其最终结果。"""

        async def atask(
            description: Annotated[str, TASK_DESCRIPTION_ARG],
            subagent_slug: Annotated[str, SUBAGENT_SLUG_ARG],
            runtime: ToolRuntime,
            thread_id: Annotated[str | None, TASK_THREAD_ID_ARG] = None,
        ) -> str | Command:
            started, error = await self._start_subagent(
                description=description,
                subagent_slug=subagent_slug,
                runtime=runtime,
                thread_id=thread_id,
                error_prefix="无法调用子智能体",
            )
            if error is not None:
                return error

            # 阻塞父智能体运行，直到子 run 终结再读取最终结果；运行失败时 result 含 error 信息
            parent_runtime = started.parent_runtime
            subagent_service = _subagent_run_service_module()
            try:
                from yuxi.services.agent_run_service import AgentRunWaitTimeout, await_agent_run_result

                result = await await_agent_run_result(run_id=started.result.run.id, current_uid=parent_runtime.uid)
                run = await self._get_verified_subagent_run(
                    run_id=started.result.run.id,
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                )
            except AgentRunWaitTimeout as exc:
                try:
                    run = await self._get_verified_subagent_run(
                        run_id=started.result.run.id,
                        uid=parent_runtime.uid,
                        created_by_run_id=parent_runtime.created_by_run_id,
                    )
                except ValueError as verify_exc:
                    return str(verify_exc)
                subagent_run = subagent_service.serialize_subagent_run_state(run)
                return _task_wait_timeout_response(exc.result, runtime.tool_call_id, subagent_run)
            except ValueError as exc:
                return str(exc)

            subagent_run = subagent_service.serialize_subagent_run_state(run)
            return _task_result_response(result, runtime.tool_call_id, subagent_run)

        return _async_only_tool(
            name="task",
            coroutine=atask,
            description=TASK_TOOL_DESCRIPTION.format(available_agents=available_agents),
        )

    def _build_async_subagent_tools(self, available_agents: str) -> list[StructuredTool]:
        """构建后台子智能体生命周期工具：start/status/events/cancel/await。"""

        async def asubagent_start(
            description: Annotated[str, TASK_DESCRIPTION_ARG],
            subagent_slug: Annotated[str, SUBAGENT_SLUG_ARG],
            runtime: ToolRuntime,
            thread_id: Annotated[str | None, ASYNC_THREAD_ID_ARG] = None,
        ) -> str | Command:
            started, error = await self._start_subagent(
                description=description,
                subagent_slug=subagent_slug,
                runtime=runtime,
                thread_id=thread_id,
                error_prefix="无法启动子智能体",
            )
            if error is not None:
                return error

            result, agent_item = started.result, started.agent_item
            subagent_service = _subagent_run_service_module()
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
            from yuxi.services.agent_run_service import get_agent_run_result

            parent_runtime, runtime_error = self._require_async_parent_runtime("无法查询子智能体")
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
                **subagent_service.subagent_run_urls(run.id),
            }
            if result:
                payload["result"] = result
            subagent_run = subagent_service.serialize_subagent_run_state(run)
            return _json_tool_command(payload, runtime.tool_call_id, subagent_run=subagent_run)

        async def asubagent_events(
            run_id: Annotated[str, SUBAGENT_RUN_ID_ARG],
            runtime: ToolRuntime,
            after_seq: Annotated[str, SUBAGENT_AFTER_SEQ_ARG] = "0-0",
            limit: Annotated[int, SUBAGENT_EVENT_LIMIT_ARG] = 20,
        ) -> str | Command:
            from yuxi.services.run_queue_service import list_run_stream_events, normalize_after_seq

            parent_runtime, runtime_error = self._require_async_parent_runtime("无法读取子智能体事件")
            if runtime_error:
                return runtime_error
            try:
                await self._get_verified_subagent_run(
                    run_id=run_id,
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                )  # 校验子智能体归属
            except ValueError as exc:
                return str(exc)

            normalized_after_seq = normalize_after_seq(after_seq)
            event_limit = min(50, max(1, int(limit or 20)))
            events = await list_run_stream_events(run_id, after_seq=normalized_after_seq, limit=event_limit)
            payload = {
                "status": "ok",
                "run_id": run_id,
                "after_seq": normalized_after_seq,
                "last_seq": str(events[-1]["seq"]) if events else normalized_after_seq,
                "events": events,
            }
            return _json_tool_command(payload, runtime.tool_call_id)

        async def asubagent_cancel(
            run_id: Annotated[str, SUBAGENT_RUN_ID_ARG],
            runtime: ToolRuntime,
        ) -> str | Command:
            from yuxi.services.agent_run_service import request_cancel_agent_run

            parent_runtime, runtime_error = self._require_async_parent_runtime("无法取消子智能体")
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

            parent_runtime, runtime_error = self._require_async_parent_runtime("无法等待子智能体")
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
                # 等待结束后重新读取已验证的最新 run 状态
                result = await await_agent_run_result(run_id=run_id, current_uid=parent_runtime.uid)
                run = await self._get_verified_subagent_run(
                    run_id=run_id,
                    uid=parent_runtime.uid,
                    created_by_run_id=parent_runtime.created_by_run_id,
                )
            except AgentRunWaitTimeout as exc:
                wait_timed_out = True
                result = exc.result
                try:
                    run = await self._get_verified_subagent_run(
                        run_id=run_id,
                        uid=parent_runtime.uid,
                        created_by_run_id=parent_runtime.created_by_run_id,
                    )
                except ValueError as verify_exc:
                    return str(verify_exc)
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
                name="subagent_events",
                coroutine=asubagent_events,
                description=SUBAGENT_EVENTS_DESCRIPTION,
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

    def _parent_runtime(self) -> _ParentRuntime:
        """从父智能体 context 中抽取子智能体运行所需的最小父运行信息。"""
        parent_thread_id = str(getattr(self.parent_context, "parent_thread_id", None) or self.parent_context.thread_id)
        file_thread_id = str(getattr(self.parent_context, "file_thread_id", None) or parent_thread_id)
        uid = str(getattr(self.parent_context, "uid", "") or "").strip()
        created_by_run_id = str(getattr(self.parent_context, "run_id", "") or "").strip()
        return _ParentRuntime(
            file_thread_id=file_thread_id,
            uid=uid,
            created_by_run_id=created_by_run_id,
        )

    def _require_async_parent_runtime(self, error_prefix: str) -> tuple[_ParentRuntime, str | None]:
        """校验后台子智能体工具必须依赖的父运行上下文。"""
        parent_runtime = self._parent_runtime()
        if not parent_runtime.uid:
            return parent_runtime, f"{error_prefix}：当前运行时缺少 uid"
        if not parent_runtime.created_by_run_id:
            return parent_runtime, f"{error_prefix}：当前运行时缺少父运行 ID"
        return parent_runtime, None

    async def _start_subagent(
        self,
        *,
        description: str,
        subagent_slug: str,
        runtime: ToolRuntime,
        thread_id: str | None,
        error_prefix: str,
    ) -> tuple[_StartedSubagent | None, str | Command | None]:
        """校验并启动（或继续）后台子智能体 run；成功返回启动结果，失败返回可直接回传的错误响应。"""
        if subagent_slug not in self.subagents:
            allowed = ", ".join(f"`{slug}`" for slug in self.subagents)
            return None, f"无法调用子智能体 {subagent_slug}，可用子智能体只有：{allowed}"
        if not runtime.tool_call_id:
            raise ValueError("Tool call ID is required for subagent invocation")

        parent_runtime, runtime_error = self._require_async_parent_runtime(error_prefix)
        if runtime_error:
            return None, runtime_error

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
                    file_thread_id=parent_runtime.file_thread_id,
                    model_spec=self._subagent_model_override(agent_item),
                )
        except subagent_service.SubagentRunBusy as exc:
            return None, _json_tool_command(exc.to_payload(), runtime.tool_call_id)
        except ValueError as exc:
            return None, str(exc)
        return _StartedSubagent(result=result, parent_runtime=parent_runtime, agent_item=agent_item), None

    def _subagent_model_override(self, agent_item: Agent) -> str | None:
        """当子智能体未显式配置模型时，沿用父智能体当前模型。"""
        config_context = (
            (agent_item.config_json or {}).get("context") if isinstance(agent_item.config_json, dict) else None
        )
        configured_model = ""
        if isinstance(config_context, dict):
            configured_model = str(config_context.get("model") or "").strip()
        if configured_model:
            return None
        return str(getattr(self.parent_context, "model", None) or "").strip() or None

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
    file_thread_id: str
    uid: str
    created_by_run_id: str


@dataclass(frozen=True)
class _StartedSubagent:
    """``_start_subagent`` 的结果：子 run 启动产物及其依赖的父运行上下文。"""

    result: Any  # SubagentStartResult
    parent_runtime: _ParentRuntime
    agent_item: Agent


def _task_result_response(result: dict[str, Any], tool_call_id: str, subagent_run: dict[str, Any]) -> Command:
    """把后台子智能体 run 的最终结果转换为同步 task 工具结果。"""
    output = str(result.get("output") or "").strip()
    error = result.get("error") if isinstance(result.get("error"), dict) else None
    if not output and error:
        output = str(error.get("message") or "子智能体运行失败")
    if not output:
        output = "子智能体已完成任务，但没有返回文本结果。"

    tool_result = _tool_result_with_thread_id(subagent_run["child_thread_id"], output)
    return Command(
        update={"messages": [ToolMessage(tool_result, tool_call_id=tool_call_id)], "subagent_runs": [subagent_run]}
    )


def _task_wait_timeout_response(result: dict[str, Any], tool_call_id: str, subagent_run: dict[str, Any]) -> Command:
    """同步 task 等待到达上限时，明确告诉父智能体子 run 仍未终结。"""
    status = str(result.get("status") or subagent_run.get("status") or "running")
    run_id = str(result.get("agent_run_id") or subagent_run["run_id"])
    output = (
        f"子智能体仍在运行（status: {status}），尚未返回最终文本结果。\n"
        f"run_id: {run_id}\n"
        "请稍后使用 subagent_status 或 subagent_await 查询结果；不要把当前结果视为任务已完成。"
    )
    tool_result = _tool_result_with_thread_id(subagent_run["child_thread_id"], output)
    return Command(
        update={"messages": [ToolMessage(tool_result, tool_call_id=tool_call_id)], "subagent_runs": [subagent_run]}
    )


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


def _tool_result_with_thread_id(child_thread_id: str, content: str) -> str:
    """把子线程 ID 放进工具结果，方便后续继续同一子任务。"""
    return f"> 子智能体线程 ID: {child_thread_id}\n\n---\n\n{content}"
