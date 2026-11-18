"""Skills 管理路由"""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_admin_user, get_db, get_required_user, get_superadmin_user
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.skills.access import (
    get_allowed_skill_access_levels,
    get_management_readable_skill_or_raise,
    is_builtin_skill,
    normalize_skill_share_config,
    user_can_manage_skill,
)
from yuxi.modules.extensions.skills.builtin.sync import init_builtin_skills
from yuxi.modules.extensions.skills.catalog import (
    list_accessible_skills,
    list_builtin_skill_definitions,
    list_managed_shared_skills,
    list_skill_cards_for_user,
)
from yuxi.modules.extensions.skills.content import save_skill_content
from yuxi.modules.extensions.skills.edit import (
    create_skill_node,
    delete_skill_node,
    edit_shared_skill_file,
    export_skill_zip,
    get_skill_tree,
    read_skill_content,
    read_skill_file,
)
from yuxi.modules.extensions.skills.install_draft import (
    create_remote_skill_draft,
    create_uploaded_skill_draft,
    discard_skill_install_draft,
)
from yuxi.modules.extensions.skills.package import SkillEditConflict
from yuxi.modules.extensions.skills.personal import (
    confirm_personal_skill_install_draft,
    delete_personal_skill,
    read_personal_skill_file,
)
from yuxi.modules.extensions.skills.remote import list_remote_skills, search_remote_skills
from yuxi.modules.extensions.skills.shared import (
    confirm_skill_install_draft,
    delete_skill,
    delete_skills_batch,
    update_skill_enabled,
    update_skill_share_config,
)
from yuxi.modules.extensions.skills.versions import (
    delete_skill_version,
    list_skill_versions,
    release_skill_version,
    restore_skill_version,
)
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import normalize_permission_config, resolve_skill_permission

skills = APIRouter(prefix="/system/skills", tags=["skills"])
user_skills = APIRouter(prefix="/skills", tags=["skills"])


class ShareConfigPayload(BaseModel):
    share_config: dict | None = Field(None, description="共享权限配置")


class SkillEnabledUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


class SkillNodeCreateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    is_dir: bool = Field(False, description="是否创建目录")
    content: str | None = Field("", description="文件内容（仅文件创建时生效）")


class SkillFileUpdateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    content: str = Field(..., description="文件内容")
    expected_revision: str = Field(..., description="读取文件时取得的 SHA-256 修订值")


class SkillVersionRequest(BaseModel):
    expected_revision: str = Field(..., min_length=1, description="当前整包修订")


class SkillContentChange(BaseModel):
    """完整编辑提交中的文件变更。"""

    action: Literal["write", "create", "mkdir", "delete"]
    path: str = ""
    content: str = ""


class SkillContentRequest(BaseModel):
    """一次保存的完整草稿与基准修订。"""

    expected_revision: str = Field(..., min_length=1)
    changes: list[SkillContentChange] = Field(default_factory=list, max_length=2000)
    release: bool = False


class RemoteSkillSourceRequest(BaseModel):
    source: str = Field(..., description="远程 Skill 来源，如 owner/repo 或允许的 HTTPS URL")


class RemoteSkillPrepareRequest(RemoteSkillSourceRequest):
    skills: list[str] = Field(..., description="需要安装的 skill 名称列表")


class RemoteSkillSearchRequest(BaseModel):
    query: str = Field(..., description="搜索关键字")


class SkillBatchDeleteRequest(BaseModel):
    slugs: list[str] = Field(..., max_length=50, description="需要批量删除的 skill slug 列表，最多支持 50 个")


class _DraftConfirmRequestBase(BaseModel):
    slugs: list[str] | None = Field(None, description="本次确认安装的 Skill slug")


class SkillDraftConfirmRequest(_DraftConfirmRequestBase):
    share_config: dict | None = Field(None, description="共享权限配置")


class PersonalSkillDraftConfirmRequest(_DraftConfirmRequestBase):
    pass


