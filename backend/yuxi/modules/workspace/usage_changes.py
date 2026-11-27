"""在共享宿主卷上记录待统计的 Workspace。"""

import hashlib
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, flock
from pathlib import Path
from typing import NamedTuple

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_user_data_dir


class WorkspaceChange(NamedTuple):
    """UID 与本次可确认的标记代次；进行中的宿主写入不提供确认令牌。"""

    uid: str
    observed_path: Path
    generation: bytes | None


@contextmanager
def workspace_mutation(uid: str, *, data_root: Path | None = None) -> Iterator[None]:
    """在宿主写入或沙盒启动前登记 UID，合并同一 UID 尚未观察的变更。"""
    key = hashlib.sha256(uid.encode()).hexdigest()
    marker = _marker_root(data_root) / f"{key}.pending"
    with _marker_lock(marker, LOCK_SH):
        if not marker.exists():
            _publish_marker(marker, uid)
        yield


@contextmanager
def workspace_cleanup(uid: str, *, data_root: Path | None = None) -> Iterator[None]:
    """先尝试登记清理，标记失败时仍释放空间并重新登记。"""
    with ExitStack() as stack:
        try:
            stack.enter_context(workspace_mutation(uid, data_root=data_root))
        except OSError:
            try:
                yield
            finally:
                try:
                    with workspace_mutation(uid, data_root=data_root):
                        pass
                except OSError:
                    logger.exception("个人空间清理后无法重新登记用量 uid={}", uid)
        else:
            yield


@contextmanager
def workspace_usage_scan_lock() -> Iterator[bool]:
    """锁定共享标记目录，巡检进程退出时由内核释放。"""
    try:
        fd = os.open(_marker_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        yield False
        return
    with _file_lock(fd, LOCK_EX | LOCK_NB) as acquired:
        yield acquired


def pending_workspace_changes() -> list[WorkspaceChange]:
    """领取待测代次；正在写入的 UID 也可测量，但此轮不能确认。"""
    root = _marker_root()
    pending = []
    keys = {path.stem for path in root.glob("*") if path.suffix in {".pending", ".observed"}}
    for key in keys:
        marker = root / f"{key}.pending"
        observed = marker.with_suffix(".observed")
        try:
            with _marker_lock(marker, LOCK_EX | LOCK_NB) as acquired:
                if acquired:
                    try:
                        marker.replace(observed)
                    except FileNotFoundError:
                        pass
                content = None
                for path in (observed, marker):
                    try:
                        content = path.read_bytes()
                        break
                    except FileNotFoundError:
                        continue
                if content is not None:
                    _, uid = content.split(b"\n", 1)
                    if hashlib.sha256(uid).hexdigest() != key:
                        raise ValueError("用量标记身份与文件名不一致")
                    pending.append(WorkspaceChange(uid.decode(), observed, content if acquired else None))
        except (OSError, ValueError) as exc:
            logger.warning("跳过暂时无法读取的个人空间用量标记 {}: {}", key, type(exc).__name__)
    return pending


def acknowledge_workspace_change(change: WorkspaceChange) -> None:
    """仅确认已观测的代次，新变更会保留在待测标记中。"""
    marker = change.observed_path
    if change.generation is None:
        return

    try:
        with _marker_lock(marker, LOCK_EX | LOCK_NB) as acquired:
            if not acquired:
                return
            if marker.read_bytes() == change.generation:
                marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("暂时无法确认个人空间用量标记 {}: {}", marker.stem, type(exc).__name__)


def _marker_root(data_root: Path | None = None) -> Path:
    """使用沙盒不可见的宿主根目录。"""
    return (data_root if data_root is not None else get_user_data_dir()) / ".workspace-usage"


def _publish_marker(marker: Path, uid: str) -> None:
    """发布新代次；读者只读取完整标记，同一代次不随文件数增长。"""
    with tempfile.NamedTemporaryFile(dir=marker.parent, delete=False) as temporary:
        staging = Path(temporary.name)
        try:
            temporary.write(f"{uuid.uuid4().hex}\n{uid}".encode())
            temporary.flush()
            # runner 以 root 启动，API 与 worker 依赖目录权限访问标记。
            os.fchmod(temporary.fileno(), 0o644)
            staging.replace(marker)
        finally:
            staging.unlink(missing_ok=True)


@contextmanager
def _marker_lock(marker: Path, operation: int) -> Iterator[bool]:
    """锁 inode 保留在索引外，避免删除标记后并发进程锁到不同 inode。"""
    locks = marker.parent / "locks"
    lock_path = locks / marker.stem
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags)
    except FileNotFoundError:
        locks.mkdir(mode=0o700, parents=True, exist_ok=True)
        # runner 以 root 和 umask 077 运行，锁须在发布前对 API/worker 可读。
        with tempfile.NamedTemporaryFile(dir=locks, delete=False) as candidate:
            temporary = Path(candidate.name)
            try:
                os.fchmod(candidate.fileno(), 0o644)
                try:
                    os.link(temporary, lock_path, follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink()
        fd = os.open(lock_path, flags)
    with _file_lock(fd, operation) as acquired:
        yield acquired


@contextmanager
def _file_lock(fd: int, operation: int) -> Iterator[bool]:
    """持有唯一的打开文件描述符，结束后关闭并释放锁。"""
    try:
        try:
            flock(fd, operation)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)
