import asyncio
import ctypes
import errno
import os
import shutil
import stat
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_RENAME_NOREPLACE = 1


class FileTransferLimitError(ValueError):
    """文件传输超过调用方声明的字节上限。"""


async def await_io[T](operation: Awaitable[T]) -> T:
    """取消时等待当前 I/O 结束，让调用方随后可靠回收副作用。"""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        # 取走线程异常，避免取消后留下未消费的任务异常。
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError()
    return result


def open_directory_fd(root: Path | int, parts: tuple[str, ...], *, create: bool = False) -> int:
    """从可信目录逐层 no-follow 打开路径，返回调用方负责关闭的 fd。

    ``parts`` 必须是已校验的单路径组件；传入 fd 时函数复制而不接管原 fd。
    """
    directory_fd = os.dup(root) if isinstance(root, int) else os.open(root, _DIRECTORY_OPEN_FLAGS)
    try:
        for part in parts:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, _PRIVATE_DIRECTORY_MODE, dir_fd=directory_fd)
            try:
                child_fd = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    try:
                        item_stat = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError:
                        raise exc
                    if stat.S_ISLNK(item_stat.st_mode):
                        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), part) from exc
                raise
            previous_fd = directory_fd
            directory_fd = child_fd
            os.close(previous_fd)
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


@contextmanager
def open_regular_file_fd(
    root: Path | int,
    parts: tuple[str, ...],
    *,
    writable: bool = False,
) -> Iterator[tuple[int, os.stat_result]]:
    """从可信根 no-follow 打开普通文件，并在同一 fd 上校验类型。"""
    if not parts:
        raise IsADirectoryError(str(root))
    try:
        parent_fd = open_directory_fd(root, parts[:-1])
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PermissionError("symlink paths are not allowed") from exc
        raise
    file_fd = None
    try:
        flags = (os.O_WRONLY if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            file_fd = os.open(parts[-1], flags, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("symlink paths are not allowed") from exc
            if exc.errno == errno.ENXIO:
                # Unix socket 等特殊文件无法先打开再 fstat。
                raise PermissionError("only regular files are allowed") from exc
            raise
        file_stat = os.fstat(file_fd)
        if stat.S_ISDIR(file_stat.st_mode):
            raise IsADirectoryError(parts[-1])
        if not stat.S_ISREG(file_stat.st_mode):
            raise PermissionError("only regular files are allowed")
        yield file_fd, file_stat
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def copy_file_fd(source_fd: int, target_fd: int, *, max_bytes: int) -> int:
    """从当前偏移有界复制已授权 fd；调用方负责打开、截断、关闭与失败清理。"""
    if max_bytes < 0:
        raise ValueError("file copy limit must be non-negative")
    total = 0
    chunk_size = 1024 * 1024
    while chunk := os.read(source_fd, min(chunk_size, max_bytes - total + 1)):
        total += len(chunk)
        if total > max_bytes:
            raise FileTransferLimitError("file exceeds transfer limit")
        write_all_fd(target_fd, chunk)
    return total


def write_all_fd(file_fd: int, content: bytes) -> None:
    """完整写入借用 fd，处理部分写入并在无进展时失败。"""
    remaining = memoryview(content)
    while remaining:
        written = os.write(file_fd, remaining)
        if written == 0:
            raise OSError(errno.EIO, "file write made no progress")
        remaining = remaining[written:]


def copy_directory_fd(source_fd: int, target_fd: int) -> None:
    """在已打开的目录间递归复制，不跟随链接且不覆盖目标。"""
    for name in sorted(os.listdir(source_fd)):
        item_stat = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(item_stat.st_mode):
            os.mkdir(name, _PRIVATE_DIRECTORY_MODE, dir_fd=target_fd)
            child_source = open_directory_fd(source_fd, (name,))
            try:
                child_target = open_directory_fd(target_fd, (name,))
                try:
                    copy_directory_fd(child_source, child_target)
                finally:
                    os.close(child_target)
            finally:
                os.close(child_source)
        else:
            with open_regular_file_fd(source_fd, (name,)) as (file_fd, _):
                copied_fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    _PRIVATE_FILE_MODE,
                    dir_fd=target_fd,
                )
                with os.fdopen(copied_fd, "wb") as target, os.fdopen(os.dup(file_fd), "rb") as source:
                    shutil.copyfileobj(source, target)


def publish_directory_fd(source_fd: int, name: str, target_fd: int, target_name: str) -> None:
    """在 Linux 上原子发布目录，已有目标即使为空也不能覆盖。"""
    renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(source_fd, os.fsencode(name), target_fd, os.fsencode(target_name), _RENAME_NOREPLACE) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), target_name)


def ensure_within_root(path: Path, root: Path, *, error_message: str) -> Path:
    """确认真实路径位于指定根目录内，否则拒绝越界访问。"""
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError(error_message) from None
    return path


__all__ = [
    "FileTransferLimitError",
    "copy_file_fd",
    "write_all_fd",
    "copy_directory_fd",
    "publish_directory_fd",
    "open_directory_fd",
    "open_regular_file_fd",
    "ensure_within_root",
]
