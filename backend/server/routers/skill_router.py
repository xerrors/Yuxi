"""Skills 管理路由"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.services.remote_skill_install_service import install_remote_skill, list_remote_skills
from yuxi.services.skill_service import (
    BuiltinSkillUpdateConflictError,
    create_skill_node,
    delete_skill,
    delete_skill_node,
    export_skill_zip,
    get_skill_dependency_options,
    get_skill_tree,
    import_skill_zip,
    install_builtin_skill,
    list_builtin_skill_specs,
    list_skills,
    read_skill_file,
    update_builtin_skill,
    update_skill_dependencies,
    update_skill_file,
)
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger

skills = APIRouter(prefix="/system/skills", tags=["skills"])


class SkillNodeCreateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    is_dir: bool = Field(False, description="是否创建目录")
    content: str | None = Field("", description="文件内容（仅文件创建时生效）")


class SkillFileUpdateRequest(BaseModel):
    path: str = Field(..., description="相对 skill 根目录的路径")
    content: str = Field(..., description="文件内容")


class SkillDependenciesUpdateRequest(BaseModel):
    tool_dependencies: list[str] = Field(default_factory=list, description="依赖的内置工具列表")
    mcp_dependencies: list[str] = Field(default_factory=list, description="依赖的 MCP 服务列表")
    skill_dependencies: list[str] = Field(default_factory=list, description="依赖的其他 skill slug 列表")


class BuiltinSkillUpdateRequest(BaseModel):
    force: bool = Field(False, description="是否强制覆盖本地已安装内容")


class RemoteSkillSourceRequest(BaseModel):
    source: str = Field(..., description="skills 仓库来源，如 owner/repo 或 GitHub URL")


class RemoteSkillInstallRequest(RemoteSkillSourceRequest):
    skill: str = Field(..., description="需要安装的 skill 名称")


def _raise_from_value_error(e: ValueError) -> None:
    message = str(e)
    status_code = 404 if "不存在" in message else 400
    raise HTTPException(status_code=status_code, detail=message)


def _cleanup_export_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to cleanup exported skill archive '{path}': {e}")


@skills.get("")
async def list_skills_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取技能列表（管理员可读）。"""
    try:
        items = await list_skills(db)
        return {"success": True, "data": [item.to_dict() for item in items]}
    except Exception as e:
        logger.error(f"Failed to list skills: {e}")
        raise HTTPException(status_code=500, detail="获取技能列表失败")


