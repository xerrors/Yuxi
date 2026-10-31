"""个人 Skill 的草稿确认、文件操作与持久来源边界。"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from yuxi.infrastructure.filesystem import await_io, ensure_within_root, open_directory_fd, open_regular_file_fd, publish_directory_fd
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.skills.install_draft import consume_installed_draft_items, load_and_select_draft_items
from yuxi.modules.extensions.skills.package import (
    TEXT_FILE_EXTENSIONS,
    copy_skill_snapshot,
    is_valid_skill_slug,
    parse_skill_dir_metadata,
    skill_tree_contains_symlink,
    validated_skill_file_parts,
)
from yuxi.modules.extensions.skills.resolved import ResolvedSkill
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir

PERSONAL_SKILL_SOURCE_TYPE = "personal"


async def confirm_personal_skill_install_draft(
    *,
    draft_id: str,
    slugs: list[str] | None,
    operator: User,
) -> list[dict[str, Any]]:
    """确认草稿并将选中 Skill 安装到当前用户个人持久源。"""
    draft_dir, data, draft_items = load_and_select_draft_items(draft_id, slugs, operator)

    results: list[dict[str, Any]] = []
    for draft_item in draft_items:
        slug = draft_item.slug
        try:
            item = await install_personal_skill_dir(
                str(operator.uid),
                draft_item.source_dir,
                expected_slug=slug,
            )
            results.append(
                {
                    "slug": item.slug,
                    "requested_slug": slug,
                    "success": True,
                    "skill": item.to_dict(),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "slug": slug,
                    "requested_slug": slug,
                    "success": False,
                    "error": str(exc),
                }
            )

    consume_installed_draft_items(draft_dir, data, {item["requested_slug"] for item in results if item["success"]})
    return results


async def list_personal_skills(uid: str) -> list[ResolvedSkill]:
    """直接扫描个人 Skill 持久目录。"""
    return await asyncio.to_thread(_scan_personal_skills, uid)


async def install_personal_skill_dir(
    uid: str,
    source_dir: Path | str,
    *,
    expected_slug: str | None = None,
) -> ResolvedSkill:
    """将一个 Skill 原子安装到当前用户个人持久源。"""
    return await await_io(asyncio.to_thread(_install_personal_skill_dir_sync, uid, Path(source_dir), expected_slug=expected_slug))


async def read_personal_skill_file(uid: str, slug: str, relative_path: str) -> dict[str, Any]:
    """读取个人 Skill 中的文本文件。"""
    skill_dir = _resolve_personal_skill_dir(_personal_skills_root(uid), slug)
    parts = validated_skill_file_parts(relative_path, error_message="个人 Skill 文件路径非法")
    if Path(parts[-1]).suffix.lower() not in TEXT_FILE_EXTENSIONS:
        raise ValueError("仅支持读取文本文件")
    try:
        with open_regular_file_fd(skill_dir, parts) as (file_fd, _file_stat):
            with os.fdopen(os.dup(file_fd), encoding="utf-8") as stream:
                content = stream.read()
    except FileNotFoundError as exc:
        raise ValueError("文件不存在") from exc
    except PermissionError as exc:
        raise ValueError("个人 Skill 文件路径非法") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("文件编码不支持（仅支持 UTF-8）") from exc
    return {"path": "/".join(parts), "content": content}


async def delete_personal_skill(uid: str, slug: str) -> None:
    """删除当前用户个人 Skill。"""
    skill_dir = _resolve_personal_skill_dir(_personal_skills_root(uid), slug)
    if not skill_dir.is_dir():
        raise ValueError("个人 Skill 不存在")
    await await_io(asyncio.to_thread(shutil.rmtree, skill_dir))


def _scan_personal_skills(uid: str) -> list[ResolvedSkill]:
    """扫描并校验当前用户个人 Skill 的直接子目录。"""
    items: list[ResolvedSkill] = []
    root = _personal_skills_root(uid)
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if entry.is_symlink() or not entry.is_dir() or not is_valid_skill_slug(entry.name):
            logger.warning(f"跳过非法个人 Skill 目录: uid={uid}, name={entry.name}")
            continue
        if skill_tree_contains_symlink(entry):
            logger.warning(f"跳过包含符号链接的个人 Skill: uid={uid}, slug={entry.name}")
            continue
        try:
            metadata = parse_skill_dir_metadata(entry)
            if metadata["slug"] != entry.name:
                raise ValueError("目录名必须与 SKILL.md slug 一致")
            items.append(_resolved_personal_skill(uid, root, metadata))
        except Exception as exc:
            logger.warning(f"跳过无法解析的个人 Skill: uid={uid}, slug={entry.name}, error={exc}")
    return items


def _install_personal_skill_dir_sync(
    uid: str,
    source_dir: Path,
    *,
    expected_slug: str | None = None,
) -> ResolvedSkill:
    """将一个 Skill 原子复制到个人目录。"""
    source_dir = source_dir.resolve()
    root = _personal_skills_root(uid)
    temp_target = root / f".install.tmp-{uuid.uuid4().hex[:8]}"
    try:
        metadata = copy_skill_snapshot(source_dir, temp_target, expected_slug=expected_slug)
        slug = metadata["slug"]
        target_dir = root / slug
        if target_dir.exists() or target_dir.is_symlink():
            raise ValueError(f"个人 Skill 源已存在同名 Skill: {slug}")
        try:
            directory_fd = open_directory_fd(root, ())
            try:
                publish_directory_fd(directory_fd, temp_target.name, directory_fd, target_dir.name)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if target_dir.exists():
                raise ValueError(f"个人 Skill 源已存在同名 Skill: {slug}") from exc
            raise
    finally:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
    return _resolved_personal_skill(uid, root, metadata)


def _resolved_personal_skill(uid: str, root: Path, metadata: dict[str, Any]) -> ResolvedSkill:
    """将个人目录元数据适配为不含共享语义的有效 Skill 描述。"""
    slug = metadata["slug"]
    source_dir = root / slug
    return ResolvedSkill(
        id=f"personal:{slug}",
        slug=slug,
        name=metadata["name"],
        description=metadata["description"],
        source_type=PERSONAL_SKILL_SOURCE_TYPE,
        source_scope=PERSONAL_SKILL_SOURCE_TYPE,
        source_dir=source_dir,
        enabled=True,
        created_by=uid,
        share_config=None,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )


def _personal_skills_root(uid: str) -> Path:
    """返回已创建且位于当前用户工作区内的个人 Skill 根。"""
    ensure_user_workspace(uid)
    workspace = user_workspace_dir(uid)
    workspace_root = workspace.resolve()
    root = ensure_within_root((workspace / "agents" / "skills").resolve(), workspace_root, error_message="个人 Skill 路径越界")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_personal_skill_dir(root: Path, slug: str) -> Path:
    """安全解析固定根下的个人 Skill 目录。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    target = root / slug
    if target.is_symlink():
        raise ValueError("个人 Skill 路径非法")
    return target
