import asyncio
import json
import traceback
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from langchain.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.types import Command
from yuxi import config as conf
from yuxi.agents.buildin import agent_manager
from yuxi.agents.state import AgentStatePayload
from yuxi.plugins.guard import content_guard
from yuxi.repositories.agent_config_repository import AgentConfigRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.langfuse_service import (
    LangfuseRunContext,
    build_run_context,
    flush_langfuse,
    get_trace_info,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger
from yuxi.utils.question_utils import (
    normalize_options as _normalize_interrupt_options,
)
from yuxi.utils.question_utils import (
    normalize_questions as _normalize_interrupt_questions,
)


def _build_state_files(attachments: list[dict]) -> dict:
    """将附件列表转换为 StateBackend 格式的 files 字典

    StateBackend 期望的格式:
    {
        "/attachments/file.md": {
            "content": ["line1", "line2", ...],
            "created_at": "...",
            "modified_at": "...",
        }
    }
    """
    files = {}
    for attachment in attachments:
        if attachment.get("status") != "parsed":
            continue

        file_path = attachment.get("file_path")
        markdown = attachment.get("markdown")

        if not file_path or not markdown:
            continue

        now = datetime.now(UTC).isoformat()
        # 将 markdown 内容按行拆分
        content_lines = markdown.split("\n")
        files[file_path] = {
            "content": content_lines,
            "created_at": attachment.get("uploaded_at", now),
            "modified_at": attachment.get("uploaded_at", now),
        }

    return files


async def _get_langgraph_messages(agent_instance, config_dict):
    graph = await agent_instance.get_graph()
    state = await graph.aget_state(config_dict)

    if not state or not state.values:
        logger.warning("No state found in LangGraph")
        return None

    return state.values.get("messages", [])


def _build_langfuse_run_context(
    *,
    current_user,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    agent_config_id: int | None = None,
    message_type: str | None = None,
) -> LangfuseRunContext:
    return build_run_context(
        user_id=str(current_user.id),
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=request_id,
        operation=operation,
        agent_config_id=agent_config_id,
        message_type=message_type,
        username=getattr(current_user, "username", None),
        login_user_id=getattr(current_user, "user_id", None),
        department_id=getattr(current_user, "department_id", None),
    )


def extract_agent_state(values: dict) -> AgentStatePayload:
    """从 LangGraph state 中提取 agent 状态"""
    if not isinstance(values, dict):
        return {"todos": [], "files": {}, "uploads": [], "artifacts": []}

    # 直接获取，信任 state 的数据结构
    todos = values.get("todos")
    artifacts = values.get("artifacts")
    uploads = values.get("uploads")
    result: AgentStatePayload = {
        "todos": list(todos)[:20] if todos else [],
        "files": values.get("files") or {},
        "uploads": list(uploads) if uploads else [],
        "artifacts": list(artifacts) if artifacts else [],
    }

    return result


def _agent_state_signature(agent_state: AgentStatePayload | dict | None) -> str:
    if not agent_state:
        return ""
    try:
        return json.dumps(agent_state, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(agent_state)


async def _stream_agent_events(agent, messages, *, input_context=None, **kwargs):
    if hasattr(agent, "stream_messages_with_state"):
        async for mode, payload in agent.stream_messages_with_state(
            messages,
            input_context=input_context,
            **kwargs,
        ):
            yield mode, payload
        return

    async for msg, metadata in agent.stream_messages(messages, input_context=input_context, **kwargs):
        yield "messages", (msg, metadata)


async def _get_existing_message_ids(conv_repo: ConversationRepository, thread_id: str) -> set[str]:
    existing_messages = await conv_repo.get_messages_by_thread_id(thread_id)
    return {
        msg.extra_metadata["id"]
        for msg in existing_messages
        if msg.extra_metadata and "id" in msg.extra_metadata and isinstance(msg.extra_metadata["id"], str)
    }


async def _save_ai_message(
    conv_repo: ConversationRepository,
    thread_id: str,
    msg_dict: dict,
    trace_info: dict[str, Any] | None = None,
) -> None:
    content = msg_dict.get("content", "")
    tool_calls_data = msg_dict.get("tool_calls", [])
    extra_metadata = dict(msg_dict)
    if trace_info:
        extra_metadata.update(trace_info)

    ai_msg = await conv_repo.add_message_by_thread_id(
        thread_id=thread_id,
        role="assistant",
        content=content,
        message_type="text",
        extra_metadata=extra_metadata,
    )

    if ai_msg and tool_calls_data:
        for tc in tool_calls_data:
            await conv_repo.add_tool_call(
                message_id=ai_msg.id,
                tool_name=tc.get("name", "unknown"),
                tool_input=tc.get("args", {}),
                status="pending",
                langgraph_tool_call_id=tc.get("id"),
            )


async def _save_tool_message(conv_repo: ConversationRepository, msg_dict: dict) -> None:
    tool_call_id = msg_dict.get("tool_call_id")
    content = msg_dict.get("content", "")

    if not tool_call_id:
        return

    if isinstance(content, list):
        tool_output = json.dumps(content) if content else ""
    else:
        tool_output = str(content)

    await conv_repo.update_tool_call_output(
        langgraph_tool_call_id=tool_call_id,
        tool_output=tool_output,
        status="success",
    )


async def save_partial_message(
    conv_repo: ConversationRepository,
    thread_id: str,
    full_msg=None,
    error_message: str | None = None,
    error_type: str = "interrupted",
    trace_info: dict[str, Any] | None = None,
):
    try:
        extra_metadata = {
            "error_type": error_type,
            "is_error": True,
            "error_message": error_message or f"发生错误: {error_type}",
        }
        if full_msg:
            msg_dict = full_msg.model_dump() if hasattr(full_msg, "model_dump") else {}
            content = full_msg.content if hasattr(full_msg, "content") else str(full_msg)
            extra_metadata = msg_dict | extra_metadata
        else:
            content = ""

        if trace_info:
            extra_metadata.update(trace_info)

        return await conv_repo.add_message_by_thread_id(
            thread_id=thread_id,
            role="assistant",
            content=content,
            message_type="text",
            extra_metadata=extra_metadata,
        )

    except Exception as e:
        logger.error(f"Error saving message: {e}")
        logger.error(traceback.format_exc())
        return None


async def save_messages_from_langgraph_state(
    agent_instance,
    thread_id: str,
    conv_repo: ConversationRepository,
    config_dict: dict,
    trace_info: dict[str, Any] | None = None,
) -> None:
    try:
        messages = await _get_langgraph_messages(agent_instance, config_dict)
        if messages is None:
            return

        existing_ids = await _get_existing_message_ids(conv_repo, thread_id)

        for msg in messages:
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
            msg_type = msg_dict.get("type", "unknown")

            if msg_type == "human" or getattr(msg, "id", None) in existing_ids:
                continue

            if msg_type == "ai":
                await _save_ai_message(conv_repo, thread_id, msg_dict, trace_info=trace_info)
            elif msg_type == "tool":
                await _save_tool_message(conv_repo, msg_dict)

    except Exception as e:
        logger.error(f"Error saving messages from LangGraph state: {e}")
        logger.error(traceback.format_exc())


def _extract_interrupt_info(state) -> Any | None:
    """从 LangGraph state 中提取中断信息"""
    if hasattr(state, "tasks") and state.tasks:
        for task in state.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                return task.interrupts[0]

    interrupt_data = state.values.get("__interrupt__")
    if isinstance(interrupt_data, list) and interrupt_data:
        return interrupt_data[0]

    return None


def _coerce_interrupt_payload(info: Any) -> dict:
    """将 LangGraph interrupt 对象转换为 dict 结构。"""
    if isinstance(info, dict):
        return info

    payload = getattr(info, "value", None)
    if isinstance(payload, dict):
        return payload

    questions = getattr(info, "questions", None)
    question = getattr(info, "question", None)
    question_id = getattr(info, "question_id", None)
    options = getattr(info, "options", None)
    multi_select = getattr(info, "multi_select", None)
    allow_other = getattr(info, "allow_other", None)
    operation = getattr(info, "operation", None)
    source = getattr(info, "source", None)
    result: dict[str, Any] = {}
    if isinstance(questions, list):
        result["questions"] = questions
    if isinstance(question, str) and question.strip():
        result["question"] = question
    if isinstance(question_id, str) and question_id.strip():
        result["question_id"] = question_id
    if isinstance(options, list):
        result["options"] = options
    if isinstance(multi_select, bool):
        result["multi_select"] = multi_select
    if isinstance(allow_other, bool):
        result["allow_other"] = allow_other
    if isinstance(operation, str) and operation.strip():
        result["operation"] = operation
    if isinstance(source, str) and source.strip():
        result["source"] = source
    return result


def _build_ask_user_question_payload(info: Any, thread_id: str) -> dict[str, Any]:
    """将 interrupt 信息标准化为 ask_user_question_required 载荷。"""
    payload = _coerce_interrupt_payload(info)

    questions = _normalize_interrupt_questions(payload.get("questions"))
    if not questions:
        legacy_question = str(payload.get("question") or "").strip()
        if legacy_question:
            legacy_item: dict[str, Any] = {
                "question_id": str(payload.get("question_id") or uuid.uuid4()),
                "question": legacy_question,
                "options": _normalize_interrupt_options(payload.get("options")),
                "multi_select": bool(payload.get("multi_select", False)),
                "allow_other": bool(payload.get("allow_other", True)),
            }
            legacy_operation = payload.get("operation")
            if isinstance(legacy_operation, str) and legacy_operation.strip():
                legacy_item["operation"] = legacy_operation.strip()
            questions = [legacy_item]

    if not questions:
        questions = [
            {
                "question_id": str(uuid.uuid4()),
                "question": "请选择一个选项",
                "options": [],
                "multi_select": False,
                "allow_other": True,
            }
        ]

    source = str(payload.get("source") or payload.get("tool_name") or "interrupt")

    return {
        "questions": questions,
        "source": source,
        "thread_id": thread_id,
    }


def _ensure_full_msg(full_msg: AIMessage | None, accumulated_content: list[str]) -> AIMessage | None:
    """如果 full_msg 为空且有累积内容，构建 AIMessage"""
    if not full_msg and accumulated_content:
        return AIMessage(content="".join(accumulated_content))
    return full_msg


def _extract_ai_message(messages: list[Any] | None) -> AIMessage | None:
    """从消息列表中提取最后一条 AIMessage。"""
    if not isinstance(messages, list):
        return None

    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg

        msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
        if msg_dict.get("type") == "ai":
            content = msg_dict.get("content", "")
            return msg if hasattr(msg, "content") else AIMessage(content=content)

    return None


async def get_agent_config_by_id(db, user: User, agent_config_id: int):
    """按配置 ID 解析 AgentConfig 记录。"""
    department_id = user.department_id

    agent_config_repo = AgentConfigRepository(db)
    config_item = await agent_config_repo.get_by_id(config_id=int(agent_config_id))
    if config_item is None or config_item.department_id != department_id:
        raise ValueError("配置不存在")

    return config_item


async def _resolve_agent_config(db, agent_id: str, user: User, agent_config_id):
    """解析 agent_config，返回 agent_config"""
    department_id = user.department_id

    agent_config_repo = AgentConfigRepository(db)
    config_item = None
    if agent_config_id is not None:
        config_item = await get_agent_config_by_id(db, user, int(agent_config_id))
        if config_item.agent_id != agent_id:
            config_item = None

    if config_item is None:
        config_item = await agent_config_repo.get_or_create_default(
            department_id=department_id, agent_id=agent_id, created_by=str(user.id)
        )

    return (config_item.config_json or {}).get("context", {})


async def check_and_handle_interrupts(
    agent,
    langgraph_config: dict,
    make_chunk,
    meta: dict,
    thread_id: str,
) -> AsyncIterator[bytes]:
    try:
        graph = await agent.get_graph()
        state = await graph.aget_state(langgraph_config)

        if not state or not state.values:
            return

        interrupt_info = _extract_interrupt_info(state)
        if interrupt_info:
            question_payload = _build_ask_user_question_payload(interrupt_info, thread_id)
            meta["interrupt"] = question_payload
            yield make_chunk(status="ask_user_question_required", meta=meta, **question_payload)

    except Exception as e:
        logger.error(f"Error checking interrupts: {e}")
        logger.error(traceback.format_exc())


async def _ensure_thread_bound_agent_config(
    *,
    conv_repo: ConversationRepository,
    thread_id: str,
    user_id: str,
    agent_id: str,
    agent_config_id: int,
) -> None:
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation:
        conversation = await conv_repo.create_conversation(
            user_id=user_id,
            agent_id=agent_id,
            thread_id=thread_id,
        )

    current_agent_config_id = (conversation.extra_metadata or {}).get("agent_config_id")
    if current_agent_config_id != int(agent_config_id):
        await conv_repo.bind_agent_config(thread_id, agent_config_id)


async def agent_chat(
    *,
    query: str,
    agent_config_id: int,
    thread_id: str | None,
    meta: dict,
    image_content: str | None,
    current_user,
    db,
) -> dict:
    """非流式对话，返回完整响应"""
    start_time = asyncio.get_event_loop().time()

    if image_content:
        human_message = HumanMessage(
            content=[
                {"type": "text", "text": query},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_content}"}},
            ]
        )
        message_type = "multimodal_image"
    else:
        human_message = HumanMessage(content=query)
        message_type = "text"

    if conf.enable_content_guard and await content_guard.check(query):
        return {
            "status": "error",
            "error_type": "content_guard_blocked",
            "error_message": "输入内容包含敏感词",
            "request_id": meta.get("request_id"),
        }

    if not current_user.department_id:
        return {
            "status": "error",
            "error_type": "invalid_config",
            "error_message": "当前用户未绑定部门",
            "request_id": meta.get("request_id"),
        }

    user_id = str(current_user.id)
    meta = dict(meta or {})
    if "request_id" not in meta or not meta.get("request_id"):
        logger.warning("请求缺少 request_id，已自动生成一个新的 request_id")
        meta["request_id"] = str(uuid.uuid4())

    try:
        config_item = await get_agent_config_by_id(db, current_user, agent_config_id)
    except ValueError as e:
        return {
            "status": "error",
            "error_type": "invalid_config",
            "error_message": str(e),
            "request_id": meta.get("request_id"),
        }

    agent_id = config_item.agent_id
    meta.update(
        {
            "query": query,
            "agent_id": agent_id,
            "server_model_name": agent_id,
            "thread_id": thread_id,
            "user_id": current_user.id,
            "has_image": bool(image_content),
        }
    )

    try:
        agent = agent_manager.get_agent(agent_id)
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {e}, {traceback.format_exc()}")
        return {
            "status": "error",
            "error_type": "agent_error",
            "error_message": f"智能体 {agent_id} 获取失败: {str(e)}",
            "request_id": meta.get("request_id"),
        }

    messages = [human_message]
    agent_config = (config_item.config_json or {}).get("context", {})

    if not thread_id:
        thread_id = str(uuid.uuid4())
        logger.warning(f"No thread_id provided, generated new thread_id: {thread_id}")

    input_context = agent_config | {"user_id": user_id, "thread_id": thread_id}
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=meta["request_id"],
        operation="agent_chat_sync",
        agent_config_id=agent_config_id,
        message_type=message_type,
    )
    trace_info: dict[str, Any] = {}

    try:
        conv_repo = ConversationRepository(db)
        await _ensure_thread_bound_agent_config(
            conv_repo=conv_repo,
            thread_id=thread_id,
            user_id=user_id,
            agent_id=agent_id,
            agent_config_id=agent_config_id,
        )

        try:
            await conv_repo.add_message_by_thread_id(
                thread_id=thread_id,
                role="user",
                content=query,
                message_type=message_type,
                image_content=image_content,
                extra_metadata={"raw_message": human_message.model_dump()},
            )
        except Exception as e:
            logger.error(f"Error saving user message: {e}")

        langgraph_config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
        invoke_result = await agent.invoke_messages(
            messages,
            input_context=input_context,
            callbacks=langfuse_run.callbacks,
            metadata=langfuse_run.metadata,
            tags=langfuse_run.tags,
        )
        full_msg = _extract_ai_message(invoke_result.get("messages") if isinstance(invoke_result, dict) else None)
        trace_info = get_trace_info(langfuse_run)

        if full_msg is None:
            try:
                graph = await agent.get_graph()
                state = await graph.aget_state(langgraph_config)
                full_msg = _extract_ai_message(getattr(state, "values", {}).get("messages", [])) if state else None
            except Exception:
                full_msg = None

        full_content = full_msg.content if full_msg else ""

        if conf.enable_content_guard and await content_guard.check(full_content):
            await save_partial_message(
                conv_repo,
                thread_id,
                full_msg,
                "content_guard_blocked",
                trace_info=trace_info,
            )
            return {
                "status": "interrupted",
                "message": "检测到敏感内容，已中断输出",
                "request_id": meta.get("request_id"),
                "time_cost": asyncio.get_event_loop().time() - start_time,
            }

        try:
            graph = await agent.get_graph()
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        await save_messages_from_langgraph_state(
            agent_instance=agent,
            thread_id=thread_id,
            conv_repo=conv_repo,
            config_dict=langgraph_config,
            trace_info=trace_info,
        )

        return {
            "status": "finished",
            "response": full_content,
            "request_id": meta.get("request_id"),
            "thread_id": thread_id,
            "agent_state": agent_state,
            "time_cost": asyncio.get_event_loop().time() - start_time,
        }

    except Exception as e:
        logger.error(f"Error in agent_chat: {e}, {traceback.format_exc()}")
        return {
            "status": "error",
            "error_type": "unexpected_error",
            "error_message": str(e),
            "request_id": meta.get("request_id"),
        }
    finally:
        flush_langfuse()


