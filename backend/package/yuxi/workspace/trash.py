"""沙盒挂载之外的no-follow文件隔离，journal由PostgreSQL持有。"""

import ctypes
import errno
import os
import stat
from pathlib import PurePosixPath

from yuxi.config import get_user_data_dir
from yuxi.utils.paths import open_directory_fd
from yuxi.workspace.filesystem import Workspace
from yuxi.workspace.paths import user_workspace_dir, workspace_uid_dirname


def describe_paths(uid: str, paths: list[str], *, optional_paths: set[str] | None = None) -> list[dict]:
    """固定待删除对象身份，拒绝根、重复嵌套、符号链接及特殊文件。"""
    result = []
    for raw in sorted(paths, key=lambda value: len(PurePosixPath(value).parts)):
        path = PurePosixPath(raw)
        if not path.is_absolute() or raw.startswith("//") or not path.parts[1:] or ".." in path.parts or "\\" in raw:
            raise ValueError("invalid trash path")
        if any(
            path == PurePosixPath(item["path"])
            or path.is_relative_to(item["path"])
            or PurePosixPath(item["path"]).is_relative_to(path)
            for item in result
        ):
            continue
        try:
            parent_fd = open_directory_fd(user_workspace_dir(uid), path.parts[1:-1])
        except FileNotFoundError:
            if raw in (optional_paths or set()):
                continue
            raise
        try:
            try:
                item = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if raw in (optional_paths or set()):
                    continue
                raise
            if not (stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode)):
                raise PermissionError("only regular files and directories can enter trash")
            result.append(
                {"path": path.as_posix(), "dev": item.st_dev, "ino": item.st_ino, "is_dir": stat.S_ISDIR(item.st_mode)}
            )
        finally:
            os.close(parent_fd)
    return result


def transition_files(uid: str, entry_id: str, paths: list[dict], operation: str) -> None:
    """幂等移动、恢复或清理；每次重试按inode核对真实对象。"""
    if len(entry_id) != 32 or any(c not in "0123456789abcdef" for c in entry_id):
        raise ValueError("invalid trash identity")
    root = get_user_data_dir()
    quarantine_fd = open_directory_fd(root, ("trash", workspace_uid_dirname(uid), entry_id), create=True)
    try:
        # 全量预检恢复目标，常见重名冲突不会造成半批恢复。
        if operation in ("restore", "check_restore"):
            for index, item in enumerate(paths):
                source = PurePosixPath(item["path"])
                parent_fd = open_directory_fd(user_workspace_dir(uid), source.parts[1:-1])
                try:
                    existing = _stat(parent_fd, source.name)
                    quarantined = _stat(quarantine_fd, str(index))
                    if existing is not None and (quarantined is not None or not _same(existing, item)):
                        raise FileExistsError("原位置已存在同名文件或文件夹，请先移走后重试")
                finally:
                    os.close(parent_fd)
        if operation == "check_restore":
            return
        for index, item in enumerate(paths):
            key = str(index)
            quarantined = _stat(quarantine_fd, key)
            if operation == "purge":
                if quarantined is not None:
                    if not _same(quarantined, item):
                        raise PermissionError("trash identity changed")
                    Workspace.purge_detached_entry(quarantine_fd, key)
                    os.fsync(quarantine_fd)
                continue
            source = PurePosixPath(item["path"])
            parent_fd = open_directory_fd(user_workspace_dir(uid), source.parts[1:-1])
            try:
                existing = _stat(parent_fd, source.name)
                if operation == "delete":
                    if quarantined is not None:
                        if not _same(quarantined, item):
                            raise PermissionError("trash identity changed")
                        continue
                    if existing is None or not _same(existing, item):
                        raise FileNotFoundError("待删除对象已改变；保留journal等待检查")
                    _rename_noreplace(parent_fd, source.name, quarantine_fd, key)
                elif operation == "restore":
                    if quarantined is None:
                        if existing is not None and _same(existing, item):
                            continue
                        raise FileNotFoundError("回收字节缺失，保留记录等待检查")
                    if not _same(quarantined, item):
                        raise PermissionError("trash identity changed")
                    _rename_noreplace(quarantine_fd, key, parent_fd, source.name)
                else:
                    raise ValueError("invalid trash operation")
                os.fsync(parent_fd)
                os.fsync(quarantine_fd)
            finally:
                os.close(parent_fd)
    finally:
        os.close(quarantine_fd)


def _stat(parent_fd: int, name: str):
    """读取目录内不跟随链接的对象身份。"""
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _same(item_stat, item: dict) -> bool:
    """确认重试面对的是最初文件或目录而非同名替换品。"""
    return item_stat.st_dev == item["dev"] and item_stat.st_ino == item["ino"]


def _rename_noreplace(source_fd: int, source: str, target_fd: int, target: str) -> None:
    """使用Linux原子非覆盖rename，拒绝竞争写入而不覆盖目标。"""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError("原位置已存在同名文件或文件夹，请先移走后重试")
        raise OSError(error, os.strerror(error))
