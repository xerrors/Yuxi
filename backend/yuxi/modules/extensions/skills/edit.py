"""共享 Skill 在线编辑用例。"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import open_directory_fd, open_regular_file_fd
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.skills.access import (
    get_manageable_skill_or_raise,
    get_management_readable_skill_or_raise,
    user_can_access_skill,
    user_can_manage_skill,
)
from yuxi.modules.extensions.skills.content import commit_skill_content, content_path
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import (
    TEXT_FILE_EXTENSIONS,
    compute_skill_directory_hash,
    validated_shared_skill_parts,
    validated_skill_file_parts,
)
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User


async def edit_shared_skill_file(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    content: str,
    expected_revision: str,
    operator: User,
) -> tuple[Skill, str]:
    """单文件协议沿用文件修订，内容交给统一提交入口。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    await commit_skill_content(
        db,
        item=item,
        operator=operator,
        expected_revision=None,
        expected_file=(relative_path, expected_revision),
        changes=[{"action": "write", "path": relative_path, "content": content}],
    )
    return item, hashlib.sha256(content.encode()).hexdigest()


async def read_skill_content(db: AsyncSession, *, slug: str, operator: User) -> dict:
    """在同一内容锁内读取文件树、可编辑文本和整包修订。"""
    item = await _lock_readable_skill(db, operator, slug)
    files = {}
    with ExitStack() as resources:
        directory_fd = open_shared_skill_dir(item)
        resources.callback(os.close, directory_fd)
        tree = _build_tree(directory_fd)

        def read_nodes(nodes):
            """从已验证文件树读取 UTF-8 文本，二进制文件保留树节点。"""
            for node in nodes:
                if node["is_dir"]:
                    read_nodes(node["children"])
                elif Path(node["path"]).suffix.lower() in TEXT_FILE_EXTENSIONS:
                    with open_regular_file_fd(directory_fd, tuple(node["path"].split("/"))) as (fd, _):
                        with os.fdopen(os.dup(fd), "rb") as stream:
                            raw = stream.read()
                    try:
                        files[node["path"]] = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        pass

        read_nodes(tree)
    return {"tree": tree, "files": files, "revision": compute_skill_directory_hash(content_path(item)).hex()}


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
    """节点创建复用完整内容提交。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    revision = compute_skill_directory_hash(content_path(item)).hex()
    await commit_skill_content(
        db,
        item=item,
        operator=operator,
        expected_revision=revision,
        changes=[
            {
                "action": "mkdir" if is_dir else "create",
                "path": relative_path,
                "content": content or "",
            }
        ],
    )


async def delete_skill_node(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    operator: User,
) -> None:
    """节点删除复用完整内容提交。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    revision = compute_skill_directory_hash(content_path(item)).hex()
    await commit_skill_content(
        db,
        item=item,
        operator=operator,
        expected_revision=revision,
        changes=[{"action": "delete", "path": relative_path}],
    )


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
