"""以一个持久化 Project Workdir 为根的文件视图。"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from yuxi.infrastructure.filesystem import await_io
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.paths import normalize_workdir_path, normalize_workspace_scope


@dataclass(frozen=True, slots=True)
class Workdir:
    """把浏览 scope `/...` 固定到一个持久化 Workspace 目录。"""

    relative_path: str
    workspace: Workspace

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_workdir_path(self.relative_path))

    @classmethod
    def open_existing(cls, uid: str, workdir_path: str) -> Workdir:
        """打开已存在的持久化 Workdir，并返回受限文件 capability。"""
        workdir = cls(workdir_path, Workspace(str(uid)))
        try:
            workdir.workspace.require_authorized_directory(workdir.root_path, root=workdir.root_path)
        except NotADirectoryError as exc:
            raise ValueError("workdir_path does not reference an existing directory") from exc
        return workdir

    @property
    def root_path(self) -> str:
        return f"/{self.relative_path}"

    def resolve_path(self, path: str | None) -> str:
        """把 Workdir scope 路径解析为 UserWorkspace scope 路径。"""
        raw = str(path or "/").strip() or "/"
        normalized = normalize_workspace_scope(raw)
        if normalized == "/":
            return self.root_path
        return f"{self.root_path}{normalized}"

    def scope_path(self, workspace_path: str) -> str:
        """把当前 Workdir 内的 UserWorkspace 路径转换为浏览 scope。"""
        normalized = normalize_workspace_scope(workspace_path)
        root = self.root_path.rstrip("/")
        if normalized == root:
            return "/"
        if not normalized.startswith(f"{root}/"):
            raise ValueError("path is outside the Workdir")
        return f"/{normalized[len(root) + 1 :]}"

    def list_directory(self, path: str = "/") -> list[dict]:
        return self.workspace.list_authorized_directory(self.resolve_path(path), root=self.root_path)

    def search(self, query: str, **limits) -> list[dict]:
        matches = self.workspace.search_authorized_tree(self.root_path, query, **limits)
        return [{**item, "path": self.scope_path(str(item["path"]))} for item in matches]

    def read_file(self, path: str, max_bytes: int) -> bytes:
        return self.workspace.read_authorized_file(self.resolve_path(path), max_bytes)

    def write_file(self, path: str, content: bytes) -> dict:
        return self.workspace.write_authorized_file(self.resolve_path(path), content)

    def stat(self, path: str) -> dict:
        return self.workspace.stat_authorized_path(self.resolve_path(path), root=self.root_path)

    def copy_file_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        return self.workspace.download_authorized_file_to_path(self.resolve_path(path), target_path, max_bytes)

    def copy_file_from_path(self, path: str, source_path: str, *, overwrite: bool = True) -> dict:
        return self.workspace.upload_authorized_file_from_path(
            self.resolve_path(path),
            source_path,
            overwrite=overwrite,
            root=self.root_path,
        )

    def copy_file_from_stream(self, path: str, source: BinaryIO, *, max_bytes: int, overwrite: bool = True) -> dict:
        """把借用的文件流限量写入当前 Workdir。"""
        return self.workspace.upload_authorized_file_from_stream(
            self.resolve_path(path), source, max_bytes=max_bytes, overwrite=overwrite, root=self.root_path
        )

    async def acopy_directory_from_path(self, source_path: str | Path, target_path: str) -> None:
        """整体复制服务目录；取消时等复制结束，再回收本次产物。"""
        copy = asyncio.create_task(
            asyncio.to_thread(
                self.workspace.copy_authorized_directory_from_path, self.resolve_path(target_path), source_path, root=self.root_path
            )
        )
        try:
            await await_io(copy)
        except asyncio.CancelledError:
            if not copy.cancelled() and copy.exception() is None:
                await await_io(
                    asyncio.to_thread(
                        self.workspace.delete_authorized_path,
                        self.resolve_path(target_path),
                        root=self.root_path,
                        expected_identity=copy.result(),
                    )
                )
            raise

    def create_directory(self, parent_path: str, name: str) -> dict:
        return self.workspace.create_authorized_directory(
            self.resolve_path(parent_path),
            name,
            root=self.root_path,
        )

    def copy_directory_from(self, source: Workdir, target_path: str, *, max_file_bytes: int) -> None:
        """通过 no-follow 文件边界复制完整目录，不暴露用户宿主路径。"""
        pending = [("/", target_path)]
        with tempfile.TemporaryDirectory(prefix="yuxi-workdir-copy-") as directory:
            temporary_file = f"{directory}/file"
            with open(temporary_file, "xb"):
                pass
            while pending:
                source_path, destination_path = pending.pop()
                for item in source.list_directory(source_path):
                    source_child = f"{source_path.rstrip('/')}/{item['name']}"
                    destination_child = f"{destination_path.rstrip('/')}/{item['name']}"
                    if item["is_dir"]:
                        pending.append((source_child, destination_child))
                    else:
                        source.copy_file_to_path(source_child, temporary_file, max_file_bytes)
                        self.copy_file_from_path(destination_child, temporary_file, overwrite=False)

    def delete(self, path: str) -> None:
        self.workspace.delete_authorized_path(self.resolve_path(path), root=self.root_path)
