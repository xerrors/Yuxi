"""共享 Skill 在线编辑用例。"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import uuid
from pathlib import Path, PurePosixPath

import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import (
    TEXT_FILE_EXTENSIONS,
    is_builtin_skill,
    is_valid_skill_slug,
    list_accessible_shared_skills,
    parse_skill_markdown,
    split_skill_frontmatter,
    user_can_manage_skill,
    validate_skill_dependencies,
)
from yuxi.config import get_skill_data_dir
from yuxi.storage.postgres.models_business import Skill, User
from yuxi.utils.paths import open_directory_fd, open_regular_file_fd


class SkillEditConflict(ValueError):
    """文件自上次读取后已被修改。"""


def _skill_file_parts(relative_path: str) -> tuple[str, ...]:
    """校验共享 Skill 内的文本文件路径。"""
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        raise ValueError("非法 Skill 文件路径")
    parts = PurePosixPath(relative_path).parts
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("非法 Skill 文件路径")
    if Path(parts[-1]).suffix.lower() not in TEXT_FILE_EXTENSIONS:
        raise ValueError("仅支持编辑文本文件")
    return parts


def _read_current_file(parent_fd: int, filename: str) -> tuple[bytes, int]:
    """从可信目录读取普通文件与其权限位。"""
    with open_regular_file_fd(parent_fd, (filename,)) as (file_fd, file_stat):
        chunks = []
        while chunk := os.read(file_fd, 1024 * 1024):
            chunks.append(chunk)
    return b"".join(chunks), stat.S_IMODE(file_stat.st_mode)


def _replace_file(staging_fd: int, parent_fd: int, filename: str, content: bytes, mode: int) -> None:
    """在 Skill 目录外暂存后原子替换文件。"""
    temporary = f".{filename}.{uuid.uuid4().hex}.tmp"
    file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=staging_fd)
    try:
        os.fchmod(file_fd, mode)
        with os.fdopen(file_fd, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(file_fd)
        os.replace(temporary, filename, src_dir_fd=staging_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(file_fd)
        try:
            os.unlink(temporary, dir_fd=staging_fd)
        except FileNotFoundError:
            pass


async def edit_shared_skill_file(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    content: str,
    expected_revision: str,
    operator: User,
) -> tuple[Skill, str]:
    """按预期修订值保存共享 Skill 文本文件。"""
    return await _edit_shared_skill(
        db,
        slug=slug,
        relative_path=relative_path,
        content=content,
        expected_revision=expected_revision,
        operator=operator,
    )


async def edit_shared_skill_dependencies(
    db: AsyncSession,
    *,
    slug: str,
    tool_dependencies: list[str],
    mcp_dependencies: list[str],
    skill_dependencies: list[str],
    expected_revision: str,
    operator: User,
) -> tuple[Skill, str]:
    """将依赖表单写入根文件并同步数据库索引。"""
    return await _edit_shared_skill(
        db,
        slug=slug,
        relative_path="SKILL.md",
        content=None,
        expected_revision=expected_revision,
        operator=operator,
        dependency_updates={
            "tool_dependencies": tool_dependencies,
            "mcp_dependencies": mcp_dependencies,
            "skill_dependencies": skill_dependencies,
        },
    )


async def _edit_shared_skill(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    content: str | None,
    expected_revision: str,
    operator: User,
    dependency_updates: dict[str, list[str]] | None = None,
) -> tuple[Skill, str]:
    """在同一行锁下校验、发布文件并提交数据库索引。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    if not expected_revision:
        raise ValueError("缺少文件修订值，请重新加载后保存")
    parts = _skill_file_parts(relative_path)
    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True)
    if item is None or not user_can_manage_skill(operator, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")

    skill_dir = Path(get_skill_data_dir()) / item.dir_path
    try:
        parent_fd = open_directory_fd(skill_dir, parts[:-1])
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("非法路径：不允许符号链接") from exc
        raise
    try:
        staging_fd = open_directory_fd(skill_dir.parent, ())
    except Exception:
        os.close(parent_fd)
        raise
    try:
        try:
            previous, mode = _read_current_file(parent_fd, parts[-1])
        except FileNotFoundError as exc:
            raise ValueError("文件不存在") from exc
        except PermissionError as exc:
            raise ValueError("非法路径：不允许符号链接或特殊文件") from exc
        if hashlib.sha256(previous).hexdigest() != expected_revision:
            raise SkillEditConflict("文件已被其他编辑更新，请复制当前草稿后重新加载")
        if dependency_updates is not None:
            _, _, _, frontmatter = parse_skill_markdown(previous.decode("utf-8"))
            frontmatter.update(dependency_updates)
            _, body = split_skill_frontmatter(previous.decode("utf-8"))
            content = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n" + body

        assert content is not None
        new_bytes = content.encode("utf-8")
        if parts == ("SKILL.md",):
            parsed_slug, name, description, meta = parse_skill_markdown(content)
            if parsed_slug != item.slug:
                raise ValueError("SKILL.md frontmatter.slug 必须与 skill slug 一致")
            for key in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
                value = meta.get(key, [])
                if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
                    raise ValueError(f"{key} 必须是字符串列表")
            available = {skill.slug: skill for skill in await list_accessible_shared_skills(db, operator)}
            tools, mcps, skills = await validate_skill_dependencies(
                parent=item,
                tool_dependencies=meta.get("tool_dependencies") or [],
                mcp_dependencies=meta.get("mcp_dependencies") or [],
                skill_dependencies=meta.get("skill_dependencies") or [],
                available_skills=available,
            )
            for key, normalized in (
                ("tool_dependencies", tools),
                ("mcp_dependencies", mcps),
                ("skill_dependencies", skills),
            ):
                if key in meta and meta[key] != normalized:
                    raise ValueError(f"{key} 含重复或空值")
            await repo.update_metadata(item, name=name, description=description, updated_by=operator.uid)
            await repo.update_dependencies(
                item,
                tool_dependencies=tools,
                mcp_dependencies=mcps,
                skill_dependencies=skills,
                updated_by=operator.uid,
            )

        try:
            _replace_file(staging_fd, parent_fd, parts[-1], new_bytes, mode)
        except Exception:
            _replace_file(staging_fd, parent_fd, parts[-1], previous, mode)
            raise
        try:
            await db.commit()
        except Exception:
            _replace_file(staging_fd, parent_fd, parts[-1], previous, mode)
            raise
        return item, hashlib.sha256(new_bytes).hexdigest()
    finally:
        os.close(parent_fd)
        os.close(staging_fd)