@skills.get("/dependency-options")
async def get_skill_dependency_options_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 skill 依赖项可选列表（管理员）。"""
    try:
        return {"success": True, "data": await get_skill_dependency_options(db)}
    except Exception as e:
        logger.error(f"Failed to get skill dependency options: {e}")
        raise HTTPException(status_code=500, detail="获取 skill 依赖选项失败")


@skills.get("/builtin")
async def list_builtin_skills_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        installed_map = {item.slug: item for item in await list_skills(db)}
        data = []
        for spec in list_builtin_skill_specs():
            installed = installed_map.get(spec["slug"])
            status = "not_installed"
            if installed:
                status = "installed"
                if installed.version != spec["version"] or installed.content_hash != spec["content_hash"]:
                    status = "update_available"
            data.append(
                {
                    "slug": spec["slug"],
                    "name": spec["name"],
                    "description": spec["description"],
                    "version": spec["version"],
                    "status": status,
                    "installed_record": installed.to_dict() if installed else None,
                }
            )
        return {"success": True, "data": data}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list builtin skills: {e}")
        raise HTTPException(status_code=500, detail="获取内置 skill 列表失败")


@skills.post("/builtin/{slug}/install")
async def install_builtin_skill_route(
    slug: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await install_builtin_skill(db, slug, installed_by=current_user.username)
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install builtin skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="安装内置 skill 失败")


@skills.post("/builtin/{slug}/update")
async def update_builtin_skill_route(
    slug: str,
    payload: BuiltinSkillUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await update_builtin_skill(
            db,
            slug,
            force=payload.force,
            updated_by=current_user.username,
        )
        return {"success": True, "data": item.to_dict()}
    except BuiltinSkillUpdateConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={"needs_confirm": True, "message": str(e)},
        )
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update builtin skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新内置 skill 失败")


@skills.post("/import")
async def import_skill_route(
    file: UploadFile = File(...),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """导入技能包（支持 ZIP 或单个 SKILL.md，管理员）。"""
    try:
        file_bytes = await file.read()
        item = await import_skill_zip(
            db,
            filename=file.filename or "",
            file_bytes=file_bytes,
            created_by=current_user.username,
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import skill package: {e}")
        raise HTTPException(status_code=500, detail="导入技能失败")


@skills.post("/remote/list")
async def list_remote_skills_route(
    payload: RemoteSkillSourceRequest,
    _current_user: User = Depends(get_admin_user),
):
    try:
        return {"success": True, "data": await list_remote_skills(payload.source)}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list remote skills from '{payload.source}': {e}")
        raise HTTPException(status_code=500, detail="获取远程 skills 列表失败")


@skills.post("/remote/install")
async def install_remote_skill_route(
    payload: RemoteSkillInstallRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await install_remote_skill(
            db,
            source=payload.source,
            skill=payload.skill,
            created_by=current_user.username,
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to install remote skill '{payload.skill}' from '{payload.source}': {e}"
        )
        raise HTTPException(status_code=500, detail="安装远程 skill 失败")


@skills.get("/{slug}/tree")
async def get_skill_tree_route(
    slug: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取技能目录树（管理员）。"""
    try:
        tree = await get_skill_tree(db, slug)
        return {"success": True, "data": tree}
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
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """读取技能文本文件（管理员）。"""
    try:
        data = await read_skill_file(db, slug, path)
        return {"success": True, "data": data}
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
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建技能文件或目录（管理员）。"""
    try:
        await create_skill_node(
            db,
            slug=slug,
            relative_path=payload.path,
            is_dir=payload.is_dir,
            content=payload.content,
            updated_by=current_user.username,
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
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新技能文本文件（管理员）。"""
    try:
        await update_skill_file(
            db,
            slug=slug,
            relative_path=payload.path,
            content=payload.content,
            updated_by=current_user.username,
        )
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill file '{slug}/{payload.path}': {e}")
        raise HTTPException(status_code=500, detail="更新技能文件失败")


@skills.put("/{slug}/dependencies")
async def update_skill_dependencies_route(
    slug: str,
    payload: SkillDependenciesUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 skill 依赖（管理员）。"""
    try:
        item = await update_skill_dependencies(
            db,
            slug=slug,
            tool_dependencies=payload.tool_dependencies,
            mcp_dependencies=payload.mcp_dependencies,
            skill_dependencies=payload.skill_dependencies,
            updated_by=current_user.username,
        )
        return {"success": True, "data": item.to_dict()}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill dependencies '{slug}': {e}")
        raise HTTPException(status_code=500, detail="更新 skill 依赖失败")


@skills.delete("/{slug}/file")
async def delete_skill_file_route(
    slug: str,
    path: str = Query(..., description="相对 skill 根目录路径"),
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除技能文件或目录（管理员）。"""
    try:
        await delete_skill_node(db, slug=slug, relative_path=path)
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
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """导出技能压缩包（管理员）。"""
    try:
        export_path, download_name = await export_skill_zip(db, slug)
        background_tasks.add_task(_cleanup_export_file, export_path)
        return FileResponse(
            path=export_path,
            media_type="application/zip",
            filename=download_name,
        )
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
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除技能（目录 + 数据库记录，管理员）。"""
    try:
        await delete_skill(db, slug=slug)
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete skill '{slug}': {e}")
        raise HTTPException(status_code=500, detail="删除技能失败")