async def stream_agent_chat(
    *,
    query: str,
    agent_config_id: int,
    thread_id: str | None,
    meta: dict,
    image_content: str | None,
    current_user,
    db,
) -> AsyncIterator[bytes]:
    start_time = asyncio.get_event_loop().time()

    def make_chunk(content=None, **kwargs):
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, **kwargs}, ensure_ascii=False
            ).encode("utf-8")
            + b"\n"
        )

    if image_content:
        human_message = HumanMessage(
            content=[
                {"type": "text", "text": query},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_content}"}},
            ]
        )
        message_type = "multimodal_image"
    else:
        human_message = HumanMessage(content=query)
        message_type = "text"

    init_msg = {"role": "user", "content": query, "type": "human"}
    if image_content:
        init_msg["message_type"] = "multimodal_image"
        init_msg["image_content"] = image_content
    else:
        init_msg["message_type"] = "text"

    yield make_chunk(status="init", meta=meta, msg=init_msg)

    if conf.enable_content_guard and await content_guard.check(query):
        yield make_chunk(
            status="error", error_type="content_guard_blocked", error_message="输入内容包含敏感词", meta=meta
        )
        return

    if not current_user.department_id:
        yield make_chunk(status="error", error_type="invalid_config", error_message="当前用户未绑定部门", meta=meta)
        return

    meta = dict(meta or {})
    if "request_id" not in meta or not meta.get("request_id"):
        logger.warning("请求缺少 request_id，已自动生成一个新的 request_id")
        meta["request_id"] = str(uuid.uuid4())

    user_id = str(current_user.id)
    try:
        config_item = await get_agent_config_by_id(db, current_user, agent_config_id)
    except ValueError as e:
        yield make_chunk(status="error", error_type="invalid_config", error_message=str(e), meta=meta)
        return

    agent_id = config_item.agent_id
    meta.update(
        {
            "query": query,
            "agent_id": agent_id,
            "server_model_name": agent_id,
            "thread_id": thread_id,
            "user_id": current_user.id,
            "has_image": bool(image_content),
        }
    )

    try:
        agent = agent_manager.get_agent(agent_id)
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {e}, {traceback.format_exc()}")
        yield make_chunk(
            status="error",
            error_type="agent_error",
            error_message=f"智能体 {agent_id} 获取失败: {str(e)}",
            meta=meta,
        )
        return

    messages = [human_message]
    agent_config = (config_item.config_json or {}).get("context", {})

    if not thread_id:
        thread_id = str(uuid.uuid4())
        logger.warning(f"No thread_id provided, generated new thread_id: {thread_id}")

    input_context = agent_config | {"user_id": user_id, "thread_id": thread_id}
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=meta["request_id"],
        operation="agent_chat_stream",
        agent_config_id=agent_config_id,
        message_type=message_type,
    )
    full_msg = None
    accumulated_content: list[str] = []
    trace_info: dict[str, Any] = {}
    last_agent_state_signature = ""

    try:
        conv_repo = ConversationRepository(db)
        await _ensure_thread_bound_agent_config(
            conv_repo=conv_repo,
            thread_id=thread_id,
            user_id=user_id,
            agent_id=agent_id,
            agent_config_id=agent_config_id,
        )

        try:
            await conv_repo.add_message_by_thread_id(
                thread_id=thread_id,
                role="user",
                content=query,
                message_type=message_type,
                image_content=image_content,
                extra_metadata={"raw_message": human_message.model_dump()},
            )
        except Exception as e:
            logger.error(f"Error saving user message: {e}")

        # 先构建 langgraph_config
        langgraph_config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}

        # LangGraph 会自动从 checkpointer 恢复 state（包括 uploads）
        # 无需手动加载或传递

        full_msg = None
        accumulated_content = []
        async for mode, payload in _stream_agent_events(
            agent,
            messages,
            input_context=input_context,
            callbacks=langfuse_run.callbacks,
            metadata=langfuse_run.metadata,
            tags=langfuse_run.tags,
        ):
            if mode == "values":
                agent_state = extract_agent_state(payload if isinstance(payload, dict) else {})
                signature = _agent_state_signature(agent_state)
                if signature and signature != last_agent_state_signature:
                    last_agent_state_signature = signature
                    yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                continue

            msg, metadata = payload
            if isinstance(msg, AIMessageChunk):
                accumulated_content.append(msg.content)
                trace_info = get_trace_info(langfuse_run)

                content_for_check = "".join(accumulated_content[-10:])
                if conf.enable_content_guard and await content_guard.check_with_keywords(content_for_check):
                    full_msg = AIMessage(content="".join(accumulated_content))
                    await save_partial_message(
                        conv_repo,
                        thread_id,
                        full_msg,
                        "content_guard_blocked",
                        trace_info=trace_info,
                    )
                    meta["time_cost"] = asyncio.get_event_loop().time() - start_time
                    yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
                    return

                yield make_chunk(content=msg.content, msg=msg.model_dump(), metadata=metadata, status="loading")
            else:
                msg_dict = msg.model_dump()
                trace_info = get_trace_info(langfuse_run)
                yield make_chunk(msg=msg_dict, metadata=metadata, status="loading")

        full_msg = _ensure_full_msg(full_msg, accumulated_content)
        trace_info = get_trace_info(langfuse_run)

        if conf.enable_content_guard and hasattr(full_msg, "content") and await content_guard.check(full_msg.content):
            await save_partial_message(
                conv_repo,
                thread_id,
                full_msg,
                "content_guard_blocked",
                trace_info=trace_info,
            )
            meta["time_cost"] = asyncio.get_event_loop().time() - start_time
            yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
            return

        async for chunk in check_and_handle_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
            yield chunk

        meta["time_cost"] = asyncio.get_event_loop().time() - start_time
        try:
            graph = await agent.get_graph()
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        final_signature = _agent_state_signature(agent_state)
        if final_signature and final_signature != last_agent_state_signature:
            last_agent_state_signature = final_signature
            yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)

        # 先存储数据库，再返回 finished，避免前端查询时数据未落库
        await save_messages_from_langgraph_state(
            agent_instance=agent,
            thread_id=thread_id,
            conv_repo=conv_repo,
            config_dict=langgraph_config,
            trace_info=trace_info,
        )

        yield make_chunk(status="finished", meta=meta)

    except (asyncio.CancelledError, ConnectionError) as e:
        logger.warning(f"Client disconnected, cancelling stream: {e}")

        async def save_cleanup():
            nonlocal full_msg
            full_msg = _ensure_full_msg(full_msg, accumulated_content)

            async with pg_manager.get_async_session_context() as new_db:
                new_conv_repo = ConversationRepository(new_db)
                await save_partial_message(
                    new_conv_repo,
                    thread_id,
                    full_msg=full_msg,
                    error_message="对话已中断" if not full_msg else None,
                    error_type="interrupted",
                    trace_info=trace_info,
                )

        cleanup_task = asyncio.create_task(save_cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Error during cleanup save: {exc}")

        yield make_chunk(status="interrupted", message="对话已中断", meta=meta)

    except Exception as e:
        logger.error(f"Error streaming messages: {e}, {traceback.format_exc()}")

        error_msg = f"Error streaming messages: {e}"
        error_type = "unexpected_error"

        full_msg = _ensure_full_msg(full_msg, accumulated_content)

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                full_msg=full_msg,
                error_message=error_msg,
                error_type=error_type,
                trace_info=trace_info,
            )

        yield make_chunk(status="error", error_type=error_type, error_message=error_msg, meta=meta)
    finally:
        flush_langfuse()


