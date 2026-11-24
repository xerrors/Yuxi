"""按授权读取并装配持久模型、工具和 Run 审计。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.runs import AgentRun, build_agent_run_timing
from yuxi.modules.agents.repositories.messages import MessageRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.agents.tool_results import durable_tool_output, normalize_tool_result
from yuxi.modules.models.utils import parse_assistant_message_body
from yuxi.shared.datetime import format_utc_datetime
from yuxi.shared.errors import PermissionDeniedError

MESSAGE_AUDIT_LIMIT = 500
AGENT_RUN_TRACE_LIMIT = 500


async def get_thread_audits(*, db: AsyncSession, scope: ActorScope, thread_id: str) -> dict:
    """只允许无 API Key 的超级管理员读取持久调试消息。"""
    if not scope.is_superadmin or scope.api_key_id is not None or scope.app_id is not None:
        raise PermissionDeniedError("无权读取模型与工具审计")
    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id)
    messages, truncated = await MessageRepository(db).list_message_audits(agent_session.id, limit=MESSAGE_AUDIT_LIMIT)
    runs, runs_truncated = await AgentRunRepository(db).list_agent_runs_for_trace(agent_session.id, limit=AGENT_RUN_TRACE_LIMIT)
    turn_ids = {run.turn_id for run in runs} | {message.turn_id for message in messages if message.turn_id}
    turns = await AgentTurnRepository(db).list_for_scope(turn_ids=list(turn_ids), thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
    return {
        "audits": [_serialize_message_audit(message) for message in messages],
        "runs": [_serialize_run_trace(run) for run in runs],
        "turns": [
            {
                "turn_id": turn.id,
                "status": turn.status,
                "created_at": format_utc_datetime(turn.created_at),
                "finished_at": format_utc_datetime(turn.finished_at),
            }
            for turn in turns.values()
        ],
        "runs_truncated": runs_truncated,
        "truncated": truncated,
    }


def _serialize_message_audit(message: Any) -> dict[str, Any]:
    """按真实角色序列化持久消息，模型和工具保留专有字段。"""
    if message.role == "tool":
        return serialize_tool_audit(message)
    if message.role == "assistant":
        return _serialize_model_audit(message)
    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    return {
        **_serialize_audit_base(message, metadata),
        "type": "human" if message.role == "user" else message.role,
        "input_id": message.source_input_id,
        "input_position": message.input_position,
        "received_at": format_utc_datetime(message.received_at),
        "delivery_status": message.delivery_status,
    }


def _serialize_run_trace(run: AgentRun) -> dict[str, Any]:
    """从 AgentRun Owner 投影调试面板所需的状态与阶段时间。"""
    return {
        "run_id": run.id,
        "turn_id": run.turn_id,
        "status": run.status,
        "timing": build_agent_run_timing(
            created_at=run.created_at,
            started_at=run.started_at,
            prepared_at=run.prepared_at,
            first_output_at=run.first_output_at,
            finished_at=run.finished_at,
            first_model_request_at=getattr(run, "first_model_request_at", None),
        ),
    }


def _serialize_model_audit(message: Any) -> dict[str, Any]:
    """将 Model 审计事实收敛为前端调试 DTO。"""
    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    content_blocks = metadata.get("content")
    model_run_id = metadata.get("model_run_id")
    return {
        **_serialize_audit_base(message, metadata),
        **parse_assistant_message_body(message.content),
        "type": "ai",
        "usage": dict(message.usage) if isinstance(message.usage, dict) else None,
        "model_run_id": model_run_id if isinstance(model_run_id, str) else None,
        "content_blocks": content_blocks if isinstance(content_blocks, list) else [],
        "error_type": metadata.get("error_type"),
        "error_message": metadata.get("error_message"),
        "tool_calls": list(metadata.get("tool_calls") or []),
    }


def serialize_tool_audit(message: Any) -> dict[str, Any]:
    """将 ToolMessage 审计事实收敛为前端调试 DTO。"""
    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    durable_output = durable_tool_output(metadata.get("output"))
    return {
        **_serialize_audit_base(message, metadata),
        "type": "tool",
        "content": normalize_tool_result(durable_output).audit_content,
        "tool_call_id": message.operation_id,
        "source": metadata.get("source"),
        "tool_name": metadata.get("tool_name"),
        "tool_input": dict(metadata["input"]) if isinstance(metadata.get("input"), dict) else {},
        "tool_output": durable_output,
        "error_message": metadata.get("error_message"),
        "source_model_operation_id": metadata.get("source_model_operation_id"),
        "usage": None,
    }


def _serialize_audit_base(message: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    """序列化 Model/Tool 审计共有字段。"""
    namespace = metadata.get("namespace")
    finished_sequence = metadata.get("finished_sequence")
    if not isinstance(finished_sequence, int) or isinstance(finished_sequence, bool):
        finished_sequence = None
    return {
        "id": message.id,
        "content": message.content,
        "created_at": format_utc_datetime(message.created_at),
        "run_id": message.run_id,
        "turn_id": message.turn_id,
        "message_type": message.message_type,
        "operation_id": message.operation_id,
        "started_at": format_utc_datetime(message.started_at),
        "finished_at": format_utc_datetime(message.finished_at),
        "duration_ms": message.duration_ms,
        "sequence": message.sequence,
        "finished_sequence": finished_sequence,
        "execution_status": message.execution_status,
        "namespace": [item for item in namespace if isinstance(item, str)] if isinstance(namespace, list) else [],
    }
