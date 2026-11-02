"""Worker 的 LangGraph 执行、输出持久化与状态投影边界。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from langchain.messages import AIMessage, HumanMessage
from langgraph.types import Command

from yuxi.infrastructure.observability.langfuse import flush_langfuse
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.input_messages import AgentRunInputMessage
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
from yuxi.modules.agents.runtime.base import GraphExecutionResult, json_safe
from yuxi.modules.agents.runtime.callbacks.model_request_timing import FirstModelRequestRecorder
from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.modules.agents.runtime.state import AgentStatePayload
from yuxi.modules.agents.services.attachments import serialize_attachment
from yuxi.modules.agents.services.checkpoint_messages import save_messages_from_langgraph_state, save_partial_message
from yuxi.modules.agents.services.message_recorder import RunMessageRecorder
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter
from yuxi.modules.agents.services.preparation import PreparedRunExecution
from yuxi.modules.agents.services.tracing import (
    LangfuseRunContext,
    attach_run_observation,
    build_run_context,
    finish_run_observation,
    get_trace_info,
    start_turn_observation,
)
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.services.bindings import resolve_session_workdir_path
from yuxi.shared.hashing import hash_id


def _with_attachment_context(message: HumanMessage, attachments: list[dict]) -> HumanMessage:
    """把已消费来源的附件路径追加到模型输入。"""
    attachment_lines = [
        f"- {item.get('file_name') or '未知文件'}: {item['path']}"
        for item in attachments
        if isinstance(item.get("path"), str) and item["path"].strip()
    ]
    if not attachment_lines:
        return message

    context = "\n".join(
        [
            "<attachment_context>",
            "以下附件来自本次输入或此前已消费的输入。需要内容时，请使用 read_file 读取对应路径：",
            *attachment_lines,
            "</attachment_context>",
        ]
    )
    if isinstance(message.content, str):
        content: str | list = f"{message.content}\n\n{context}"
    else:
        content = [*message.content, {"type": "text", "text": context}]
    return message.model_copy(update={"content": content})


def _build_langfuse_run_context(
    *,
    current_user,
    thread_id: str,
    agent_id: str,
    turn_id: str,
    run_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    meta: dict | None = None,
) -> LangfuseRunContext:
    """为当前执行段建立同一 Turn 的 trace 上下文。"""
    return build_run_context(
        user_id=str(getattr(current_user, "uid", None) or getattr(current_user, "id", "")),
        thread_id=thread_id,
        agent_id=agent_id,
        turn_id=turn_id,
        run_id=run_id,
        operation=operation,
        backend_id=backend_id,
        message_type=message_type,
        username=getattr(current_user, "username", None),
        login_user_id=getattr(current_user, "uid", None),
        department_id=getattr(current_user, "department_id", None),
        parent_observation_id=(meta or {}).get("langfuse_root_observation_id"),
    )


async def _flush_langfuse_best_effort(*, timeout: float = 2) -> None:
    """限制可选追踪网络等待，不延迟 Run 的持久终态与事件发布。"""
    try:
        await asyncio.wait_for(asyncio.to_thread(flush_langfuse), timeout=timeout)
    except TimeoutError:
        logger.warning("刷新 Langfuse 超时，后台导出仍会继续")


def extract_agent_state(values: dict) -> AgentStatePayload:
    """从 LangGraph state 中提取 agent 状态"""
    if not isinstance(values, dict):
        return {"artifacts": [], "cooperation": {"sessions": []}, "token_usage": None, "warnings": []}

    # 直接获取，信任 state 的数据结构
    artifacts = values.get("artifacts")
    token_usage = values.get("token_usage")
    result: AgentStatePayload = {
        "warnings": values.get("warnings", []),
        "artifacts": list(artifacts) if artifacts else [],
        "cooperation": {"sessions": []},
        "token_usage": dict(token_usage) if isinstance(token_usage, dict) else None,
    }

    return result


def _agent_state_signature(agent_state: AgentStatePayload | dict | None) -> str:
    if not agent_state:
        return ""
    try:
        return json.dumps(agent_state, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(agent_state)


def _current_run_token_usage(agent_state: AgentStatePayload | dict | None, run_id: str | None) -> dict:
    """提取只属于当前 Run 的用量；缺失时保留明确的不可用事实。"""

    token_usage = agent_state.get("token_usage") if isinstance(agent_state, dict) else None
    if isinstance(token_usage, dict) and run_id and token_usage.get("current_run_id") == run_id:
        run_usage = token_usage.get("run")
        if isinstance(run_usage, dict):
            return dict(run_usage)
    return {"available": False}


async def _persist_model_request_timing(
    recorder: FirstModelRequestRecorder | None,
    meta: dict,
) -> None:
    """在 Run 终态事件发布前持久化首次模型请求时间。"""
    if recorder is not None:
        await recorder.persist(
            run_id=str(meta.get("run_id") or ""),
            worker_id=str(meta.get("worker_id") or ""),
        )


def _extract_interrupts(state) -> list:
    """从当前 checkpoint 任务收集未完成工具的全部中断。"""
    # 部分恢复后已完成工具仍保留旧 interrupt；只有实际结果才能排除该任务。
    return [item for task in state.tasks if not task.result for item in task.interrupts]


def _coerce_interrupt_payload(info: Any) -> dict:
    """将 LangGraph interrupt 对象转换为 dict 结构。"""
    if isinstance(info, dict):
        return info

    payload = getattr(info, "value", None)
    if isinstance(payload, dict):
        return payload

    questions = getattr(info, "questions", None)
    source = getattr(info, "source", None)
    result: dict[str, Any] = {}
    if isinstance(questions, list):
        result["questions"] = questions
    if isinstance(source, str) and source.strip():
        result["source"] = source
    return result


def _build_ask_user_question_payload(payload: dict, thread_id: str) -> dict[str, Any]:
    """将已标准化的 interrupt payload 转换为 ask_user_question_required 载荷。"""

    # checkpoint 中的问题已在工具入口校验；投影不重新生成 ID 或修复参数。
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("提问等待点缺少标准 questions 列表")

    source = str(payload.get("source") or payload.get("tool_name") or "interrupt")

    return {
        "questions": questions,
        "source": source,
        "thread_id": thread_id,
    }


def _build_tool_approval_payload(payload: dict, thread_id: str) -> dict[str, Any] | None:
    """将已标准化的 interrupt payload 转换为 tool_approval_required 载荷。"""
    action_requests = payload.get("action_requests")
    review_configs = payload.get("review_configs")
    if not isinstance(action_requests, list) or not isinstance(review_configs, list):
        return None
    if not action_requests or len(action_requests) != len(review_configs):
        return None
    return {
        "approval": {
            "action_requests": json_safe(action_requests),
            "review_configs": json_safe(review_configs),
        },
        "thread_id": thread_id,
    }


def build_pending_interrupt_payload(interrupts: list, thread_id: str) -> dict[str, Any]:
    """将 checkpoint 中断列表转换为前端可恢复的统一载荷；多个协作等待聚合为一次恢复。"""
    coerced_items = [_coerce_interrupt_payload(item) for item in interrupts]
    if len(coerced_items) > 1 and not all(item.get("kind") == "cooperation" for item in coerced_items):
        # 并行等待只有全为协作等待才有统一恢复入口；混合或重复的审批/回答显式失败优于静默丢弃。
        raise ValueError("同一轮并行产生了多个等待点，只有全部为协作等待才能聚合恢复")
    if coerced_items[0].get("kind") == "cooperation":
        waits = [_cooperation_wait_entry(info, coerced) for info, coerced in zip(interrupts, coerced_items)]
        return {"status": "cooperation_waiting", "thread_id": thread_id, "waits": waits}
    coerced = coerced_items[0]
    approval_payload = _build_tool_approval_payload(coerced, thread_id)
    if approval_payload:
        return {"status": "human_approval_required", **approval_payload}

    question_payload = _build_ask_user_question_payload(coerced, thread_id)
    return {"status": "ask_user_question_required", **question_payload}


def _cooperation_wait_entry(info: Any, coerced: dict) -> dict:
    """把单个协作等待固定为携带中断 ID 的恢复条目。"""
    interrupt_id = getattr(info, "id", None)
    if not isinstance(interrupt_id, str) or not interrupt_id:
        raise ValueError("协作等待缺少可恢复的中断 ID")
    target = (
        {"target_inputs": coerced["target_inputs"]}
        if "target_inputs" in coerced
        else {"target_sessions": coerced["target_sessions"], "after_cursor": coerced["after_cursor"]}
    )
    return {"interrupt_id": interrupt_id, **target, "deadline": coerced["deadline"]}


def _interrupt_terminal_details(interrupt: dict[str, Any]) -> tuple[str, str]:
    """从结构化中断增量提取持久终态的类型与摘要。"""
    status = str(interrupt.get("status") or "interrupted")
    if status == "human_approval_required":
        return status, "需要用户审批工具操作"
    if status == "cooperation_waiting":
        return status, "等待其他 Session 更新"
    questions = interrupt.get("questions")
    if isinstance(questions, list) and questions and isinstance(questions[0], dict):
        question = str(questions[0].get("question") or "").strip()
        if question:
            return status, question
    return status, str(interrupt.get("message") or "需要用户回答问题")


def _waitpoint_from_interrupt(interrupt: dict[str, Any], run_id: str) -> dict:
    """为具体 interrupted Run 固定等待点和审批调用身份。"""
    if interrupt.get("status") == "cooperation_waiting":
        return {
            "id": hash_id("wait_", run_id, length=64),
            "run_id": run_id,
            "kind": "cooperation",
            "waits": interrupt["waits"],
        }
    if interrupt.get("status") == "human_approval_required":
        approval = dict(interrupt.get("approval") or {})
        actions = approval.get("action_requests") or []
        configs = approval.get("review_configs") or []
        calls = [
            {
                **action,
                "call_id": hash_id("call_", f"{run_id}:{index}", length=64),
                "allowed_decisions": configs[index].get("allowed_decisions", []),
            }
            for index, action in enumerate(actions)
        ]
        kind = "approval"
        questions = []
    else:
        kind = "answer"
        questions = interrupt.get("questions") or []
        calls = []
    return {
        "id": hash_id("wait_", run_id, length=64),
        "run_id": run_id,
        "kind": kind,
        "calls": calls,
        "questions": questions,
    }


async def _resolve_agent_runtime(
    *,
    db,
    user: User,
    requested_agent_slug: str | None,
    thread_id: str,
    prepared_execution: PreparedRunExecution,
) -> tuple[Agent, Any, BaseContext, Session]:
    """校验执行时的线程与 Agent 权限，使用 worker 已固化的配置。"""
    agent_session = await SessionRepository(db).get_session_by_thread_id(thread_id)
    if not agent_session or agent_session.uid != str(user.uid) or agent_session.status != "active":
        raise ValueError("对话线程不存在")
    # Session.agent_id 是历史字段名，实际保存的是 Agent.slug。
    if requested_agent_slug and requested_agent_slug != agent_session.agent_id:
        raise ValueError("已有线程已绑定智能体，不能切换")
    await resolve_session_workdir_path(agent_session=agent_session, uid=str(user.uid), db=db)

    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_session.agent_id, user=user)
    if not agent_item:
        raise ValueError("智能体不存在或无权限访问")

    backend = get_agent_backend(agent_item.backend_id)

    if agent_item.backend_id != prepared_execution.backend_id:
        raise ValueError("智能体后端在执行准备后发生变化")
    return agent_item, backend, prepared_execution.context, agent_session


@dataclass(frozen=True)
class RunExecutionResult:
    """交付执行结果，业务状态不通过公开事件反推。"""

    checkpoint: Any
    status: str
    committed: bool = False
    error_type: str | None = None
    error_message: str | None = None


async def stream_agent_execution(
    *,
    thread_id: str,
    meta: dict,
    current_user,
    db,
    prepared_execution: PreparedRunExecution,
    on_prepared: Callable[[], Awaitable[None]] | None = None,
    model_request_recorder: FirstModelRequestRecorder | None = None,
    agent_slug: str | None = None,
    input_messages: list[AgentRunInputMessage] | None = None,
    resume_input: Any = None,
    is_resume: bool = False,
) -> AsyncIterator[dict[str, Any] | RunExecutionResult]:
    """用同一图事件循环执行 chat 和 resume，最终结果携带 checkpoint。"""
    meta = dict(meta or {})
    if not thread_id or not meta.get("run_id") or not meta.get("turn_id"):
        raise ValueError("执行需要已持久化的 Thread、Turn 和 Run")
    if not is_resume and not input_messages:
        raise ValueError("Run 缺少已消费的输入消息")

    langfuse_run = None
    adapter = OpenAIEventAdapter(run_id=meta["run_id"], turn_id=meta["turn_id"], thread_id=thread_id, worker_id=meta["worker_id"])
    accumulated_content = []
    trace_info = {}
    try:
        agent_item, agent, context, agent_session = await _resolve_agent_runtime(
            db=db,
            user=current_user,
            requested_agent_slug=None if is_resume else agent_slug,
            thread_id=thread_id,
            prepared_execution=prepared_execution,
        )
        if is_resume:
            graph_input = Command(resume=resume_input)
            message_type = "resume"
        else:
            graph_input = [message.require_langchain_message() for message in input_messages]
            message_type = input_messages[0].message_type
            attachments = await AttachmentRepository(db).list_for_thread(
                thread_id,
                agent_session.uid,
                agent_session.app_id,
                for_model=True,
            )
            authorized_attachments = [serialize_attachment(item, thread_id=thread_id) for item in attachments]
            if authorized_attachments:
                message_ids = {message.extra_metadata["message_id"] for message in input_messages}
                for position, message in enumerate(input_messages):
                    own_id = message.extra_metadata["message_id"]
                    visible = [
                        attachment
                        for attachment in authorized_attachments
                        if attachment["message_id"] == own_id
                        or (position == len(input_messages) - 1 and attachment["message_id"] not in message_ids)
                    ]
                    graph_input[position] = _with_attachment_context(graph_input[position], visible)
        langfuse_run = _build_langfuse_run_context(
            current_user=current_user,
            thread_id=thread_id,
            agent_id=agent_item.slug,
            backend_id=agent_item.backend_id,
            turn_id=meta["turn_id"],
            run_id=meta["run_id"],
            operation="agent_chat_resume" if is_resume else "agent_chat_stream",
            message_type=message_type,
            meta=meta,
        )
        try:
            await _persist_agent_run_langfuse_trace(db=db, meta=meta, run_context=langfuse_run)
            # 结束 runtime/附件/追踪准备事务后才进入模型流，禁用追踪也经过此提交点。
            await db.commit()
        except BaseException:
            await db.rollback()
            langfuse_run.terminal_status = "failed"
            finish_run_observation(langfuse_run)
            raise
        trace_info = get_trace_info(langfuse_run)
        callbacks = list(langfuse_run.callbacks)
        if model_request_recorder is not None:
            callbacks.append(model_request_recorder)
        kwargs = {
            "context": context,
            "callbacks": callbacks,
            "metadata": langfuse_run.metadata,
            "tags": langfuse_run.tags,
            "run_name": agent_item.name or agent_item.slug,
            "on_prepared": on_prepared,
        }
        recorder = RunMessageRecorder(run_id=meta["run_id"], thread_id=thread_id, worker_id=meta["worker_id"])
        state_signature = ""
        sequence_offset = 0
        last_sequence = -1
        while True:
            source = (
                agent.stream_resume_with_state(graph_input, **kwargs)
                if is_resume
                else agent.stream_messages_with_state(graph_input, **kwargs)
            )
            final_state = None
            steer_before_model = False
            async with aclosing(source):
                async for event in source:
                    if isinstance(event, GraphExecutionResult):
                        final_state = event.checkpoint
                        steer_before_model = event.steer_before_model
                        continue
                    # 同 Run 续图时，序号在公开投影与审计之前统一为递增序列。
                    event = {**event, "seq": sequence_offset + event["seq"]}
                    last_sequence = event["seq"]
                    public_events, state_signature = await _project_graph_event(
                        event, recorder=recorder, adapter=adapter, state_signature=state_signature
                    )
                    for public_event in public_events:
                        if public_event["type"] == "agent.session.turn.output_text.delta":
                            accumulated_content.append(public_event["delta"])
                        yield public_event
            if final_state is None:
                raise ValueError("Agent 执行流缺少最终 checkpoint")
            interrupts = _extract_interrupts(final_state)
            interrupt = build_pending_interrupt_payload(interrupts, thread_id) if interrupts else None
            waitpoint = _waitpoint_from_interrupt(interrupt, meta["run_id"]) if interrupt else None
            error_type, error_message = _interrupt_terminal_details(interrupt) if interrupt else (None, None)
            state = extract_agent_state(final_state.values)
            await _persist_model_request_timing(model_request_recorder, meta)
            trace_info = get_trace_info(langfuse_run)
            terminal_status = await save_messages_from_langgraph_state(
                state=final_state,
                thread_id=thread_id,
                db=db,
                trace_info=trace_info,
                run_id=meta["run_id"],
                turn_id=meta["turn_id"],
                worker_id=meta["worker_id"],
                complete_run=interrupt is None,
                steer_before_model=steer_before_model,
                interrupt_run=interrupt is not None,
                waitpoint=waitpoint,
                interrupt_error_type=error_type,
                interrupt_error_message=error_message,
                token_usage=_current_run_token_usage(state, meta["run_id"]),
            )
            if terminal_status != "running":
                break
            # 让位批次在提交前被取消：同 owner 从已有 checkpoint 继续，不重放用户输入。
            graph_input, is_resume = [], False
            kwargs["on_prepared"] = None
            sequence_offset = last_sequence + 1
        langfuse_run.terminal_status = terminal_status
        for event in await adapter.finish():
            yield event
        yield RunExecutionResult(checkpoint=final_state, status=terminal_status, committed=True)
    except (asyncio.CancelledError, GeneratorExit):
        # 消费者停在 yield 时通过 aclose 关闭，仍须保存已展示的取消正文。
        async with pg_manager.get_async_session_context() as db:
            current = await AgentRunRepository(db).get_run(meta["run_id"])
        if current is not None and current.status == "cancel_requested" and current.worker_id == meta["worker_id"]:
            await adapter.persist_incomplete(cancelled_partial=True)
        raise
    except Exception as exc:
        async with pg_manager.get_async_session_context() as db:
            current = await AgentRunRepository(db).get_run(meta["run_id"])
        if current is not None and current.status == "cancel_requested" and current.worker_id == meta["worker_id"]:
            await adapter.persist_incomplete(cancelled_partial=True)
            raise asyncio.CancelledError("输出提交与用户取消竞争") from exc
        logger.exception("Agent 执行失败")
        error_message = str(exc)
        try:
            await adapter.persist_incomplete()
        except Exception:
            logger.opt(exception=True).warning("失败执行的部分 item 未能持久化")
        await _persist_model_request_timing(model_request_recorder, meta)
        async with pg_manager.get_async_session_context() as new_db:
            output = await save_partial_message(
                new_db,
                thread_id,
                full_msg=AIMessage(content="".join(accumulated_content)) if accumulated_content else None,
                error_message=error_message,
                error_type="execution_error",
                trace_info=trace_info,
                run_id=meta["run_id"],
                turn_id=meta["turn_id"],
                worker_id=meta["worker_id"],
            )
        if output is not None:
            for event in await adapter.finish():
                yield event
        yield RunExecutionResult(
            checkpoint=None,
            status="failed",
            committed=output is not None,
            error_type="execution_error",
            error_message=error_message,
        )
    finally:
        if langfuse_run is not None:
            finish_run_observation(langfuse_run)
        await _flush_langfuse_best_effort()


async def _persist_agent_run_langfuse_trace(*, db, meta: dict, run_context: LangfuseRunContext) -> None:
    """按 Thread→Turn→Run 顺序固定跨执行段的 trace 与观察身份。"""
    run_id = meta.get("run_id")
    worker_id = meta.get("worker_id")
    if not run_id or not worker_id or not run_context.trace_id:
        return

    root_thread_id = str(meta.get("thread_id") or "")
    agent_session = await SessionRepository(db).lock_session_by_thread_id(root_thread_id)
    if agent_session is None:
        raise ValueError("Langfuse 根 Thread 不存在")
    turn = await AgentTurnRepository(db).get_for_scope(
        turn_id=str(meta["turn_id"]),
        thread_id=root_thread_id,
        uid=str(meta["uid"]),
        app_id=agent_session.app_id,
        for_update=True,
    )
    if turn is None:
        raise ValueError("Langfuse Turn 不存在")
    run_repo = AgentRunRepository(db)
    run = await run_repo.get_run(str(run_id))
    if run is None or run.turn_id != turn.id or run.thread_id != root_thread_id:
        raise ValueError("Langfuse Run 与 Turn 归属不一致")
    root_id = turn.langfuse_root_observation_id
    if root_id is None:
        root_id = start_turn_observation(run_context)
        if root_id:
            await AgentTurnRepository(db).set_langfuse_root_observation_id(turn, root_id)
    else:
        earlier_runs = await AgentTurnRepository(db).list_runs(turn.id)
        trace_id = next((item.langfuse_trace_id for item in earlier_runs if item.langfuse_trace_id), None)
        if trace_id is None:
            raise ValueError("Langfuse Turn 根观察缺少持久 trace")
        run_context.trace_id = trace_id
    if root_id:
        await run_repo.set_langfuse_trace_id(str(run_id), str(run_context.trace_id), worker_id=str(worker_id))
        observation_id = attach_run_observation(
            run_context,
            root_observation_id=root_id,
            existing_observation_id=run.langfuse_observation_id,
        )
        if observation_id and run.langfuse_observation_id is None:
            await run_repo.set_langfuse_observation_id(str(run_id), observation_id, worker_id=str(worker_id))


async def _project_graph_event(event, *, recorder, adapter, state_signature: str) -> tuple[list[dict], str]:
    """将单个原生事件投影到公开协议，审计在公开输出之前完成。"""
    method, params = event["method"], event["params"]
    if method == "values" and not params.get("namespace"):
        state = extract_agent_state(params["data"])
        signature = _agent_state_signature(state)
        if signature != state_signature:
            return [adapter.extension("turn.state", f"state:{event['seq']}", agent_state=state)], signature
        return [], state_signature
    if method == "custom":
        payload = params["data"]
        if isinstance(payload, dict) and payload.get("type") == "yuxi.context_compression":
            return [adapter.extension("turn.context_compression", f"compression:{event['seq']}", compression=payload)], state_signature
        return [], state_signature
    event = await recorder.consume(event)
    return await adapter.consume(event), state_signature
