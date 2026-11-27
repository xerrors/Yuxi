from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import os
import re
import subprocess
import unicodedata
import uuid
from collections.abc import Collection, Iterator
from datetime import datetime
from itertools import islice
from pathlib import Path, PurePosixPath

from yuxi.infrastructure.filesystem import open_directory_fd
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_user_data_dir
from yuxi.modules.workspace.usage_changes import workspace_mutation
from yuxi.shared.datetime import ensure_shanghai, shanghai_now

WORKSPACE_DIR_NAME = "workspace"
WORKSPACE_AGENTS_DIR_NAME = "agents"
WORKSPACE_AGENT_CONTEXT_FILES = {
    "AGENTS.md": "# AGENTS\n\n以下是约束 Agent 行为的一些要求\n",
    "USER.md": "# USER\n\n以下是有关用户的一些信息\n",
    "MEMORY.md": "# MEMORY\n\n以下是 Agent 需要记住的一些信息\n",
}

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TASK_WORKDIR_ROOT = (WORKSPACE_AGENTS_DIR_NAME, "tasks")
_TASK_WORKDIR_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TASK_WORKDIR_WORD_LIMIT = 4
_TASK_WORKDIR_PREFIX_LIMIT = 40
# 前缀词数由分配器与校验器共用同一常量，避免生成器产出被自身拒收的名称。
# 非贪婪量词优先让短标识尽早匹配，长前缀后的十六进制词才不会被并进前缀。
_TASK_WORKDIR_NAME_RE = re.compile(
    rf"(?P<prefix>[a-z0-9]+(?:-[a-z0-9]+){{0,{_TASK_WORKDIR_WORD_LIMIT - 1}}}?)-[0-9a-f]{{6}}(?:-[1-9][0-9]*)?"
)

# 不可信路径的校验与规范化：纯字符串判定，不访问文件系统。


def normalize_workspace_scope(path: str) -> str:
    """规范化用户文件 scope，拒绝跳转、URL、反斜杠与 NUL。"""
    raw = str(path or "").strip()
    pure = PurePosixPath(raw)
    if not raw or not pure.is_absolute() or ".." in pure.parts or "\\" in raw or "://" in raw or "\0" in raw:
        raise ValueError("invalid Workspace scope path")
    return pure.as_posix()


def validate_directory_name(name: str) -> str:
    """目录创建只接受单个普通 POSIX 组件。"""
    if not name or name in {".", ".."} or any(char in name for char in ("/", "\\", "\0")):
        raise ValueError("directory name must be one path component")
    return name


def validate_thread_id(thread_id: str) -> str:
    """thread_id 只接受非空且仅含安全字符的标识。"""
    value = str(thread_id or "").strip()
    if not value:
        raise ValueError("thread_id is required")
    if not _SAFE_ID_RE.match(value):
        raise ValueError("thread_id contains invalid characters")
    return value


def normalize_workdir_path(workdir_path: str) -> str:
    """规范化数据库中的 UserWorkspace 相对 Workdir 路径。"""
    raw = str(workdir_path or "").strip()
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or "\\" in raw or "://" in raw:
        raise ValueError("workdir_path must be a relative POSIX path")
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        raise ValueError("workdir_path contains invalid path components")
    return pure.as_posix()


def normalize_managed_workdir_path(workdir_path: str) -> str:
    """校验新建任务目录的路径形态与真实日期。"""
    try:
        pure = PurePosixPath(normalize_workdir_path(workdir_path))
        depth_ok = len(pure.parts) == 4 and pure.parts[:2] == _TASK_WORKDIR_ROOT
        if not depth_ok:
            raise ValueError
        date, name = pure.parts[2:]
        match = _TASK_WORKDIR_NAME_RE.fullmatch(name)
        if not match or len(match.group("prefix")) > _TASK_WORKDIR_PREFIX_LIMIT or not _TASK_WORKDIR_DATE_RE.fullmatch(date):
            raise ValueError
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("managed workdir_path must use a supported managed directory layout") from None
    return pure.as_posix()


# 逻辑 UID 到宿主目录名的映射与用户路径解析。


def workspace_uid_dirname(uid: str) -> str:
    """Return a path-safe, stable workspace directory name for a logical UID.

    Database and OIDC subject identifiers may contain characters such as ``:``
    that are valid identity data but unsafe in filesystem path components.
    Identity-safe UIDs keep a readable directory name; all other values use a
    namespaced SHA-256 digest at the filesystem boundary only.
    """
    value = str(uid or "").strip()
    if not value:
        raise ValueError("uid is required")
    if _SAFE_ID_RE.fullmatch(value):
        return value
    return f"uid-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def global_user_data_dir(uid: str) -> Path:
    """Return the shared host-side directory used for one user's workspace files."""
    safe_uid = workspace_uid_dirname(uid)
    return get_user_data_dir() / "shared" / safe_uid