@user_skills.get("")
async def list_skill_cards_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_skill_cards_for_user(db, current_user)
        return {
            "success": True,
            "data": [_serialize_skill_for_user(item, current_user) for item in items],
            "allowed_access_levels": get_allowed_skill_access_levels(current_user),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list Skill cards: {e}")
        raise HTTPException(status_code=500, detail="获取 Skill 列表失败")


@user_skills.get("/accessible")
async def list_accessible_skills_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_accessible_skills(db, current_user)
        return {"success": True, "data": [_serialize_skill_for_user(item, current_user) for item in items]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list accessible skills: {e}")
        raise HTTPException(status_code=500, detail="获取可访问 Skills 失败")


@user_skills.post("/import/prepare")
async def create_uploaded_skill_draft_route(
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
):
    try:
        data = await create_uploaded_skill_draft(
            filename=file.filename or "",
            file_bytes=await file.read(),
            operator=current_user,
        )
        allowed = get_allowed_skill_access_levels(current_user)
        data["allowed_access_levels"] = allowed
        data["default_share_config"] = normalize_skill_share_config(None, operator_uid=current_user.uid, allowed_access_levels=set(allowed))
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare skill upload: {e}")
        raise HTTPException(status_code=500, detail="解析上传 Skill 失败")


@user_skills.post("/remote/list")
async def list_remote_skills_route(
    payload: RemoteSkillSourceRequest,
    _current_user: User = Depends(get_required_user),
):
    try:
        return {"success": True, "data": await list_remote_skills(payload.source, uid=str(_current_user.uid))}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="获取远程 skills 列表失败")


@user_skills.post("/remote/search")
async def search_remote_skills_route(payload: RemoteSkillSearchRequest, _current_user: User = Depends(get_required_user)):
    try:
        return {"success": True, "data": await search_remote_skills(payload.query, uid=str(_current_user.uid))}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search remote skills with query '{payload.query}': {e}")
        raise HTTPException(status_code=500, detail="搜索远程 skills 失败")


@user_skills.post("/remote/prepare")
async def prepare_remote_skills_route(
    payload: RemoteSkillPrepareRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        data = await create_remote_skill_draft(
            source=payload.source,
            skills=payload.skills,
            operator=current_user,
        )
        allowed = get_allowed_skill_access_levels(current_user)
        data["allowed_access_levels"] = allowed
        data["default_share_config"] = normalize_skill_share_config(None, operator_uid=current_user.uid, allowed_access_levels=set(allowed))
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="解析远程 Skills 失败")


@user_skills.post("/install-drafts/{draft_id}/confirm")
async def confirm_skill_install_draft_route(
    draft_id: str,
    payload: SkillDraftConfirmRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        results = await confirm_skill_install_draft(
            db,
            draft_id=draft_id,
            share_config=payload.share_config,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm skill install draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="确认安装 Skill 失败")


@user_skills.post("/personal/install-drafts/{draft_id}/confirm")
async def confirm_personal_skill_install_draft_route(
    draft_id: str,
    payload: PersonalSkillDraftConfirmRequest,
    current_user: User = Depends(get_required_user),
):
    try:
        results = await confirm_personal_skill_install_draft(
            draft_id=draft_id,
            slugs=payload.slugs,
            operator=current_user,
        )
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm personal Skill draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="确认安装个人 Skill 失败")


@user_skills.get("/personal/{slug}/file")
async def read_personal_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 Skill 根目录的文件路径"),
    current_user: User = Depends(get_required_user),
):
    try:
        return {
            "success": True,
            "data": await read_personal_skill_file(str(current_user.uid), slug, path),
        }
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to read personal Skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="读取个人 Skill 文件失败")


@user_skills.delete("/personal/{slug}")
async def delete_personal_skill_route(
    slug: str,
    current_user: User = Depends(get_required_user),
):
    try:
        await delete_personal_skill(str(current_user.uid), slug)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete personal Skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除个人 Skill 失败")


