from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileDownloadResponse, FileUploadResponse, WriteResult

from yuxi.services.skill_service import get_skills_root_dir, is_valid_skill_slug


class SelectedSkillsReadonlyBackend(FilesystemBackend):
    """只读 skills backend，仅暴露选中的技能目录。"""

    def __init__(self, *, selected_slugs: list[str] | None):
        super().__init__(root_dir=get_skills_root_dir(), virtual_mode=True)
        self._selected_slugs = {
            str(slug).strip()
            for slug in (selected_slugs or [])
            if isinstance(slug, str) and is_valid_skill_slug(str(slug))
        }

    def _extract_slug(self, path: str | None) -> str | None:
        if not path:
            return None
        normalized = (path or "").strip()
        if not normalized or normalized == "/":
            return None
        pure = PurePosixPath(normalized if normalized.startswith("/") else f"/{normalized}")
        parts = [p for p in pure.parts if p not in ("/", "")]
        return parts[0] if parts else None

    def _is_allowed_path(self, path: str | None) -> bool:
        slug = self._extract_slug(path)
        if slug is None:
            return True
        return slug in self._selected_slugs

    def _is_allowed_file(self, file_path: str) -> bool:
        slug = self._extract_slug(file_path)
        return slug is not None and slug in self._selected_slugs

    def ls_info(self, path: str) -> list[dict[str, Any]]:
        if not self._selected_slugs:
            return []

        normalized = (path or "/").strip() or "/"
        if not self._is_allowed_path(normalized):
            return []

        infos = super().ls_info(normalized)
        if normalized == "/":
            result = []
            for item in infos:
                slug = self._extract_slug(item.get("path", ""))
                if slug in self._selected_slugs:
                    result.append(item)
            return result
        return infos

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        if not self._is_allowed_file(file_path):
            return "Access denied: file is outside selected skills."
        return super().read(file_path, offset=offset, limit=limit)

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[dict[str, Any]] | str:
        if not self._selected_slugs:
            return []

        if path is not None:
            if not self._is_allowed_path(path):
                return "Access denied: path is outside selected skills."
            return super().grep_raw(pattern=pattern, path=path, glob=glob)

        matches: list[dict[str, Any]] = []
        for slug in sorted(self._selected_slugs):
            result = super().grep_raw(pattern=pattern, path=f"/{slug}", glob=glob)
            if isinstance(result, str):
                continue
            matches.extend(result)
        return matches

    def glob_info(self, pattern: str, path: str = "/") -> list[dict[str, Any]]:
        if not self._selected_slugs:
            return []
        if not self._is_allowed_path(path):
            return []
        infos = super().glob_info(pattern=pattern, path=path)
        return [item for item in infos if self._extract_slug(item.get("path", "")) in self._selected_slugs]

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="Skills path is read-only.")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error="Skills path is read-only.")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not self._is_allowed_file(path):
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))
                continue
            target = self._resolve_path(path)
            if not target.exists():
                responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
                continue
            if target.is_dir():
                responses.append(FileDownloadResponse(path=path, content=None, error="is_directory"))
                continue
            responses.append(FileDownloadResponse(path=path, content=target.read_bytes(), error=None))
        return responses
