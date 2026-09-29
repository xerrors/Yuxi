"""Skill 安装草稿的创建、读取、消费与删除。"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from yuxi.config import get_runtime_dir
from yuxi.services.skills.package import copy_skill_snapshot, is_valid_skill_slug
from yuxi.storage.postgres.models_business import User

SKILL_DRAFT_TTL_SECONDS = 60 * 60
ADMIN_ROLES = {"admin", "superadmin"}


@dataclass(frozen=True, slots=True)
class PreparedSkillDraftItem:
    """已经验证且可交给目标来源安装的临时包。"""

    slug: str
    source_dir: Path


async def create_uploaded_skill_draft(
    *,
    filename: str,
    file_bytes: bytes,
    operator: User,
) -> dict[str, Any]:
    """解析上传文件并暂存安装草稿。"""
    normalized_filename = filename.lower()
    is_zip_upload = normalized_filename.endswith(".zip")
    is_skill_md_upload = normalized_filename.endswith("skill.md")
    if not is_zip_upload and not is_skill_md_upload:
        raise ValueError("仅支持上传 .zip 或 SKILL.md 文件")

    draft_dir = get_skill_drafts_root_dir() / str(uuid.uuid4())
    items_dir = draft_dir / "items"
    draft_dir.mkdir(parents=True, exist_ok=False)
    items_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix=".skill-prepare-", dir=str(draft_dir)) as temp_root:
            extract_dir = Path(temp_root) / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            if is_zip_upload:
                with zipfile.ZipFile(BytesIO(file_bytes), "r") as zf:
                    _validate_zip_paths(zf)
                    zf.extractall(extract_dir)
                skill_md_files = list(extract_dir.rglob("SKILL.md"))
                if len(skill_md_files) != 1:
                    raise ValueError("ZIP 必须且只能包含一个技能（检测到一个 SKILL.md）")
                source_skill_dir = skill_md_files[0].parent
            else:
                source_skill_dir = extract_dir
                (source_skill_dir / "SKILL.md").write_bytes(file_bytes)

            item = _stage_skill_draft_item(source_skill_dir=source_skill_dir, draft_items_dir=items_dir)

        return _write_skill_draft(
            draft_dir, operator=operator, source_type="upload", source=filename, items=[item], failures=[]
        )
    except Exception:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise


async def create_remote_skill_draft(
    *,
    source: str,
    skills: list[str],
    operator: User,
) -> dict[str, Any]:
    """拉取远程 Skill 并暂存安装草稿。"""
    from yuxi.services.skills.remote import SkillDownloadFailure, download_remote_skills

    draft_dir = get_skill_drafts_root_dir() / str(uuid.uuid4())
    items_dir = draft_dir / "items"
    draft_dir.mkdir(parents=True, exist_ok=False)
    items_dir.mkdir(parents=True, exist_ok=True)

    downloads = None
    try:
        downloads = await download_remote_skills(source=source, skills=skills)
        items: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        seen_slugs: set[str] = set()
        for result in downloads.results:
            slug = result.slug
            if isinstance(result, SkillDownloadFailure):
                failures.append({"slug": slug, "error": result.error})
                continue

            try:
                item = _stage_skill_draft_item(
                    source_skill_dir=result.source_dir,
                    draft_items_dir=items_dir,
                )
            except Exception as e:
                failures.append({"slug": slug, "error": str(e)})
                continue

            if item["slug"] in seen_slugs:
                shutil.rmtree(draft_dir / item["source_dir"], ignore_errors=True)
                failures.append({"slug": slug, "error": f"Skill slug 重复: {item['slug']}"})
                continue
            seen_slugs.add(item["slug"])
            items.append(item)

        return _write_skill_draft(
            draft_dir, operator=operator, source_type="remote", source=source, items=items, failures=failures
        )
    except Exception:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise
    finally:
        if downloads is not None:
            await downloads.cleanup()


async def discard_skill_install_draft(*, draft_id: str, operator: User) -> None:
    """删除当前操作人可管理的安装草稿。"""
    draft_dir, data = load_skill_draft(draft_id)
    if data.get("created_by") != operator.uid and operator.role not in ADMIN_ROLES:
        raise ValueError("无权删除该安装草稿")
    shutil.rmtree(draft_dir, ignore_errors=True)


def load_and_select_draft_items(
    draft_id: str, slugs: list[str] | None, operator: User
) -> tuple[Path, dict, list[PreparedSkillDraftItem]]:
    """加载草稿并只返回结构、身份和路径均有效的可安装条目。"""
    draft_dir, data = load_skill_draft(draft_id)
    if not isinstance(data.get("created_by"), str) or not data["created_by"]:
        raise ValueError("安装草稿元数据非法")
    if data.get("created_by") != operator.uid and operator.role not in ADMIN_ROLES:
        raise ValueError("无权确认该安装草稿")
    if data.get("source_type") not in {"upload", "remote"}:
        raise ValueError("无效的安装草稿来源")
    failures = data.get("failures")
    if not isinstance(failures, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("slug"), str) or not isinstance(item.get("error"), str)
        for item in failures
    ):
        raise ValueError("安装草稿失败记录非法")

    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("安装草稿条目非法")
    items: list[PreparedSkillDraftItem] = []
    seen_slugs: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) or "success" in raw_item:
            raise ValueError("安装草稿条目非法")
        slug = raw_item.get("slug")
        relative_source = raw_item.get("source_dir")
        if not is_valid_skill_slug(slug) or not isinstance(relative_source, str):
            raise ValueError("安装草稿条目非法")
        if slug in seen_slugs:
            raise ValueError("安装草稿包含重复 Skill slug")
        if not re.fullmatch(r"items/[0-9a-f]{32}", relative_source):
            raise ValueError("安装草稿路径非法")
        source_dir = draft_dir / relative_source
        if source_dir.is_symlink() or source_dir.parent.is_symlink() or not source_dir.is_dir():
            raise ValueError("安装草稿路径非法")
        if not source_dir.resolve().is_relative_to(draft_dir):
            raise ValueError("安装草稿路径非法")
        items.append(PreparedSkillDraftItem(slug=slug, source_dir=source_dir))
        seen_slugs.add(slug)

    if slugs is not None:
        selected_slugs = set(slugs)
        if not selected_slugs:
            raise ValueError("至少选择一个 Skill")
        if selected_slugs - seen_slugs:
            raise ValueError("确认安装包含草稿外的 Skill 或不可安装的 Skill")
        items = [item for item in items if item.slug in selected_slugs]

    if not items:
        raise ValueError("安装草稿没有可安装的 Skill")

    return draft_dir, data, items


def consume_installed_draft_items(draft_dir: Path, data: dict, installed_slugs: set[str]) -> None:
    """移除已安装条目，保留失败和未选中的快照供后续确认。"""
    if not installed_slugs:
        return
    remaining = [item for item in data["items"] if item["slug"] not in installed_slugs]
    if not remaining:
        shutil.rmtree(draft_dir, ignore_errors=True)
        return
    updated = {**data, "items": remaining}
    metadata = draft_dir / "metadata.json"
    temporary = draft_dir / f".metadata-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(metadata)
    finally:
        temporary.unlink(missing_ok=True)
    for item in data["items"]:
        if item["slug"] in installed_slugs:
            shutil.rmtree(draft_dir / item["source_dir"], ignore_errors=True)


def load_skill_draft(draft_id: str) -> tuple[Path, dict]:
    """加载尚未过期的安装草稿及其元数据。"""
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", str(draft_id or "")):
        raise ValueError("无效的安装草稿")
    draft_dir = (get_skill_drafts_root_dir() / draft_id).resolve()
    try:
        draft_dir.relative_to(get_skill_drafts_root_dir().resolve())
    except ValueError:
        raise ValueError("无效的安装草稿") from None
    metadata_path = draft_dir / "metadata.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise ValueError("安装草稿不存在或已过期")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("expires_at"), (int, float)):
        raise ValueError("安装草稿元数据非法")
    if data["expires_at"] < time.time():
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise ValueError("安装草稿已过期")
    return draft_dir, data


def get_skill_drafts_root_dir() -> Path:
    """返回可丢弃的 Skill 安装草稿目录。"""
    root = get_runtime_dir() / "skill_import_drafts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_skill_draft(
    draft_dir: Path,
    *,
    operator: User,
    source_type: str,
    source: str,
    items: list[dict[str, Any]],
    failures: list[dict[str, str]],
) -> dict[str, Any]:
    """为已暂存的条目写入统一草稿元数据并返回预览。"""
    created_at = time.time()
    data = {
        "draft_id": draft_dir.name,
        "created_by": operator.uid,
        "source_type": source_type,
        "source": source,
        "created_at": created_at,
        "expires_at": created_at + SKILL_DRAFT_TTL_SECONDS,
        "items": items,
        "failures": failures,
    }
    (draft_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _validate_zip_paths(zip_file: zipfile.ZipFile) -> None:
    """拒绝压缩包中的越界路径。"""
    for name in zip_file.namelist():
        pure = PurePosixPath(name)
        if pure.is_absolute():
            raise ValueError(f"ZIP 包含不安全绝对路径: {name}")
        if ".." in pure.parts:
            raise ValueError(f"ZIP 包含路径穿越片段: {name}")


def _stage_skill_draft_item(
    *,
    source_skill_dir: Path,
    draft_items_dir: Path,
) -> dict[str, Any]:
    """暂存包内容，保留其原始 slug 供目标来源确认安装。"""
    item_id = uuid.uuid4().hex
    item_dir = draft_items_dir / item_id
    try:
        parsed = copy_skill_snapshot(source_skill_dir, item_dir)
    except Exception:
        shutil.rmtree(item_dir, ignore_errors=True)
        raise
    return {"source_dir": f"items/{item_id}", **parsed}
