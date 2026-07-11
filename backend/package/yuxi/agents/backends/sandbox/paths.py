from __future__ import annotations

import re
from pathlib import Path

from yuxi import config as conf
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import (
    OUTPUTS_DIR_NAME,
    UPLOADS_DIR_NAME,
    VIRTUAL_PATH_PREFIX,
    WORKSPACE_AGENTS_DIR_NAME,
    WORKSPACE_AGENTS_PROMPT_FILE_NAME,
    WORKSPACE_DIR_NAME,
    WORKSPACE_MEMORY_DIR_NAME,
    WORKSPACE_MEMORY_FILE_NAME,
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def get_virtual_path_prefix() -> str:
    return "/" + VIRTUAL_PATH_PREFIX.strip("/")


def validate_thread_id(thread_id: str) -> str:
    value = str(thread_id or "").strip()
    if not value:
        raise ValueError("thread_id is required")
    if not _SAFE_ID_RE.match(value):
        raise ValueError("thread_id contains invalid characters")
    return value


def _thread_root_dir(thread_id: str) -> Path:
    safe_thread_id = validate_thread_id(thread_id)
    return Path(conf.save_dir) / "threads" / safe_thread_id / "user-data"


def _validate_uid(uid: str) -> str:
    value = str(uid or "").strip()
    if not value:
        raise ValueError("uid is required")
    if not _SAFE_ID_RE.match(value):
        raise ValueError("uid contains invalid characters")
    return value


def _global_user_data_dir(uid: str) -> Path:
    """Return the shared host-side directory used for one user's workspace files."""
    safe_uid = _validate_uid(uid)
    return Path(conf.save_dir) / "threads" / "shared" / safe_uid


def sandbox_user_data_dir(thread_id: str) -> Path:
    return _thread_root_dir(thread_id)


def sandbox_workspace_dir(thread_id: str, uid: str) -> Path:
    validate_thread_id(thread_id)
    return _global_user_data_dir(uid) / WORKSPACE_DIR_NAME


def sandbox_workspace_agents_prompt_file(thread_id: str, uid: str) -> Path:
    return sandbox_workspace_dir(thread_id, uid) / WORKSPACE_AGENTS_DIR_NAME / WORKSPACE_AGENTS_PROMPT_FILE_NAME


def _threads_root_dir() -> Path:
    return (Path(conf.save_dir) / "threads").resolve(strict=False)


def _resolve_threads_child_path(path: Path) -> Path:
    root = _threads_root_dir()
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("path resolved outside threads root")
    return resolved


def _chmod_writable(path: Path, *, dir: bool = False) -> None:
    safe_path = _resolve_threads_child_path(path)
    mode = 0o777 if dir else 0o666
    try:
        safe_path.chmod(mode)
    except OSError:
        pass


def ensure_workspace_default_files(workspace_dir: Path) -> None:
    workspace_dir = _resolve_threads_child_path(workspace_dir)
    default_files = (
        ("Agents", workspace_dir / WORKSPACE_AGENTS_DIR_NAME / WORKSPACE_AGENTS_PROMPT_FILE_NAME, b""),
        (
            "Memory",
            workspace_dir / WORKSPACE_MEMORY_DIR_NAME / WORKSPACE_MEMORY_FILE_NAME,
            b"# User Memory\n",
        ),
    )

    for label, file_path, initial_content in default_files:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            _chmod_writable(file_path.parent, dir=True)
        except FileExistsError:
            logger.warning(f"工作区默认 {label} 目录创建失败：路径已被文件占用")
            continue
        except OSError as exc:
            logger.warning(f"工作区默认 {label} 目录初始化失败: {exc}")
            continue

        try:
            with file_path.open("xb") as buffer:
                buffer.write(initial_content)
            _chmod_writable(file_path)
        except FileExistsError:
            if file_path.is_dir():
                logger.warning(f"工作区默认 {file_path.name} 创建失败：路径已被目录占用")
        except OSError as exc:
            logger.warning(f"工作区默认 {label} 文件初始化失败: {exc}")


def sandbox_uploads_dir(thread_id: str) -> Path:
    return _thread_root_dir(thread_id) / UPLOADS_DIR_NAME


def sandbox_outputs_dir(thread_id: str) -> Path:
    return _thread_root_dir(thread_id) / OUTPUTS_DIR_NAME


def ensure_thread_dirs(thread_id: str, uid: str) -> None:
    _resolve_threads_child_path(_global_user_data_dir(uid)).mkdir(parents=True, exist_ok=True)
    workspace_dir = _resolve_threads_child_path(sandbox_workspace_dir(thread_id, uid))
    workspace_dir.mkdir(parents=True, exist_ok=True)
    ensure_workspace_default_files(workspace_dir)
    _resolve_threads_child_path(sandbox_uploads_dir(thread_id)).mkdir(parents=True, exist_ok=True)
    _resolve_threads_child_path(sandbox_outputs_dir(thread_id)).mkdir(parents=True, exist_ok=True)


def _resolve_user_data_base_dir(thread_id: str, uid: str, relative_path: str) -> tuple[Path, Path]:
    """Map a virtual user-data path to the correct host-side base directory."""
    parts = Path(relative_path).parts
    if not parts:
        base_dir = sandbox_user_data_dir(thread_id)
        return base_dir.resolve(), base_dir.resolve()

    namespace = parts[0]
    if namespace == WORKSPACE_DIR_NAME:
        # Workspace is shared across one user's threads, so it lives outside the per-thread root.
        base_dir = sandbox_workspace_dir(thread_id, uid)
        target_path = base_dir.joinpath(*parts[1:]) if len(parts) > 1 else base_dir
        return base_dir.resolve(), target_path.resolve()
    if namespace == UPLOADS_DIR_NAME:
        base_dir = sandbox_uploads_dir(thread_id)
        target_path = base_dir.joinpath(*parts[1:]) if len(parts) > 1 else base_dir
        return base_dir.resolve(), target_path.resolve()
    if namespace == OUTPUTS_DIR_NAME:
        base_dir = sandbox_outputs_dir(thread_id)
        target_path = base_dir.joinpath(*parts[1:]) if len(parts) > 1 else base_dir
        return base_dir.resolve(), target_path.resolve()

    base_dir = sandbox_user_data_dir(thread_id)
    return base_dir.resolve(), (base_dir / relative_path).resolve()


def resolve_virtual_path(thread_id: str, virtual_path: str, *, uid: str) -> Path:
    clean_virtual_path = "/" + str(virtual_path or "").strip().lstrip("/")
    virtual_prefix = get_virtual_path_prefix()

    if clean_virtual_path != virtual_prefix and not clean_virtual_path.startswith(f"{virtual_prefix}/"):
        raise ValueError(f"path must start with {virtual_prefix}")

    relative_path = clean_virtual_path[len(virtual_prefix) :].lstrip("/")
    base_dir, target_path = _resolve_user_data_base_dir(thread_id, uid, relative_path)

    try:
        target_path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError("path traversal detected") from exc

    return target_path


def virtual_path_for_thread_file(thread_id: str, path: str | Path, *, uid: str) -> str:
    target_path = Path(path).resolve()
    thread_root = sandbox_user_data_dir(thread_id).resolve()
    global_workspace_root = sandbox_workspace_dir(thread_id, uid).resolve()

    try:
        relative_path = target_path.relative_to(global_workspace_root)
    except ValueError:
        try:
            relative_path = target_path.relative_to(thread_root)
        except ValueError as exc:
            raise ValueError("file is outside allowed user-data directories") from exc
        relative_path_str = relative_path.as_posix()
    else:
        workspace_relative = relative_path.as_posix()
        relative_path_str = (
            WORKSPACE_DIR_NAME if workspace_relative in {"", "."} else f"{WORKSPACE_DIR_NAME}/{workspace_relative}"
        )

    prefix = get_virtual_path_prefix().rstrip("/")
    if not relative_path_str:
        return prefix
    return f"{prefix}/{relative_path_str}"
