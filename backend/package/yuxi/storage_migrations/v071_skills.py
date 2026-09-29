"""把 v0.7.1 共享 Skill 源一次性迁入当前存储域。"""

import asyncio
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.config import get_legacy_storage_dir, get_skill_data_dir
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.skills.package import copy_skill_tree_no_symlinks, parse_skill_dir_metadata
from yuxi.services.skills.projection import skill_dirs_equal
from yuxi.services.skills.shared import SKILL_STORAGE_LOCK, get_skills_root_dir

_MIGRATION_MARKER = ".legacy-migration-complete"


async def migrate_shared_skills(db: AsyncSession) -> None:
    """迁移 v0.7.1 共享 Skill，不触碰 UserWorkspace 中的个人 Skill。"""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SKILL_STORAGE_LOCK})

    repo = SkillRepository(db)
    migrated_sources: list[Path] = []
    legacy_shared_root = get_legacy_storage_dir() / "skills"
    if legacy_shared_root.is_symlink():
        raise ValueError(f"共享 Skill 历史根目录非法: {legacy_shared_root}")

    shared_items = await repo.list_all()
    for item in shared_items:
        legacy_path = legacy_shared_root / item.slug
        target_path = get_skills_root_dir() / item.slug
        legacy_path_exists = legacy_path.exists() or legacy_path.is_symlink()
        legacy_dir_path = Path("skills") / item.slug
        current_dir_path = Path("shared") / item.slug
        uses_legacy_path = Path(item.dir_path) == legacy_dir_path
        if legacy_path_exists and (uses_legacy_path or Path(item.dir_path) == current_dir_path):
            await asyncio.to_thread(_migrate_skill_tree, legacy_path, target_path, expected_slug=item.slug)
            migrated_sources.append(legacy_path)
        if uses_legacy_path:
            if not legacy_path_exists and item.source_type != "builtin" and not target_path.is_dir():
                raise RuntimeError(f"Skill 持久源缺失，拒绝切换: {item.slug}")
            item.dir_path = current_dir_path.as_posix()

    registered_shared_names = {item.slug for item in shared_items}
    if legacy_shared_root.is_dir():
        orphan_root = get_skill_data_dir() / "legacy-orphans" / "shared"
        for entry in sorted(legacy_shared_root.iterdir(), key=lambda path: path.name):
            if entry.name in registered_shared_names:
                continue
            if entry.is_symlink() or not entry.is_dir():
                raise ValueError(f"未登记的共享 Skill 历史来源非法: {entry}")
            await asyncio.to_thread(_migrate_orphan_tree, entry, orphan_root / entry.name)
            migrated_sources.append(entry)

    await db.commit()
    for source in sorted(migrated_sources, key=lambda path: len(path.parts), reverse=True):
        if source.exists() and source.is_dir() and not source.is_symlink():
            shutil.rmtree(source)


def migration_completed() -> bool:
    """返回 v0.7.1 共享 Skill 是否已经完成接管。"""
    return (get_skill_data_dir() / _MIGRATION_MARKER).is_file()


def mark_migrated() -> None:
    """原子写入 v0.7.1 共享 Skill 迁移完成标记。"""
    root = get_skill_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / _MIGRATION_MARKER
    temp = root / f".{_MIGRATION_MARKER}.{uuid.uuid4().hex}.tmp"
    temp.write_text("1\n", encoding="utf-8")
    temp.replace(marker)


def _migrate_skill_tree(source_dir: Path, target_dir: Path, *, expected_slug: str) -> None:
    """安全复制一个 v0.7.1 Skill 源。"""
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError(f"历史 Skill 来源不是可信目录: {source_dir}")

    def validate(temp_target: Path) -> None:
        metadata = parse_skill_dir_metadata(temp_target)
        if metadata["slug"] != expected_slug:
            raise ValueError(f"历史 Skill slug 不一致: expected={expected_slug}, actual={metadata['slug']}")

    _copy_tree_atomically(
        source_dir,
        target_dir,
        validate=validate,
        conflict_error=f"Skill 新旧持久源内容冲突: {expected_slug}",
    )


def _migrate_orphan_tree(source_dir: Path, target_dir: Path) -> None:
    """保留未登记 v0.7.1 Skill 字节但不注册为可执行 Skill。"""
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError(f"未登记 Skill 历史来源非法: {source_dir}")
    _copy_tree_atomically(
        source_dir,
        target_dir,
        conflict_error=f"未登记 Skill 新旧持久源内容冲突: {source_dir.name}",
    )


def _copy_tree_atomically(
    source_dir: Path,
    target_dir: Path,
    *,
    conflict_error: str,
    validate: Callable[[Path], None] | None = None,
) -> None:
    """复制并验证目录树，再原子发布或确认幂等目标。"""
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_dir.with_name(f".{target_dir.name}.migrate-{uuid.uuid4().hex[:8]}")
    try:
        copy_skill_tree_no_symlinks(source_dir, temp_target)
        if validate is not None:
            validate(temp_target)
        if target_dir.exists() or target_dir.is_symlink():
            if target_dir.is_symlink() or not target_dir.is_dir() or not skill_dirs_equal(target_dir, temp_target):
                raise ValueError(conflict_error)
            return
        temp_target.rename(target_dir)
    finally:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
