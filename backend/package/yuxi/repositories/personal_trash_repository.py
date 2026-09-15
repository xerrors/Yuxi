"""个人回收站journal与原附件Owner的事务访问。"""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.orm.attributes import flag_modified

from yuxi.agents.backends.paths import workspace_scope_from_runtime_path
from yuxi.storage.postgres.models_business import (
    AGENT_RUN_TERMINAL_STATUSES,
    AgentRun,
    AgentRunRequest,
    Conversation,
    Project,
    SubagentThread,
)
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry
from yuxi.utils.datetime_utils import utc_now_naive


class PersonalTrashRepository:
    """以用户事务锁串行化文件生命周期与请求接入。"""

    def __init__(self, db):
        self.db = db

    async def lock_user(self, uid: str) -> None:
        """所有影响运行可读字节的变更使用同一事务锁。"""
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"user-files:{uid}"}
        )

    async def require_idle(self, uid: str) -> None:
        """单SQL快照覆盖queued到run的交接，避免两个查询之间漏掉活动Owner。"""
        active = (
            select(AgentRun.id)
            .where(
                AgentRun.uid == uid,
                or_(AgentRun.status.not_in(AGENT_RUN_TERMINAL_STATUSES), AgentRun.runtime_cleanup_pending.is_(True)),
            )
            .exists()
        )
        queued = (
            select(AgentRunRequest.id)
            .where(
                AgentRunRequest.uid == uid,
                AgentRunRequest.status == "queued",
            )
            .exists()
        )
        if await self.db.scalar(select(or_(active, queued))):
            raise HTTPException(status_code=409, detail="请先等待本账号的运行和排队任务结束，再删除或恢复文件")

    async def require_settled(self, uid: str) -> None:
        """中断操作恢复前拒绝新的文件引用。"""
        entry = await self.db.scalar(
            select(PersonalTrashEntry.id)
            .where(
                PersonalTrashEntry.uid == uid, PersonalTrashEntry.state.in_(("pending_delete", "restoring", "purging"))
            )
            .limit(1)
        )
        if entry:
            raise HTTPException(status_code=409, detail="文件回收操作尚在处理，请在统一回收站重试或稍后再试")

    async def get(self, uid: str, entry_id: str):
        """按owner锁定真实记录，跨用户查询等同不存在。"""
        return await self.db.scalar(
            select(PersonalTrashEntry)
            .where(PersonalTrashEntry.uid == uid, PersonalTrashEntry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list(self, uid: str, page: int, page_size: int) -> dict:
        """分页列出个人文件与附件，错误状态保留可见。"""
        condition = (PersonalTrashEntry.uid == uid, PersonalTrashEntry.state.not_in(("restored", "purged")))
        total = await self.db.scalar(select(func.count()).select_from(PersonalTrashEntry).where(*condition))
        rows = (
            await self.db.scalars(
                select(PersonalTrashEntry)
                .where(*condition)
                .order_by(PersonalTrashEntry.deleted_at.desc(), PersonalTrashEntry.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return {"items": [self.serialize(row) for row in rows], "total": total}

    async def deleted_paths(self, uid: str) -> list[str]:
        """提供已删除路径前缀，purged保留tombstone使旧引用不复活。"""
        rows = (
            await self.db.scalars(
                select(PersonalTrashEntry.paths).where(
                    PersonalTrashEntry.uid == uid, PersonalTrashEntry.state != "restored"
                )
            )
        ).all()
        return [item["path"] for paths in rows for item in paths]

    async def expand_attachment_paths(self, uid: str, paths: list[str]) -> list[str]:
        """任一正式附件字节被回收时，从原Owner补齐原件和解析件的完整批次。"""
        conversations = (
            await self.db.scalars(
                select(Conversation)
                .where(Conversation.uid == uid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        groups = []
        for conversation in conversations:
            for item in (conversation.extra_metadata or {}).get("attachments", []):
                if not isinstance(item, dict) or item.get("trash_id"):
                    continue
                group = []
                for raw in (item.get("path"), item.get("original_path")):
                    if not isinstance(raw, str):
                        continue
                    try:
                        group.append(workspace_scope_from_runtime_path(raw))
                    except ValueError:
                        continue
                groups.append(group)
        expanded = set(paths)
        while True:
            before = len(expanded)
            for group in groups:
                if any(PurePosixPath(member).is_relative_to(path) for member in group for path in expanded):
                    expanded.update(group)
            if len(expanded) == before:
                return sorted(expanded, key=lambda value: (len(PurePosixPath(value).parts), value))

    async def mark_attachments(self, uid: str, entry, *, restore: bool = False) -> None:
        """直接更新Conversation附件元数据，不建立独立附件副本。"""
        conversations = (
            await self.db.scalars(
                select(Conversation)
                .where(Conversation.uid == uid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        paths = [PurePosixPath(item["path"]) for item in entry.paths]
        for conversation in conversations:
            metadata = dict(conversation.extra_metadata or {})
            attachments = [dict(item) for item in metadata.get("attachments", [])]
            changed = False
            for item in attachments:
                if restore:
                    if item.get("trash_id") == entry.id:
                        item.pop("trash_id", None)
                        item.pop("deleted_at", None)
                        changed = True
                    continue
                if item.get("trash_id"):
                    continue
                for raw in (item.get("path"), item.get("original_path")):
                    if not isinstance(raw, str):
                        continue
                    try:
                        candidate = PurePosixPath(workspace_scope_from_runtime_path(raw))
                    except ValueError:
                        continue
                    if any(candidate == path or candidate.is_relative_to(path) for path in paths):
                        item["trash_id"] = entry.id
                        item["deleted_at"] = entry.deleted_at.isoformat() + "Z"
                        changed = True
                        break
            if changed:
                metadata["attachments"] = attachments
                conversation.extra_metadata = metadata
                flag_modified(conversation, "extra_metadata")

    async def due(self, limit: int) -> list[tuple[str, str]]:
        """失败项延后重试，避免毒性条目长期饿死后续到期记录。"""
        now = utc_now_naive()
        rows = (
            await self.db.execute(
                select(PersonalTrashEntry.uid, PersonalTrashEntry.id)
                .where(
                    or_(PersonalTrashEntry.retry_after.is_(None), PersonalTrashEntry.retry_after <= now),
                    or_(
                        PersonalTrashEntry.state.in_(("pending_delete", "restoring", "purging")),
                        (PersonalTrashEntry.state == "trashed") & (PersonalTrashEntry.purge_after <= now),
                    ),
                )
                .order_by(PersonalTrashEntry.deleted_at)
                .limit(limit)
            )
        ).all()
        return [(row.uid, row.id) for row in rows]

    @staticmethod
    def serialize(row) -> dict:
        """只输出owner可见恢复信息，不暴露宿主路径或inode。"""
        return {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "paths": [item["path"] for item in row.paths],
            "state": row.state,
            "deleted_at": row.deleted_at.isoformat() + "Z",
            "purge_after": row.purge_after.isoformat() + "Z",
            "error": row.error,
        }

    async def lock_conversations(self, uid, thread_id):
        """沿持久子会话关系找到祖先，稳定锁顺序合并来源戳。"""
        active_project = exists(
            select(Project.id).where(
                Project.id == Conversation.project_id, Project.uid == str(uid), Project.status == "active"
            )
        )
        current = await self.db.scalar(
            select(Conversation).where(
                active_project,
                Conversation.thread_id == thread_id,
                Conversation.uid == str(uid),
                Conversation.status == "active",
            )
        )
        if current is None:
            return []
        ids = {current.id}
        while True:
            parent = await self.db.scalar(
                select(SubagentThread.parent_conversation_id).where(
                    SubagentThread.child_conversation_id == current.id, SubagentThread.uid == str(uid)
                )
            )
            if parent is None:
                break
            if parent in ids or len(ids) >= 32:
                raise ValueError("会话来源链无效")
            current = await self.db.scalar(
                select(Conversation).where(
                    Conversation.id == parent,
                    Conversation.uid == str(uid),
                    Conversation.status == "active",
                    active_project,
                )
            )
            if current is None:
                raise ValueError("父会话已失效")
            ids.add(current.id)
        rows = list(
            await self.db.scalars(
                select(Conversation)
                .where(
                    Conversation.id.in_(ids),
                    Conversation.uid == str(uid),
                    Conversation.status == "active",
                    active_project,
                )
                .order_by(Conversation.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

        if {row.id for row in rows} != ids:
            raise ValueError("会话链在锁定前变化")
        return rows
