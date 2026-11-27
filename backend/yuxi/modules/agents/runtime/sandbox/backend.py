"""Workspace 本地文件与 Cube 普通用户执行组成的 Agent 工具后端。"""

import asyncio
import base64
import json
import os
import shlex
import threading
from pathlib import PurePosixPath

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    PERMISSION_DENIED,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
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

from yuxi.infrastructure.filesystem import await_io
from yuxi.modules.agents.runtime.sandbox.provider import get_sandbox_provider
from yuxi.modules.workspace.runtime_files import MAX_FILE_BYTES, RuntimeFiles
from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH, normalize_runtime_path, runtime_workdir_path

_IMAGE_EXTENSIONS = {".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp"}
_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}
_DOCUMENT_READ_ERROR = "read_file does not support PDF or Office documents. Use ocr_parse_file to convert the file to Markdown first."
_BINARY_READ_ERROR = "read_file only supports UTF-8 text and image files. This file type is not supported."
# 搜索内容匹配的单文件预算；本地扫描与 VM Guest 脚本共用同一上限。
_SEARCH_MAX_FILE_BYTES = 10 * 1024 * 1024

# VM Home 文件操作的 Guest 脚本，调用处在其前拼接 params 字面量。以模块常量持有并
# 接受编译断言，脚本编辑引入的语法缺陷在测试层暴露而不是运行时让 ls/read/write 全部失效。
_GUEST_FILE_SCRIPT = """import os,sys,json,stat,base64
try:
    path=params['path']
    if params['operation']=='list':
        result=[]
        with os.scandir(path) as entries:
            for entry in entries:
                info=entry.stat()
                result.append({'path':os.path.join(path,entry.name),'is_dir':stat.S_ISDIR(info.st_mode),'size':info.st_size,'modified_at':str(info.st_mtime)})
                if len(result)>10000: raise ValueError('directory entries exceed limit')
    else:
        writing=params['operation']=='write'
        if writing: os.makedirs(os.path.dirname(path),exist_ok=True)
        flags=os.O_NONBLOCK | (os.O_WRONLY|os.O_CREAT if writing else os.O_RDONLY)
        if writing and not params['overwrite']: flags |= os.O_EXCL
        fd=os.open(path,flags,0o600)
        with os.fdopen(fd,'wb' if writing else 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode): raise ValueError('only regular files supported')
            if writing:
                content=sys.stdin.buffer.read(params['size'])
                if len(content)!=params['size']: raise ValueError('incomplete file input')
                if params['overwrite']: file.truncate(0)
                file.write(content)
                result=True
            else:
                content=file.read(params['max_bytes']+1)
                if len(content)>params['max_bytes']: raise ValueError('file exceeds transfer limit')
                result=base64.b64encode(content).decode()
    print(json.dumps({'result':result}))
except Exception as error:
    print(json.dumps({'error':type(error).__name__}))
"""

# VM 侧 glob/grep 的 Guest 搜索脚本，搜索参数经 SEARCH_* 环境变量传入。
_GUEST_SEARCH_SCRIPT = """import os,json,fnmatch
from pathlib import Path
root=Path(os.environ["SEARCH_ROOT"])
pattern=os.environ["SEARCH_PATTERN"]
text=json.loads(os.environ["SEARCH_TEXT"])
limit=int(os.environ["SEARCH_LIMIT"])
result=[]
scanned=0
paths=[root] if root.is_file() else root.rglob("*")
for p in paths:
    scanned+=1
    if scanned>10000: raise ValueError("file search entries exceed limit")
    if p.is_symlink(): continue
    relative=str(p.relative_to(root)) if p!=root else p.name
    if not (fnmatch.fnmatch(relative,pattern) or p.match(pattern)): continue
    if text is None:
        result.append({"path":str(p),"is_dir":p.is_dir(),"size":p.stat().st_size})
    elif p.is_file():
        if p.stat().st_size>int(os.environ["SEARCH_MAX_FILE_BYTES"]): continue
        try: content=p.read_text()
        except (UnicodeError,PermissionError): continue
        for n,line in enumerate(content.splitlines(),1):
            if text in line:
                result.append({"path":str(p),"line":n,"text":line})
                if len(result)>limit: break
    if len(result)>limit: break
print(json.dumps({"matches":result[:limit],"truncated":len(result)>limit}))
"""