async def stream_agent_resume(
    *,
    agent_id: str,
    thread_id: str,
    resume_input: Any,
    meta: dict,
    config: dict,
    current_user,
    db,
) -> AsyncIterator[bytes]:
    start_time = asyncio.get_event_loop().time()

    def make_resume_chunk(content=None, **kwargs):
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, **kwargs}, ensure_ascii=False
            ).encode("utf-8")
            + b"\n"
        )

    try:
        agent = agent_manager.get_agent(agent_id)
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {e}, {traceback.format_exc()}")
        yield (
            f'{{"request_id": "{meta.get("request_id")}", "message": '
            f'"Error getting agent {agent_id}: {e}", "status": "error"}}\n'
        )
        return

    init_msg = {"type": "system", "content": f"Resume with input: {resume_input}"}
    yield make_resume_chunk(status="init", meta=meta, msg=init_msg)

    resume_command = Command(resume=resume_input)

    user_id = str(current_user.id)
    agent_config_id = (config or {}).get("agent_config_id")
    try:
        agent_config = await _resolve_agent_config(db, agent_id, current_user, agent_config_id)
    except ValueError as e:
        yield make_resume_chunk(status="error", error_type="invalid_config", error_message=str(e), meta=meta)
        return

    context = agent.context_schema()
    context.update(agent_config or {})
    context.update({"user_id": user_id, "thread_id": thread_id})
    graph = await agent.get_graph(context=context)
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=meta.get("request_id") or str(uuid.uuid4()),
        operation="agent_chat_resume",
        agent_config_id=agent_config_id,
        message_type="resume",
    )
    trace_info: dict[str, Any] = {}
    last_agent_state_signature = ""

    stream_source = graph.astream(
        resume_command,
        context=context,
        config={
            "configurable": {"thread_id": thread_id, "user_id": user_id},
            "callbacks": langfuse_run.callbacks,
            "metadata": langfuse_run.metadata,
            "tags": langfuse_run.tags,
        },
        stream_mode=["messages", "values"],
    )

    try:
        async for mode, payload in stream_source:
            if mode == "values":
                agent_state = extract_agent_state(payload if isinstance(payload, dict) else {})
                signature = _agent_state_signature(agent_state)
                if signature and signature != last_agent_state_signature:
                    last_agent_state_signature = signature
                    yield make_resume_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                continue

            msg, metadata = payload
            trace_info = get_trace_info(langfuse_run)
            msg_dict = msg.model_dump()
            if "id" not in msg_dict:
                msg_dict["id"] = str(uuid.uuid4())

            yield make_resume_chunk(
                content=getattr(msg, "content", ""), msg=msg_dict, metadata=metadata, status="loading"
            )

        langgraph_config = {"configurable": {"thread_id": thread_id, "user_id": str(current_user.id)}}
        async for chunk in check_and_handle_interrupts(agent, langgraph_config, make_resume_chunk, meta, thread_id):
            yield chunk

        meta["time_cost"] = asyncio.get_event_loop().time() - start_time

        try:
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        final_signature = _agent_state_signature(agent_state)
        if final_signature and final_signature != last_agent_state_signature:
            yield make_resume_chunk(status="agent_state", agent_state=agent_state, meta=meta)

        # 先存储数据库，再返回 finished，避免前端查询时数据未落库
        conv_repo = ConversationRepository(db)
        await save_messages_from_langgraph_state(
            agent_instance=agent,
            thread_id=thread_id,
            conv_repo=conv_repo,
            config_dict=langgraph_config,
            trace_info=trace_info,
        )

        yield make_resume_chunk(status="finished", meta=meta)

    except (asyncio.CancelledError, ConnectionError) as e:
        logger.warning(f"Client disconnected during resume: {e}")

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                error_message="对话恢复已中断",
                error_type="resume_interrupted",
                trace_info=trace_info,
            )

        yield make_resume_chunk(status="interrupted", message="对话恢复已中断", meta=meta)

    except Exception as e:
        logger.error(f"Error during resume: {e}, {traceback.format_exc()}")

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                error_message=f"Error during resume: {e}",
                error_type="resume_error",
                trace_info=trace_info,
            )

        yield make_resume_chunk(message=f"Error during resume: {e}", status="error")
    finally:
        flush_langfuse()


async def get_agent_state_view(
    *,
    thread_id: str,
    current_user_id: str,
    db,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(current_user_id) or conversation.status == "deleted":
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="对话线程不存在")

    agent = agent_manager.get_agent(conversation.agent_id)
    graph = await agent.get_graph()
    langgraph_config = {"configurable": {"user_id": str(current_user_id), "thread_id": thread_id}}
    state = await graph.aget_state(langgraph_config)
    agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}

    return {"agent_state": agent_state}
