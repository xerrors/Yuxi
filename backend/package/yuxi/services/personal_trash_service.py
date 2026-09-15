"""30天个人文件与附件回收，先持久意图，再执行可重试文件操作。"""

import asyncio
import errno
import uuid
from datetime import timedelta

from fastapi import HTTPException
from yuxi.repositories.personal_trash_repository import PersonalTrashRepository
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.workspace.trash import describe_paths, transition_files


async def lock_user_files(db, uid: str) -> None:
    """请求接入与文件生命周期使用同一个用户事务锁。"""
    await PersonalTrashRepository(db).lock_user(str(uid))


async def require_no_pending_file_operations(db, uid: str) -> None:
    """中断journal恢复前拒绝运行或确认附件。"""
    await PersonalTrashRepository(db).require_settled(str(uid))


async def list_deleted_user_paths(db, uid: str) -> list[str]:
    """返回旧文件引用需要失效的Workspace路径前缀。"""
    return await PersonalTrashRepository(db).deleted_paths(str(uid))


async def trash_personal_paths(*, db, uid: str, paths: list[str], name: str, kind: str) -> dict:
    """将用户文件移动到30天回收站，pending状态中断后仍可恢复处理。"""
    uid = str(uid)
    repository = PersonalTrashRepository(db)
    await repository.lock_user(uid)
    await repository.require_idle(uid)
    await repository.require_settled(uid)
    expanded = await repository.expand_attachment_paths(uid, paths)
    try:
        identities = await asyncio.to_thread(describe_paths, uid, expanded, optional_paths=set(expanded) - set(paths))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except (ValueError, PermissionError, NotADirectoryError) as exc:
        raise HTTPException(status_code=403, detail="路径不允许回收") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise HTTPException(status_code=403, detail="符号链接路径不允许回收") from exc
        raise
    if not identities:
        raise HTTPException(status_code=400, detail="没有可回收文件")
    now = utc_now_naive()
    entry = PersonalTrashEntry(
        id=uuid.uuid4().hex,
        uid=uid,
        name=name,
        kind=kind,
        paths=identities,
        state="pending_delete",
        deleted_at=now,
        purge_after=now + timedelta(days=30),
    )
    db.add(entry)
    await repository.mark_attachments(uid, entry)
    entry_id = entry.id
    await db.commit()
    return await process_personal_entry(db=db, uid=uid, entry_id=entry_id)


async def restore_personal_entry(*, db, uid: str, entry_id: str) -> dict:
    """恢复原位置；到期项或同名冲突始终保留无覆盖语义。"""
    repository = PersonalTrashRepository(db)
    await repository.lock_user(str(uid))
    await repository.require_idle(str(uid))
    entry = await repository.get(str(uid), entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="回收项目不存在")
    if entry.state == "restored":
        return repository.serialize(entry)
    if entry.state not in ("trashed", "restoring") or entry.purge_after <= utc_now_naive():
        raise HTTPException(status_code=409, detail="该项目正在处理或已到期，暂不支持恢复")
    try:
        await asyncio.to_thread(transition_files, str(uid), entry.id, entry.paths, "check_restore")
    except (FileExistsError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail="原位置冲突或父目录缺失，请先调整原位置后重试") from exc
    entry.state = "restoring"
    entry.error = None
    await db.commit()
    return await process_personal_entry(db=db, uid=str(uid), entry_id=entry_id)


async def process_personal_entry(*, db, uid: str, entry_id: str) -> dict:
    """API重试与worker共享恢复器；行锁和用户锁保护最终状态。"""
    repository = PersonalTrashRepository(db)
    await repository.lock_user(str(uid))
    entry = await repository.get(str(uid), entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="回收项目不存在")
    if entry.state == "trashed" and entry.purge_after <= utc_now_naive():
        entry.state = "purging"
        await db.commit()
        return await process_personal_entry(db=db, uid=uid, entry_id=entry_id)
    transitions = {
        "pending_delete": ("delete", "trashed"),
        "restoring": ("restore", "restored"),
        "purging": ("purge", "purged"),
    }
    if entry.state not in transitions:
        return repository.serialize(entry)
    operation, final_state = transitions[entry.state]
    try:
        await _transition_without_detaching(uid, entry.id, entry.paths, operation)
        if operation == "restore":
            await repository.mark_attachments(uid, entry, restore=True)
        entry.state = final_state
        entry.error = None
        entry.retry_after = None
        result = repository.serialize(entry)
        await db.commit()
        return result
    except Exception as exc:
        # 状态意图已在之前提交；保留journal供下一次同入口重试。
        await db.rollback()
        await repository.lock_user(str(uid))
        entry = await repository.get(str(uid), entry_id)
        entry.error = (
            "恢复原位置冲突，请移走同名项目后重试"
            if isinstance(exc, FileExistsError)
            else f"文件操作未完成（{type(exc).__name__}），请重试或联系管理员"
        )
        entry.retry_after = utc_now_naive() + timedelta(hours=1)
        await db.commit()
        raise HTTPException(status_code=409 if isinstance(exc, FileExistsError) else 503, detail=entry.error) from exc


async def process_due_personal_trash(*, limit: int = 50) -> dict:
    """既有worker定时执行恢复与到期清理，失败不丢弃状态。"""
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        rows = await PersonalTrashRepository(db).due(limit)
    completed = failed = 0
    for uid, entry_id in rows:
        async with pg_manager.get_async_session_context() as db:
            try:
                await process_personal_entry(db=db, uid=uid, entry_id=entry_id)
                completed += 1
            except HTTPException:
                failed += 1
    return {"completed": completed, "failed": failed}


async def _transition_without_detaching(uid: str, entry_id: str, paths: list[dict], operation: str) -> None:
    """取消HTTP任务时先等待文件线程结束，避免提前释放PG锁。"""
    task = asyncio.create_task(asyncio.to_thread(transition_files, uid, entry_id, paths, operation))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
