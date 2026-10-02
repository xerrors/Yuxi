"""个人 Skill 的草稿确认、文件操作与持久来源边界。"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from yuxi.agents.backends.paths import VIRTUAL_PATH_PREFIX
from yuxi.agents.backends.sandbox.download import download_sandbox_directory
from yuxi.services.skills.draft import consume_installed_draft_items, load_and_select_draft_items
from yuxi.services.skills.package import (
    TEXT_FILE_EXTENSIONS,
    copy_skill_snapshot,
    is_valid_skill_slug,
    parse_skill_dir_metadata,
    skill_tree_contains_symlink,
    validated_skill_file_parts,
)
from yuxi.services.skills.resolved import ResolvedSkill
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import ensure_within_root, open_regular_file_fd
from yuxi.workspace.paths import ensure_user_workspace, user_workspace_dir

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


async def install_personal_skills_from_source(
    *,
    uid: str,
    thread_id: str,
    source: str,
    skill_names: list[str] | None = None,
    workdir_relative_path: str | None = None,
    workdir_path: str | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """获取沙盒或远程包并安装到用户个人来源，汇总逐项结果。"""
    source = source.strip()
    if not source:
        raise ValueError("Skill 来源不能为空")
    if source.startswith("/"):
        with tempfile.TemporaryDirectory(prefix=".skill-install-") as tmp:
            source_dir = await asyncio.to_thread(
                _download_sandbox_skill, source, thread_id, uid, Path(tmp), workdir_relative_path, workdir_path
            )
            item = await install_personal_skill_dir(uid, source_dir)
            return [item.slug], []
    if not skill_names:
        raise ValueError("从 Git 安装时必须通过 skill_names 指定技能名称")

    from yuxi.services.skills.remote import SkillDownloadFailure, download_remote_skills

    installed: list[str] = []
    failures: list[dict[str, str]] = []
    downloads = await download_remote_skills(source=source, skills=skill_names)
    try:
        for result in downloads.results:
            if isinstance(result, SkillDownloadFailure):
                failures.append({"slug": result.slug, "error": result.error})
                continue
            try:
                item = await install_personal_skill_dir(uid, result.source_dir)
                installed.append(item.slug)
            except Exception as exc:
                failures.append({"slug": result.slug, "error": str(exc)})
    finally:
        await downloads.cleanup()
    return installed, failures


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
    return await asyncio.to_thread(
        _install_personal_skill_dir_sync,
        uid,
        Path(source_dir),
        expected_slug=expected_slug,
    )


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
    await asyncio.to_thread(shutil.rmtree, skill_dir)


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
            temp_target.rename(target_dir)
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
    root = ensure_within_root(
        (workspace / "agents" / "skills").resolve(), workspace_root, error_message="个人 Skill 路径越界"
    )
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


def _download_sandbox_skill(
    sandbox_path: str,
    thread_id: str,
    uid: str,
    staging_root: Path,
    workdir_relative_path: str | None = None,
    workdir_path: str | None = None,
) -> Path:
    """从当前用户沙盒下载 Skill 目录到本地暂存区。"""
    from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend

    allowed = sandbox_path.startswith(f"{VIRTUAL_PATH_PREFIX.rstrip('/')}/")
    allowed = allowed or bool(workdir_path and sandbox_path.startswith(f"{workdir_path.rstrip('/')}/"))
    if not allowed:
        raise ValueError(
            f"不支持的沙盒路径: {sandbox_path}。请使用当前 Project Workdir 下的目录，或 /home/gem/user-data/..."
        )

    staging = staging_root / "package"
    backend = ProvisionerSandboxBackend(
        thread_id=thread_id,
        uid=uid,
        workdir_path=workdir_relative_path,
        create_if_missing=True,
    )
    download_sandbox_directory(
        backend,
        sandbox_path,
        staging,
        empty_message=f"沙盒路径 {sandbox_path} 中未发现可下载文件",
    )
    if not (staging / "SKILL.md").exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"沙盒路径 {sandbox_path} 中未找到 SKILL.md")

    return staging
