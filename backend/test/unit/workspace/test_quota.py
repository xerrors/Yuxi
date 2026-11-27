"""验证目录计量的文件系统边界。"""

import asyncio
import errno
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.workspace.paths import ensure_user_workspace, measure_allocated_bytes, user_workspace_dir
from yuxi.modules.workspace.services import quota
from yuxi.shared.errors import StateConflictError


async def test_allocated_bytes_ignores_external_target_and_counts_linked_inode_once(tmp_path, monkeypatch):
    """稀疏文件按分配块计，两个硬链接共享一个 inode，软链接保持在树内。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    uid = "quota:fixture"
    assert await measure_allocated_bytes(uid) == 0
    ensure_user_workspace(uid)
    root = user_workspace_dir(uid)
    sparse = root / "sparse"
    with sparse.open("wb") as writer:
        writer.truncate(200 * 1024**2)
    os.link(sparse, root / "alias")
    outside = tmp_path / "outside"
    outside.write_bytes(b"x" * 65536)
    (root / "external").symlink_to(outside)
    entries = [root, *(p for p in root.rglob("*") if p.name != "alias")]
    assert await measure_allocated_bytes(uid) == sum(p.lstat().st_blocks * 512 for p in entries)
    root.rename(root.with_name("previous"))
    root.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="符号链接"):
        await measure_allocated_bytes(uid)


async def test_cancelled_workspace_measurement_reaps_its_process(tmp_path, monkeypatch):
    """停机取消时终止并回收正运行的计量进程。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    ensure_user_workspace("cancellable")
    spawn = asyncio.create_subprocess_exec
    started = asyncio.Future()

    async def spawn_slow_measurement(*_args, **kwargs):
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        started.set_result(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_slow_measurement)
    task = asyncio.create_task(measure_allocated_bytes("cancellable"))
    try:
        process = await asyncio.wait_for(started, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert process.returncode is not None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_last_success_controls_quota_even_if_next_scan_failed(monkeypatch):
    """上传依照默认额度和最后一次成功扫描准入。"""
    monkeypatch.delenv("WORKSPACE_QUOTA_BYTES", raising=False)
    limit = 10 * 1024**3
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    assert await quota.workspace_usage(db, "user") == {
        "used_bytes": None,
        "measurement_failed": False,
        "measured_at": None,
        "limit_bytes": limit,
        "over_quota": False,
    }
    await quota.require_upload_capacity(db, "user")

    db.get.return_value = SimpleNamespace(used_bytes=limit - 1, measured_at=1, last_attempt_at=2)
    await quota.require_upload_capacity(db, "user")

    db.get.return_value.used_bytes = limit
    assert await quota.workspace_usage(db, "user") == {
        "used_bytes": limit,
        "measurement_failed": True,
        "measured_at": 1,
        "limit_bytes": limit,
        "over_quota": True,
    }
    with pytest.raises(StateConflictError, match="个人空间已满") as error:
        await quota.require_upload_capacity(db, "user")
    assert error.value.code == "workspace_quota_exceeded"


def test_workspace_marker_coalesces_writes_and_preserves_changes_during_scan(tmp_path, monkeypatch):
    """扫描领取后发生的编辑独立排队，确认旧观测不能清除新变更。"""
    from yuxi.modules.workspace.filesystem import Workspace
    from yuxi.modules.workspace.usage_changes import acknowledge_workspace_change, pending_workspace_changes

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    uid = "quota:fixture"
    ensure_user_workspace(uid)
    workspace = Workspace(uid)
    marker = next((tmp_path / ".workspace-usage").glob("*.pending"))
    original = marker.read_bytes()
    workspace.replace_authorized_file("/notes.txt", b"old")
    assert marker.read_bytes() == original

    (first,) = pending_workspace_changes()
    assert first.uid == uid
    workspace.write_authorized_file("/notes.txt", b"new")
    acknowledge_workspace_change(first)

    (second,) = pending_workspace_changes()
    assert second.uid == uid and second.generation != first.generation
    acknowledge_workspace_change(first)
    assert pending_workspace_changes() == [second]
    acknowledge_workspace_change(second)
    ensure_user_workspace(uid)
    assert workspace.read_authorized_file("/notes.txt", 32) == b"new"
    assert pending_workspace_changes() == []


def test_workspace_marker_during_mutation_remains_after_scan(tmp_path, monkeypatch):
    """写入跨越扫描时点时，扫描可以刷新快照，但该次扫描无法确认。"""
    from yuxi.modules.workspace.usage_changes import acknowledge_workspace_change, pending_workspace_changes, workspace_mutation

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    with workspace_mutation("uid"):
        (during,) = pending_workspace_changes()
        assert during.uid == "uid" and during.generation is None
    acknowledge_workspace_change(during)
    (after,) = pending_workspace_changes()
    assert after.uid == "uid" and after.generation is not None
    acknowledge_workspace_change(after)
    assert pending_workspace_changes() == []


async def test_full_volume_still_allows_delete_and_marks_after_reclaim(tmp_path, monkeypatch):
    """即使默认上下文缺失，首次登记写满时也能删除并重新排队。"""
    from yuxi.modules.workspace import usage_changes
    from yuxi.modules.workspace.services.files import delete_workspace_path

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    uid = "full"
    ensure_user_workspace(uid)
    root = user_workspace_dir(uid)
    target = root / "reclaim.bin"
    target.write_bytes(b"reclaim")
    (root / "agents" / "AGENTS.md").unlink()
    for change in usage_changes.pending_workspace_changes():
        usage_changes.acknowledge_workspace_change(change)
    publish = usage_changes._publish_marker

    def publish_after_delete(marker, owner):
        """模拟删除前卷已满，释放块后可重新登记。"""
        if target.exists():
            raise OSError(errno.ENOSPC, "fixture volume is full")
        publish(marker, owner)

    monkeypatch.setattr(usage_changes, "_publish_marker", publish_after_delete)
    assert await delete_workspace_path(path="/reclaim.bin", current_user=SimpleNamespace(uid=uid)) == {
        "success": True,
        "path": "/reclaim.bin",
    }
    assert not target.exists()
    [change] = usage_changes.pending_workspace_changes()
    assert change.uid == uid


async def test_scan_lock_is_released_when_worker_process_is_killed(tmp_path, monkeypatch):
    """共享标记目录禁止并行巡检，持锁进程退出后下一轮可接管。"""
    from yuxi.modules.workspace.usage_changes import workspace_usage_scan_lock

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    ensure_user_workspace("scan-owner")
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from yuxi.modules.workspace.usage_changes import workspace_usage_scan_lock\n"
        "import sys\n"
        "with workspace_usage_scan_lock() as acquired:\n"
        "    print(acquired, flush=True)\n"
        "    sys.stdin.read()\n",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(child.stdout.readline(), 5) == b"True\n"
        with workspace_usage_scan_lock() as acquired:
            assert not acquired
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    with workspace_usage_scan_lock() as acquired:
        assert acquired


def test_bad_marker_does_not_delay_other_workspaces(tmp_path, monkeypatch):
    """坏标记继续待测，正常 UID 仍可计量和确认。"""
    from yuxi.modules.workspace.usage_changes import acknowledge_workspace_change, pending_workspace_changes

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    ensure_user_workspace("broken")
    markers = tmp_path / ".workspace-usage"
    broken = next(markers.glob("*.pending"))
    broken.write_bytes(b"no separator")
    ensure_user_workspace("valid")
    [change] = pending_workspace_changes()
    assert change.uid == "valid"
    acknowledge_workspace_change(change)
    assert broken.with_suffix(".observed").read_bytes() == b"no separator"
    assert pending_workspace_changes() == []


def test_runner_umask_still_publishes_a_shared_readable_lock(tmp_path, monkeypatch):
    """runner 先建的锁在生产 umask 下也能被 worker 读取，后续沿用同一 inode。"""
    from yuxi.modules.workspace.usage_changes import workspace_mutation

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    previous_umask = os.umask(0o077)
    try:
        with workspace_mutation("runner-first"):
            pass
    finally:
        os.umask(previous_umask)
    [lock] = (tmp_path / ".workspace-usage" / "locks").iterdir()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644
    inode = lock.stat().st_ino
    with workspace_mutation("runner-first"):
        assert lock.stat().st_ino == inode


@pytest.mark.parametrize("confirmation_fails", [False, True])
async def test_usage_scan_handles_commit_and_confirmation_failures(tmp_path, monkeypatch, confirmation_fails):
    """逐项提交或确认失败保留对应代次，排在后面的 UID 仍会更新。"""
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import SQLAlchemyError

    from yuxi.modules.workspace.usage_changes import pending_workspace_changes

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    for uid in ("absent", "failed", "successful"):
        ensure_user_workspace(uid)
    if confirmation_fails:
        absent = next(change for change in pending_workspace_changes() if change.uid == "absent")
        unlink = Path.unlink

        def unlink_except_absent(path, *args, **kwargs):
            if path == absent.observed_path:
                raise PermissionError(errno.EACCES, "fixture cannot confirm marker")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink_except_absent)
    used = {}

    @asynccontextmanager
    async def session():
        """将一次真实计量记录到可注入提交故障的事务替身。"""
        writes = {}
        yield writes
        if "failed" in writes:
            raise SQLAlchemyError("提交失败")
        used.update(writes)

    async def attempts(_db, uids):
        """模拟 PostgreSQL 过滤已删除和未登记身份。"""
        assert set(uids) == {"absent", "failed", "successful"}
        return {"failed": None, "successful": None}

    async def save(db, uid, measured, _now):
        """保留实际目录计量结果，直到事务被提交。"""
        db[uid] = measured

    monkeypatch.setattr(quota.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(quota, "get_sandbox_provider", lambda: SimpleNamespace(active_workspace_uids=lambda _: set()))
    monkeypatch.setattr(quota, "usage_attempts", attempts)
    monkeypatch.setattr(quota, "save_usage", save)
    await quota.scan_workspace_usage()
    assert used == {"successful": await measure_allocated_bytes("successful")}
    assert {change.uid for change in pending_workspace_changes()} == ({"absent", "failed"} if confirmation_fails else {"failed"})
