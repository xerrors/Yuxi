from __future__ import annotations

from deepagents.backends.composite import (
    CompositeBackend,
    _remap_file_info_path,
    _route_for_path,
    _strip_route_from_pattern,
)
from deepagents.backends.protocol import FileInfo

from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills

from .sandbox import ProvisionerSandboxBackend
from .skills_backend import SelectedSkillsReadonlyBackend


class CustomCompositeBackend(CompositeBackend):
    """修复 glob_info 路由逻辑的 CompositeBackend。

    修复内容：当 path 不匹配任何路由时应该只搜索 default 后端，
    而不是错误地遍历所有路由后端搜索。
    """

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            infos = backend.glob_info(pattern, backend_path)
            return [_remap_file_info_path(fi, route_prefix) for fi in infos]

        # 只在 path 为 None 或 "/" 时搜索所有后端，其他只搜索 default
        if path is None or path == "/":
            results: list[FileInfo] = []
            results.extend(self.default.glob_info(pattern, path))
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                infos = backend.glob_info(route_pattern, "/")
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in infos)
            results.sort(key=lambda x: x.get("path", ""))
            return results

        return self.default.glob_info(pattern, path)

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            infos = await backend.aglob_info(pattern, backend_path)
            return [_remap_file_info_path(fi, route_prefix) for fi in infos]

        if path is None or path == "/":
            results: list[FileInfo] = []
            results.extend(await self.default.aglob_info(pattern, path))
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                infos = await backend.aglob_info(route_pattern, "/")
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in infos)
            results.sort(key=lambda x: x.get("path", ""))
            return results

        return await self.default.aglob_info(pattern, path)


def _get_visible_skills_from_runtime(runtime) -> list[str]:
    """获取运行时可见的 skills 列表"""
    context = getattr(runtime, "context", None)
    selected = getattr(context, "_visible_skills", None)
    if not isinstance(selected, list):
        selected = getattr(context, "skills", None) or []
    return normalize_selected_skills(selected)


def _extract_thread_id(runtime) -> str:
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                return thread_id.strip()

    context = getattr(runtime, "context", None)
    thread_id = getattr(context, "thread_id", None)
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id.strip()

    raise ValueError("thread_id is required in runtime configurable context")


def _extract_user_id(runtime) -> str:
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            user_id = configurable.get("user_id")
            if isinstance(user_id, str) and user_id.strip():
                return user_id.strip()

    context = getattr(runtime, "context", None)
    user_id = getattr(context, "user_id", None)
    if isinstance(user_id, str) and user_id.strip():
        return user_id.strip()

    raise ValueError("user_id is required in runtime configurable context")


def create_agent_composite_backend(runtime) -> CompositeBackend:
    visible_skills = _get_visible_skills_from_runtime(runtime)
    thread_id = _extract_thread_id(runtime)
    user_id = _extract_user_id(runtime)
    return CustomCompositeBackend(
        default=ProvisionerSandboxBackend(thread_id=thread_id, user_id=user_id, visible_skills=visible_skills),
        routes={
            "/skills/": SelectedSkillsReadonlyBackend(selected_slugs=visible_skills),
        },
    )
