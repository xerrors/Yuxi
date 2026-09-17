"""Agent Evaluation HTTP 协议适配与轻量轨迹摘要。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.agent_run_service import AgentRunWaitTimeout, await_agent_run_result
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.services.agent_request_service import RunOrigin, AgentRequestInput, submit_agent_request
from yuxi.storage.postgres.models_business import User
from yuxi.utils.hash_utils import hash_id
from yuxi.utils.logging_config import logger

from server.utils.auth_middleware import get_db, get_required_user

agent_invocation_eval_router = APIRouter(prefix="/agent-invocation/eval", tags=["agent-invocation"])

EVALUATION_FIELDS = ("dataset_name", "dataset_item_id", "experiment_name")
EVALUATION_SOURCE = "agent_evaluation"
TRAJECTORY_SUMMARY_EVENT_LIMIT = 500
INTERRUPT_STATUSES = {"ask_user_question_required", "human_approval_required", "interrupted"}


class AgentEvaluationContext(BaseModel):
    """评估运行关联的 Langfuse 数据集上下文。"""

    dataset_name: str | None = Field(None, description="Langfuse dataset 名称")
    dataset_item_id: str | None = Field(None, description="Langfuse dataset item ID")
    experiment_name: str | None = Field(None, description="Langfuse experiment/run 名称")


class AgentEvalRunCreate(BaseModel):
    """Agent Eval 创建请求。"""

    query: str = Field(..., description="评估样例输入")
    agent_slug: str = Field(..., description="要运行的智能体 slug")
    thread_id: str | None = Field(
        None,
        max_length=64,
        description="可选会话线程 ID，不传则自动创建临时线程",
    )
    evaluation: AgentEvaluationContext = Field(default_factory=AgentEvaluationContext, description="评估上下文")
    meta: dict = Field(default_factory=dict, description="可选请求追踪信息")
    image_content: str | None = Field(None, description="可选，base64 图片内容")
    model_spec: str | None = Field(None, description="可选模型覆盖")
    tool_approval_mode: str | None = Field(None, description="可选工具审批模式覆盖")
    include_trajectory_summary: bool = Field(False, description="是否返回轻量工具调用轨迹摘要")


@agent_invocation_eval_router.post("/runs")
async def create_agent_eval_run(
    payload: AgentEvalRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """运行一次评估样例，并阻塞等待最终 AgentRun 结果。"""
    agent_slug = str(payload.agent_slug or "").strip()
    if not agent_slug:
        raise HTTPException(status_code=422, detail="agent_slug 不能为空")
    if not payload.query:
        raise HTTPException(status_code=422, detail="query 不能为空")

    meta = dict(payload.meta or {})
    request_id = _normalize_request_id(meta)
    evaluation = _normalize_evaluation(payload.evaluation.model_dump(exclude_none=True))
    origin_metadata = {"agent_invocation_meta": {"evaluation": evaluation}} if evaluation else {}
    run_response = await submit_agent_request(
        request_input=AgentRequestInput(
            agent_slug=agent_slug,
            thread_id=(payload.thread_id or "").strip()
            or hash_id("invocation_", f"{current_user.uid}:{agent_slug}:{request_id}", length=64),
            request_id=request_id,
            input_message=build_chat_input_message(payload.query, payload.image_content),
            origin=RunOrigin(
                source=EVALUATION_SOURCE,
                channel="api",
                external_id=request_id,
                metadata=origin_metadata,
            ),
            request_metadata={"request_id": request_id, "attachment_file_ids": meta.get("attachment_file_ids") or []},
            model_spec=payload.model_spec,
            tool_approval_mode=payload.tool_approval_mode,
            queue_policy="reject",
            create_conversation=True,
            conversation_title="Agent Evaluation Run",
        ),
        current_user=current_user,
        db=db,
    )
    try:
        result = await await_agent_run_result(run_id=run_response["run_id"], current_uid=str(current_user.uid))
    except AgentRunWaitTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail={"message": "运行仍在进行中，等待最终结果超时", "run": exc.result},
        ) from exc
    if payload.include_trajectory_summary:
        try:
            summary = await _load_trajectory_summary(run_response["run_id"])
            if result.get("langfuse_trace_id"):
                summary["langfuse_trace_id"] = result["langfuse_trace_id"]
            result["trajectory_summary"] = summary
        except Exception as exc:
            logger.warning("Failed to load trajectory summary for run %s: %s", run_response["run_id"], exc)
    return result


def _normalize_request_id(meta: dict[str, Any]) -> str:
    """从评估元数据中提取或生成请求幂等 ID。"""
    request_id = str(meta.get("request_id") or "").strip()
    if request_id:
        if len(request_id) > 64:
            raise HTTPException(status_code=422, detail="request_id 不能超过 64 个字符")
        return request_id
    import uuid

    return str(uuid.uuid4())


def _normalize_evaluation(evaluation: dict[str, Any]) -> dict[str, str]:
    """只保留非空的评估上下文字段。"""
    normalized: dict[str, str] = {}
    for key in EVALUATION_FIELDS:
        value = evaluation.get(key)
        if value is not None and str(value).strip():
            normalized[key] = str(value).strip()
    return normalized


async def _load_trajectory_summary(run_id: str) -> dict[str, Any]:
    """读取运行事件并生成轻量轨迹摘要。"""
    events = await list_run_stream_events(run_id, after_seq="0-0", limit=TRAJECTORY_SUMMARY_EVENT_LIMIT)
    return _build_trajectory_summary(events)


def _build_trajectory_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """从运行事件中统计工具调用、错误和中断概览。"""
    summary = {
        "schema_version": 1,
        "source": "run_events",
        "event_count": len(events),
        "events_truncated": len(events) >= TRAJECTORY_SUMMARY_EVENT_LIMIT,
        "event_range": {
            "first_seq": str(events[0].get("seq")) if events and events[0].get("seq") is not None else None,
            "last_seq": str(events[-1].get("seq")) if events and events[-1].get("seq") is not None else None,
        },
        "tool_call_count": 0,
        "tool_error_count": 0,
        "interrupt_count": 0,
        "tools": [],
    }
    tool_calls: dict[str, str] = {}
    tool_errors: set[str] = set()
    open_tools: dict[str, list[str]] = {}
    fallback_index = 0

    def tool_key(tool_call_id: str | None, name: str, *, start: bool, finish: bool) -> str:
        nonlocal fallback_index
        if tool_call_id:
            return str(tool_call_id)
        if finish and open_tools.get(name):
            return open_tools[name].pop(0)
        key = f"name:{name}:{fallback_index}"
        fallback_index += 1
        if start and not finish:
            open_tools.setdefault(name, []).append(key)
        return key

    for event in events:
        event_type = event.get("event_type")
        if event_type == "interrupt":
            summary["interrupt_count"] += 1
        for chunk in _iter_event_chunks(event):
            if event_type not in {"interrupt", "end"} and chunk.get("status") in INTERRUPT_STATUSES:
                summary["interrupt_count"] += 1
            stream_event = chunk.get("stream_event")
            if isinstance(stream_event, dict) and stream_event.get("type") == "tool_call":
                name = str(stream_event.get("name") or "unknown")
                key = tool_key(stream_event.get("tool_call_id"), name, start=True, finish=False)
                tool_calls.setdefault(key, name)
            tool_event = chunk.get("event")
            data = tool_event.get("data") if isinstance(tool_event, dict) else None
            if not isinstance(data, dict):
                continue
            name = str(data.get("tool_name") or data.get("name") or "unknown")
            event_name = data.get("event")
            key = tool_key(
                data.get("tool_call_id"),
                name,
                start=event_name == "tool-started",
                finish=event_name == "tool-finished",
            )
            if event_name == "tool-started" or key not in tool_calls:
                tool_calls.setdefault(key, name)
            if data.get("error") or event_type == "error":
                tool_errors.add(key)

    tools: dict[str, dict[str, Any]] = {}
    for key, name in tool_calls.items():
        item = tools.setdefault(name, {"name": name, "call_count": 0, "error_count": 0})
        item["call_count"] += 1
        if key in tool_errors:
            item["error_count"] += 1
    summary["tool_call_count"] = len(tool_calls)
    summary["tool_error_count"] = len(tool_errors)
    summary["tools"] = sorted(tools.values(), key=lambda item: item["name"])
    return summary


def _iter_event_chunks(event: dict[str, Any]):
    """遍历单个运行事件里的有效 chunk。"""
    envelope = event.get("payload")
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        return
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                yield item
    chunk = payload.get("chunk")
    if isinstance(chunk, dict):
        yield chunk
