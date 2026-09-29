"""Skill 运行时解析。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.backends.paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from yuxi.agents.toolkits import get_all_tool_instances
from yuxi.services.skills.package import normalize_string_list
from yuxi.services.skills.personal import list_personal_skills
from yuxi.services.skills.shared import lock_accessible_shared_skills_for_runtime, resolved_shared_skill
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import open_regular_file_fd


class RuntimeSkill(TypedDict):
    """单个 Skill 的运行时信息。"""

    name: str
    description: str
    path: str
    tools: list[str]
    mcps: list[str]
    skills: list[str]


async def resolve_runtime_skills_for_context(
    context,
    *,
    db: AsyncSession,
    user: User,
) -> dict:
    """合并已选共享与全部个人 Skill，派生运行范围和预加载快照。"""
    selected = normalize_string_list(getattr(context, "skills", None))
    personal_items = await list_personal_skills(str(user.uid))
    personal_slugs = {item.slug for item in personal_items}
    shared_rows = await lock_accessible_shared_skills_for_runtime(
        db,
        user,
        selected,
        shadowed_slugs=personal_slugs,
    )
    skill_items_by_slug = {item.slug: resolved_shared_skill(item) for item in shared_rows if item.slug}
    skill_items_by_slug.update({item.slug: item for item in personal_items if item.slug})
    runtime_skills = build_runtime_skills(list(skill_items_by_slug.values()))
    selected_skills = [slug for slug in selected if slug in skill_items_by_slug]
    context_skills = normalize_string_list([*selected_skills, *(item.slug for item in personal_items)])
    effective_skills = expand_skill_closure(context_skills, runtime_skills)
    configured_preloads = normalize_string_list(getattr(context, "preload_skills", None))
    context_preload_skills = [slug for slug in configured_preloads if slug in selected_skills]
    preloaded_skills = expand_skill_closure(context_preload_skills, runtime_skills)
    preloaded_contents = (
        await asyncio.to_thread(_read_preloaded_skill_contents, preloaded_skills, skill_items_by_slug)
        if preloaded_skills
        else {}
    )
    return {
        "context_skills": context_skills,
        "context_preload_skills": context_preload_skills,
        "effective_skills": effective_skills,
        "runtime_skills": runtime_skills,
        "skill_metadata": {
            slug: {
                "source_scope": skill_items_by_slug[slug].source_scope,
                "version": skill_items_by_slug[slug].version,
                "content_hash": skill_items_by_slug[slug].content_hash,
            }
            for slug in effective_skills
        },
        "preloaded_skills": preloaded_skills,
        "preloaded_skill_contents": preloaded_contents,
    }


def build_runtime_skills(skills: list) -> dict[str, RuntimeSkill]:
    """从已授权 Skill 构建运行时信息。"""
    result: dict[str, RuntimeSkill] = {}
    for item in skills:
        if not item.slug:
            continue
        root = (
            VIRTUAL_PERSONAL_SKILLS_PATH if getattr(item, "source_scope", None) == "personal" else VIRTUAL_SKILLS_PATH
        )
        result[item.slug] = {
            "name": item.name,
            "description": item.description,
            "path": f"{root}/{item.slug}/SKILL.md",
            "tools": normalize_string_list(item.tool_dependencies or []),
            "mcps": normalize_string_list(item.mcp_dependencies or []),
            "skills": normalize_string_list(item.skill_dependencies or []),
        }
    return result


def expand_skill_closure(
    slugs: list[str] | None,
    runtime_skills: dict[str, RuntimeSkill],
) -> list[str]:
    """展开 Skill 依赖闭包并保持根与依赖的声明顺序。"""
    ordered_roots = normalize_string_list(slugs)
    if not ordered_roots:
        return []

    result: list[str] = []
    seen: set[str] = set()

    def dfs(slug: str, stack: set[str]) -> None:
        if slug in stack:
            logger.warning(f"Cycle detected in skill dependencies, skip: {' -> '.join([*stack, slug])}")
            return
        if slug in seen:
            return

        node = runtime_skills.get(slug)
        if not node:
            logger.warning(f"Skill dependency target not found in DB, skip: {slug}")
            return

        seen.add(slug)
        result.append(slug)
        next_stack = set(stack)
        next_stack.add(slug)
        for dep in node.get("skills", []):
            dfs(dep, next_stack)

    for root in ordered_roots:
        dfs(root, set())
    return result


def resolve_skill_gated_tools(context) -> list:
    """解析所有可见 Skill 依赖且需注册到 ToolNode 的本地工具。"""
    runtime_skills = getattr(context, "_skill_runtime_snapshot", {}).get("runtime_skills", {}) or {}
    effective_skills = getattr(context, "_skill_runtime_snapshot", {}).get("effective_skills", []) or []
    tool_names: set[str] = set()
    for slug in effective_skills:
        node = runtime_skills.get(slug) or {}
        tool_names.update(node.get("tools", []))
    if not tool_names:
        return []
    return [tool for tool in get_all_tool_instances() if tool.name in tool_names]


def build_dependency_bundle(
    activated_skills: list[str],
    runtime_skills: dict[str, RuntimeSkill],
) -> dict[str, list[str]]:
    """汇总直接激活 Skill 的本地工具和 MCP 依赖。"""
    tools: list[str] = []
    mcps: list[str] = []
    seen_tools: set[str] = set()
    seen_mcps: set[str] = set()

    for slug in activated_skills:
        dependency = runtime_skills.get(slug, {})
        for tool_name in dependency.get("tools", []):
            if tool_name in seen_tools:
                continue
            seen_tools.add(tool_name)
            tools.append(tool_name)
        for mcp_name in dependency.get("mcps", []):
            if mcp_name in seen_mcps:
                continue
            seen_mcps.add(mcp_name)
            mcps.append(mcp_name)

    return {"tools": tools, "mcps": mcps}


def _read_preloaded_skill_contents(slugs: list[str], skill_items: dict[str, Any]) -> dict[str, str]:
    """从授权解析得到的真实来源读取根级 SKILL.md。"""

    contents: dict[str, str] = {}
    for slug in slugs:
        try:
            source_dir = Path(skill_items[slug].source_dir)
            if not source_dir.is_absolute() or ".." in source_dir.parts:
                raise OSError("Skill 来源目录必须是规范化绝对路径")
            with open_regular_file_fd(
                Path(source_dir.anchor),
                (*source_dir.parts[1:], "SKILL.md"),
            ) as (file_fd, _file_stat):
                with os.fdopen(os.dup(file_fd), encoding="utf-8") as skill_file:
                    contents[slug] = skill_file.read()
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"预加载 Skill '{slug}' 失败：根级 SKILL.md 不可读") from exc
    return contents
