"""实时和历史共用的公开 item 白名单投影。"""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun, build_agent_run_timing
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.services.attachments import serialize_attachment
from yuxi.modules.agents.services.public_resources import public_run_error_message
from yuxi.shared.datetime import format_utc_datetime
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError


async def list_session_items(
    *,
    db: AsyncSession,
    scope,
    thread_id: str,
    after: str | None = None,
    limit: int = 20,
    order: str = "desc",
    turn_id: str | None = None,
) -> dict:
    """按授权会话的稳定公开 item 身份读取分页，不暴露内部消息审计。"""
    from yuxi.modules.agents.repositories.public_items import PublicItemRepository
    from yuxi.modules.agents.services.thread_access import require_thread

    await require_thread(db=db, scope=scope, thread_id=thread_id)
    if turn_id is not None:
        from yuxi.modules.agents.repositories.turn import AgentTurnRepository

        turn = await AgentTurnRepository(db).get_for_scope(turn_id=turn_id, thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
        if turn is None:
            raise ResourceNotFoundError("Turn 不存在")
    try:
        rows = await PublicItemRepository(db).list_page(
            thread_id=thread_id,
            uid=scope.uid,
            app_id=scope.app_id,
            turn_id=turn_id,
            after=after,
            limit=limit,
            order=order,
        )
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc
    attachments = await AttachmentRepository(db).for_messages([row.id for row in rows[:limit] if row.role == "user"])
    page = [
        serialize_public_items(
            row,
            row.AgentRun,
            row.result_run_id,
            item=row.public_item,
            attachments=[serialize_attachment(attachment, thread_id=thread_id) for attachment in attachments.get(row.id, [])],
        )[0]
        for row in rows[:limit]
    ]
    from yuxi.modules.agents.repositories.input import AgentInputRepository

    input_ids = await AgentInputRepository(db).input_ids_for_runs([row.AgentRun.id for row in rows[:limit] if row.AgentRun])
    runs = {row.AgentRun.id: serialize_public_run(row.AgentRun, input_ids[row.AgentRun.id]) for row in rows[:limit] if row.AgentRun}
    return {
        "object": "list",
        "data": page,
        "first_id": page[0]["id"] if page else None,
        "last_id": page[-1]["id"] if page else None,
        "has_more": len(rows) > limit,
        "yuxi": {"runs": list(runs.values())},
    }


def serialize_public_items(
    message: Message,
    run: AgentRun | None,
    result_run_id: str | None,
    *,
    item: dict | None = None,
    attachments: list[dict] | None = None,
) -> list[dict]:
    """只投影公开身份已登记的输出和原始用户输入。"""
    if message.role == "user":
        raw = (message.extra_metadata or {}).get("raw_message") or {}
        content = raw.get("content")
        if not isinstance(content, list):
            content = [{"type": "input_text", "text": message.content or ""}]
        content = [
            {"type": "input_text", "text": part["text"]}
            if part.get("type") in {"text", "input_text"}
            else {
                "type": "input_image",
                "image_url": (part["image_url"]["url"] if isinstance(part.get("image_url"), dict) else part["image_url"]),
            }
            for part in content
            if part.get("type") in {"text", "input_text", "image_url", "input_image"}
        ]
        return [
            {
                "type": "message",
                "id": f"input_{message.id}",
                "turn_id": message.turn_id,
                "role": "user",
                "content": content,
                "status": "completed",
                "phase": None,
                "yuxi": {
                    "run_id": message.run_id,
                    "input_id": message.source_input_id,
                    "input_position": message.input_position,
                    "received_at": format_utc_datetime(message.received_at),
                    "message_id": message.id,
                    "delivery_status": message.delivery_status,
                    "created_at": format_utc_datetime(message.created_at),
                    "message_type": message.message_type,
                    "attachments": attachments or [],
                },
            }
        ]
    items = deepcopy([item] if item is not None else list((message.extra_metadata or {}).get("public_items", {}).values()))
    for item in items:
        if item["type"] == "message":
            item["phase"] = (
                "final_answer" if run is not None and run.id == result_run_id and run.output_message_id == message.id else "commentary"
            )
        if run is not None and run.status in {"failed", "cancelled", "interrupted", "yielded"}:
            if item["status"] == "in_progress":
                item["status"] = "failed" if run.status == "failed" and item["type"] != "message" else "incomplete"
            if run.status == "interrupted" and run.error_type == "cooperation_waiting":
                item["yuxi"]["waiting_kind"] = "cooperation"
    return sorted(items, key=lambda item: item["yuxi"]["output_index"])


def serialize_public_run(run: AgentRun, input_ids: list[str]) -> dict:
    """公开快照只包含执行身份、可见状态和时间，不泄漏配置与 manifest。"""
    result = {
        key: getattr(run, key)
        for key in (
            "id",
            "execution_seq",
            "turn_id",
            "thread_id",
            "agent_slug",
            "run_type",
            "status",
            "created_by_run_id",
            "resume_from_run_id",
            "token_usage",
            "langfuse_trace_id",
            "error_type",
            "error_message",
        )
    }
    result["error_message"] = public_run_error_message(result["error_message"])
    timestamps = (
        "started_at",
        "prepared_at",
        "first_model_request_at",
        "first_output_at",
        "finished_at",
        "created_at",
        "updated_at",
    )
    result.update({key: format_utc_datetime(getattr(run, key)) for key in timestamps})
    result["input_ids"] = input_ids
    result["timing"] = build_agent_run_timing(
        created_at=run.created_at,
        started_at=run.started_at,
        prepared_at=run.prepared_at,
        first_output_at=run.first_output_at,
        finished_at=run.finished_at,
        first_model_request_at=run.first_model_request_at,
    )
    return result
