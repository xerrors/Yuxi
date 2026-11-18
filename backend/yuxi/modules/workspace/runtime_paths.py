"""Agent Backend 的 Sandbox runtime 路径契约。"""

from __future__ import annotations

from pathlib import PurePosixPath

from yuxi.modules.workspace.paths import normalize_workdir_path, normalize_workspace_scope

WORKSPACE_PATH = "/home/user/workspace"
SHARED_SKILLS_PATH = "/home/user/skills"
PERSONAL_SKILLS_PATH = f"{WORKSPACE_PATH.rstrip('/')}/agents/skills"
LARGE_TOOL_RESULTS_DIR_NAME = "large_tool_results"
CONVERSATION_HISTORY_DIR_NAME = "conversation_history"
TOOL_MEDIA_DIR_NAME = "tool_media"


def normalize_runtime_path(path: str) -> str:
    """规范化 Sandbox 绝对路径，拒绝跳转、URL 与反斜杠。"""
    raw = str(path or "").strip()
    pure = PurePosixPath(raw)
    if not raw:
        raise ValueError("path is required")
    if not pure.is_absolute():
        raise ValueError("path must start with /")
    if ".." in pure.parts:
        raise ValueError("path traversal is not allowed")
    if "\\" in raw or "://" in raw or "\0" in raw:
        raise ValueError("invalid runtime path")
    return pure.as_posix()


def normalize_artifact_path(workdir_path: str, path: str) -> str:
    """产物只接受已绑定 Workdir 对应的用户可见 runtime 路径。"""
    if not workdir_path:
        raise ValueError("Workdir binding is required")
    normalize_runtime_path(workdir_path)
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("artifact path is required")
    normalized = normalize_runtime_path(raw if raw.startswith("/") else f"/{raw}")
    if not is_runtime_path(normalized):
        raise ValueError("文件不在当前用户可见范围内")
    return normalized


def runtime_workdir_path(workdir_path: str) -> str:
    """把持久化 Workdir 标识映射到 Sandbox runtime。"""
    return f"{WORKSPACE_PATH.rstrip('/')}/{normalize_workdir_path(workdir_path)}"


def workdir_runtime_paths(workdir_path: str) -> tuple[str, str]:
    """返回当前 Workdir runtime 的大结果与对话历史目录。"""
    normalized = normalize_runtime_path(workdir_path).rstrip("/")
    if not normalized.startswith(f"{WORKSPACE_PATH.rstrip('/')}/"):
        raise ValueError("workdir_path must be a Backend runtime path")
    outputs = f"{normalized}/outputs"
    return (
        f"{outputs}/{LARGE_TOOL_RESULTS_DIR_NAME}",
        f"{outputs}/{CONVERSATION_HISTORY_DIR_NAME}",
    )


def workdir_tool_media_dir(workdir_path: str) -> str:
    """返回当前 Workdir runtime 的工具图片媒体目录。"""
    normalized = normalize_runtime_path(workdir_path).rstrip("/")
    if not normalized.startswith(f"{WORKSPACE_PATH.rstrip('/')}/"):
        raise ValueError("workdir_path must be a Backend runtime path")
    return f"{normalized}/outputs/{TOOL_MEDIA_DIR_NAME}"


def runtime_workspace_path(workspace_path: str) -> str:
    """把 UserWorkspace scope 映射到 Sandbox Workspace runtime。"""
    pure = PurePosixPath(normalize_workspace_scope(workspace_path))
    root = WORKSPACE_PATH.rstrip("/")
    return root if pure.as_posix() == "/" else f"{root}{pure.as_posix()}"


def workspace_scope_from_runtime_path(runtime_path: str) -> str:
    """把 Sandbox Workspace runtime 路径还原为 UserWorkspace scope。"""
    normalized = normalize_runtime_path(runtime_path)
    root = WORKSPACE_PATH.rstrip("/")
    if normalized == root:
        return "/"
    if not normalized.startswith(f"{root}/"):
        raise ValueError("runtime path is outside Workspace")
    return f"/{normalized[len(root) + 1 :]}"


def runtime_path_for_workdir_scope(workdir_path: str, path: str | None) -> str:
    """把 Workdir scope 映射到 Sandbox runtime 绝对路径。"""
    raw = str(path or "/").strip() or "/"
    pure = PurePosixPath(normalize_workspace_scope(raw))
    workspace_root = f"/{normalize_workdir_path(workdir_path)}"
    workspace_path = workspace_root if pure.as_posix() == "/" else f"{workspace_root}{pure.as_posix()}"
    return runtime_workspace_path(workspace_path)


def workdir_scope_from_runtime_path(workdir_path: str, runtime_path: str) -> str:
    """把当前 Workdir runtime 路径还原为持久化 Workdir scope。"""
    normalized = normalize_runtime_path(runtime_path)
    root = runtime_workdir_path(workdir_path).rstrip("/")
    if normalized == root:
        return "/"
    if not normalized.startswith(f"{root}/"):
        raise ValueError("runtime path is outside the Workdir")
    return f"/{normalized[len(root) + 1 :]}"


def is_runtime_path(path: str) -> bool:
    """判断绝对路径是否属于 Agent Backend 的 runtime 命名空间。"""
    try:
        normalized = normalize_runtime_path(path)
    except ValueError:
        return False
    roots = (WORKSPACE_PATH.rstrip("/"), SHARED_SKILLS_PATH.rstrip("/"))
    return any(normalized == root or normalized.startswith(f"{root}/") for root in roots)


__all__ = [
    "CONVERSATION_HISTORY_DIR_NAME",
    "LARGE_TOOL_RESULTS_DIR_NAME",
    "TOOL_MEDIA_DIR_NAME",
    "WORKSPACE_PATH",
    "PERSONAL_SKILLS_PATH",
    "SHARED_SKILLS_PATH",
    "is_runtime_path",
    "normalize_runtime_path",
    "normalize_artifact_path",
    "runtime_path_for_workdir_scope",
    "runtime_workspace_path",
    "runtime_workdir_path",
    "workdir_scope_from_runtime_path",
    "workspace_scope_from_runtime_path",
    "workdir_runtime_paths",
    "workdir_tool_media_dir",
]