def create_agent_composite_backend(context) -> CompositeBackend:
    """集中装配现有工具协议和当前 Project 的产物根。"""
    if not context.workdir_relative_path:
        raise ValueError("workdir_relative_path is required")
    workdir = runtime_workdir_path(context.workdir_relative_path)
    return CompositeBackend(
        default=UserSandboxBackend(uid=context.uid, workdir_path=context.workdir_relative_path),
        routes={},
        artifacts_root=f"{workdir}/outputs",
    )


class UserSandboxBackend(BaseSandbox):
    """文件按挂载边界分流，所有命令使用同用户 VM。"""

    def __init__(self, *, uid: str, workdir_path: str | None = None):
        """用户拥有环境，Project Workdir 决定命令 cwd 和目录内写入的根。"""
        if not str(uid).strip():
            raise ValueError("uid is required")
        self._uid = str(uid)
        self._workdir_path = workdir_path
        self._files = RuntimeFiles(self._uid, workdir_path=workdir_path)
        self._provider = get_sandbox_provider()
        self._command_timeout_seconds = int(os.environ.get("SANDBOX_EXEC_TIMEOUT_SECONDS", "180"))

    @property
    def id(self) -> str:
        """读取 Backend 身份不创建或唤醒环境；container 执行环境按 Run 创建，身份仍固定为用户。"""
        return self._uid

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """保持文本分页与图片协议；挂载内本地读取，VM Home 以普通账号经执行节点读取。"""
        try:
            path = normalize_runtime_path(file_path)
            if limit is not None and limit <= 0:
                return ReadResult(no_lines_requested=True)
            image = PurePosixPath(path).suffix.lower() in _IMAGE_EXTENSIONS
            content = self._read_bytes(path, MAX_BINARY_BYTES if image else MAX_FILE_BYTES)
            if image:
                return ReadResult(file_data={"content": base64.b64encode(content).decode(), "encoding": "base64"})
            if PurePosixPath(path).suffix.lower() in _DOCUMENT_EXTENSIONS:
                return ReadResult(error=_DOCUMENT_READ_ERROR)
            if _get_file_type(path) != "text" or b"\0" in content:
                return ReadResult(error=_BINARY_READ_ERROR)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                return ReadResult(error=_BINARY_READ_ERROR)
            lines = text.splitlines(keepends=True)
            start = max(0, offset)
            window = lines[start:] if limit is None else lines[start : start + limit]
            if not window:
                return ReadResult(file_data={"content": "", "encoding": "utf-8"})
            end = start + len(window)
            return ReadResult(
                file_data={"content": "".join(window), "encoding": "utf-8"}, start_line=start + 1, end_line=end, next_offset=end
            )
        except Exception as exc:
            return ReadResult(error=self._file_error(file_path, exc))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """文件 I/O 收尾完成后才传播协程取消。"""
        return await await_io(asyncio.to_thread(self.read, file_path, offset, limit))

    def write(self, file_path: str, content: str) -> WriteResult:
        """整体写入文本文件，已存在文件按工具契约覆盖；共享 Skills 的写入直接拒绝。"""
        try:
            path = normalize_runtime_path(file_path)
            if not isinstance(content, str):
                raise ValueError("write() only supports text content")
            self._write_bytes(path, content.encode(), overwrite=True)
            return WriteResult(path=path)
        except Exception as exc:
            return WriteResult(error=self._file_error(file_path, exc))

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """普通 UTF-8 文件替换不增加文件写入串行锁。"""
        try:
            path = normalize_runtime_path(file_path)
            self._check_writable(path)
            content = self._read_bytes(path, MAX_FILE_BYTES).decode("utf-8")
            count = content.count(old_string)
            if count == 0:
                return EditResult(error=f"String not found in file: '{old_string}'")
            if count > 1 and not replace_all:
                return EditResult(error="String appears multiple times. Use replace_all=True to replace all occurrences.")
            self._write_bytes(path, content.replace(old_string, new_string, -1 if replace_all else 1).encode(), overwrite=True)
            return EditResult(path=path, occurrences=count if replace_all else 1)
        except Exception as exc:
            return EditResult(error=self._file_error(file_path, exc))

    def ls(self, path: str) -> LsResult:
        """本地挂载与 VM 普通账号目录共用 FileInfo 协议。"""
        try:
            path = normalize_runtime_path(path)
            if self._files.route(path):
                return LsResult(entries=self._files.list(path))
            return LsResult(entries=self._guest_io("list", path))
        except Exception as exc:
            return LsResult(error=self._file_error(path, exc))

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        """本地目录有界搜索，其他目录以普通账号在 VM 搜索。"""
        try:
            path = normalize_runtime_path(path)
            if self._files.route(path):
                relative_pattern = pattern.lstrip("/")
                prefix = path.strip("/") + "/"
                if relative_pattern.startswith(prefix):
                    relative_pattern = relative_pattern[len(prefix) :]
                return GlobResult(matches=self._files.walk(path, relative_pattern))
            result = self._guest_search(path, pattern=pattern)
            return GlobResult(**result)
        except Exception as exc:
            return GlobResult(error=self._file_error(path, exc))

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        """搜索普通文本文件，单文件超过搜索预算时与 VM 侧一致跳过。"""
        try:
            max_count = 1000 if max_count is None else max_count
            if max_count <= 0:
                return GrepResult(error="max_count must be positive")
            path = normalize_runtime_path(path or WORKSPACE_PATH)
            if self._files.route(path):
                try:
                    entries = self._files.walk(path, glob or "*")
                except NotADirectoryError:
                    # 单文件路径直接搜索同样按预算跳过，与目录扫描和 VM 侧一致。
                    info = self._files.workspace.stat_authorized_path(self._files.scope(path), root="/")
                    entries = [{"path": path, "is_dir": False, "size": info["size"]}]
                matches = []
                for entry in entries:
                    if entry["is_dir"]:
                        continue
                    if entry.get("size", 0) > _SEARCH_MAX_FILE_BYTES:
                        continue
                    raw = self._files.read(entry["path"], MAX_FILE_BYTES)
                    if b"\0" in raw:
                        continue
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    for number, line in enumerate(text.splitlines(), 1):
                        if pattern in line:
                            if len(matches) == max_count:
                                return GrepResult(matches=matches, truncated=True)
                            matches.append(GrepMatch(path=entry["path"], line=number, text=line))
                return GrepResult(matches=matches)
            return GrepResult(**self._guest_search(path, pattern=glob or "*", text=pattern, max_count=max_count))
        except Exception as exc:
            return GrepResult(error=self._file_error(path, exc))

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """同步工具调用也登记可恢复命令句柄。"""
        return self._execute(command, timeout, threading.Event())

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """协程取消通知远端命令，并等待线程完成清理。"""
        cancelled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self._execute, command, timeout, cancelled))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.exception()
            raise

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """批量上传保留逐项结果，大小与路径校验失败如实回传原因。"""
        results = []
        for path, content in files:
            try:
                self._write_bytes(normalize_runtime_path(path), content, overwrite=True)
                results.append(FileUploadResponse(path=path, error=None))
            except FileNotFoundError:
                results.append(FileUploadResponse(path=path, error=FILE_NOT_FOUND))
            except ValueError as exc:
                results.append(FileUploadResponse(path=path, error=str(exc) or PERMISSION_DENIED))
            except Exception:
                results.append(FileUploadResponse(path=path, error=PERMISSION_DENIED))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载有界原始字节，缺失与超限不折叠成权限语义。"""
        results = []
        for path in paths:
            try:
                results.append(
                    FileDownloadResponse(path=path, content=self._read_bytes(normalize_runtime_path(path), MAX_FILE_BYTES), error=None)
                )
            except FileNotFoundError:
                results.append(FileDownloadResponse(path=path, content=None, error=FILE_NOT_FOUND))
            except ValueError as exc:
                results.append(FileDownloadResponse(path=path, content=None, error=str(exc) or PERMISSION_DENIED))
            except Exception:
                results.append(FileDownloadResponse(path=path, content=None, error=PERMISSION_DENIED))
        return results

    def regular_file_exists(self, path: str) -> bool:
        """成果只接受共享 Workspace 的普通文件，不唤醒 VM。"""
        try:
            scope = self._files.scope(path)
            info = self._files.workspace.stat_authorized_path(scope, root="/")
            return not info["is_dir"]
        except (OSError, ValueError):
            return False

    def upload_authorized_file_from_path(self, path: str, source_path: str) -> None:
        """受信服务的附件只发布到用户 Workspace。"""
        self._files.upload_from_path(path, source_path)

    def download_authorized_file_to_path(self, path: str, target_path: str, max_bytes: int) -> int:
        """受信服务下载不扩大到 VM Home 或平台宿主路径。"""
        return self._files.workspace.download_authorized_file_to_path(self._files.scope(path), target_path, max_bytes)

    def list_authorized_directory(self, path: str, *, root: str) -> list[dict]:
        """复用 Workspace 的公开目录边界。"""
        return self._files.workspace.list_authorized_directory(self._files.scope(path), root=self._files.scope(root))

    def create_authorized_directory(self, parent_path: str, name: str, *, root: str) -> dict:
        """复用 Workspace 的公开目录创建。"""
        return self._files.workspace.create_authorized_directory(self._files.scope(parent_path), name, root=self._files.scope(root))

    def delete_authorized_path(self, path: str, *, root: str) -> None:
        """受信 UI 删除只针对已授权 Workspace。"""
        self._files.workspace.delete_authorized_path(self._files.scope(path), root=self._files.scope(root))

    def delete(self, file_path: str) -> DeleteResult:
        """删除与写入同界：共享 Skill 投影只读；其余路径交上游在用户 VM 内删除。"""
        try:
            path = normalize_runtime_path(file_path)
            self._check_writable(path)
        except Exception as exc:
            return DeleteResult(error=self._file_error(file_path, exc))
        return super().delete(path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """文件线程收尾后才传播取消。"""
        return await await_io(asyncio.to_thread(self.write, file_path, content))

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """编辑取消不会遗留未完成宿主写入。"""
        return await await_io(asyncio.to_thread(self.edit, file_path, old_string, new_string, replace_all))

    async def als(self, path: str) -> LsResult:
        """目录查询保留实际 I/O 生命周期。"""
        return await await_io(asyncio.to_thread(self.ls, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        """搜索取消时等待远端或宿主扫描收尾。"""
        return await await_io(asyncio.to_thread(self.glob, pattern, path))

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        """搜索保持结构化结果。"""
        return await await_io(asyncio.to_thread(self.grep, pattern, path, glob, max_count=max_count))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传取消前完成本次文件 I/O。"""
        return await await_io(asyncio.to_thread(self.upload_files, files))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """下载取消前关闭远端文件读取。"""
        return await await_io(asyncio.to_thread(self.download_files, paths))

    def _execute(self, command: str, timeout: int | None, cancelled: threading.Event) -> ExecuteResponse:
        """每条命令有独立 cwd，协议错误以工具失败返回。"""
        try:
            if not isinstance(command, str):
                raise ValueError("command must be text")
            seconds = timeout if timeout is not None else self._command_timeout_seconds
            if seconds <= 0:
                raise ValueError("timeout must be positive")
            cwd = runtime_workdir_path(self._workdir_path) if self._workdir_path else WORKSPACE_PATH
            output, exit_code, truncated = self._provider.execute(
                self._uid,
                command,
                cwd,
                seconds,
                cancelled,
                workdir=self._workdir_path,
            )
            return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)
        except Exception as exc:
            message = str(exc) if str(exc) else "执行失败，请核对用户环境"
            return ExecuteResponse(output=f"Error: {type(exc).__name__}: {message}", exit_code=1, truncated=False)

    def _read_bytes(self, path: str, max_bytes: int) -> bytes:
        """路径路由失败时不回退，VM 调用固定普通账号。"""
        if self._files.route(path):
            return self._files.read(path, max_bytes)
        return base64.b64decode(self._guest_io("read", path, max_bytes=max_bytes), validate=True)

    def _write_bytes(self, path: str, data: bytes, *, overwrite: bool) -> None:
        """共享文件本地写入，Home 文件使用普通账号接口。"""
        self._check_writable(path)
        if self._files.route(path):
            self._files.write(path, data, overwrite=overwrite)
            return
        self._guest_io("write", path, data=data, overwrite=overwrite)

    def _guest_io(self, operation: str, path: str, *, data: bytes | None = None, overwrite: bool = False, max_bytes: int = MAX_FILE_BYTES):
        """VM 文件操作以普通账号的追踪进程执行，权限由内核拥有。"""
        if data is not None and len(data) > MAX_FILE_BYTES:
            raise ValueError("file exceeds transfer limit")
        parameters = {"operation": operation, "path": path, "overwrite": overwrite, "max_bytes": max_bytes, "size": len(data or b"")}
        script = "params=" + repr(parameters) + "\n" + _GUEST_FILE_SCRIPT
        output, exit_code, truncated = self._provider.execute(
            self._uid,
            script,
            "/home/user",
            60,
            stdin_data=data,
            output_budget=((max_bytes + 2) // 3 * 4 + 1024) if operation == "read" else 4 * 1024 * 1024,
            python=True,
            workdir=self._workdir_path,
        )
        if exit_code != 0 or truncated:
            raise OSError("Guest file operation failed or exceeded output budget")
        payload = json.loads(output)
        if payload.get("error"):
            errors = {
                "FileNotFoundError": FileNotFoundError,
                "FileExistsError": FileExistsError,
                "PermissionError": PermissionError,
                "IsADirectoryError": IsADirectoryError,
            }
            raise errors.get(payload["error"], OSError)(path)
        return payload["result"]

    def _check_writable(self, path: str) -> None:
        """共享 Skill 投影即使 VM 未创建也不能写入。"""
        route = self._files.route(path)
        if route and route[3]:
            raise PermissionError("shared Skills are read-only")

    def _guest_search(self, path: str, *, pattern: str, text: str | None = None, max_count: int | None = None) -> dict:
        """Guest 搜索作为可取消命令执行，返回有界结构化结果。"""
        envs = {
            "SEARCH_ROOT": path,
            "SEARCH_PATTERN": pattern,
            "SEARCH_TEXT": json.dumps(text),
            "SEARCH_LIMIT": str(1000 if max_count is None else max_count),
            "SEARCH_MAX_FILE_BYTES": str(_SEARCH_MAX_FILE_BYTES),
        }
        prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in envs.items())
        response = self.execute(prefix + " python3 -c " + shlex.quote(_GUEST_SEARCH_SCRIPT))
        if response.exit_code != 0 or response.truncated:
            raise RuntimeError("Guest file search failed or exceeded output budget")
        return json.loads(response.output)

    @staticmethod
    def _file_error(path: str, exc: Exception) -> str:
        """错误不暴露 SDK 请求或控制凭据。"""
        if isinstance(exc, FileNotFoundError):
            return f"File '{path}' not found"
        if isinstance(exc, FileExistsError):
            return f"File '{path}' already exists"
        if isinstance(exc, IsADirectoryError):
            return f"Path '{path}' is a directory"
        if isinstance(exc, PermissionError):
            return f"Access denied for '{path}'"
        return f"Failed to access '{path}': {type(exc).__name__}"
