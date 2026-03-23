from __future__ import annotations

import re
import shlex

from yuxi.agents.backends.sandbox_config import (
    LARGE_TOOL_RESULTS_DIR,
    OUTPUTS_DIR,
    UPLOADS_DIR,
    USER_DATA_PATH,
    WORKSPACE_DIR,
)

ALLOWED_PREFIXES = ("/mnt/user-data", "/mnt/skills")
SYSTEM_EXEC_ALLOWED_PREFIXES = ("/bin/", "/usr/bin/", "/usr/local/bin/")


def normalize_virtual_path(path: str, thread_id: str, *, allow_root: bool = False) -> str:
    """将输入路径标准化为 /mnt 命名空间路径。"""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("path is required")

    if not raw.startswith("/"):
        raw = f"/{raw}"

    if raw in {"/", "/mnt", "/mnt/"}:
        if allow_root:
            return "/"
        raise PermissionError("Access denied: path '/' is not writable")

    if raw.startswith("/mnt/"):
        normalized = raw.rstrip("/") if raw != "/mnt/user-data/" else "/mnt/user-data"
        ensure_path_allowed(normalized)
        return normalized

    if raw == "/attachments":
        return f"{USER_DATA_PATH}/{UPLOADS_DIR}/attachments"
    if raw.startswith("/attachments/"):
        suffix = raw[len("/attachments/") :]
        return f"{USER_DATA_PATH}/{UPLOADS_DIR}/attachments/{suffix}".rstrip("/")

    # 兼容旧版技能路径
    if raw == "/skills":
        return "/mnt/skills"
    if raw.startswith("/skills/"):
        suffix = raw[len("/skills/") :]
        return f"/mnt/skills/{suffix}".rstrip("/")

    # 兼容部分模型/工具默认工作目录探测（如 /home）
    if raw == "/home":
        return f"{USER_DATA_PATH}/{WORKSPACE_DIR}"
    if raw.startswith("/home/"):
        return f"{USER_DATA_PATH}/{WORKSPACE_DIR}/{raw[len('/home/') :]}".rstrip("/")

    if raw == "/workspace":
        return f"{USER_DATA_PATH}/{WORKSPACE_DIR}"
    if raw.startswith("/workspace/"):
        return f"{USER_DATA_PATH}/{WORKSPACE_DIR}/{raw[len('/workspace/') :]}".rstrip("/")

    if raw == "/outputs":
        return f"{USER_DATA_PATH}/{OUTPUTS_DIR}"
    if raw.startswith(f"/outputs/{thread_id}/"):
        return f"{USER_DATA_PATH}/{OUTPUTS_DIR}/{raw[len(f'/outputs/{thread_id}/') :]}".rstrip("/")
    if raw.startswith("/outputs/"):
        return f"{USER_DATA_PATH}/{OUTPUTS_DIR}/{raw[len('/outputs/') :]}".rstrip("/")

    if raw == "/uploads":
        return f"{USER_DATA_PATH}/{UPLOADS_DIR}"
    if raw.startswith(f"/uploads/{thread_id}/"):
        return f"{USER_DATA_PATH}/{UPLOADS_DIR}/{raw[len(f'/uploads/{thread_id}/') :]}".rstrip("/")
    if raw.startswith("/uploads/"):
        return f"{USER_DATA_PATH}/{UPLOADS_DIR}/{raw[len('/uploads/') :]}".rstrip("/")

    if raw == "/large_tool_results":
        return f"{USER_DATA_PATH}/{LARGE_TOOL_RESULTS_DIR}"
    if raw.startswith(f"/large_tool_results/{thread_id}/"):
        suffix = raw[len(f"/large_tool_results/{thread_id}/") :]
        return f"{USER_DATA_PATH}/{LARGE_TOOL_RESULTS_DIR}/{suffix}".rstrip("/")
    if raw.startswith("/large_tool_results/"):
        return f"{USER_DATA_PATH}/{LARGE_TOOL_RESULTS_DIR}/{raw[len('/large_tool_results/') :]}".rstrip("/")

    raise PermissionError(f"Access denied: '{raw}' is outside /mnt namespace")


def ensure_path_allowed(path: str) -> None:
    normalized = path.rstrip("/") if path != "/" else path
    if normalized in {"/mnt", "/mnt/"}:
        return
    if not any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in ALLOWED_PREFIXES):
        raise PermissionError(f"Access denied: '{path}' is outside allowed namespaces")
    if ".." in normalized.split("/"):
        raise PermissionError("Access denied: path traversal is not allowed")


def is_skills_path(path: str) -> bool:
    p = path.rstrip("/")
    return p == "/mnt/skills" or p.startswith("/mnt/skills/")


def validate_execute_command_paths(command: str) -> None:
    """限制命令中的绝对路径只允许 /mnt 或少量系统路径。"""
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        raise PermissionError(f"Invalid shell command: {e}") from e

    for token in tokens:
        # 处理重定向符号后面的路径
        candidate = token
        if token.startswith((">", "<")) and len(token) > 1:
            candidate = token[1:]

        if not candidate.startswith("/"):
            continue

        if candidate.startswith("/mnt/"):
            ensure_path_allowed(candidate)
            continue

        if any(candidate.startswith(prefix) for prefix in SYSTEM_EXEC_ALLOWED_PREFIXES):
            continue

        # /usr/lib/python 等运行时依赖路径可能会出现在参数里，放行 /usr/lib
        if candidate.startswith("/usr/lib/"):
            continue

        raise PermissionError(f"Access denied: absolute path '{candidate}' is not allowed")


def mask_host_paths(text: str, path_mappings: list[tuple[str, str]]) -> str:
    masked = text
    for host_path, virtual_path in path_mappings:
        if not host_path:
            continue
        escaped = re.escape(host_path)
        masked = re.sub(escaped, virtual_path, masked)
    return masked
