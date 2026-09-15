"""删除后阻止旧会话的文件衍生历史再次发送模型。"""

from contextvars import ContextVar
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from yuxi.repositories.personal_trash_repository import PersonalTrashRepository
from yuxi.services.personal_trash_service import lock_user_files, require_no_pending_file_operations
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry


class PersonalFileEvidenceUnavailable(RuntimeError):
    """当前历史可能引用已删除的个人文件。"""


personal_file_evidence_db = ContextVar("personal_file_evidence_db", default=None)

MEMORY_HISTORY_STARTED_AT = "memory_history_started_at"


async def require_current_personal_file_history(db, uid: str, thread_id: str) -> datetime:
    """无可靠文件摘要来源戳的旧历史按删除时间保守隔离，包含父会话。"""
    await lock_user_files(db, str(uid))
    await require_no_pending_file_operations(db, str(uid))
    conversations = await PersonalTrashRepository(db).lock_conversations(str(uid), str(thread_id))
    if not conversations:
        raise HTTPException(404, "会话不存在或已失效")
    earliest = min(
        min(row.created_at, datetime.fromisoformat((row.extra_metadata or {})[MEMORY_HISTORY_STARTED_AT]))
        if (row.extra_metadata or {}).get(MEMORY_HISTORY_STARTED_AT)
        else row.created_at
        for row in conversations
    )
    deleted = await db.scalar(
        select(PersonalTrashEntry.id)
        .where(
            PersonalTrashEntry.uid == str(uid),
            PersonalTrashEntry.state != "restored",
            PersonalTrashEntry.deleted_at >= earliest,
        )
        .limit(1)
    )
    if deleted:
        raise HTTPException(409, "此旧会话可能引用已删除文件；请恢复文件或新建会话，原聊天记录仍保留")
    return earliest


async def validate_personal_file_evidence(context) -> None:
    """每次模型与摘要发送前重新核对，数据库故障不放行。"""
    if context is None:
        return
    uid = getattr(context, "uid", None)
    thread_id = getattr(context, "thread_id", None)
    try:
        if not uid or not thread_id:
            raise ValueError("缺少可信会话身份")
        current_db = personal_file_evidence_db.get()
        if current_db is not None:
            await require_current_personal_file_history(current_db, str(uid), str(thread_id))
        else:
            async with pg_manager.get_async_session_context() as db:
                await require_current_personal_file_history(db, str(uid), str(thread_id))
    except Exception as exc:
        raise PersonalFileEvidenceUnavailable("个人文件历史复核未通过；如已删除文件，请恢复文件或新建会话") from exc
