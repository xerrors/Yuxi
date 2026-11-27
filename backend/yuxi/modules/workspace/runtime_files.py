"""Agent 虚拟路径与宿主共享文件的 no-follow 边界。"""

from __future__ import annotations

import fnmatch
import io
import os
import stat
from pathlib import PurePosixPath

from yuxi.infrastructure.filesystem import copy_file_fd, open_directory_fd, open_regular_file_fd
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.paths import normalize_workdir_path, user_workspace_dir
from yuxi.modules.workspace.runtime_paths import SHARED_SKILLS_PATH, WORKSPACE_PATH, normalize_runtime_path

MAX_FILE_BYTES = 100 * 1024 * 1024


class RuntimeFiles:
    """Workspace 可写，共享 Skill 投影只读；其他路径交给 VM。"""

    def __init__(self, uid: str, *, workdir_path: str | None = None):
        """身份由运行上下文或认证层提供。"""
        self.uid = uid
        self.workspace = Workspace(uid)
        self._workdir_root = f"/{normalize_workdir_path(workdir_path)}" if workdir_path else None

    def route(self, path: str):
        """只按受信挂载前缀路由，不存在或链接错误不能回退到 VM。"""
        normalized = normalize_runtime_path(path)
        for prefix, root, readonly in (
            (WORKSPACE_PATH, user_workspace_dir(self.uid), False),
            (SHARED_SKILLS_PATH, get_user_skills_root_dir(self.uid), True),
        ):
            if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
                relative = normalized[len(prefix) :].lstrip("/")
                return normalized, root, tuple(PurePosixPath(relative).parts), readonly
        return None

    def scope(self, path: str) -> str:
        """把 Workspace 虚拟路径转换为公开文件 API 的 scope。"""
        route = self.route(path)
        if route is None or route[3]:
            raise PermissionError("path is outside the user Workspace")
        return "/" + "/".join(route[2])

    def require_readable_file(self, path: str) -> None:
        """在运行时挂载内校验可读取的普通文件，不复制内容。"""
        route = self.route(path)
        if route is None:
            raise PermissionError("path is outside the user mounts")
        _, root, parts, _ = route
        with open_regular_file_fd(root, parts):
            pass

    def read(self, path: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
        """普通文件有界读取，共享 Skill 同样拒绝链接。"""
        normalized, root, parts, readonly = self.route(path)
        if not readonly:
            return self.workspace.read_authorized_file(self.scope(normalized), max_bytes)
        with open_regular_file_fd(root, parts) as (fd, info):
            if info.st_size > max_bytes:
                raise ValueError("file exceeds transfer limit")
            with os.fdopen(os.dup(fd), "rb") as source:
                content = source.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ValueError("file exceeds transfer limit")
            return content

    def write(self, path: str, content: bytes, *, overwrite: bool) -> None:
        """新建或完整发布 Workspace 文件，不跟随宿主链接。"""
        scope = self.scope(path)
        self.workspace.upload_authorized_file_from_stream(
            scope,
            io.BytesIO(content),
            max_bytes=MAX_FILE_BYTES,
            overwrite=overwrite,
            root=self._write_root(scope),
        )

    def upload_from_path(self, path: str, source_path: str) -> None:
        """从服务临时文件发布到当前用户 Workspace。"""
        scope = self.scope(path)
        self.workspace.upload_authorized_file_from_path(scope, source_path, root=self._write_root(scope))

    def download_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        """将当前用户挂载中的普通文件有界导出到服务临时文件。"""
        route = self.route(path)
        if route is None:
            raise PermissionError("path is outside the user mounts")
        normalized, root, parts, readonly = route
        if not readonly:
            return self.workspace.download_authorized_file_to_path(self.scope(normalized), target_path, max_bytes)
        with open_regular_file_fd(root, parts) as (source_fd, info):
            if info.st_size > max_bytes:
                raise FileTransferLimitError("file exceeds transfer limit")
            target_fd = os.open(target_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            try:
                return copy_file_fd(source_fd, target_fd, max_bytes=max_bytes)
            finally:
                os.close(target_fd)

    def list(self, path: str) -> list[dict]:
        """返回同一虚拟路径下的普通文件与目录。"""
        normalized, root, parts, _ = self.route(path)
        fd = open_directory_fd(root, parts)
        try:
            result = []
            for name in sorted(os.listdir(fd)):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    continue
                result.append(
                    {
                        "path": normalized.rstrip("/") + "/" + name,
                        "is_dir": stat.S_ISDIR(info.st_mode),
                        "size": info.st_size,
                        "modified_at": str(info.st_mtime),
                    }
                )
            return result
        finally:
            os.close(fd)

    def walk(self, path: str, pattern: str = "*") -> list[dict]:
        """有界递归扫描共享目录，搜索不唤醒 VM。"""
        pending = [(normalize_runtime_path(path), 0)]
        entries = []
        scanned = 0
        while pending:
            current, depth = pending.pop()
            if depth > 64:
                raise ValueError("file search depth exceeds limit")
            for entry in self.list(current):
                scanned += 1
                if scanned > 10000:
                    raise ValueError("file search entries exceed limit")
                relative = entry["path"][len(path.rstrip("/")) + 1 :]
                if fnmatch.fnmatch(relative, pattern) or PurePosixPath(relative).match(pattern):
                    entries.append(entry)
                if entry["is_dir"]:
                    pending.append((entry["path"], depth + 1))
        return sorted(entries, key=lambda item: item["path"])

    def _write_root(self, scope: str) -> str:
        """写入当前 Workdir 时要求目录仍存在，其他 Workspace 路径沿用自身权限。"""
        root = self._workdir_root
        if root and (scope == root or scope.startswith(f"{root}/")):
            return root
        return "/"
