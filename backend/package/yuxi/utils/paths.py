import errno
import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_directory_fd(root: Path | int, parts: tuple[str, ...], *, create: bool = False) -> int:
    """从可信目录逐层 no-follow 打开路径，返回调用方负责关闭的 fd。

    ``parts`` 必须是已校验的单路径组件；传入 fd 时函数复制而不接管原 fd。
    """
    directory_fd = os.dup(root) if isinstance(root, int) else os.open(root, _DIRECTORY_OPEN_FLAGS)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
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


def replace_regular_file(
    root: Path | int, parts: tuple[str, ...], content: bytes, *, preserve_mode: bool = False
) -> os.stat_result:
    """在 no-follow 父目录内完整写入并原子替换普通文件。"""
    if not parts:
        raise IsADirectoryError(str(root))
    parent_fd = open_directory_fd(root, parts[:-1])
    target_fd = None
    temp_name = f".yuxi-replace-{uuid.uuid4().hex}"
    try:
        try:
            target_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None:
            if stat.S_ISLNK(target_stat.st_mode):
                raise PermissionError("symlink paths are not allowed")
            if not stat.S_ISREG(target_stat.st_mode):
                raise PermissionError("only regular files can be replaced")
        target_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        if preserve_mode and target_stat is not None:
            os.fchmod(target_fd, stat.S_IMODE(target_stat.st_mode))
        offset = 0
        while offset < len(content):
            offset += os.write(target_fd, content[offset:])
        os.fsync(target_fd)
        final_stat = os.fstat(target_fd)
        os.close(target_fd)
        target_fd = None
        os.rename(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        return final_stat
    finally:
        if target_fd is not None:
            os.close(target_fd)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def ensure_within_root(path: Path, root: Path, *, error_message: str) -> Path:
    """确认真实路径位于指定根目录内，否则拒绝越界访问。"""
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError(error_message) from None
    return path


__all__ = [
    "open_directory_fd",
    "open_regular_file_fd",
    "replace_regular_file",
    "ensure_within_root",
]