def user_workspace_dir(uid: str) -> Path:
    """返回用户级实时 Workspace 根。"""
    return global_user_data_dir(uid) / WORKSPACE_DIR_NAME


async def measure_allocated_bytes(uid: str) -> int:
    """从固定目录 fd 统计已分配块，硬链接计一次，忽略其他文件系统。"""
    try:
        fd = _open_user_workspace_fd(uid)
    except FileNotFoundError:
        # 数据根缺失是扫描失败，只有用户目录尚未创建才记为零。
        get_user_data_dir().stat()
        return 0
    try:
        command = ("du", "-sx", "--block-size=1", "--", f"/proc/self/fd/{fd}/.")
        process = await asyncio.create_subprocess_exec(
            *command,
            pass_fds=(fd,),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(240):
                output, _ = await process.communicate()
        except (asyncio.CancelledError, TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        return int(output.split(b"\t", 1)[0])
    finally:
        os.close(fd)


def user_workdir_host_dir(uid: str, workdir_path: str) -> Path:
    """解析当前用户 Workdir，拒绝任意 symlink 路径组件。"""
    parts = PurePosixPath(normalize_workdir_path(workdir_path)).parts
    _open_workspace_directory(uid, parts)
    target = user_workspace_dir(uid).joinpath(*parts)
    if not target.is_dir():
        raise ValueError("workdir_path does not reference an existing directory")
    return target


# Workspace 生命周期：创建默认结构、分配 Workdir 名称、物化数据库绑定。


def ensure_user_workspace(uid: str) -> None:
    """创建用户级 Workspace 与默认 Agent 上下文文件。"""
    try:
        workspace_fd = _open_user_workspace_fd(uid)
    except FileNotFoundError:
        with workspace_mutation(uid):
            workspace_fd = _open_user_workspace_fd(uid, create=True)
            try:
                _ensure_workspace_default_files_fd(uid, workspace_fd)
            finally:
                os.close(workspace_fd)
        return

    try:
        _ensure_workspace_default_files_fd(uid, workspace_fd)
    finally:
        os.close(workspace_fd)


def allocate_default_user_workdir_path(
    uid: str,
    project_id: str | uuid.UUID,
    *,
    naming_text: str | None = None,
    allocated_at: datetime | None = None,
    reserved_paths: Collection[str] = (),
) -> str:
    """按上海日期、首条用户文本和六位 Project 标识分配任务目录。"""
    short_id = uuid.UUID(str(project_id)).hex[:6]
    date = ensure_shanghai(allocated_at or shanghai_now()).date().isoformat()
    parent = PurePosixPath(*_TASK_WORKDIR_ROOT, date)
    base_name = f"{_task_workdir_prefix(naming_text or '')}-{short_id}"

    suffix = 0
    while True:
        name = base_name if suffix == 0 else f"{base_name}-{suffix}"
        relative_path = (parent / name).as_posix()
        if relative_path not in reserved_paths and not _user_workspace_entry_exists(uid, relative_path):
            return relative_path
        suffix += 1


def _task_workdir_prefix(text: str) -> str:
    """含汉字时使用默认前缀，其余只保留前四个 ASCII 字母数字词。"""
    han_names = ("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-", "IDEOGRAPHIC NUMBER ZERO")
    if any(unicodedata.name(char, "").startswith(han_names) for char in text if not char.isascii()):
        return "new-chat"

    matches = islice(re.finditer(r"[A-Za-z0-9]+", text), _TASK_WORKDIR_WORD_LIMIT)
    prefix = "-".join(match.group().lower() for match in matches)
    return prefix[:_TASK_WORKDIR_PREFIX_LIMIT].rstrip("-") or "new-chat"


def _user_workspace_entry_exists(uid: str, workdir_path: str) -> bool:
    """以 no-follow 方式判断 managed Workdir 名称是否已被占用。"""
    try:
        workspace_fd = _open_user_workspace_fd(uid)
    except FileNotFoundError:
        return False

    parts = PurePosixPath(workdir_path).parts
    parent_fd = None
    try:
        try:
            parent_fd = _open_workspace_child_fd(workspace_fd, parts[:-1])
        except FileNotFoundError:
            return False
        try:
            os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(workspace_fd)


def create_managed_workdir(uid: str, workdir_path: str) -> None:
    """排他物化新分配的目录，避免将后来占名的目录绑定给对话。"""
    parts = PurePosixPath(normalize_managed_workdir_path(workdir_path)).parts
    with workspace_mutation(uid):
        ensure_user_workspace(uid)
        workspace_fd = _open_user_workspace_fd(uid)
        try:
            parent_fd = _open_workspace_child_fd(workspace_fd, parts[:-1], create=True)
            try:
                os.mkdir(parts[-1], 0o700, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            os.close(workspace_fd)


# fd 边界实现：目录访问均经 O_NOFOLLOW 逐层打开，symlink 与非目录组件统一翻译为 ValueError。


@contextlib.contextmanager
def _workspace_fd_boundary() -> Iterator[None]:
    """把打开 Workspace 目录时的 symlink/非目录边界错误统一翻译为 ValueError。"""
    try:
        yield
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("UserWorkspace 路径包含符号链接或非目录组件") from exc
        raise


def _open_user_workspace_fd(uid: str, *, create: bool = False) -> int:
    """从配置根逐层打开 uid 的 Workspace，拒绝中间 symlink。"""
    root = get_user_data_dir()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    with _workspace_fd_boundary():
        return open_directory_fd(
            root,
            ("shared", workspace_uid_dirname(uid), WORKSPACE_DIR_NAME),
            create=create,
        )


def _open_workspace_child_fd(directory_fd: int, parts: tuple[str, ...], *, create: bool = False) -> int:
    """从已固定的 UserWorkspace fd 打开子目录并翻译边界错误。"""
    with _workspace_fd_boundary():
        return open_directory_fd(directory_fd, parts, create=create)


def _open_workspace_directory(uid: str, parts: tuple[str, ...]) -> None:
    """逐层以 O_NOFOLLOW 打开 UserWorkspace 目录。"""
    directory_fd = _open_user_workspace_fd(uid)
    try:
        child_fd = _open_workspace_child_fd(directory_fd, parts)
        os.close(child_fd)
    finally:
        os.close(directory_fd)


def _ensure_workspace_default_files_fd(uid: str, workspace_fd: int) -> None:
    """通过已校验的 Workspace fd 初始化默认 Agent 上下文文件。

    初始化是尽力而为：Agents 目录或单个文件失败只记录告警，不阻塞 Workspace 创建；
    写入中途失败则关闭 fd、删除半成品文件后原样抛出。
    """
    try:
        agents_fd = open_directory_fd(workspace_fd, (WORKSPACE_AGENTS_DIR_NAME,))
    except FileNotFoundError:
        try:
            with workspace_mutation(uid):
                agents_fd = open_directory_fd(workspace_fd, (WORKSPACE_AGENTS_DIR_NAME,), create=True)
        except OSError as exc:
            logger.warning("工作区默认 Agents 目录初始化失败: {}", exc)
            return
    except OSError as exc:
        logger.warning("工作区默认 Agents 目录初始化失败: {}", exc)
        return
    try:
        missing = []
        for filename in WORKSPACE_AGENT_CONTEXT_FILES:
            try:
                os.stat(filename, dir_fd=agents_fd, follow_symlinks=False)
            except FileNotFoundError:
                missing.append(filename)
            except OSError as exc:
                logger.warning("工作区默认 Agent 上下文检查失败 {}: {}", filename, exc)
        if missing:
            try:
                with workspace_mutation(uid):
                    for filename in missing:
                        _write_default_agent_context_file(agents_fd, filename, WORKSPACE_AGENT_CONTEXT_FILES[filename])
            except OSError as exc:
                logger.warning("工作区默认 Agent 上下文初始化失败: {}", exc)
    finally:
        os.close(agents_fd)


def _write_default_agent_context_file(agents_fd: int, filename: str, content: str) -> None:
    """以 O_EXCL|O_NOFOLLOW 幂等写入单个默认上下文文件，已存在即跳过。"""
    try:
        file_fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=agents_fd,
        )
    except FileExistsError:
        return
    except OSError as exc:
        logger.warning(f"工作区默认 {filename} 初始化失败: {exc}")
        return
    try:
        encoded = content.encode("utf-8")
        offset = 0
        while offset < len(encoded):
            offset += os.write(file_fd, encoded[offset:])
    except BaseException:
        os.close(file_fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(filename, dir_fd=agents_fd)
        raise
    os.close(file_fd)
