"""当前用户持久化 UserWorkspace 的 no-follow 文件访问边界。"""

from __future__ import annotations

import errno
import itertools
import os
import stat
import tempfile
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from yuxi.infrastructure.filesystem import (
    copy_directory_fd,
    copy_file_fd,
    open_directory_fd,
    open_regular_file_fd,
    publish_directory_fd,
    write_all_fd,
)
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.paths import normalize_workspace_scope, user_workspace_dir, validate_directory_name
from yuxi.modules.workspace.usage_changes import workspace_cleanup, workspace_mutation

# 文件读写统一按 1MB 分块，控制单次读取的内存占用。
_READ_CHUNK_SIZE = 1024 * 1024


class Workspace:
    """以 uid 为边界访问持久化 UserWorkspace。"""

    def __init__(self, uid: str):
        self.uid = str(uid)
        self._workspace_root = user_workspace_dir(self.uid)

    def list_authorized_directory(self, path: str, *, root: str) -> list[dict]:
        """列出 Workdir 内的普通文件与真实目录。"""
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        directory_fd = self._open_directory(base, parts)
        try:
            entries = []
            for name in sorted(os.listdir(directory_fd), key=str.lower):
                item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not (stat.S_ISDIR(item_stat.st_mode) or stat.S_ISREG(item_stat.st_mode)):
                    continue
                entries.append({"name": name, **self._metadata_from_stat(item_stat)})
            return entries
        finally:
            os.close(directory_fd)

    def require_authorized_directory(self, path: str, *, root: str) -> None:
        """验证真实目录在当前权限下可打开、可向下访问。"""
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        directory_fd = self._open_directory(base, parts)
        try:
            if not os.access(".", os.X_OK, dir_fd=directory_fd, effective_ids=True):
                raise PermissionError("directory is not searchable")
        finally:
            os.close(directory_fd)

    def search_authorized_tree(
        self,
        root: str,
        query: str,
        *,
        include_directories: bool = True,
        exclude_directories: frozenset[str] = frozenset(),
        exclude_hidden: bool = False,
        max_results: int = 500,
        max_directories: int = 600,
        max_depth: int = 15,
        max_entries_per_directory: int = 500,
        max_scanned_entries: int = 10_000,
    ) -> list[dict]:
        """在授权根内执行有界实时扫描并返回文件名或路径匹配项。"""
        normalized_query = str(query or "").strip().lower()
        pending: deque[tuple[str, int]] = deque([(root, 0)])
        results: list[dict] = []
        visited_directories = 0
        scanned_entries = 0

        while pending and visited_directories < max_directories and scanned_entries < max_scanned_entries:
            directory, depth = pending.popleft()
            visited_directories += 1
            remaining_entries = max_scanned_entries - scanned_entries
            entries, examined_entries = self._list_authorized_directory_limited(
                directory,
                root=root,
                max_entries=min(max_entries_per_directory, remaining_entries),
            )
            scanned_entries += examined_entries
            for item in entries:
                name = str(item["name"])
                path = f"{directory.rstrip('/')}/{name}"
                is_dir = bool(item["is_dir"])
                excluded = is_dir and (name in exclude_directories or (exclude_hidden and name.startswith(".")))
                if is_dir and not excluded and depth < max_depth:
                    pending.append((path, depth + 1))

                # path 以 name 结尾，匹配 path 即同时覆盖纯文件名匹配。
                matched = not normalized_query or normalized_query in path.lower()
                if matched and not excluded and (include_directories or not is_dir):
                    results.append({"path": path, **item})
                    if len(results) >= max_results:
                        return results
        return results

    def download_authorized_file_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        """把授权普通文件有界复制到服务临时文件。"""
        if max_bytes < 0:
            raise ValueError("file download limit must be non-negative")
        with self._open_regular_file(path, writable=False) as (source_fd, _source_stat):
            target_fd = os.open(target_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            try:
                return copy_file_fd(source_fd, target_fd, max_bytes=max_bytes)
            finally:
                os.close(target_fd)

    def stat_readable_file(self, path: str) -> dict:
        """按预览的读取权限打开普通文件，从同一描述符返回元数据。"""
        with self._open_regular_file(path, writable=False) as (_fd, file_stat):
            return self._metadata_from_stat(file_stat)

    def read_authorized_file(self, path: str, max_bytes: int) -> bytes:
        """在 no-follow 边界内有界读取普通文件。"""
        if max_bytes < 0:
            raise ValueError("file read limit must be non-negative")
        with self._open_regular_file(path, writable=False) as (source_fd, source_stat):
            if source_stat.st_size > max_bytes:
                raise FileTransferLimitError("file exceeds transfer limit")
            content, truncated = self._read_bounded(source_fd, max_bytes)
            if truncated:
                raise FileTransferLimitError("file exceeds transfer limit")
            return content

    def read_authorized_file_prefix(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        """有界读取普通文件前缀，并报告内容是否被截断。"""
        if max_bytes < 0:
            raise ValueError("file read limit must be non-negative")
        with self._open_regular_file(path, writable=False) as (source_fd, _source_stat):
            return self._read_bounded(source_fd, max_bytes)

    def write_authorized_file(self, path: str, content: bytes) -> dict:
        """通过已打开的普通文件描述符覆盖内容，拒绝 symlink 与目录。"""
        with self._open_regular_file(path, writable=True) as (target_fd, _target_stat):
            with workspace_mutation(self.uid):
                os.ftruncate(target_fd, 0)
                write_all_fd(target_fd, content)
            final_stat = os.fstat(target_fd)
            return self._metadata_from_stat(final_stat)

    def replace_authorized_file(self, path: str, content: bytes) -> dict:
        """在同一目录内完整写入并原子替换普通文件。"""
        base, parts = self._resolve_path(path)
        if not parts:
            raise IsADirectoryError(path)
        parent_fd = self._open_directory(base, parts[:-1])
        try:
            try:
                target_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                # 目标不存在时 rename 直接创建，无需校验既有类型。
                pass
            else:
                if stat.S_ISLNK(target_stat.st_mode):
                    raise PermissionError("symlink paths are not allowed")
                if not stat.S_ISREG(target_stat.st_mode):
                    raise PermissionError("only regular files can be replaced")

            with workspace_mutation(self.uid), self._staged_temp_file(parent_fd, ".yuxi-replace-") as (temp_fd, temp_name):
                write_all_fd(temp_fd, content)
                os.fsync(temp_fd)
                final_stat = os.fstat(temp_fd)
                os.rename(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            return self._metadata_from_stat(final_stat)
        finally:
            os.close(parent_fd)

    def stat_authorized_path(self, path: str, *, root: str) -> dict:
        """在 no-follow 边界内读取普通文件或真实目录元数据。"""
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        if not parts:
            item_stat = os.stat(base, follow_symlinks=False)
        else:
            parent_fd = self._open_directory(base, parts[:-1])
            try:
                item_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            finally:
                os.close(parent_fd)
            if stat.S_ISLNK(item_stat.st_mode):
                raise PermissionError("symlink paths are not allowed")
        if not (stat.S_ISDIR(item_stat.st_mode) or stat.S_ISREG(item_stat.st_mode)):
            raise PermissionError("only regular files and directories are allowed")
        return self._metadata_from_stat(item_stat)

    def upload_authorized_file_from_path(
        self,
        path: str,
        source_path: str,
        *,
        overwrite: bool = True,
        create_parents: bool = True,
        root: str = "/",
    ) -> dict:
        """从受信任服务临时文件原子写入 UserWorkspace；root 必须是已存在的授权根，父目录只在它内部补建。"""
        with os.fdopen(os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("upload source is not a regular file")
            return self.upload_authorized_file_from_stream(
                path, source, max_bytes=None, overwrite=overwrite, create_parents=create_parents, root=root
            )

    def upload_authorized_file_from_stream(
        self,
        path: str,
        source: BinaryIO,
        *,
        max_bytes: int | None,
        overwrite: bool = True,
        create_parents: bool = True,
        root: str = "/",
    ) -> dict:
        """分块消费借用的文件流，超限或读取失败时回收未发布文件；写入根同 root 约束。"""
        with workspace_mutation(self.uid):
            parent_fd, name = self._open_write_parent(path, root=root, create_parents=create_parents)
            try:
                with self._staged_temp_file(parent_fd, ".yuxi-write-") as (temp_fd, temp_name):
                    written = 0
                    while chunk := source.read(_READ_CHUNK_SIZE):
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise FileTransferLimitError("file exceeds limit")
                        write_all_fd(temp_fd, chunk)
                    final_stat = os.fstat(temp_fd)
                    if overwrite:
                        os.rename(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    else:
                        os.link(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                        os.unlink(temp_name, dir_fd=parent_fd)
                return self._metadata_from_stat(final_stat)
            finally:
                os.close(parent_fd)

    def copy_authorized_directory_from_path(self, path: str, source_path: str | Path, *, root: str = "/") -> tuple[int, int]:
        """复制服务临时目录到独占的新目录，失败时回收本次产物。"""
        # Workspace 的父目录由服务拥有，不挂载进沙盒；半成品只在这里存在。
        with (
            workspace_mutation(self.uid),
            tempfile.TemporaryDirectory(prefix=".yuxi-copy-", dir=self._workspace_root.parent) as staging,
            ExitStack() as stack,
        ):
            source_fd = open_directory_fd(Path(source_path), ())
            stack.callback(os.close, source_fd)
            parent_fd, name = self._open_write_parent(path, root=root, create_parents=True)
            stack.callback(os.close, parent_fd)
            staging_fd = open_directory_fd(Path(staging), ())
            stack.callback(os.close, staging_fd)
            # 先在暂存层组装完整目录，再整体原子发布到目标名，不暴露半成品。
            os.mkdir("directory", 0o700, dir_fd=staging_fd)
            target_fd = open_directory_fd(staging_fd, ("directory",))
            stack.callback(os.close, target_fd)
            target_stat = os.fstat(target_fd)
            identity = (target_stat.st_dev, target_stat.st_ino)
            copy_directory_fd(source_fd, target_fd)
            publish_directory_fd(staging_fd, "directory", parent_fd, name)
            return identity

    def create_authorized_directory(self, parent_path: str, name: str, *, root: str) -> dict:
        """在 Workdir 内创建一个单层目录。"""
        validate_directory_name(name)
        self._require_within(parent_path, root)
        base, parts = self._resolve_path(parent_path)
        parent_fd = self._open_directory(base, parts)
        try:
            with workspace_mutation(self.uid):
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.close(parent_fd)
        return self._metadata_from_stat(item_stat)

    def delete_authorized_path(self, path: str, *, root: str, expected_identity: tuple[int, int] | None = None) -> None:
        """递归删除授权目录项，符号链接只删除链接本身，不允许删除根。"""
        self._require_within(path, root, allow_root=False)
        base, parts = self._resolve_path(path)
        parent_fd = self._open_directory(base, parts[:-1])
        try:
            with workspace_cleanup(self.uid):
                self._remove_entry(parent_fd, parts[-1], expected_identity=expected_identity)
        finally:
            os.close(parent_fd)

    def _resolve_path(self, path: str) -> tuple[Path, tuple[str, ...]]:
        """把 UserWorkspace scope 路径解析为持久化根内组件。"""
        pure = PurePosixPath(normalize_workspace_scope(path))
        return self._workspace_root, tuple(pure.parts[1:])

    @staticmethod
    def _require_within(path: str, root: str, *, allow_root: bool = True) -> None:
        normalized_path = normalize_workspace_scope(path)
        normalized_root = normalize_workspace_scope(root)
        if normalized_path == normalized_root:
            if allow_root:
                return
            raise ValueError("operation cannot target the Workdir root")
        if normalized_root == "/":
            # normalize_workspace_scope 保证路径为绝对路径，根授权即放行。
            return
        normalized_root = normalized_root.rstrip("/")
        if not normalized_path.startswith(f"{normalized_root}/"):
            raise ValueError("path is outside the Workdir")

    @staticmethod
    def _open_directory(base: Path | int, parts: tuple[str, ...], *, create: bool = False) -> int:
        try:
            return open_directory_fd(base, parts, create=create)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("symlink paths are not allowed") from exc
            raise

    def _open_write_parent(self, path: str, *, root: str, create_parents: bool) -> tuple[int, str]:
        """先打开既有授权根，只在其内部补建目标的父目录。"""
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        _, root_parts = self._resolve_path(root)
        if len(parts) == len(root_parts):
            raise IsADirectoryError(path)
        root_fd = self._open_directory(base, root_parts)
        try:
            return self._open_directory(root_fd, parts[len(root_parts) : -1], create=create_parents), parts[-1]
        finally:
            os.close(root_fd)

    def _open_regular_file(self, path: str, *, writable: bool):
        """固定父目录与最终普通文件，统一拒绝 symlink、目录和特殊文件。"""
        base, parts = self._resolve_path(path)
        return open_regular_file_fd(base, parts, writable=writable)

    @staticmethod
    @contextmanager
    def _staged_temp_file(parent_fd: int, prefix: str) -> Iterator[tuple[int, str]]:
        """在已固定父目录内创建未发布临时文件，退出时关闭并回收半成品。"""
        temp_name = f"{prefix}{uuid.uuid4().hex}"
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            yield temp_fd, temp_name
        finally:
            os.close(temp_fd)
            # 已发布的条目不再使用临时名，统一回收并容忍缺失。
            with suppress(FileNotFoundError):
                os.unlink(temp_name, dir_fd=parent_fd)

    @staticmethod
    def _read_bounded(source_fd: int, max_bytes: int) -> tuple[bytes, bool]:
        """最多读取 max_bytes 字节，并报告源中是否仍有剩余内容。"""
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(source_fd, min(_READ_CHUNK_SIZE, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        return content[:max_bytes], len(content) > max_bytes

    def _list_authorized_directory_limited(
        self,
        path: str,
        *,
        root: str,
        max_entries: int,
    ) -> tuple[list[dict], int]:
        """最多检查指定数量的目录项，避免宽目录触发全量 I/O。"""
        if max_entries < 0:
            raise ValueError("directory entry limit must be non-negative")
        self._require_within(path, root)
        base, parts = self._resolve_path(path)
        directory_fd = self._open_directory(base, parts)
        entries: list[dict] = []
        examined_entries = 0
        try:
            with os.scandir(directory_fd) as iterator:
                for entry in itertools.islice(iterator, max_entries):
                    examined_entries += 1
                    item_stat = entry.stat(follow_symlinks=False)
                    if not (stat.S_ISDIR(item_stat.st_mode) or stat.S_ISREG(item_stat.st_mode)):
                        continue
                    entries.append({"name": entry.name, **self._metadata_from_stat(item_stat)})
        finally:
            os.close(directory_fd)
        entries.sort(key=lambda item: str(item["name"]).lower())
        return entries, examined_entries

    @staticmethod
    def _metadata_from_stat(item_stat: os.stat_result) -> dict:
        is_dir = stat.S_ISDIR(item_stat.st_mode)
        return {
            "is_dir": is_dir,
            "size": 0 if is_dir else item_stat.st_size,
            "modified_at": item_stat.st_mtime,
        }

    @staticmethod
    def _identity_matches(item_stat: os.stat_result, expected_identity: tuple[int, int] | None) -> bool:
        """无身份约束或 (st_dev, st_ino) 一致时才允许删除目标。"""
        return expected_identity is None or (item_stat.st_dev, item_stat.st_ino) == expected_identity

    @classmethod
    def _remove_entry(cls, parent_fd: int, name: str, *, expected_identity: tuple[int, int] | None = None) -> None:
        try:
            item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if expected_identity is not None:
                return
            raise
        if not cls._identity_matches(item_stat, expected_identity):
            # 目录项已被并发替换，放弃本次定向删除。
            return
        if not stat.S_ISDIR(item_stat.st_mode):
            if not (stat.S_ISREG(item_stat.st_mode) or stat.S_ISLNK(item_stat.st_mode)):
                raise PermissionError("only regular files, directories and symlinks can be deleted")
            # unlink 只移除已固定父目录中的目录项，不访问链接目标。
            os.unlink(name, dir_fd=parent_fd)
            return
        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            opened_stat = os.fstat(child_fd)
            if not cls._identity_matches(opened_stat, expected_identity):
                return
            for child_name in os.listdir(child_fd):
                cls._remove_entry(child_fd, child_name)
        finally:
            os.close(child_fd)
        if expected_identity is not None:
            try:
                remaining_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not cls._identity_matches(remaining_stat, expected_identity):
                # 递归期间目录被并发替换，不能删除冒名的新目录。
                return
        os.rmdir(name, dir_fd=parent_fd)
