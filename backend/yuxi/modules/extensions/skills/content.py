"""受管理 Skill 的完整内容提交与不可变目录存储。"""

import asyncio
import errno
import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yuxi.infrastructure.filesystem import open_directory_fd
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.skills.access import (
    get_manageable_skill_or_raise,
    is_builtin_skill,
    validate_skill_dependencies,
)
from yuxi.modules.extensions.skills.models import Skill, SkillVersion
from yuxi.modules.extensions.skills.package import (
    TEXT_FILE_EXTENSIONS,
    SkillEditConflict,
    compute_skill_directory_hash,
    copy_skill_snapshot,
    parse_skill_dir_metadata,
    parse_skill_markdown,
    split_skill_frontmatter,
    validate_content_budget,
    validated_shared_skill_parts,
    validated_skill_file_parts,
)
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User
from yuxi.shared.datetime import utc_now


@dataclass
class SkillCommit:
    """一次提交的明确返回值，避免在 ORM 上挂临时状态。"""

    skill: Skill
    revision: str
    published_version: dict | None = None
    root_revision: str = ""


async def save_skill_content(
    db: AsyncSession,
    *,
    slug: str,
    expected_revision: str,
    operator: User,
    changes: list[dict],
    release: bool = False,
) -> SkillCommit:
    """一次提交全部文件与配置编辑，以整包修订保护覆盖。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    return await commit_skill_content(
        db,
        item=item,
        operator=operator,
        expected_revision=expected_revision,
        changes=changes,
        release=release,
    )


async def commit_skill_content(
    db: AsyncSession,
    *,
    item: Skill,
    operator: User,
    expected_revision: str | None,
    changes: list[dict] | None = None,
    source: Path | None = None,
    source_slug: str | None = None,
    release: bool = False,
    content_ref: Path | None = None,
    expected_file: tuple[str, str] | None = None,
) -> SkillCommit:
    """持有资源写锁时先写完整内容，再提交当前引用和可选历史记录。"""
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")
    if release and item.bound_agent_id is None:
        raise ValueError("只有 Agent 专属 Skill 支持历史版本")
    current = content_path(item) if item.dir_path else None
    if current is not None:
        try:
            revision = (await asyncio.to_thread(compute_skill_directory_hash, current)).hex()
        except PermissionError as exc:
            raise ValueError("非法路径：不允许符号链接或特殊文件") from exc
        if expected_file:
            path, expected = expected_file
            parts = validated_skill_file_parts(path)
            from yuxi.infrastructure.filesystem import open_regular_file_fd

            try:
                with open_regular_file_fd(current, parts) as (fd, _):
                    with os.fdopen(os.dup(fd), "rb") as stream:
                        file_revision = hashlib.sha256(stream.read()).hexdigest()
            except FileNotFoundError as exc:
                raise ValueError("文件不存在") from exc
            if not expected or file_revision != expected:
                raise SkillEditConflict("文件已被其他编辑更新，请重新加载后保存")
        elif not expected_revision or revision != expected_revision:
            raise SkillEditConflict("Skill 内容已被其他编辑更新，请重新加载后保存")
    elif expected_revision:
        raise SkillEditConflict("专属 Skill 绑定已变化，请重新加载")

    reuse = content_ref is not None or (not changes and source is None and item.dir_path.startswith("packages/"))
    parent_fd = open_directory_fd(get_skill_data_dir(), ("packages", item.slug), create=True)
    os.close(parent_fd)
    target = (content_ref or current) if reuse else get_skill_data_dir() / "packages" / item.slug / uuid.uuid4().hex
    committed = False
    try:
        if not reuse:
            await asyncio.to_thread(
                copy_skill_snapshot,
                source or current,
                target,
                expected_slug=source_slug or item.slug,
                final_slug=item.slug,
            )
            await asyncio.to_thread(apply_content_changes, target, changes or [])
        await asyncio.to_thread(validate_content_budget, target)
        parsed = await asyncio.to_thread(parse_skill_dir_metadata, target)
        if parsed["slug"] != item.slug:
            raise ValueError("SKILL.md frontmatter.slug 必须与 skill slug 一致")
        dependencies = await validate_skill_dependencies(
            db=db,
            parent=item,
            tool_dependencies=parsed["tool_dependencies"],
            mcp_dependencies=parsed["mcp_dependencies"],
            skill_dependencies=parsed["skill_dependencies"],
            available_skills={row.slug: row for row in await SkillRepository(db).list_enabled_readable(operator)},
        )
        item.name, item.description = parsed["name"], parsed["description"]
        item.tool_dependencies, item.mcp_dependencies, item.skill_dependencies = dependencies
        item.dir_path = target.relative_to(get_skill_data_dir()).as_posix()
        item.content_hash = (await asyncio.to_thread(compute_skill_directory_hash, target)).hex()
        item.updated_by = operator.uid
        db.add(item)
        await db.flush()
        snapshot = await record_skill_version(db, item=item, operator=operator) if release else None
        result = SkillCommit(
            item,
            item.content_hash,
            snapshot.to_dict() if snapshot else None,
            hashlib.sha256((target / "SKILL.md").read_bytes()).hexdigest(),
        )
        slug = item.slug
        await db.commit()
        committed = True
    except Exception:
        await db.rollback()
        raise
    finally:
        if not committed and not reuse:
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
            try:
                # 只回收空的 Skill 目录，已有当前或历史内容时保留目录。
                await asyncio.to_thread(target.parent.rmdir)
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, errno.ENOENT}:
                    logger.exception("Skill 失败内容的空目录清理失败: {}", target.parent)
    await prune_skill_content(db, slug)
    return result


async def record_skill_version(db: AsyncSession, *, item: Skill, operator: User) -> SkillVersion:
    """历史记录引用已校验的不可变完整内容，不再次复制文件。"""
    if item.bound_agent_id is None:
        raise ValueError("只有 Agent 专属 Skill 支持历史版本")
    row = SkillVersion(
        skill_id=item.id,
        version=f"{utc_now().astimezone(ZoneInfo('Asia/Shanghai')):%Y%m%d}-{uuid.uuid4().hex[:8]}",
        dir_path=item.dir_path,
        metadata_json={"name": item.name, "description": item.description},
        content_hash=item.content_hash,
        created_by=operator.uid,
    )
    db.add(row)
    from sqlalchemy.exc import IntegrityError

    try:
        await db.flush()
    except IntegrityError as exc:
        raise SkillEditConflict("历史版本标识冲突，请重新发版") from exc
    return row


async def prune_skill_content(owner_db: AsyncSession, slug: str) -> None:
    """在新事务的内容锁下清理无当前或历史引用的目录；失败留待下次提交。"""
    try:
        async with async_sessionmaker(owner_db.bind, expire_on_commit=False)() as db:
            repo = SkillRepository(db)
            item = await repo.get_by_slug(slug, for_update=True)
            if item is None:
                return
            keep = {item.dir_path, *(row.dir_path for row in await repo.list_versions(item.id))}
            root = get_skill_data_dir() / "packages" / slug
            if root.is_dir():
                for child in root.iterdir():
                    relative = child.relative_to(get_skill_data_dir()).as_posix()
                    if relative not in keep:
                        validated_shared_skill_parts(slug, relative)
                        await asyncio.to_thread(shutil.rmtree, child)
    except Exception:
        logger.exception("Skill 无引用内容清理失败: {}", slug)


def content_path(item: Skill) -> Path:
    """只使用 Skill 自身的合法持久内容引用。"""
    return get_skill_data_dir().joinpath(*validated_shared_skill_parts(item.slug, item.dir_path))


def apply_content_changes(target: Path, changes: list[dict]) -> None:
    """在独占的新内容目录内应用文件与依赖编辑。"""
    for change in changes:
        action = change["action"]
        if action == "dependencies":
            root = target / "SKILL.md"
            previous = root.read_text(encoding="utf-8")
            _, _, _, meta = parse_skill_markdown(previous)
            for key in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
                meta[key] = change[key]
            _, body = split_skill_frontmatter(previous)
            root.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + body, encoding="utf-8")
            continue
        parts = validated_skill_file_parts(change["path"])
        path = target.joinpath(*parts)
        if action in {"write", "create"}:
            if path.suffix.lower() not in TEXT_FILE_EXTENSIONS:
                raise ValueError("仅支持编辑文本文件")
            path.parent.mkdir(parents=True, exist_ok=True)
            if action == "create" and path.exists():
                raise ValueError("文件已存在")
            if action == "write" and not path.is_file():
                raise ValueError("文件不存在")
            path.write_text(change.get("content", ""), encoding="utf-8")
        elif action == "mkdir":
            if parts == ("SKILL.md",):
                raise ValueError("根级 SKILL.md 必须是文本文件")
            path.mkdir(parents=True, exist_ok=False)
        elif action == "delete":
            if parts == ("SKILL.md",):
                raise ValueError("不允许删除根目录 SKILL.md")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        else:
            raise ValueError("无效的 Skill 内容操作")
