"""源码内置 Skill 的发现、来源同步与数据库索引。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.extensions.plugins.registry import get_plugin_skill_directories
from yuxi.modules.extensions.skills.access import (
    BUILTIN_SKILL_SHARE_CONFIG as BUILTIN_SKILL_SHARE_CONFIG,
)
from yuxi.modules.extensions.skills.access import (
    get_skills_root_dir,
    is_builtin_skill,
    normalize_skill_share_config,
)
from yuxi.modules.extensions.skills.builtin import BUILTIN_SKILLS_DIR
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import (
    compute_skill_directory_hash,
    copy_skill_tree_no_symlinks,
    parse_skill_markdown,
)
from yuxi.modules.extensions.skills.repository import SkillRepository

BUILTIN_SKILL_OPERATOR = "builtin-system"
SKILL_STORAGE_LOCK = 0x5958534B


async def init_builtin_skills(db: AsyncSession, *, created_by: str = "system") -> list[Skill]:
    """将内置 Skill 定义同步到共享索引。"""
    if db is not None and db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SKILL_STORAGE_LOCK})

    repo = SkillRepository(db)
    synced_items: list[Skill] = []

    for spec in list_builtin_skill_specs():
        slug = spec["slug"]
        existing = await repo.get_by_slug(slug)
        if existing and not is_builtin_skill(existing):
            raise ValueError(f"内置 skill '{slug}' 与已存在的非内置 skill 冲突")

        target_dir = get_skills_root_dir() / slug
        _replace_skill_target(target_dir, Path(spec["source_dir"]))

        if existing:
            existing.dir_path = (Path("shared") / slug).as_posix()
            # 仅修复可识别的旧系统默认值，不覆盖管理员分享或未知历史配置。
            if existing.share_config == BUILTIN_SKILL_SHARE_CONFIG:
                await repo.update_share_config(
                    existing,
                    share_config=normalize_skill_share_config(None, operator_uid=created_by, source_type="builtin"),
                    updated_by=created_by,
                )
            if existing.name != spec["name"] or existing.description != spec["description"]:
                await repo.update_metadata(
                    existing,
                    name=spec["name"],
                    description=spec["description"],
                    updated_by=created_by,
                )
            synced_items.append(
                await repo.update_builtin_install(
                    existing,
                    version=spec["version"],
                    content_hash=spec["content_hash"],
                    updated_by=created_by,
                )
            )
            continue

        synced_items.append(
            await repo.create(
                slug=slug,
                name=spec["name"],
                description=spec["description"],
                source_type="builtin",
                dir_path=(Path("shared") / slug).as_posix(),
                share_config=normalize_skill_share_config(None, operator_uid=created_by, source_type="builtin"),
                enabled=True,
                version=spec["version"],
                content_hash=spec["content_hash"],
                created_by=created_by or BUILTIN_SKILL_OPERATOR,
            )
        )

    if db is not None:
        await db.commit()
    return synced_items


def list_builtin_skill_specs() -> list[dict[str, Any]]:
    """发现源码目录中的 Skill，并以 frontmatter 作为唯一元数据。"""
    specs: list[dict[str, Any]] = []
    sources = [BUILTIN_SKILLS_DIR, *get_plugin_skill_directories()]
    source_dirs = [source_dir for directory in sources for source_dir in sorted(directory.iterdir())]
    seen_slugs: set[str] = set()
    for source_dir in source_dirs:
        if not source_dir.is_dir() or source_dir.name.startswith(("_", ".")):
            continue
        slug = source_dir.name
        if slug in seen_slugs:
            raise ValueError(f"内置 skill slug 重复: {slug}")
        seen_slugs.add(slug)
        skill_md = source_dir / "SKILL.md"
        if not skill_md.exists():
            raise ValueError(f"内置 skill 缺少 SKILL.md: {source_dir}")

        content = skill_md.read_text(encoding="utf-8")
        parsed_slug, parsed_name, parsed_desc, meta = parse_skill_markdown(content)
        if parsed_slug != slug:
            raise ValueError(f"内置 skill frontmatter.slug 必须等于 slug: {slug}")

        specs.append(
            {
                "slug": slug,
                "name": parsed_name,
                "description": parsed_desc,
                "version": str(meta.get("version", "1.0.0")),
                "content_hash": compute_skill_directory_hash(source_dir).hex(),
                "source_dir": source_dir,
            }
        )

    return specs


def _replace_skill_target(
    target_dir: Path,
    source_dir: Path,
) -> None:
    """将 source_dir 复制到临时目录，再替换 target_dir。"""
    temp_target = target_dir.with_name(f".{target_dir.name}.tmp-{uuid.uuid4().hex[:8]}")
    trash_dir: Path | None = None
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)

    copy_skill_tree_no_symlinks(source_dir, temp_target)
    try:
        if target_dir.exists():
            trash_dir = target_dir.with_name(f".{target_dir.name}.bak-{uuid.uuid4().hex[:8]}")
            target_dir.rename(trash_dir)
        temp_target.rename(target_dir)
    except Exception:
        shutil.rmtree(temp_target, ignore_errors=True)
        if trash_dir and trash_dir.exists() and not target_dir.exists():
            trash_dir.rename(target_dir)
        raise

    if trash_dir and trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)
