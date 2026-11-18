"""Skill 运行时解析。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import open_regular_file_fd
from yuxi.modules.extensions.plugins.registry import get_plugin, get_plugins
from yuxi.modules.extensions.skills.access import lock_accessible_shared_skills_for_runtime, resolved_shared_skill
from yuxi.modules.extensions.skills.package import normalize_string_list
from yuxi.modules.extensions.skills.personal import list_personal_skills
from yuxi.modules.extensions.skills.projection import refresh_user_skill_projection_async
from yuxi.modules.extensions.skills.resolved import RuntimeSkill, SkillRuntimeSnapshot
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.runtime_paths import PERSONAL_SKILLS_PATH, SHARED_SKILLS_PATH


async def sync_agent_context_skills(context) -> None:
    """构图前校验运行身份，并刷新用户获授权的共享 Skill 投影。"""
    thread_id = context.get("thread_id") if isinstance(context, dict) else getattr(context, "thread_id", None)
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id is required in runtime context")
    uid = context.get("uid") if isinstance(context, dict) else getattr(context, "uid", None)
    if not isinstance(uid, str) or not uid.strip():
        raise ValueError("uid is required in runtime context")
    await refresh_user_skill_projection_async(uid.strip())


async def resolve_runtime_skills_for_context(
    context,
    *,
    db: AsyncSession,
    user: User,
    agent=None,
) -> SkillRuntimeSnapshot:
    """合并授权内容和插件配套来源；正文读取不授予运行能力。"""
    plugins = [get_plugin(plugin_id) for plugin_id in getattr(context, "plugins", [])]
    owned_entries = {plugin.entry_skill for plugin in get_plugins() if plugin.entry_skill}
    plugin_skills = normalize_string_list([slug for plugin in plugins for slug in (plugin.entry_skill, *plugin.skills) if slug])
    selected = [slug for slug in normalize_string_list(getattr(context, "skills", None)) if slug not in owned_entries]
    personal_items = await list_personal_skills(str(user.uid))
    personal_slugs = {item.slug for item in personal_items}
    bound_slug = None
    agent_slug = getattr(context, "agent_slug", None)
    if agent_slug:
        from yuxi.modules.agents.repositories.definitions import AgentRepository
        from yuxi.modules.extensions.skills.repository import SkillRepository

        if agent is None:
            agent = await AgentRepository(db).get_visible_by_slug(slug=agent_slug, user=user)
        if agent is None:
            raise PermissionError("智能体不存在或无权限访问")
        bound = await SkillRepository(db).get_by_bound_agent_id(agent.id)
        previous = context.skill_runtime_snapshot
        if bound is not None and previous is not None:
            prepared_bound = {slug for slug, metadata in previous["skill_metadata"].items() if metadata["source_scope"] == "agent_bound"}
            # 执行边界只复核已准备绑定；中途新增绑定在下一 Run 生效。
            if bound.slug not in prepared_bound:
                bound = None
        if bound is not None:
            if bound.slug in personal_slugs and bound.slug not in owned_entries:
                raise ValueError("个人 Skill 与智能体专属 Skill 标识冲突，请重命名个人 Skill")
            bound_slug = bound.slug
            if bound_slug not in owned_entries:
                selected = [*selected, bound_slug]
    shared_rows = await lock_accessible_shared_skills_for_runtime(
        db,
        user,
        normalize_string_list([*selected, *plugin_skills]),
        shadowed_slugs=personal_slugs,
        **({"bound_slug": bound_slug} if bound_slug else {}),
    )
    skill_items_by_slug = {item.slug: resolved_shared_skill(item) for item in shared_rows if item.slug}
    skill_items_by_slug.update({item.slug: item for item in personal_items if item.slug})
    runtime_skills = build_runtime_skills(list(skill_items_by_slug.values()))
    selected_skills = [slug for slug in selected if slug in skill_items_by_slug]
    context_skills = normalize_string_list([*selected_skills, *(item.slug for item in personal_items)])
    effective_skills = normalize_string_list([*context_skills, *(slug for slug in plugin_skills if slug in runtime_skills)])
    limited = any(slug not in runtime_skills or slug in personal_slugs for slug in plugin_skills)
    configured_preloads = normalize_string_list(getattr(context, "preload_skills", None))
    context_preload_skills = [slug for slug in configured_preloads if slug in selected_skills]
    if bound_slug and bound_slug not in owned_entries:
        if bound_slug not in runtime_skills:
            raise PermissionError("智能体专属 Skill 不可用")
        context_preload_skills = normalize_string_list([*context_preload_skills, bound_slug])
    preloaded_skills = context_preload_skills
    metadata = {
        slug: {
            "source_scope": skill_items_by_slug[slug].source_scope,
            "version": skill_items_by_slug[slug].version,
            "content_hash": skill_items_by_slug[slug].content_hash,
            "source_identity": _skill_source_identity(skill_items_by_slug[slug]),
        }
        for slug in effective_skills
    }
    previous = getattr(context, "skill_runtime_snapshot", None) or {}
    previous_metadata = previous.get("skill_metadata", {})
    previous_contents = previous.get("preloaded_skill_contents", {})
    preloaded_contents = {}
    for slug in preloaded_skills:
        if slug not in previous_contents:
            continue
        current = metadata[slug]
        old = previous_metadata.get(slug)
        if current["source_scope"] == "personal" and old == current:
            preloaded_contents[slug] = previous_contents[slug]
        elif current["source_scope"] == "agent_bound" and old is not None:
            # 同一行修订沿用本 Run 准备正文；新身份不得与旧正文拼接。
            if old["source_scope"] == "agent_bound" and old["source_identity"].split(":", 1)[0] == current["source_identity"]:
                preloaded_contents[slug] = previous_contents[slug]
                metadata[slug] = dict(old)
    unread = [slug for slug in preloaded_skills if slug not in preloaded_contents]
    if unread:
        preloaded_contents.update(await asyncio.to_thread(_read_preloaded_skill_contents, unread, skill_items_by_slug))
        if any(_skill_source_identity(skill_items_by_slug[slug]) != metadata[slug]["source_identity"] for slug in unread):
            raise RuntimeError("读取 Skill 期间来源发生变化")
        for slug in unread:
            if metadata[slug]["source_scope"] != "personal":
                digest = hashlib.sha256(preloaded_contents[slug].encode("utf-8")).hexdigest()
                metadata[slug]["source_identity"] += f":{digest}"
    entry_slugs = [
        plugin.entry_skill for plugin in plugins if plugin.entry_skill in runtime_skills and plugin.entry_skill not in personal_slugs
    ]
    entry_contents = await asyncio.to_thread(_read_preloaded_skill_contents, entry_slugs, skill_items_by_slug) if entry_slugs else {}
    for slug, content in entry_contents.items():
        if _skill_source_identity(skill_items_by_slug[slug]) != metadata[slug]["source_identity"]:
            raise RuntimeError("读取插件入口期间来源发生变化")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        metadata[slug]["source_identity"] += f":{digest}"
    return {
        "plugin_entry_contents": {
            plugin.plugin_id: entry_contents[plugin.entry_skill] for plugin in plugins if plugin.entry_skill in entry_contents
        },
        "capability_limited": limited,
        "context_skills": context_skills,
        "context_preload_skills": context_preload_skills,
        "effective_skills": effective_skills,
        "runtime_skills": runtime_skills,
        "skill_metadata": metadata,
        "preloaded_skills": preloaded_skills,
        "preloaded_skill_contents": preloaded_contents,
    }


def _skill_source_identity(item) -> str:
    """识别共享行或个人根文件的当前来源，避免同名替换复用旧提示。"""
    if item.source_scope != "personal":
        return str(item.id)
    source_dir = Path(item.source_dir)
    with open_regular_file_fd(Path(source_dir.anchor), (*source_dir.parts[1:], "SKILL.md")) as (_, stat):
        return f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"


def build_runtime_skills(skills: list) -> dict[str, RuntimeSkill]:
    """从已授权 Skill 构建运行时信息。"""
    result: dict[str, RuntimeSkill] = {}
    for item in skills:
        if not item.slug:
            continue
        root = PERSONAL_SKILLS_PATH if getattr(item, "source_scope", None) == "personal" else SHARED_SKILLS_PATH
        result[item.slug] = {
            "name": item.name,
            "description": item.description,
            "path": f"{root}/{item.slug}/SKILL.md",
        }
    return result


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