@user_skills.delete("/install-drafts/{draft_id}")
async def discard_skill_install_draft_route(draft_id: str, current_user: User = Depends(get_required_user)):
    try:
        await discard_skill_install_draft(draft_id=draft_id, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to discard skill install draft '{draft_id}': {e}")
        raise HTTPException(status_code=500, detail="取消安装 Skill 失败")


@skills.get("")
async def list_skills_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_managed_shared_skills(db, current_user)
        return {
            "success": True,
            "data": [_serialize_skill_for_user(item, current_user) for item in items],
            "allowed_access_levels": get_allowed_skill_access_levels(current_user),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list manageable skills: {e}")
        raise HTTPException(status_code=500, detail="获取技能列表失败")


@skills.get("/builtin")
async def list_builtin_skills_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await list_builtin_skill_definitions(db)
        return {"success": True, "data": [item.to_dict() for item in items]}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list builtin skills: {e}")
        raise HTTPException(status_code=500, detail="获取内置 skill 列表失败")


@skills.post("/builtin/sync")
async def sync_builtin_skills_route(
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await init_builtin_skills(db, created_by=current_user.uid)
        return {"success": True, "data": [item.to_dict() for item in items]}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to sync builtin skills: {e}")
        raise HTTPException(status_code=500, detail="同步内置 skill 失败")


@skills.post("/delete-batch")
async def delete_skills_batch_route(
    payload: SkillBatchDeleteRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        results = await delete_skills_batch(db, slugs=payload.slugs, operator=current_user)
        return {"success": True, "data": results, "summary": _summarize_results(results)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skills batch: {e}")
        raise HTTPException(status_code=500, detail="批量删除技能失败")


@skills.put("/{slug}/share-config")
async def update_skill_share_config_route(
    slug: str,
    payload: ShareConfigPayload,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_share_config(db, slug=slug, share_config=payload.share_config, operator=current_user)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill share config '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 Skill 共享范围失败")


@skills.put("/{slug}/enabled")
async def update_skill_enabled_route(
    slug: str,
    payload: SkillEnabledUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_skill_enabled(db, slug=slug, enabled=payload.enabled, operator=current_user)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill enabled '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 Skill 启用状态失败")


@skills.get("/{slug}")
async def get_skill_detail_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """按当前权限读取单个 Skill，包含隐藏于普通列表的绑定资源。"""
    try:
        item = await get_management_readable_skill_or_raise(db, current_user, slug)
        return {"success": True, "data": _serialize_skill_for_user(item, current_user)}
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.get("/{slug}/content")
async def get_skill_content_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取一份完整编辑基准。"""
    try:
        return {"success": True, "data": await read_skill_content(db, slug=slug, operator=current_user)}
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.put("/{slug}/content")
async def save_skill_content_route(
    slug: str,
    payload: SkillContentRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """一次提交全部草稿，并可为结果发版。"""
    try:
        result = await save_skill_content(
            db,
            slug=slug,
            operator=current_user,
            expected_revision=payload.expected_revision,
            changes=[change.model_dump() for change in payload.changes],
            release=payload.release,
        )
        return {
            "success": True,
            "data": {
                "skill": _serialize_skill_for_user(result.skill, current_user),
                "revision": result.revision,
                "published_version": result.published_version,
            },
        }
    except SkillEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.get("/{slug}/tree")
async def get_skill_tree_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {"success": True, "data": await get_skill_tree(db, slug=slug, operator=current_user)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get skill tree '{slug}': {e}")
        raise HTTPException(status_code=500, detail="获取技能目录树失败")


@skills.get("/{slug}/file")
async def get_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return {
            "success": True,
            "data": await read_skill_file(db, slug=slug, relative_path=path, operator=current_user),
        }
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to read skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="读取技能文件失败")


@skills.post("/{slug}/file")
async def create_skill_file_route(
    slug: str,
    payload: SkillNodeCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await create_skill_node(
            db,
            slug=slug,
            relative_path=payload.path,
            is_dir=payload.is_dir,
            content=payload.content,
            operator=current_user,
        )
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create skill node '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="创建技能文件失败")


@skills.put("/{slug}/file")
async def update_skill_file_route(
    slug: str,
    payload: SkillFileUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item, revision = await edit_shared_skill_file(
            db,
            slug=slug,
            relative_path=payload.path,
            content=payload.content,
            expected_revision=payload.expected_revision,
            operator=current_user,
        )
        return {
            "success": True,
            "data": {
                "skill": _serialize_skill_for_user(item, current_user),
                "revision": revision,
            },
        }
    except SkillEditConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill file '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="更新技能文件失败")


@skills.get("/{slug}/versions")
async def list_skill_versions_route(slug: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """读取专属 Skill 历史与 latest 修订。"""
    try:
        return {"success": True, "data": await list_skill_versions(db, slug=slug, operator=current_user)}
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.post("/{slug}/versions")
async def release_skill_version_route(
    slug: str,
    payload: SkillVersionRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """为已保存的 latest 发版。"""
    try:
        result = await release_skill_version(db, slug=slug, expected_revision=payload.expected_revision, operator=current_user)
        return {"success": True, "data": result}
    except SkillEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.post("/{slug}/versions/{version}/restore")
async def restore_skill_version_route(
    slug: str,
    version: str,
    payload: SkillVersionRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """覆盖 latest，保留当前绑定 slug。"""
    try:
        result = await restore_skill_version(
            db,
            slug=slug,
            version=version,
            expected_revision=payload.expected_revision,
            operator=current_user,
        )
        return {"success": True, "data": result}
    except SkillEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.delete("/{slug}/versions/{version}")
async def delete_skill_version_route(
    slug: str, version: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    """删除历史快照，不改变 latest。"""
    try:
        await delete_skill_version(db, slug=slug, version=version, operator=current_user)
        return {"success": True}
    except ValueError as exc:
        _raise_from_value_error(exc)


@skills.delete("/{slug}/file")
async def delete_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_skill_node(db, slug=slug, relative_path=path, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skill file '{slug}/{path}': {e}")
        raise HTTPException(status_code=500, detail="删除技能文件失败")


@skills.get("/{slug}/export")
async def export_skill_route(
    slug: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        export_path, download_name = await export_skill_zip(db, slug=slug, operator=current_user)
        background_tasks.add_task(_cleanup_export_file, export_path)
        return FileResponse(path=export_path, media_type="application/zip", filename=download_name)
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="导出技能失败")


@skills.delete("/{slug}")
async def delete_skill_route(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_skill(db, slug=slug, operator=current_user)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除技能失败")


def _raise_from_value_error(e: ValueError) -> None:
    """保留状态映射，以业务 detail 返回可展示的校验原因。"""
    message = str(e)
    status_code = 404 if "不存在" in message or "无权" in message else 400
    raise HTTPException(status_code=status_code, detail={"code": "skill_request_invalid", "message": message})


def _cleanup_export_file(path: str) -> None:
    """响应完成后清理临时导出文件，记录清理失败。"""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to cleanup exported skill archive '{path}': {e}")


def _summarize_results(results: list[dict]) -> dict[str, int]:
    """汇总逐项安装或删除结果。"""
    succeeded = sum(1 for item in results if item.get("success"))
    return {"total": len(results), "success": succeeded, "failed": len(results) - succeeded}


def _serialize_skill_for_user(item, user: User) -> dict:
    """为 Skill 描述附加当前用户的管理权限。"""
    data = item.to_dict()
    data["share_config_invalid"] = False
    if getattr(item, "source_scope", None) != "personal" and data.get("bound_agent_id") is None:
        try:
            data["share_config"] = normalize_permission_config(item.share_config)
        except (TypeError, ValueError) as exc:
            data["share_config"] = item.share_config
            data["share_config_invalid"] = True
            logger.warning("Invalid skill share config exposed for repair: slug={}, error={}", item.slug, str(exc))
    data["can_manage"] = user_can_manage_skill(user, item)
    data["effective_permission"] = resolve_skill_permission(user, item).value
    data["is_builtin"] = is_builtin_skill(item)
    if data.get("bound_agent_id") is not None:
        data.pop("share_config", None)
        data["source_scope"] = "agent_bound"
    return data
