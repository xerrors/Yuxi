"""维护窗口内审计并转换 knowledge v1；默认只读，--apply 才写入。"""

import argparse
import asyncio
import json
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func, select, text
from sqlalchemy.orm import DeclarativeBase

from yuxi.infrastructure.postgres.base import JSON_VALUE
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.models import KnowledgeFile as CurrentKnowledgeFile
from yuxi.modules.knowledge.models import KnowledgeImport
from yuxi.modules.knowledge.names import normalize_name, source_segments
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, StateConflictError


class DirectoryMigrationBase(DeclarativeBase):
    """旧目录迁移使用独立历史映射，不消费后续运行模型的新列。"""


class KnowledgeFile(DirectoryMigrationBase):
    """冻结 v1/v2 目录转换实际读写的列，保证旧数据库可直接审计和转换。"""

    __tablename__ = "knowledge_files"

    id = Column(Integer, primary_key=True)
    file_id = Column(String(64))
    kb_id = Column(String(80))
    parent_id = Column(String(64))
    filename = Column(String(512))
    original_filename = Column(String(512))
    path = Column(String(1024))
    file_type = Column(String(64))
    is_folder = Column(Boolean)
    status = Column(String(32))
    processing_params = Column(JSON_VALUE)
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now)
    deleted_at = Column(DateTime(timezone=True))


class DirectoryMigrationRepository(DirectoryRepository):
    """冻结旧文件的查询和创建；纯目录名称分配继续复用既有实现。"""

    async def named(self, session, kb_id, parent_id, name, exclude_id=None):
        """读取旧表已有同名节点，不请求新版本列。"""
        return await session.scalar(
            select(KnowledgeFile).where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.parent_id.is_not_distinct_from(parent_id),
                KnowledgeFile.deleted_at.is_(None),
                KnowledgeFile.file_id != exclude_id if exclude_id else True,
                func.normalize(func.lower(func.normalize(func.btrim(KnowledgeFile.filename), text("NFC"))), text("NFC"))
                == func.normalize(func.lower(func.normalize(func.btrim(name), text("NFC"))), text("NFC")),
            )
        )

    async def folder(self, session, kb_id, parent_id, name, operator_id, *, reuse):
        """创建旧版本目录行，避免当前 ORM 默认值向旧表写入新列。"""
        existing = await self.named(session, kb_id, parent_id, name)
        if existing is not None:
            if reuse and existing.is_folder:
                return existing
            raise StateConflictError("目录名称已被占用", code="name_conflict")
        node = KnowledgeFile(
            file_id=f"folder-{uuid4()}",
            kb_id=kb_id,
            parent_id=parent_id,
            filename=name,
            path=name,
            file_type="folder",
            is_folder=True,
            status="done",
            created_by=operator_id,
        )
        session.add(node)
        await self.flush(session)
        return node


async def audit(manager=pg_manager) -> dict:
    """只读检查不可猜测的目录歧义、无效父关系和未结束旧摄取任务。"""
    report = {"files": 0, "path_files": 0, "blocking_ids": [], "old_jobs": []}
    async with manager.get_async_session_context() as session:
        report["old_jobs"] = list(
            (
                await session.scalars(
                    select(BackgroundJobRecord.id).where(
                        BackgroundJobRecord.type == "knowledge_ingest",
                        BackgroundJobRecord.status.in_(["pending", "running"]),
                    )
                )
            ).all()
        )
        records = (await session.scalars(select(KnowledgeFile).where(KnowledgeFile.deleted_at.is_(None)))).all()
        by_id = {record.file_id: record for record in records}
        folders = {}
        path_files, required_paths = {}, []
        repo = DirectoryMigrationRepository()
        for node in sorted(records, key=lambda row: not row.is_folder):
            report["files"] += not node.is_folder
            report["path_files"] += bool(not node.is_folder and ("/" in node.filename or "\\" in node.filename))
            try:
                if node.is_folder:
                    name = normalize_name(node.filename)
                    key = await session.scalar(text("SELECT normalize(lower(normalize(:name, NFC)), NFC)"), {"name": name})
                    location = (node.kb_id, node.parent_id, key)
                    if location in folders:
                        report["blocking_ids"].extend([folders[location], node.file_id])
                    folders[location] = node.file_id
                else:
                    segments = _segments(node)
                    key = await session.scalar(text("SELECT normalize(lower(normalize(:name, NFC)), NFC)"), {"name": "/".join(segments)})
                    path_files.setdefault((node.kb_id, node.parent_id, key), []).append(node.file_id)
                    parts = key.split("/")
                    # 完整前缀与所有历史叶子比较，包含尚未实体化的目录。
                    required_paths.extend(
                        ((node.kb_id, node.parent_id, "/".join(parts[:depth])), node.file_id) for depth in range(1, len(parts))
                    )
            except DomainError:
                report["blocking_ids"].append(node.file_id)
            if not node.is_folder:
                try:
                    target = node.parent_id
                    for segment in _segments(node)[:-1]:
                        key = await session.scalar(
                            text("SELECT normalize(lower(normalize(:name, NFC)), NFC)"),
                            {"name": segment},
                        )
                        folder_id = folders.get((node.kb_id, target, key))
                        if folder_id is None:
                            occupied = await repo.named(session, node.kb_id, target, segment)
                            if occupied is not None and not occupied.is_folder:
                                report["blocking_ids"].extend([node.file_id, occupied.file_id])
                            break
                        folder = by_id[folder_id]
                        if not (folder.processing_params or {}).get("materialized_from_path"):
                            report["blocking_ids"].extend([node.file_id, folder_id])
                            break
                        target = folder_id
                except DomainError:
                    pass  # 上面的名称审计已经记录该节点。
            current, seen = node, {node.file_id}
            while current.parent_id:
                parent = by_id.get(current.parent_id)
                if parent is None or not parent.is_folder or parent.kb_id != node.kb_id or parent.file_id in seen:
                    report["blocking_ids"].append(node.file_id)
                    break
                seen.add(parent.file_id)
                current = parent
        for location, file_id in required_paths:
            if location in path_files:
                report["blocking_ids"].extend([file_id, *path_files[location]])
        report["blocking_ids"] = sorted(set(report["blocking_ids"]))
    return report


