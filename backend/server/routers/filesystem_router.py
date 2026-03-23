"""文件系统路由

提供沙盒文件系统的 API 端点。
- /filesystem/* - Agent 工具使用 (composite_backend)
- /viewer/filesystem/* - Viewer UI 使用 (readonly backend)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.filesystem_service import (
    list_filesystem_entries_view,
    read_file_content_view,
)
from yuxi.services.viewer_filesystem_service import (
    download_viewer_file,
    list_viewer_filesystem_tree,
    read_viewer_file_content,
)
from yuxi.storage.postgres.models_business import User

filesystem_router = APIRouter(prefix="/filesystem", tags=["filesystem"])
viewer_filesystem_router = APIRouter(prefix="/viewer/filesystem", tags=["viewer-filesystem"])


# ==================== Agent 文件系统接口 ====================

@filesystem_router.get("/ls", response_model=dict)
async def list_filesystem_entries(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query("/", description="目录路径"),
    agent_id: str | None = Query(None, description="智能体 ID"),
    agent_config_id: int | None = Query(None, description="智能体配置 ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_filesystem_entries_view(
        thread_id=thread_id,
        path=path,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )


@filesystem_router.get("/cat", response_model=dict)
async def read_file_content(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query(..., description="文件路径"),
    agent_id: str | None = Query(None, description="智能体 ID"),
    agent_config_id: int | None = Query(None, description="智能体配置 ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await read_file_content_view(
        thread_id=thread_id,
        path=path,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )


# ==================== Viewer 文件系统接口 ====================

@viewer_filesystem_router.get("/tree", response_model=dict)
async def get_viewer_tree(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query("/", description="目录路径"),
    agent_id: str | None = Query(None, description="智能体 ID"),
    agent_config_id: int | None = Query(None, description="智能体配置 ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_viewer_filesystem_tree(
        thread_id=thread_id,
        path=path,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )


@viewer_filesystem_router.get("/file", response_model=dict)
async def get_viewer_file(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query(..., description="文件路径"),
    agent_id: str | None = Query(None, description="智能体 ID"),
    agent_config_id: int | None = Query(None, description="智能体配置 ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await read_viewer_file_content(
        thread_id=thread_id,
        path=path,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )


@viewer_filesystem_router.get("/download")
async def download_viewer(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query(..., description="文件路径"),
    agent_id: str | None = Query(None, description="智能体 ID"),
    agent_config_id: int | None = Query(None, description="智能体配置 ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await download_viewer_file(
        thread_id=thread_id,
        path=path,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )
