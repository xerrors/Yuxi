from deepagents.backends import CompositeBackend, StateBackend

from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills

from .skills_backend import SelectedSkillsReadonlyBackend


def _get_visible_skills_from_runtime(runtime) -> list[str]:
    """获取运行时可见的 skills 列表"""
    context = getattr(runtime, "context", None)
    selected = getattr(context, "skills", None) or []
    return normalize_selected_skills(selected)


def create_agent_composite_backend(runtime, *, sandbox_backend=None) -> CompositeBackend:
    """为 agent 构建 backend：默认 State/Sandbox backend + /skills 路由只读 backend。"""
    visible_skills = _get_visible_skills_from_runtime(runtime)
    return CompositeBackend(
        default=sandbox_backend or StateBackend(runtime),
        routes={
            "/mnt/skills/": SelectedSkillsReadonlyBackend(selected_slugs=visible_skills),
        },
    )