async def convert(manager=pg_manager, *, stop_after: int | None = None) -> dict:
    """逐条提交可重入转换；全部收敛后建立约束并发布新版本。"""
    async with manager.get_async_session_context() as session:
        version = await session.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain = 'knowledge'"))
        if version not in {1, 2}:
            raise StateConflictError("仅支持 knowledge v1 到 v2 的离线转换")
    report = await audit(manager)
    if report["blocking_ids"] or report["old_jobs"]:
        raise StateConflictError("目录歧义、无效父关系或活动摄取任务阻止离线转换")
    repo = DirectoryMigrationRepository()
    converted = 0
    async with manager.get_async_session_context() as session:
        ids = list(
            (
                await session.scalars(
                    select(KnowledgeFile.file_id)
                    .where(
                        KnowledgeFile.deleted_at.is_(None),
                    )
                    .order_by(KnowledgeFile.is_folder.desc(), KnowledgeFile.created_at, KnowledgeFile.file_id)
                )
            ).all()
        )
    for file_id in ids:
        async with manager.get_async_session_context() as session:
            node = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id).with_for_update())
            if node is None or node.deleted_at is not None:
                continue
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(node.kb_id))))
            original = node.filename
            if node.is_folder:
                node.filename = normalize_name(original)
                node.path = node.filename
            else:
                target = node.parent_id
                segments = _segments(node)
                for segment in segments[:-1]:
                    folder = await repo.folder(session, node.kb_id, target, segment, node.created_by, reuse=True)
                    folder.processing_params = {**(folder.processing_params or {}), "materialized_from_path": True}
                    target = folder.file_id
                node.parent_id = target
                node.filename = await repo.available_name(session, node.kb_id, target, segments[-1], exclude_id=node.file_id)
                if node.original_filename is None:
                    node.original_filename = original
                if len(segments) > 1 or original.startswith(("http://", "https://")):
                    node.processing_params = {**(node.processing_params or {}), "original_source_path": original}
            await session.flush()
        converted += 1
        if stop_after is not None and converted >= stop_after:
            return {**report, "converted": converted, "complete": False}

    async with manager.async_engine.begin() as connection:
        await connection.run_sync(lambda conn: KnowledgeImport.__table__.create(conn, checkfirst=True))
        index = next(index for index in CurrentKnowledgeFile.__table__.indexes if index.name == "uq_knowledge_files_active_name")
        await connection.run_sync(lambda conn: index.create(conn, checkfirst=True))
        exists = await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'knowledge_files'::regclass "
                "AND conname = 'ck_knowledge_files_leaf_name')"
            )
        )
        if not exists:
            await connection.execute(
                text(
                    "ALTER TABLE knowledge_files ADD CONSTRAINT ck_knowledge_files_leaf_name CHECK ("
                    "deleted_at IS NOT NULL OR (filename = btrim(filename) AND filename NOT IN ('', '.', '..') "
                    "AND strpos(filename, '/') = 0 AND strpos(filename, chr(92)) = 0 "
                    "AND filename !~ '[[:cntrl:]]' AND filename = normalize(filename, NFC)))"
                )
            )
        await connection.execute(text("UPDATE yuxi_schema_migrations SET version = 2 WHERE domain = 'knowledge'"))
    return {**report, "converted": converted, "complete": True}


def _segments(node: KnowledgeFile) -> list[str]:
    """URL 保留来源信息，历史路径显式展开为真实目录。"""
    if node.filename.startswith(("http://", "https://")):
        parsed = urlsplit(node.filename)
        title = unquote(parsed.path.rsplit("/", 1)[-1]) or parsed.hostname or "page"
        return [normalize_name(title[:480] + ".html")]
    return source_segments(node.filename)


async def main() -> None:
    """输出不含名称或对象内容的审计计数与阻断身份。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="API/worker 停写后执行转换")
    args = parser.parse_args()
    pg_manager.initialize()
    try:
        result = await convert() if args.apply else await audit()
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
