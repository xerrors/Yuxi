"""Yuxi 沙盒后端实现。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
from fnmatch import fnmatch
from os import path as ospath
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox

from yuxi.agents.backends.path_security import (
    is_skills_path,
    mask_host_paths,
    normalize_virtual_path,
    validate_execute_command_paths,
)
from yuxi.agents.backends.sandbox_config import (
    SKILLS_PATH,
    USER_DATA_PATH,
)
from yuxi.utils.logging_config import logger


class YuxiSandboxBackend(BaseSandbox):
    """Yuxi 沙盒后端，通过 docker exec 在容器内执行。"""

    def __init__(
        self,
        *,
        sandbox_key: str,
        container_name: str,
        thread_id: str,
        host_user_data_dir: Path,
        skills_host_path: Path | None = None,
    ):
        """初始化沙盒后端

        Args:
            sandbox_key: 沙盒唯一标识
            container_name: Docker 容器名称
            thread_id: 线程 ID（用于路径隔离）
            host_user_data_dir: 宿主机侧用户数据根目录
            skills_host_path: 宿主机侧 skills 目录路径
        """
        self._sandbox_key = sandbox_key
        self._container_name = container_name
        self._thread_id = thread_id
        self._host_user_data_dir = host_user_data_dir
        self._skills_host_path = skills_host_path
        self._has_docker_cli = shutil.which("docker") is not None
        self._docker_api_base = os.getenv("YUXI_DOCKER_API_BASE", "http://localhost").rstrip("/")
        self._docker_api_socket = os.getenv("YUXI_DOCKER_API_SOCKET", "/var/run/docker.sock")

    @property
    def id(self) -> str:
        """返回沙盒唯一标识"""
        return self._sandbox_key

    def _normalize_path(self, path: str, *, allow_root: bool = False) -> str:
        return normalize_virtual_path(path, self._thread_id, allow_root=allow_root)

    def _mask_output(self, output: str) -> str:
        mappings: list[tuple[str, str]] = [
            (str(self._host_user_data_dir.resolve()), USER_DATA_PATH),
        ]
        if self._skills_host_path:
            mappings.append((str(self._skills_host_path.resolve()), SKILLS_PATH))
        return mask_host_paths(output, mappings)

    def _docker_exec(self, command: str, timeout: int | None = None) -> ExecuteResponse:
        """通过 docker exec 执行命令

        Args:
            command: 要执行的命令
            timeout: 超时时间（秒）

        Returns:
            ExecuteResponse
        """
        if not self._has_docker_cli:
            return self._docker_exec_via_api(command, timeout=timeout)

        cmd = ["docker", "exec", self._container_name, "sh", "-c", command]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return ExecuteResponse(
                output=self._mask_output(result.stdout + result.stderr),
                exit_code=result.returncode,
                truncated=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output="Command timed out",
                exit_code=-1,
                truncated=False,
            )
        except Exception as e:
            logger.error(f"Error executing command in sandbox: {e}")
            return ExecuteResponse(
                output=str(e),
                exit_code=-1,
                truncated=False,
            )

    def _docker_api_call(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        url = f"{self._docker_api_base}{path}"
        cmd = [
            "curl",
            "-sS",
            "--unix-socket",
            self._docker_api_socket,
            "-X",
            method,
            url,
        ]
        if payload is not None:
            cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="ignore")
        stderr = (result.stderr or b"").decode("utf-8", errors="ignore")
        return result.returncode, stdout, stderr

    def _docker_exec_via_api(self, command: str, timeout: int | None = None) -> ExecuteResponse:
        container_name = urllib.parse.quote(self._container_name)
        rc, out, err = self._docker_api_call(
            "POST",
            f"/containers/{container_name}/exec",
            {
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": True,
                "Cmd": ["sh", "-c", command],
            },
            timeout=timeout,
        )
        if rc != 0:
            return ExecuteResponse(output=self._mask_output(err or out), exit_code=1, truncated=False)

        try:
            exec_id = json.loads(out).get("Id")
        except json.JSONDecodeError:
            exec_id = None

        if not exec_id:
            return ExecuteResponse(output=self._mask_output(out), exit_code=1, truncated=False)

        exec_q = urllib.parse.quote(str(exec_id))
        rc2, out2, err2 = self._docker_api_call(
            "POST",
            f"/exec/{exec_q}/start",
            {"Detach": False, "Tty": True},
            timeout=timeout,
        )
        if rc2 != 0:
            return ExecuteResponse(output=self._mask_output(err2 or out2), exit_code=1, truncated=False)

        rc3, out3, err3 = self._docker_api_call("GET", f"/exec/{exec_q}/json", timeout=timeout)
        if rc3 != 0:
            return ExecuteResponse(output=self._mask_output((out2 + err2 + err3).strip()), exit_code=1, truncated=False)

        exit_code = 0
        try:
            exit_code = int((json.loads(out3) or {}).get("ExitCode", 0))
        except Exception:
            exit_code = 1

        return ExecuteResponse(output=self._mask_output((out2 + err2).strip()), exit_code=exit_code, truncated=False)

    def _download_file_via_exec(self, container_path: str) -> FileDownloadResponse:
        encoded_path = base64.b64encode(container_path.encode("utf-8")).decode("ascii")
        command = f"""python3 -c "
