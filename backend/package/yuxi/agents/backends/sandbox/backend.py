from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import uuid
from contextlib import aclosing, suppress
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

import httpx
from deepagents.backends.protocol import (
    ASYNC_GREP_TIMEOUT,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.sandbox import MAX_BINARY_BYTES, BaseSandbox
from deepagents.backends.utils import _get_file_type

from yuxi.agents.backends.paths import (
    VIRTUAL_PATH_PREFIX,
    VIRTUAL_SKILLS_PATH,
)
from yuxi.utils.logging_config import logger
from yuxi.workspace.errors import FileTransferLimitError

from .provider import get_sandbox_provider, sandbox_id_for_thread, sandbox_provisioner_token

_USER_DATA_ROOT = "/" + VIRTUAL_PATH_PREFIX.strip("/")
_SKILLS_ROOT = "/" + VIRTUAL_SKILLS_PATH.strip("/")
_BINARY_PREVIEW_TOO_LARGE_ERROR = f"Binary file exceeds maximum preview size of {MAX_BINARY_BYTES} bytes"
_IMAGE_EXTENSIONS = frozenset({".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp"})
_DOCUMENT_EXTENSIONS = frozenset({".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"})
_DOCUMENT_READ_ERROR = (
    "read_file does not support PDF or Office documents. Use ocr_parse_file to convert the file to Markdown first."
)
_BINARY_READ_ERROR = "read_file only supports UTF-8 text and image files. This file type is not supported."


def _read_file_kind(path: str) -> str:
    """按读取契约分类文件。"""
    extension = PurePosixPath(path).suffix.lower()
    if extension in _IMAGE_EXTENSIONS:
        return "image"
    if extension in _DOCUMENT_EXTENSIONS:
        return "document"
    if _get_file_type(path) != "text":
        return "binary"
    return "text"


def _read_window(offset: int, limit: int | None) -> tuple[int, int | None]:
    """将读取偏移和数量转换为 sandbox 行窗口。"""
    start_line = max(0, int(offset))
    end_line = start_line + int(limit) if limit is not None else None
    return start_line, end_line


def _normalize_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path is required")
    if not raw.startswith("/"):
        raise ValueError("path must start with /")
    pure = PurePosixPath(raw)
    if ".." in pure.parts:
        raise ValueError("path traversal is not allowed")
    return str(pure)


def _is_same_or_child(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    if root == "/":
        return path == "/" or path.startswith("/")
    return path == root or path.startswith(f"{root}/")


def _path_overlaps_root(path: str, root: str) -> bool:
    return _is_same_or_child(path, root) or _is_same_or_child(root, path)


def _glob_for_search_root(pattern: str, root: str) -> str:
    bare_pattern = str(pattern or "*").lstrip("/")
    bare_root = root.strip("/")
    if bare_pattern == bare_root:
        return "*"
    root_prefix = f"{bare_root}/"
    if bare_pattern.startswith(root_prefix):
        return bare_pattern[len(root_prefix) :] or "*"
    return pattern


def _permission_error(operation: str, path: str) -> str:
    return f"permission denied for {operation} on '{path}'"


def _raise_authorized_path_operation_error(output: str | None, path: str, fallback: str) -> None:
    """把 sandbox 安全文件脚本的失败恢复为稳定边界异常。"""
    detail = str(output or "")
    if "FileNotFoundError" in detail or "No such file or directory" in detail:
        raise FileNotFoundError(path)
    if "IsADirectoryError" in detail or "source is a directory" in detail:
        raise IsADirectoryError(path)
    if "OverflowError" in detail or "exceeds transfer limit" in detail:
        raise FileTransferLimitError("file exceeds transfer limit")
    if any(
        marker in detail
        for marker in (
            "NotADirectoryError",
            "PermissionError",
            "Too many levels of symbolic links",
            "source is not regular",
        )
    ):
        raise PermissionError(path)
    raise RuntimeError(detail or fallback)


def _describe_read_error(file_path: str, exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"Error: File '{file_path}' not found"
    if isinstance(exc, IsADirectoryError):
        return f"Error: Path '{file_path}' is a directory"
    if isinstance(exc, PermissionError):
        return f"Error: Access denied for '{file_path}'"
    if isinstance(exc, ValueError):
        return f"Error: Invalid path '{file_path}': {exc}"
    detail = str(exc).strip()
    if detail:
        return f"Error: Failed to read '{file_path}': {detail}"
    return f"Error: Failed to read '{file_path}'"


def _is_missing_file_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True

    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code == 404 or getattr(response, "status_code", None) == 404:
        return True

    detail = str(exc).lower()
    return "status_code: 404" in detail or "file does not exist" in detail


def _looks_like_binary(content: bytes) -> bool:
    if not content:
        return False
    if b"\x00" in content:
        return True
    try:
        content.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _is_utf8_decode_failure(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "utf-8" in detail and "can't decode" in detail


class ProvisionerSandboxBackend(BaseSandbox):
    def __init__(
        self,
        thread_id: str,
        *,
        uid: str,
        inherit_env: bool = True,
        create_if_missing: bool = True,
        workdir_path: str | None = None,
    ):
        self._thread_id = str(thread_id or "").strip()
        if not self._thread_id:
            raise ValueError("thread_id is required for ProvisionerSandboxBackend")
        self._uid = str(uid or "").strip()
        if not self._uid:
            raise ValueError("uid is required for ProvisionerSandboxBackend")

        self._inherit_env = inherit_env
        self._create_if_missing = create_if_missing
        self._workdir_path = str(workdir_path or "").strip() or None
        self._provider = get_sandbox_provider()
        self._client: Any | None = None
        self._client_url: str | None = None
        self._command_timeout_seconds = int(os.getenv("SANDBOX_EXEC_TIMEOUT_SECONDS") or 180)
        self._max_output_bytes = int(os.getenv("SANDBOX_MAX_OUTPUT_BYTES") or 262_144)

    def _readable_roots(self) -> tuple[str, ...]:
        return (_USER_DATA_ROOT, _SKILLS_ROOT)

    def _writable_roots(self) -> tuple[str, ...]:
        return (_USER_DATA_ROOT,)

    def _can_read_path(self, path: str) -> bool:
        return any(_is_same_or_child(path, root) for root in self._readable_roots())

    def _can_list_path(self, path: str) -> bool:
        return any(_path_overlaps_root(path, root) for root in self._readable_roots())

    def _can_write_path(self, path: str) -> bool:
        return any(_is_same_or_child(path, root) for root in self._writable_roots())

    def _readable_search_paths(self, path: str) -> list[str]:
        if self._can_read_path(path):
            return [path]
        return [root for root in self._readable_roots() if _is_same_or_child(root, path)]

    def _filter_readable_infos(self, infos: list[FileInfo]) -> list[FileInfo]:
        result: list[FileInfo] = []
        for info in infos:
            try:
                path = _normalize_path(info.get("path", ""))
            except ValueError:
                continue
            if self._can_list_path(path):
                result.append(info)
        return result

    def _filter_readable_matches(self, matches: list[GrepMatch]) -> list[GrepMatch]:
        result: list[GrepMatch] = []
        for match in matches:
            try:
                path = _normalize_path(match.get("path", ""))
            except ValueError:
                continue
            if self._can_read_path(path):
                result.append(match)
        return result

    @property
    def id(self) -> str:
        return sandbox_id_for_thread(self._thread_id, uid=self._uid)

    def _build_client(self, sandbox_url: str):
        try:
            from agent_sandbox import Sandbox as AgentSandboxClient
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "agent-sandbox is required. Install dependency `agent-sandbox` in the docker image."
            ) from exc

        return AgentSandboxClient(
            base_url=sandbox_url,
            headers={"Authorization": f"Bearer {sandbox_provisioner_token()}"},
            timeout=self._command_timeout_seconds,
        )

    def _build_async_client(self, sandbox_url: str, http_client: httpx.AsyncClient):
        """使用显式生命周期的 HTTP client 构造异步 sandbox client。"""
        try:
            from agent_sandbox import AsyncSandbox as AsyncAgentSandboxClient
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "agent-sandbox is required. Install dependency `agent-sandbox` in the docker image."
            ) from exc

        return AsyncAgentSandboxClient(
            base_url=sandbox_url,
            headers={"Authorization": f"Bearer {sandbox_provisioner_token()}"},
            timeout=self._command_timeout_seconds,
            httpx_client=http_client,
        )

    def _get_connection(self) -> Any:
        """发现当前 runtime scope 对应的 sandbox 连接。"""
        connection = self._provider.get(
            self._thread_id,
            uid=self._uid,
            create_if_missing=self._create_if_missing,
            inherit_env=self._inherit_env,
            workdir_path=self._workdir_path,
        )
        if connection is None:
            raise RuntimeError(f"sandbox is unavailable for thread {self._thread_id}")
        return connection

    def _get_client(self) -> Any:
        connection = self._get_connection()

        if self._client is None or self._client_url != connection.sandbox_url:
            self._client = self._build_client(connection.sandbox_url)
            self._client_url = connection.sandbox_url

        return self._client

    def ensure_available(self) -> str:
        """显式确保本实例 sandbox 已创建并返回稳定 ID。"""
        self._get_client()
        return self.id

    @staticmethod
    def _normalize_read_content(result: Any) -> bytes:
        """将 sandbox 文件响应统一转换为原始字节。"""
        content = result.content
        if content is None:
            return b""
        if isinstance(content, bytes):
            return content
        if not isinstance(content, str):
            return str(content).encode("utf-8")

        encoding = getattr(result, "encoding", None)
        if isinstance(encoding, str) and encoding.lower() == "base64":
            return base64.b64decode(content, validate=True)
        return content.encode("utf-8")

    def _read_binary(self, path: str, offset: int = 0, limit: int | None = None) -> bytes:
        """Read file content from the sandbox file API and normalize it to bytes."""
        start_line, end_line = _read_window(offset, limit)
        result = self._get_client().file.read_file(
            file=path,
            start_line=start_line,
            end_line=end_line,
        )
        return self._normalize_read_content(result.data)

    async def _aread_binary(self, client: Any, path: str, offset: int, limit: int | None) -> bytes:
        """通过原生异步文件 API 读取文本窗口。"""
        start_line, end_line = _read_window(offset, limit)
        result = await client.file.read_file(
            file=path,
            start_line=start_line,
            end_line=end_line,
        )
        return self._normalize_read_content(result.data)

    async def _aread_base64_file(self, client: Any, path: str) -> ReadResult:
        """流式下载并有界编码异步图片预览。"""
        content = bytearray()
        chunks = client.file.download_file(
            path=path,
            request_options={"timeout_in_seconds": self._command_timeout_seconds},
        )
        async with aclosing(chunks):
            async for chunk in chunks:
                content.extend(chunk)
                if len(content) > MAX_BINARY_BYTES:
                    return ReadResult(error=_BINARY_PREVIEW_TOO_LARGE_ERROR)
        encoded_content = base64.b64encode(content).decode("ascii")
        return ReadResult(file_data={"content": encoded_content, "encoding": "base64"})

    async def _aensure_file_exists(self, client: Any, path: str) -> None:
        """通过原生下载端点确认非文本文件可读取。"""
        chunks = client.file.download_file(
            path=path,
            request_options={"timeout_in_seconds": self._command_timeout_seconds},
        )
        async with aclosing(chunks):
            await anext(chunks, None)

    def _file_size_bytes(self, path: str) -> int:
        path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
        command = (
            'python3 -c "'
            "import base64, os, stat; "
            f"path = base64.b64decode('{path_b64}').decode('utf-8'); "
            "st = os.stat(path); "
            "print(st.st_size if stat.S_ISREG(st.st_mode) else -1)"
            '"'
        )
        result = self.execute(command)
        if result.exit_code not in (0, None):
            detail = (result.output or "").strip()
            raise RuntimeError(detail or f"failed to stat '{path}'")

        output = (result.output or "").strip().splitlines()
        try:
            size = int(output[-1])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"failed to stat '{path}'") from exc
        if size < 0:
            raise IsADirectoryError(path)
        return size

    def _read_file_base64(self, path: str) -> str:
        path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
        output_path = f"/tmp/yuxi-read-file-{uuid.uuid4().hex}.b64"
        output_path_b64 = base64.b64encode(output_path.encode("utf-8")).decode("ascii")
        command = (
            'python3 -c "'
            "import base64; "
            f"path = base64.b64decode('{path_b64}').decode('utf-8'); "
            f"output_path = base64.b64decode('{output_path_b64}').decode('utf-8'); "
            "open(output_path, 'w').write(base64.b64encode(open(path, 'rb').read()).decode('ascii'))"
            '"'
        )
        client = self._get_client()
        try:
            result = client.shell.exec_command(
                command=command,
                timeout=self._command_timeout_seconds,
                truncate=False,
            )
            output = result.data.output or ""
            if result.data.exit_code not in (0, None):
                raise RuntimeError(output.strip() or f"failed to read '{path}'")

            content = self._read_binary(output_path).decode("ascii").strip()
            base64.b64decode(content, validate=True)
            return content
        finally:
            with suppress(Exception):
                client.shell.exec_command(command=f"rm -f {output_path}", timeout=10)

    def _read_base64_file(self, path: str) -> ReadResult:
        if self._file_size_bytes(path) > MAX_BINARY_BYTES:
            return ReadResult(error=_BINARY_PREVIEW_TOO_LARGE_ERROR)
        return ReadResult(file_data={"content": self._read_file_base64(path), "encoding": "base64"})

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read allowed file content via the sandbox file API."""
        try:
            normalized_path = _normalize_path(file_path)
        except Exception as exc:  # noqa: BLE001
            return ReadResult(error=f"Invalid path '{file_path}': {exc}")
        if not self._can_read_path(normalized_path):
            return ReadResult(error=_permission_error("read", normalized_path))

        if limit is not None and int(limit) <= 0:
            return ReadResult(no_lines_requested=True)
        start_line = max(0, int(offset)) + 1

        try:
            file_kind = _read_file_kind(normalized_path)
            if file_kind == "image":
                return self._read_base64_file(normalized_path)
            if file_kind == "document":
                self._file_size_bytes(normalized_path)
                return ReadResult(error=_DOCUMENT_READ_ERROR)
            if file_kind == "binary":
                self._file_size_bytes(normalized_path)
                return ReadResult(error=_BINARY_READ_ERROR)

            try:
                content = self._read_binary(
                    normalized_path,
                    offset=start_line - 1,
                    limit=int(limit) if limit is not None else None,
                )
            except Exception as exc:  # noqa: BLE001
                if not _is_utf8_decode_failure(exc):
                    raise
                return ReadResult(error=_BINARY_READ_ERROR)

            return self._text_content_read_result(content, start_line=start_line)
        except Exception as exc:  # noqa: BLE001
            error = _describe_read_error(file_path, exc)
            return ReadResult(error=error.removeprefix("Error: "))

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """通过 sandbox 原生异步文件 API 读取授权内容。"""
        try:
            normalized_path = _normalize_path(file_path)
        except Exception as exc:  # noqa: BLE001
            return ReadResult(error=f"Invalid path '{file_path}': {exc}")
        if not self._can_read_path(normalized_path):
            return ReadResult(error=_permission_error("read", normalized_path))
        if limit is not None and int(limit) <= 0:
            return ReadResult(no_lines_requested=True)

        start_line = max(0, int(offset)) + 1

        try:
            connection = await asyncio.to_thread(self._get_connection)
            async with httpx.AsyncClient(
                timeout=self._command_timeout_seconds,
                follow_redirects=True,
            ) as http_client:
                client = self._build_async_client(connection.sandbox_url, http_client)
                file_kind = _read_file_kind(normalized_path)
                if file_kind == "image":
                    return await self._aread_base64_file(client, normalized_path)
                if file_kind == "document":
                    await self._aensure_file_exists(client, normalized_path)
                    return ReadResult(error=_DOCUMENT_READ_ERROR)
                if file_kind == "binary":
                    await self._aensure_file_exists(client, normalized_path)
                    return ReadResult(error=_BINARY_READ_ERROR)

                try:
                    content = await self._aread_binary(
                        client,
                        normalized_path,
                        offset=start_line - 1,
                        limit=int(limit) if limit is not None else None,
                    )
                except Exception as exc:  # noqa: BLE001
                    if not _is_utf8_decode_failure(exc):
                        raise
                    return ReadResult(error=_BINARY_READ_ERROR)

                return self._text_content_read_result(content, start_line=start_line)
        except Exception as exc:  # noqa: BLE001
            error = _describe_read_error(file_path, exc)
            return ReadResult(error=error.removeprefix("Error: "))

    def _text_content_read_result(self, content: bytes, *, start_line: int) -> ReadResult:
        """将文本候选字节转换为读取结果。"""
        if _looks_like_binary(content):
            return ReadResult(error=_BINARY_READ_ERROR)
        return self._text_read_result(content.decode("utf-8"), start_line=start_line)

    @staticmethod
    def _text_read_result(text: str, *, start_line: int) -> ReadResult:
        """按 0.7 协议补齐分页窗口字段，支持可靠续读提示。"""
        lines_returned = len(text.splitlines())
        if lines_returned <= 0:
            return ReadResult(file_data={"content": text, "encoding": "utf-8"})
        end_line = start_line + lines_returned - 1
        return ReadResult(
            file_data={"content": text, "encoding": "utf-8"},
            start_line=start_line,
            end_line=end_line,
            next_offset=end_line,
        )

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a shell command in the sandbox.

        Output is normalized to text and truncated to the configured maximum
        payload size before being returned.
        """
        try:
            kwargs: dict[str, Any] = {"command": command}
            if timeout is not None:
                kwargs["timeout"] = timeout
                kwargs["hard_timeout"] = timeout
                kwargs["request_options"] = {"timeout_in_seconds": timeout}
            result = self._get_client().shell.exec_command(**kwargs)

            output = result.data.output or ""
            exit_code = result.data.exit_code

            truncated = False
            encoded = output.encode("utf-8", errors="ignore")
            if len(encoded) > self._max_output_bytes:
                output = encoded[: self._max_output_bytes].decode("utf-8", errors="ignore")
                truncated = True

            return ExecuteResponse(
                output=output,
                exit_code=exit_code if isinstance(exit_code, int) else None,
                truncated=truncated,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Sandbox execute failed for thread {self._thread_id}: {exc}")
            return ExecuteResponse(output=f"Error: {exc}", exit_code=1, truncated=False)

    def ls(self, path: str) -> LsResult:
        """List direct children of an allowed sandbox path with lightweight metadata."""
        try:
            normalized_path = _normalize_path(path)
        except Exception as exc:  # noqa: BLE001
            return LsResult(error=f"Invalid path '{path}': {exc}")
        if not self._can_list_path(normalized_path):
            return LsResult(error=_permission_error("read", normalized_path))

        try:
            result = self._get_client().file.list_path(path=normalized_path, recursive=False, include_size=True)
        except Exception as exc:  # noqa: BLE001
            return LsResult(error=str(exc) or f"Failed to list '{path}'")

        entries = result.data.files or []
        infos: list[FileInfo] = []
        for entry in entries:
            info: FileInfo = {"path": entry.path, "is_dir": entry.is_directory}
            size = entry.size
            if isinstance(size, int):
                info["size"] = size
            modified_time = entry.modified_time
            if modified_time:
                if isinstance(modified_time, str) and modified_time.isdigit():
                    info["modified_at"] = datetime.fromtimestamp(int(modified_time)).isoformat()
                elif isinstance(modified_time, str):
                    try:
                        info["modified_at"] = datetime.fromisoformat(modified_time).isoformat()
                    except ValueError:
                        info["modified_at"] = modified_time
                elif isinstance(modified_time, (int, float)):
                    info["modified_at"] = datetime.fromtimestamp(modified_time).isoformat()
            infos.append(info)
        return LsResult(entries=self._filter_readable_infos(infos))

    def _ensure_parent_directory(self, file_path: str) -> None:
        """在 UserWorkspace 内按需创建父目录，且不跟随符号链接。"""
        relative_parts = tuple(PurePosixPath(file_path[len(_USER_DATA_ROOT) :].lstrip("/")).parts[:-1])
        if not relative_parts:
            return
        script = f"""
import os

directory_fd = os.open({_USER_DATA_ROOT!r}, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for part in {relative_parts!r}:
        try:
            os.mkdir(part, 0o755, dir_fd=directory_fd)
        except FileExistsError:
            pass
        child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = child_fd
finally:
    os.close(directory_fd)
"""
        encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        result = self.execute(f"python3 -c \"import base64;exec(base64.b64decode('{encoded_script}'))\"")
        if result.exit_code not in (0, None):
            raise PermissionError(result.output or file_path)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write a new text file.

        This method is intentionally text-only. Binary payloads should go through
        upload_files(), which uses base64 encoding for the sandbox file API.
        """
        try:
            normalized_path = _normalize_path(file_path)
        except Exception as exc:  # noqa: BLE001
            return WriteResult(error=f"Error: Invalid path '{file_path}': {exc}")
        if not self._can_write_path(normalized_path):
            return WriteResult(error=f"Error: {_permission_error('write', normalized_path)}")
        if not isinstance(content, str):
            return WriteResult(error="Error: write() only supports text content; use upload_files() for binary data")
        try:
            self._read_binary(normalized_path)
        except Exception:  # noqa: BLE001
            pass
        else:
            return WriteResult(error=f"Error: File '{file_path}' already exists")

        try:
            self._ensure_parent_directory(normalized_path)
            result = self._get_client().file.write_file(file=normalized_path, content=content)
            if not result.success:
                return WriteResult(error=result.message or f"Failed to write file '{file_path}'")
        except Exception as exc:  # noqa: BLE001
            return WriteResult(error=str(exc) or f"Failed to write file '{file_path}'")

        return WriteResult(path=normalized_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit an existing text file by replacing string content.

        This method operates on UTF-8-decoded text content only. Binary files
        are not supported here and should be handled via download/upload flows.
        """
        try:
            normalized_path = _normalize_path(file_path)
        except Exception as exc:  # noqa: BLE001
            return EditResult(error=f"Error: Invalid path '{file_path}': {exc}")
        if not self._can_write_path(normalized_path):
            return EditResult(error=f"Error: {_permission_error('write', normalized_path)}")

        # Check if old_string exists
        try:
            text = self._read_binary(normalized_path).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return EditResult(error=f"Error: File '{file_path}' not found")

        count = text.count(old_string)
        if count == 0:
            return EditResult(error=f"Error: String not found in file: '{old_string}'")
        if count > 1 and not replace_all:
            return EditResult(
                error=(
                    f"Error: String '{old_string}' appears multiple times. "
                    "Use replace_all=True to replace all occurrences."
                )
            )

        # Use str_replace_editor API
        replace_mode = "ALL" if replace_all else "FIRST"
        try:
            result = self._get_client().file.str_replace_editor(
                command="str_replace",
                path=normalized_path,
                old_str=old_string,
                new_str=new_string,
                replace_mode=replace_mode,
            )
            if not result.success:
                return EditResult(error=result.message or f"Error editing file '{file_path}'")
        except Exception as exc:  # noqa: BLE001
            return EditResult(error=f"Error editing file: {exc}")

        return EditResult(path=normalized_path, occurrences=count if replace_all else 1)

    def _grep_root(self, pattern: str, path: str, glob: str | None, max_count: int | None) -> GrepResult:
        """调用沙盒原生文件搜索并映射结构化结果。"""
        if glob and ".." in glob.replace("\\", "/").split("/"):
            return GrepResult(error="Invalid glob pattern: path traversal is not allowed")
        kwargs: dict[str, Any] = {
            "path": path,
            "pattern": pattern,
            "fixed_strings": True,
            "recursive": True,
        }
        if glob:
            # 原生 include 按完整路径匹配，目录 glob 必须锚定到当前搜索根。
            escaped_root = "".join("\\" + char if char in "\\*?[]{}" else char for char in path.rstrip("/"))
            kwargs["include"] = [f"{escaped_root}/{glob.lstrip('/')}" if "/" in glob else glob]
        if max_count is not None:
            kwargs["max_results"] = max_count
        try:
            from agent_sandbox.types import FileGrepResult

            connection = self._get_connection()
            # SDK 0.0.30 把失败响应也解码为成功模型，HTTP 边界先区分两者。
            response = httpx.post(
                f"{connection.sandbox_url.rstrip('/')}/v1/file/grep",
                json=kwargs,
                headers={"Authorization": f"Bearer {sandbox_provisioner_token()}"},
                timeout=ASYNC_GREP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                if (payload.get("data") or {}).get("error_type") == "not_found":
                    return GrepResult(matches=[])
                return GrepResult(error=payload.get("message") or "Sandbox grep failed")
            data = FileGrepResult.model_validate(payload["data"])
            if data.truncated is None:
                return GrepResult(error="Invalid sandbox grep result")
            matches = [
                {"path": match.file, "line": match.line_number, "text": match.line_content}
                for match in data.matches or []
            ]
            if len(json.dumps(matches, ensure_ascii=False).encode("utf-8")) > self._max_output_bytes:
                return GrepResult(error="grep output exceeded sandbox limit")
            return GrepResult(matches=matches, truncated=data.truncated)
        except Exception as exc:  # noqa: BLE001
            return GrepResult(error=str(exc) or "Sandbox grep failed")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search allowed sandbox paths for literal text with a global match cap."""
        try:
            normalized_path = _normalize_path(path or "/")
        except Exception as exc:  # noqa: BLE001
            return GrepResult(error=f"Invalid path '{path or '/'}': {exc}")

        search_paths = self._readable_search_paths(normalized_path)
        if not search_paths:
            return GrepResult(error=_permission_error("read", normalized_path))

        matches: list[GrepMatch] = []
        truncated = False
        for search_path in search_paths:
            if max_count is not None:
                remaining = max(max_count - len(matches), 0)
                if remaining == 0:
                    truncated = True
                    break
            else:
                remaining = None
            result = self._grep_root(pattern, search_path, glob, remaining)
            if result.error:
                return result
            matches.extend(result.matches or [])
            truncated = truncated or result.truncated

        if max_count is not None and len(matches) > max_count:
            matches = matches[:max_count]
            truncated = True
        return GrepResult(matches=self._filter_readable_matches(matches), truncated=truncated)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """在线程中执行同一授权搜索，避免阻塞 Agent 事件循环。"""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.grep, pattern, path, glob, max_count=max_count),
                timeout=ASYNC_GREP_TIMEOUT,
            )
        except TimeoutError:
            return GrepResult(
                error=(
                    f"Error: grep timed out after {ASYNC_GREP_TIMEOUT}s. "
                    "Try a more specific pattern or a narrower path."
                )
            )

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        """Return files matching a glob pattern under allowed sandbox paths."""
        try:
            normalized_path = _normalize_path(path)
        except Exception as exc:  # noqa: BLE001
            return GlobResult(error=f"Invalid path '{path}': {exc}")
        if ".." in PurePosixPath(str(pattern or "")).parts:
            return GlobResult(error="Invalid glob pattern: path traversal is not allowed")

        search_paths = self._readable_search_paths(normalized_path)
        if not search_paths:
            return GlobResult(error=_permission_error("read", normalized_path))

        infos: list[FileInfo] = []
        for search_path in search_paths:
            try:
                result = self._get_client().file.find_files(
                    path=search_path,
                    glob=_glob_for_search_root(pattern, search_path),
                )
            except Exception as exc:  # noqa: BLE001
                return GlobResult(error=str(exc) or f"Failed to glob '{path}'")
            for file_path in result.data.files or []:
                infos.append({"path": file_path})
        infos = self._filter_readable_infos(infos)
        infos.sort(key=lambda item: item.get("path", ""))
        return GlobResult(matches=infos)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload binary or text file payloads via the sandbox file API.

        Contents are base64-encoded before calling the remote write_file API so
        arbitrary bytes can be transferred safely.
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                normalized_path = _normalize_path(path)
                if not self._can_write_path(normalized_path):
                    responses.append(FileUploadResponse(path=normalized_path, error="permission_denied"))
                    continue
                self._ensure_parent_directory(normalized_path)
                result = self._get_client().file.write_file(
                    file=normalized_path,
                    content=base64.b64encode(content).decode("ascii"),
                    encoding="base64",
                )
                if not result.success:
                    raise Exception(result.message or "Upload failed")
                responses.append(FileUploadResponse(path=normalized_path, error=None))
            except PermissionError:
                normalized_path = str(path)
                responses.append(FileUploadResponse(path=normalized_path, error="permission_denied"))
            except IsADirectoryError:
                normalized_path = str(path)
                responses.append(FileUploadResponse(path=normalized_path, error="is_directory"))
            except FileNotFoundError:
                normalized_path = str(path)
                responses.append(FileUploadResponse(path=normalized_path, error="file_not_found"))
            except Exception as exc:  # noqa: BLE001
                normalized_path = str(path)
                logger.warning(f"Upload to sandbox failed for {normalized_path}: {exc}")
                responses.append(FileUploadResponse(path=normalized_path, error="invalid_path"))
        return responses

    def upload_authorized_file_from_path(self, path: str, source_path: str) -> None:
        """从受信任服务向 Project 或 User Data 写入普通文件。"""
        normalized_path = _normalize_path(path)
        if normalized_path == _USER_DATA_ROOT or not _is_same_or_child(normalized_path, _USER_DATA_ROOT):
            raise ValueError(f"write path is outside authorized roots: {normalized_path}")
        relative_parts = normalized_path[len(_USER_DATA_ROOT) + 1 :].split("/")
        export_path = f"/tmp/.yuxi-file-upload-{uuid.uuid4().hex}"
        with open(source_path, "rb") as source:
            result = self._get_client().file.upload_file(
                file=source,
                path=export_path,
                request_options={"timeout_in_seconds": self._command_timeout_seconds},
            )
        if not result.success:
            raise RuntimeError(result.message or f"failed to upload source for {normalized_path}")
        script = f"""
import os
import stat

root = {_USER_DATA_ROOT!r}
parts = {relative_parts!r}
source_path = {export_path!r}
temp_name = {f".yuxi-write-{uuid.uuid4().hex}"!r}
directory_fd = None
source_fd = None
target_fd = None
try:
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    for part in parts[:-1]:
        try:
            os.mkdir(part, 0o755, dir_fd=directory_fd)
        except FileExistsError:
            pass
        child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = child_fd
    source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
        raise ValueError("upload source is not regular")
    target_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=directory_fd)
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        offset = 0
        while offset < len(chunk):
            offset += os.write(target_fd, chunk[offset:])
    os.close(target_fd)
    target_fd = None
    os.rename(temp_name, parts[-1], src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
finally:
    if target_fd is not None:
        os.close(target_fd)
    if source_fd is not None:
        os.close(source_fd)
    if directory_fd is not None:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    try:
        os.unlink(source_path)
    except FileNotFoundError:
        pass
"""
        encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        install_result = self.execute(f"python3 -c \"import base64;exec(base64.b64decode('{encoded_script}'))\"")
        if install_result.exit_code not in (0, None):
            _raise_authorized_path_operation_error(
                install_result.output,
                normalized_path,
                f"failed to write {normalized_path}",
            )

    def list_authorized_directory(self, path: str, *, root: str) -> list[dict[str, Any]]:
        """不跟随链接地列出授权目录中的普通文件与真实目录。"""
        normalized_path = _normalize_path(path)
        normalized_root = _normalize_path(root)
        if normalized_path != normalized_root and not _is_same_or_child(normalized_path, normalized_root):
            raise ValueError(f"directory path is outside authorized root: {normalized_path}")
        relative_parts = (
            ()
            if normalized_path == normalized_root
            else tuple(PurePosixPath(normalized_path[len(normalized_root) + 1 :]).parts)
        )
        script = f"""
import base64
import json
import os
import stat

root = {normalized_root!r}
parts = {relative_parts!r}
directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for part in parts:
        child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = child_fd
    entries = []
    for name in sorted(os.listdir(directory_fd), key=str.lower):
        item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not (stat.S_ISDIR(item_stat.st_mode) or stat.S_ISREG(item_stat.st_mode)):
            continue
        entries.append({{
            "name": name,
            "is_dir": stat.S_ISDIR(item_stat.st_mode),
            "size": 0 if stat.S_ISDIR(item_stat.st_mode) else item_stat.st_size,
            "modified_at": item_stat.st_mtime,
        }})
    print("YUXI_SAFE_LIST " + base64.b64encode(json.dumps(entries).encode()).decode())
finally:
    os.close(directory_fd)
"""
        encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        result = self.execute(f"python3 -c \"import base64;exec(base64.b64decode('{encoded_script}'))\"")
        if result.exit_code not in (0, None):
            raise FileNotFoundError(normalized_path)
        payload = next(
            (
                line.removeprefix("YUXI_SAFE_LIST ")
                for line in (result.output or "").splitlines()
                if line.startswith("YUXI_SAFE_LIST ")
            ),
            None,
        )
        if payload is None:
            raise RuntimeError("sandbox directory listing did not return a safe payload")
        decoded = json.loads(base64.b64decode(payload).decode("utf-8"))
        return list(decoded) if isinstance(decoded, list) else []

    def create_authorized_directory(self, parent_path: str, name: str, *, root: str) -> str:
        """在授权根内以 dir-fd 创建一个单层目录。"""
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("directory name must be one path component")
        normalized_parent = _normalize_path(parent_path)
        normalized_root = _normalize_path(root)
        if normalized_parent != normalized_root and not _is_same_or_child(normalized_parent, normalized_root):
            raise ValueError("parent path is outside authorized root")
        target_path = f"{normalized_parent.rstrip('/')}/{name}"
        relative_parts = (
            ()
            if normalized_parent == normalized_root
            else tuple(PurePosixPath(normalized_parent[len(normalized_root) + 1 :]).parts)
        )
        script = f"""
import os
root = {normalized_root!r}
parts = {relative_parts!r}
name = {name!r}
directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for part in parts:
        child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = child_fd
    os.mkdir(name, 0o755, dir_fd=directory_fd)
finally:
    os.close(directory_fd)
"""
        encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        result = self.execute(f"python3 -c \"import base64;exec(base64.b64decode('{encoded_script}'))\"")
        if result.exit_code not in (0, None):
            raise ValueError(result.output or "failed to create directory")
        return target_path

    def delete_authorized_path(self, path: str, *, root: str) -> None:
        """不跟随链接地递归删除授权根内路径，但不允许删除根。"""
        normalized_path = _normalize_path(path)
        normalized_root = _normalize_path(root)
        if normalized_path == normalized_root or not _is_same_or_child(normalized_path, normalized_root):
            raise ValueError("delete path is outside authorized root or is the root")
        parts = tuple(PurePosixPath(normalized_path[len(normalized_root) + 1 :]).parts)
        script = f"""
import os
import stat

root = {normalized_root!r}
parts = {parts!r}

def remove_entry(parent_fd, name):
    item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(item_stat.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        for child_name in os.listdir(child_fd):
            remove_entry(child_fd, child_name)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)

directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for part in parts[:-1]:
        child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = child_fd
    remove_entry(directory_fd, parts[-1])
finally:
    os.close(directory_fd)
"""
        encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
        result = self.execute(f"python3 -c \"import base64;exec(base64.b64decode('{encoded_script}'))\"")
        if result.exit_code not in (0, None):
            raise FileNotFoundError(normalized_path)

    def download_authorized_file_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        """安全下载 Project、User Data 或 Skills 内普通文件到 worker。"""
        normalized_path = _normalize_path(path)
        root = next(
            (
                candidate
                for candidate in self._readable_roots()
                if normalized_path != candidate and _is_same_or_child(normalized_path, candidate)
            ),
            None,
        )
        if root is None:
            raise ValueError(f"file path is outside authorized roots: {normalized_path}")
        return self._download_scoped_file_to_path(normalized_path, root, target_path, max_bytes)

    def regular_file_exists(self, path: str) -> bool:
        """确认授权根内路径是未越界的普通文件。"""
        normalized_path = _normalize_path(path)
        if not self._can_read_path(normalized_path):
            return False
        root = next(
            (
                candidate
                for candidate in self._readable_roots()
                if normalized_path != candidate and _is_same_or_child(normalized_path, candidate)
            ),
            None,
        )
        if root is None:
            return False
        path_b64 = base64.b64encode(normalized_path.encode("utf-8")).decode("ascii")
        root_b64 = base64.b64encode(root.encode("utf-8")).decode("ascii")
        command = (
            'python3 -c "'
            "import base64, os, stat; "
            f"path = base64.b64decode('{path_b64}').decode('utf-8'); "
            f"root = base64.b64decode('{root_b64}').decode('utf-8'); "
            "st = os.lstat(path); "
            "inside = os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root); "
            "raise SystemExit(0 if stat.S_ISREG(st.st_mode) and inside else 2)"
            '"'
        )
        try:
            result = self.execute(command)
        except (FileNotFoundError, IsADirectoryError, RuntimeError, ValueError):
            return False
        return result.exit_code in (0, None)

    def _download_scoped_file_to_path(self, normalized_path: str, root: str, target_path: str, max_bytes: int) -> int:
        """通过目录 fd 固化普通文件，再做有界的 sandbox→worker 传输。"""
        if max_bytes < 0:
            raise ValueError("file download limit must be non-negative")

        relative_parts = normalized_path[len(root) + 1 :].split("/")
        export_path = f"/tmp/.yuxi-file-snapshot-{uuid.uuid4().hex}"
        script = f"""
import hashlib
import os
import stat

root = {root!r}
parts = {relative_parts!r}
export_path = {export_path!r}
max_bytes = {max_bytes!r}
directory_fd = None
source_fd = None
target_fd = None
try:
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    for part in parts[:-1]:
        child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = child_fd
    source_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    source_mode = os.fstat(source_fd).st_mode
    if stat.S_ISDIR(source_mode):
        raise IsADirectoryError("source is a directory")
    if not stat.S_ISREG(source_mode):
        raise PermissionError("source is not regular")
    target_fd = os.open(export_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    size = 0
    digest = hashlib.sha256()
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise OverflowError("file exceeds transfer limit")
        digest.update(chunk)
        offset = 0
        while offset < len(chunk):
            offset += os.write(target_fd, chunk[offset:])
    os.close(target_fd)
    target_fd = None
    print(f"YUXI_FILE_SNAPSHOT {{size}} {{digest.hexdigest()}}")
except Exception:
    if target_fd is not None:
        os.close(target_fd)
    try:
        os.unlink(export_path)
    except FileNotFoundError:
        pass
    raise
finally:
    if source_fd is not None:
        os.close(source_fd)
    if directory_fd is not None:
        os.close(directory_fd)
"""
        operation_error: Exception | None = None
        try:
            encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
            result = self.execute(f"python3 -c \"import base64;exec(base64.b64decode('{encoded_script}'))\"")
            if result.exit_code not in (0, None):
                _raise_authorized_path_operation_error(
                    result.output,
                    normalized_path,
                    f"authorized file snapshot failed: {normalized_path}",
                )
            snapshot_line = next(
                (line for line in str(result.output or "").splitlines() if line.startswith("YUXI_FILE_SNAPSHOT ")),
                None,
            )
            if snapshot_line is None:
                export_path_b64 = base64.b64encode(export_path.encode("utf-8")).decode("ascii")
                metadata_result = self.execute(
                    'python3 -c "import base64,hashlib,os,stat; '
                    f"p=base64.b64decode('{export_path_b64}').decode(); "
                    "fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW); st=os.fstat(fd); "
                    "(_ for _ in ()).throw(PermissionError()) if not stat.S_ISREG(st.st_mode) else None; "
                    "digest=hashlib.sha256(); "
                    "size=sum((digest.update(chunk) or len(chunk)) "
                    "for chunk in iter(lambda:os.read(fd,1048576),b'')); "
                    "os.close(fd); "
                    "print(f'YUXI_FILE_SNAPSHOT {size} {digest.hexdigest()}')\""
                )
                if metadata_result.exit_code not in (0, None):
                    _raise_authorized_path_operation_error(
                        metadata_result.output,
                        normalized_path,
                        "sandbox snapshot metadata read failed",
                    )
                snapshot_line = next(
                    (
                        line
                        for line in str(metadata_result.output or "").splitlines()
                        if line.startswith("YUXI_FILE_SNAPSHOT ")
                    ),
                    None,
                )
                if snapshot_line is None:
                    raise RuntimeError("sandbox file snapshot did not report size and checksum")
            _, expected_size_text, expected_digest = snapshot_line.rsplit(" ", 2)
            expected_size = int(expected_size_text)

            actual_size = 0
            actual_digest = hashlib.sha256()
            chunks = self._get_client().file.download_file(
                path=export_path,
                request_options={"timeout_in_seconds": self._command_timeout_seconds},
            )
            with open(target_path, "wb") as target:
                for chunk in chunks:
                    actual_size += len(chunk)
                    if actual_size > max_bytes:
                        raise FileTransferLimitError(f"file exceeds transfer limit: {normalized_path}")
                    actual_digest.update(chunk)
                    target.write(chunk)
            if actual_size != expected_size or actual_digest.hexdigest() != expected_digest:
                raise ValueError(f"file changed during transfer: {normalized_path}")
            return actual_size
        except Exception as exc:
            operation_error = exc
            with suppress(FileNotFoundError):
                os.unlink(target_path)
            raise
        finally:
            cleanup_path = base64.b64encode(export_path.encode("utf-8")).decode("ascii")
            try:
                cleanup_result = self.execute(
                    'python3 -c "import base64,os; '
                    f"p=base64.b64decode('{cleanup_path}').decode(); "
                    'os.path.exists(p) and os.unlink(p)"'
                )
                if cleanup_result.exit_code not in (0, None):
                    raise RuntimeError(f"sandbox file snapshot cleanup failed: {export_path}")
            except Exception as exc:  # noqa: BLE001
                if operation_error is None:
                    with suppress(FileNotFoundError):
                        os.unlink(target_path)
                    raise
                logger.error("Failed to remove sandbox file snapshot %s: %s", export_path, exc)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download file payloads as raw bytes from the sandbox file API."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                normalized_path = _normalize_path(path)
                if not self._can_read_path(normalized_path):
                    responses.append(
                        FileDownloadResponse(path=normalized_path, content=None, error="permission_denied")
                    )
                    continue
                content = b"".join(
                    self._get_client().file.download_file(
                        path=normalized_path,
                        request_options={"timeout_in_seconds": self._command_timeout_seconds},
                    )
                )
                responses.append(FileDownloadResponse(path=normalized_path, content=content, error=None))
            except PermissionError:
                normalized_path = str(path)
                responses.append(FileDownloadResponse(path=normalized_path, content=None, error="permission_denied"))
            except IsADirectoryError:
                normalized_path = str(path)
                responses.append(FileDownloadResponse(path=normalized_path, content=None, error="is_directory"))
            except FileNotFoundError:
                normalized_path = str(path)
                responses.append(FileDownloadResponse(path=normalized_path, content=None, error="file_not_found"))
            except ValueError:
                normalized_path = str(path)
                responses.append(FileDownloadResponse(path=normalized_path, content=None, error="invalid_path"))
            except Exception as exc:  # noqa: BLE001
                normalized_path = str(path)
                if _is_missing_file_error(exc):
                    responses.append(FileDownloadResponse(path=normalized_path, content=None, error="file_not_found"))
                    continue
                logger.warning(f"Download from sandbox failed for {normalized_path}: {exc}")
                responses.append(FileDownloadResponse(path=normalized_path, content=None, error=f"read_failed: {exc}"))
        return responses
