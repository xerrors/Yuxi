"""Viewer 文件系统路由

提供 Viewer UI 使用的文件系统 API 端点。
- /viewer/filesystem/* - Viewer UI 使用
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.responses.files import render_file_result
from yuxi.api.uploads import prepare_upload_files
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.services.viewer import (
    MAX_VIEWER_UPLOAD_BYTES,
    create_viewer_directory,
    delete_viewer_file,
    download_viewer_file,
    list_viewer_filesystem_tree,
    read_viewer_file_content,
    search_viewer_files,
    upload_viewer_files,
)

filesystem_router = APIRouter(prefix="/viewer/filesystem", tags=["viewer-filesystem"])


class CreateViewerDirectoryRequest(BaseModel):
    thread_id: str
    parent_path: str
    name: str


@filesystem_router.get("/tree", response_model=dict)
async def get_viewer_tree(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query("/", description="目录路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_viewer_filesystem_tree(
        thread_id=thread_id,
        path=path,
        current_user=current_user,
        db=db,
    )


@filesystem_router.get("/file")
async def get_viewer_file(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query(..., description="文件路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return render_file_result(
        await read_viewer_file_content(
            thread_id=thread_id,
            path=path,
            current_user=current_user,
            db=db,
        )
    )


@filesystem_router.delete("/file", response_model=dict)
async def delete_viewer_file_route(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query(..., description="文件路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_viewer_file(
        thread_id=thread_id,
        path=path,
        current_user=current_user,
        db=db,
    )


@filesystem_router.post("/directory", response_model=dict)
async def create_viewer_directory_route(
    payload: CreateViewerDirectoryRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_viewer_directory(
        thread_id=payload.thread_id,
        parent_path=payload.parent_path,
        name=payload.name,
        current_user=current_user,
        db=db,
    )


@filesystem_router.post("/upload", response_model=dict)
async def upload_viewer_files_route(
    thread_id: str = Form(..., description="线程 ID"),
    parent_path: str = Form(..., description="父目录路径"),
    files: list[UploadFile] = File(..., description="上传文件列表"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        inputs = await prepare_upload_files(
            files,
            max_size_bytes=MAX_VIEWER_UPLOAD_BYTES,
            too_large_message="文件过大",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await upload_viewer_files(
        thread_id=thread_id,
        parent_path=parent_path,
        files=inputs,
        current_user=current_user,
        db=db,
    )


@filesystem_router.get("/search", response_model=dict)
async def search_viewer_files_route(
    thread_id: str = Query(..., description="线程 ID"),
    query: str = Query(..., description="搜索关键词"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await search_viewer_files(
        thread_id=thread_id,
        query=query,
        current_user=current_user,
        db=db,
    )


@filesystem_router.get("/download")
async def download_viewer(
    thread_id: str = Query(..., description="线程 ID"),
    path: str = Query(..., description="文件路径"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return render_file_result(
        await download_viewer_file(
            thread_id=thread_id,
            path=path,
            current_user=current_user,
            db=db,
        )
    )
