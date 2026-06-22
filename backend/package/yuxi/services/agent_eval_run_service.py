"""Agent evaluation run service.

This service intentionally does not implement dataset storage or judging. It
creates a normal conversation-backed AgentRun, blocks until it finishes, and
returns the run's final result by reusing the shared agent_run base capability.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_run_service import await_agent_run_result, create_agent_run_view
from yuxi.storage.postgres.models_business import User

EVALUATION_SOURCE = "agent_evaluation"
EVALUATION_FIELDS = ("dataset_name", "dataset_item_id", "experiment_name")
MAX_REQUEST_ID_LENGTH = 64


def _normalize_evaluation(evaluation: dict[str, Any] | None) -> dict[str, str]:
    """仅保留已知评估字段，并统一转成去空白的非空字符串。"""
    if not isinstance(evaluation, dict):
        return {}

    normalized: dict[str, str] = {}
    for key in EVALUATION_FIELDS:
        value = evaluation.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text
    return normalized


def _normalize_request_id(meta: dict[str, Any] | None) -> str:
    """返回去空白并校验长度的 request_id；缺省时生成新的 UUID。"""
    raw_request_id = (meta or {}).get("request_id")
    if raw_request_id is None or not str(raw_request_id).strip():
        return str(uuid.uuid4())

    request_id = str(raw_request_id).strip()
    if len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise HTTPException(status_code=422, detail=f"request_id 不能超过 {MAX_REQUEST_ID_LENGTH} 个字符")
    return request_id


async def run_agent_eval(
    *,
    query: str,
    agent_slug: str,
    evaluation: dict[str, Any] | None,
    meta: dict[str, Any] | None,
    image_content: str | None,
    model_spec: str | None,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    """创建评估 AgentRun，阻塞至运行结束并返回最终结果。

    评估调用方只关心最终输出，因此不做 SSE 流式封装：建 run 后直接复用
    ``await_agent_run_result`` 等待运行终结并返回结果。注意这会让 HTTP 请求阻塞至
    运行结束（无中间字节），网关链路上长运行需相应放宽空闲超时。
    """
    agent_slug = agent_slug.strip()
    if not agent_slug:
        raise HTTPException(status_code=422, detail="agent_slug 不能为空")
    if not query:
        raise HTTPException(status_code=422, detail="query 不能为空")

    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_slug, user=current_user)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    evaluation_metadata = _normalize_evaluation(evaluation)
    request_id = _normalize_request_id(meta)
    thread_id = str(uuid.uuid4())

    await ConversationRepository(db).create_conversation(
        uid=str(current_user.uid),
        agent_id=agent_item.slug,
        title="Agent Evaluation Run",
        thread_id=thread_id,
        metadata={
            "source": EVALUATION_SOURCE,
            "evaluation": evaluation_metadata,
        },
    )

    run_meta = {
        "request_id": request_id,
        "source": EVALUATION_SOURCE,
        "evaluation": evaluation_metadata,
        "attachment_file_ids": (meta or {}).get("attachment_file_ids") or [],
    }
    run_response = await create_agent_run_view(
        query=query,
        agent_id=agent_item.slug,
        thread_id=thread_id,
        meta=run_meta,
        image_content=image_content,
        current_uid=str(current_user.uid),
        db=db,
        model_spec=model_spec,
    )
    return await await_agent_run_result(run_id=run_response["run_id"], current_uid=str(current_user.uid))
