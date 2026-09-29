"""共享 Skill 在线编辑用例。"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.config import get_skill_data_dir
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.skills.package import (
    TEXT_FILE_EXTENSIONS,
    is_valid_skill_slug,
    parse_skill_markdown,
    split_skill_frontmatter,
    validated_shared_skill_parts,
    validated_skill_file_parts,
)
from yuxi.services.skills.shared import (
    get_manageable_skill_or_raise,
    get_management_readable_skill_or_raise,
    is_builtin_skill,
    user_can_access_skill,
    user_can_manage_skill,
    validate_skill_dependencies,
)
from yuxi.storage.postgres.models_business import Skill, User
from yuxi.utils.paths import open_directory_fd, open_regular_file_fd


class SkillEditConflict(ValueError):
    """文件自上次读取后已被修改。"""


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
        update_content=lambda _previous: content,
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

    def update_dependencies(previous: str) -> str:
        """保留根文件正文，将依赖表单写入前置元数据。"""
        _, _, _, frontmatter = parse_skill_markdown(previous)
        frontmatter.update(
            tool_dependencies=tool_dependencies,
            mcp_dependencies=mcp_dependencies,
            skill_dependencies=skill_dependencies,
        )
        _, body = split_skill_frontmatter(previous)
        return "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n" + body

    return await _edit_shared_skill(
        db,
        slug=slug,
        relative_path="SKILL.md",
        update_content=update_dependencies,
        expected_revision=expected_revision,
        operator=operator,
    )


async def get_skill_tree(db: AsyncSession, *, slug: str, operator: User) -> list[dict[str, object]]:
    """在共享行锁下读取目录树，并拒绝链接及特殊文件。"""
    item = await _lock_readable_skill(db, operator, slug)
    skill_fd = open_shared_skill_dir(item)
    try:
        return _build_tree(skill_fd)
    finally:
        os.close(skill_fd)


async def read_skill_file(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    operator: User,
) -> dict[str, object]:
    """锁定共享来源，读取原始字节及同一版本的索引。"""
    parts = _skill_path_parts(relative_path, text_only=True)
    item = await _lock_readable_skill(db, operator, slug)
    with ExitStack() as resources:
        skill_fd = open_shared_skill_dir(item)
        resources.callback(os.close, skill_fd)
        try:
            parent_fd = _open_parent_dir(skill_fd, parts)
            resources.callback(os.close, parent_fd)
            raw, _mode = _read_current_file(parent_fd, parts[-1])
        except FileNotFoundError as exc:
            raise ValueError(f"文件不存在: {relative_path}") from exc
        except PermissionError as exc:
            raise ValueError("非法路径：不允许符号链接或特殊文件") from exc
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文件编码不支持（仅支持 UTF-8）") from exc
    return {
        "path": "/".join(parts),
        "content": content,
        "revision": hashlib.sha256(raw).hexdigest(),
        "skill": (
            {
                "name": item.name,
                "description": item.description,
                "tool_dependencies": item.tool_dependencies or [],
                "mcp_dependencies": item.mcp_dependencies or [],
                "skill_dependencies": item.skill_dependencies or [],
            }
            if parts == ("SKILL.md",)
            else None
        ),
    }


async def create_skill_node(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    is_dir: bool,
    content: str | None,
    operator: User,
) -> None:
    """在共享行锁下创建节点，提交失败时撤回新节点。"""
    parts = _skill_path_parts(relative_path, text_only=not is_dir)
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")
    if parts == ("SKILL.md",) and is_dir:
        raise ValueError("根级 SKILL.md 必须是文本文件")
    if parts == ("SKILL.md",):
        await _sync_root_index(db, SkillRepository(db), item, content or "", operator)

    with ExitStack() as resources:
        skill_fd = open_shared_skill_dir(item)
        resources.callback(os.close, skill_fd)
        parent_fd = _open_parent_dir(skill_fd, parts, create=True)
        resources.callback(os.close, parent_fd)
        created = False
        try:
            if is_dir:
                os.mkdir(parts[-1], 0o755, dir_fd=parent_fd)
                created = True
            else:
                file_fd = os.open(
                    parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent_fd
                )
                created = True
                resources.callback(os.close, file_fd)
                with os.fdopen(file_fd, "wb", closefd=False) as stream:
                    stream.write((content or "").encode("utf-8"))
                    stream.flush()
                    os.fsync(file_fd)
            os.fsync(parent_fd)
            await db.commit()
        except Exception:
            if created:
                if is_dir:
                    os.rmdir(parts[-1], dir_fd=parent_fd)
                else:
                    os.unlink(parts[-1], dir_fd=parent_fd)
            raise


async def delete_skill_node(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    operator: User,
) -> None:
    """把待删除节点移出 Skill 目录，提交失败时恢复。"""
    parts = _skill_path_parts(relative_path)
    if parts == ("SKILL.md",):
        raise ValueError("不允许删除根目录 SKILL.md")
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")
    with ExitStack() as resources:
        skill_fd = open_shared_skill_dir(item)
        resources.callback(os.close, skill_fd)
        parent_fd = _open_parent_dir(skill_fd, parts)
        resources.callback(os.close, parent_fd)
        entry = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        if not (stat.S_ISDIR(entry.st_mode) or stat.S_ISREG(entry.st_mode)):
            raise ValueError("Skill 文件路径非法")
        staging_fd = _open_staging_dir()
        trash = f".deleted-{slug}-{uuid.uuid4().hex}"
        resources.callback(os.close, staging_fd)
        os.rename(parts[-1], trash, src_dir_fd=parent_fd, dst_dir_fd=staging_fd)
        try:
            os.fsync(parent_fd)
            await db.commit()
        except Exception:
            os.rename(trash, parts[-1], src_dir_fd=staging_fd, dst_dir_fd=parent_fd)
            raise
        if stat.S_ISDIR(entry.st_mode):
            shutil.rmtree(trash, dir_fd=staging_fd)
        else:
            os.unlink(trash, dir_fd=staging_fd)


async def export_skill_zip(db: AsyncSession, *, slug: str, operator: User) -> tuple[str, str]:
    """在共享行锁下逐个 no-follow 读取并打包文件。"""
    item = await _lock_readable_skill(db, operator, slug)
    if not user_can_manage_skill(operator, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    with ExitStack() as resources:
        skill_fd = open_shared_skill_dir(item)
        resources.callback(os.close, skill_fd)
        export_fd, export_path = tempfile.mkstemp(prefix=f"skill-{slug}-", suffix=".zip")
        os.close(export_fd)
        try:
            with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                _write_archive_tree(archive, skill_fd, slug)
        except Exception:
            Path(export_path).unlink(missing_ok=True)
            raise
    return export_path, f"{slug}.zip"


def open_shared_skill_dir(item: Skill) -> int:
    """从可信共享根逐层 no-follow 打开数据库指向的目录。"""
    return open_directory_fd(Path(get_skill_data_dir()), validated_shared_skill_parts(item.slug, item.dir_path))


async def _edit_shared_skill(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    update_content: Callable[[str], str],
    expected_revision: str,
    operator: User,
) -> tuple[Skill, str]:
    """在同一行锁下校验、发布文件并提交数据库索引。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    if not expected_revision:
        raise ValueError("缺少文件修订值，请重新加载后保存")
    parts = _skill_path_parts(relative_path, text_only=True)
    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True)
    if item is None or not user_can_manage_skill(operator, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")

    with ExitStack() as resources:
        skill_fd = open_shared_skill_dir(item)
        resources.callback(os.close, skill_fd)
        parent_fd = _open_parent_dir(skill_fd, parts)
        resources.callback(os.close, parent_fd)
        staging_fd = _open_staging_dir()
        resources.callback(os.close, staging_fd)
        try:
            previous, mode = _read_current_file(parent_fd, parts[-1])
        except FileNotFoundError as exc:
            raise ValueError("文件不存在") from exc
        except PermissionError as exc:
            raise ValueError("非法路径：不允许符号链接或特殊文件") from exc
        if hashlib.sha256(previous).hexdigest() != expected_revision:
            raise SkillEditConflict("文件已被其他编辑更新，请复制当前草稿后重新加载")
        content = update_content(previous.decode("utf-8"))
        new_bytes = content.encode("utf-8")
        if parts == ("SKILL.md",):
            await _sync_root_index(db, repo, item, content, operator)

        try:
            _replace_file(staging_fd, parent_fd, parts[-1], new_bytes, mode)
            await db.commit()
        except Exception:
            _replace_file(staging_fd, parent_fd, parts[-1], previous, mode)
            raise
        return item, hashlib.sha256(new_bytes).hexdigest()


async def _lock_readable_skill(db: AsyncSession, operator: User, slug: str) -> Skill:
    """锁前筛候选，锁后重新检查读取或管理权限。"""
    candidate = await get_management_readable_skill_or_raise(db, operator, slug)
    item = await SkillRepository(db).get_by_slug_for_read(candidate.slug)
    if item is None or (not user_can_manage_skill(operator, item) and not user_can_access_skill(operator, item)):
        raise ValueError(f"技能 '{slug}' 不存在或无权访问")
    return item


def _skill_path_parts(relative_path: str, *, text_only: bool = False) -> tuple[str, ...]:
    """校验共享 Skill 根下的相对路径组件。"""
    parts = validated_skill_file_parts(relative_path)
    if text_only and Path(parts[-1]).suffix.lower() not in TEXT_FILE_EXTENSIONS:
        raise ValueError("仅支持编辑文本文件")
    return parts


def _open_staging_dir() -> int:
    """打开共享 Skill 根，用于目录外的发布与补偿。"""
    return open_directory_fd(Path(get_skill_data_dir()), ("shared",))


def _open_parent_dir(skill_fd: int, parts: tuple[str, ...], *, create: bool = False) -> int:
    """打开相对父目录，并将链接路径统一映射为输入错误。"""
    try:
        return open_directory_fd(skill_fd, parts[:-1], create=create)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("非法路径：不允许符号链接") from exc
        raise


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


def _build_tree(directory_fd: int, prefix: str = "") -> list[dict[str, object]]:
    """从已打开目录生成拒绝符号链接的文件树。"""
    children = []
    for name in os.listdir(directory_fd):
        mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("Skill 来源包含链接或特殊文件")
        path = f"{prefix}/{name}" if prefix else name
        if stat.S_ISDIR(mode):
            child_fd = open_directory_fd(directory_fd, (name,))
            try:
                children.append({"name": name, "path": path, "is_dir": True, "children": _build_tree(child_fd, path)})
            finally:
                os.close(child_fd)
        else:
            children.append({"name": name, "path": path, "is_dir": False})
    return sorted(children, key=lambda child: (not child["is_dir"], str(child["name"]).lower()))


def _write_archive_tree(archive: zipfile.ZipFile, directory_fd: int, prefix: str) -> None:
    """从目录 fd 向 ZIP 写入普通文件及空目录。"""
    for name in sorted(os.listdir(directory_fd)):
        mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
        path = f"{prefix}/{name}"
        if stat.S_ISDIR(mode):
            child_fd = open_directory_fd(directory_fd, (name,))
            try:
                archive.writestr(f"{path}/", b"")
                _write_archive_tree(archive, child_fd, path)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(mode):
            with open_regular_file_fd(directory_fd, (name,)) as (file_fd, _file_stat):
                with archive.open(path, "w") as output:
                    while chunk := os.read(file_fd, 1024 * 1024):
                        output.write(chunk)
        else:
            raise ValueError("Skill 来源包含链接或特殊文件")


async def _sync_root_index(db: AsyncSession, repo: SkillRepository, item: Skill, content: str, operator: User) -> None:
    """根文件发布前校验元数据和平台依赖，并更新同一行索引。"""
    parsed_slug, name, description, meta = parse_skill_markdown(content)
    if parsed_slug != item.slug:
        raise ValueError("SKILL.md frontmatter.slug 必须与 skill slug 一致")
    for key in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
        value = meta.get(key, [])
        if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
            raise ValueError(f"{key} 必须是字符串列表")
    available = {skill.slug: skill for skill in await SkillRepository(db).list_enabled_readable(operator)}
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