import base64
import json
import os
from pathlib import Path

path = base64.b64decode('{encoded_path}').decode('utf-8')
target = Path(path)

if not target.exists():
    print(json.dumps({{'error': 'file_not_found'}}))
elif target.is_dir():
    print(json.dumps({{'error': 'is_directory'}}))
else:
    print(json.dumps({{'content': base64.b64encode(target.read_bytes()).decode('ascii')}}))
" """
        result = self._docker_exec(command, timeout=30)
        if result.exit_code != 0:
            return FileDownloadResponse(path=container_path, content=None, error="permission_denied")

        try:
            payload = json.loads(result.output.strip())
        except json.JSONDecodeError:
            return FileDownloadResponse(path=container_path, content=None, error="invalid_path")

        error = payload.get("error")
        if error:
            return FileDownloadResponse(path=container_path, content=None, error=str(error))

        encoded_content = payload.get("content")
        if not isinstance(encoded_content, str):
            return FileDownloadResponse(path=container_path, content=None, error="invalid_path")

        try:
            content = base64.b64decode(encoded_content)
        except Exception:
            return FileDownloadResponse(path=container_path, content=None, error="invalid_path")

        return FileDownloadResponse(path=container_path, content=content, error=None)

    def execute(self, command: str, *, timeout: int = 60) -> ExecuteResponse:
        """执行命令

        Args:
            command: 要执行的 shell 命令
            timeout: 超时时间（秒），默认 60 秒

        Returns:
            ExecuteResponse
        """
        try:
            validate_execute_command_paths(command)
        except PermissionError as e:
            return ExecuteResponse(output=str(e), exit_code=1, truncated=False)

        return self._docker_exec(command, timeout=timeout)

    def _coerce_file_info(self, data: Any) -> FileInfo | None:
        if isinstance(data, dict):
            path = data.get("path")
            is_dir = bool(data.get("is_dir", False))
            size = int(data.get("size", 0) or 0)
            modified_at = str(data.get("modified_at", "") or "")
        else:
            path = getattr(data, "path", None)
            is_dir = bool(getattr(data, "is_dir", False))
            size = int(getattr(data, "size", 0) or 0)
            modified_at = str(getattr(data, "modified_at", "") or "")

        if not isinstance(path, str) or not path:
            return None

        return FileInfo(
            path=path,
            is_dir=is_dir,
            size=size,
            modified_at=modified_at,
        )

    def _scan_dir_info(self, path: str) -> list[FileInfo]:
        path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
        cmd = f"""python3 -c "
import json
import os
import base64
from datetime import datetime, UTC

path = base64.b64decode('{path_b64}').decode('utf-8')

try:
    with os.scandir(path) as it:
        for entry in it:
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                stat = None

            result = {{
                'path': os.path.join(path, entry.name),
                'is_dir': entry.is_dir(follow_symlinks=False),
                'size': int(stat.st_size) if stat else 0,
                'modified_at': datetime.fromtimestamp(stat.st_mtime, UTC).isoformat() if stat else '',
            }}
            print(json.dumps(result))
except (FileNotFoundError, NotADirectoryError, PermissionError):
    pass
" 2>/dev/null"""

        result = self.execute(cmd)
        infos: list[FileInfo] = []
        for line in result.output.strip().splitlines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            info = self._coerce_file_info(data)
            if info is not None:
                infos.append(info)
        return infos

    def ls_info(self, path: str) -> list[FileInfo]:
        normalized = self._normalize_path(path, allow_root=True)

        # 根目录仅暴露虚拟命名空间
        if normalized == "/":
            return [
                FileInfo(path=f"{USER_DATA_PATH}/", is_dir=True, size=0, modified_at=""),
            ]

        if normalized in {USER_DATA_PATH, f"{USER_DATA_PATH}/"}:
            return self._scan_dir_info(USER_DATA_PATH)

        return self._scan_dir_info(normalized)

    def glob_info(self, pattern: str, path: str = "/") -> list[dict[str, Any]]:
        normalized = self._normalize_path(path, allow_root=True)
        start_path = normalized if normalized != "/" else USER_DATA_PATH

        def _as_dict(item: Any) -> dict[str, Any] | None:
            if isinstance(item, dict):
                raw = dict(item)
            else:
                raw = {
                    "path": getattr(item, "path", ""),
                    "is_dir": bool(getattr(item, "is_dir", False)),
                    "size": int(getattr(item, "size", 0) or 0),
                    "modified_at": getattr(item, "modified_at", "") or "",
                }

            p = raw.get("path")
            if not isinstance(p, str) or not p:
                return None

            raw.setdefault("is_dir", False)
            raw.setdefault("size", 0)
            raw.setdefault("modified_at", "")
            raw.setdefault("name", PurePosixPath(p.rstrip("/")).name)
            return raw

        def _matches(path_value: str, base_path: str) -> bool:
            normalized_base = base_path.rstrip("/")
            if normalized_base and path_value.startswith(f"{normalized_base}/"):
                rel = path_value[len(normalized_base) + 1 :]
            else:
                rel = path_value.lstrip("/")

            rel_path = PurePosixPath(rel)
            abs_path = PurePosixPath(path_value.lstrip("/"))
            name = rel_path.name

            return bool(
                rel_path.match(pattern)
                or abs_path.match(pattern)
                or fnmatch(name, pattern)
                or fnmatch(path_value, pattern)
            )

        matched: list[dict[str, Any]] = []
        visited: set[str] = set()
        queue: list[str] = [start_path]

        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)

            try:
                infos = self._scan_dir_info(current)
            except Exception:
                continue

            for info in infos or []:
                item = _as_dict(info)
                if item is None:
                    continue
                item_path = item["path"]
                if _matches(item_path, start_path):
                    matched.append(item)

                if bool(item.get("is_dir")):
                    queue.append(item_path.rstrip("/"))

        return matched

    async def aglob_info(self, pattern: str, path: str = "/") -> list[dict[str, Any]]:
        return self.glob_info(pattern, path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        normalized = self._normalize_path(file_path)
        return super().read(normalized, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        normalized = self._normalize_path(file_path)
        if is_skills_path(normalized):
            return WriteResult(error="Cannot write to read-only skills path")
        return super().write(normalized, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        normalized = self._normalize_path(file_path)
        if is_skills_path(normalized):
            return EditResult(error="Cannot edit read-only skills path")
        return super().edit(normalized, old_string, new_string, replace_all=replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到沙盒

        Args:
            files: (路径, 内容) 元组列表

        Returns:
            FileUploadResponse 列表
        """
        responses = []
        for file_path, content in files:
            try:
                container_path = self._normalize_path(file_path)
                if is_skills_path(container_path):
                    responses.append(FileUploadResponse(path=file_path, error="permission_denied"))
                    continue

                parent_dir = ospath.dirname(container_path)
                mkdir_result = self._docker_exec(f"mkdir -p {parent_dir}", timeout=30)
                if mkdir_result.exit_code != 0:
                    responses.append(FileUploadResponse(path=file_path, error="permission_denied"))
                    continue

                # 创建临时文件
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_path).name) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    # docker cp 到容器
                    subprocess.run(
                        ["docker", "cp", tmp_path, f"{self._container_name}:{container_path}"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    logger.debug(f"Uploaded {file_path} to {container_path}")
                    responses.append(FileUploadResponse(path=file_path, error=None))
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to upload {file_path}: {e.stderr}")
                responses.append(FileUploadResponse(path=file_path, error="permission_denied"))
            except Exception as e:
                logger.error(f"Failed to upload {file_path}: {e}")
                responses.append(FileUploadResponse(path=file_path, error="invalid_path"))

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙盒下载文件

        Args:
            paths: 要下载的文件路径列表

        Returns:
            FileDownloadResponse 列表
        """
        responses = []

        for path in paths:
            try:
                container_path = self._normalize_path(path)

                if not self._has_docker_cli:
                    response = self._download_file_via_exec(container_path)
                    responses.append(FileDownloadResponse(path=path, content=response.content, error=response.error))
                    continue

                # 创建临时目录
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir) / Path(container_path).name

                    # docker cp 从容器复制
                    subprocess.run(
                        ["docker", "cp", f"{self._container_name}:{container_path}", str(tmpdir)],
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                    if tmp_path.exists():
                        content = tmp_path.read_bytes()
                        responses.append(FileDownloadResponse(path=path, content=content, error=None))
                    else:
                        responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))

            except subprocess.CalledProcessError as e:
                if "No such file or directory" in e.stderr or "not exist" in e.stderr.lower():
                    responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
                else:
                    logger.error(f"Failed to download {path}: {e.stderr}")
                    responses.append(FileDownloadResponse(path=path, content=None, error="permission_denied"))
            except Exception as e:
                logger.error(f"Failed to download {path}: {e}")
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))

        return responses
