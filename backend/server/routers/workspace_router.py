from __future__ import annotations

import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.utils.auth_middleware import get_required_user
from yuxi import knowledge_base
from yuxi.services.workspace_service import (
    create_workspace_directory,
    delete_workspace_path,
    download_workspace_file,
    list_workspace_tree,
    read_workspace_file_content,
    upload_workspace_files,
    write_workspace_file_content,
)
from yuxi.storage.postgres.models_business import User

workspace = APIRouter(prefix="/workspace", tags=["workspace"])


class CreateWorkspaceDirectoryRequest(BaseModel):
    parent_path: str
    name: str


class UpdateWorkspaceFileContentRequest(BaseModel):
    path: str
    content: str


async def _ensure_knowledge_read_access(current_user: User, kb_id: str) -> None:
    allowed = await knowledge_base.check_accessible(
        {
            "uid": current_user.uid,
            "role": current_user.role,
            "department_id": current_user.department_id,
        },
        kb_id,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")


def _raise_knowledge_read_error(error: ValueError) -> None:
    message = str(error) or "知识库文件读取失败"
    if message.startswith("Dify 知识库不支持"):
        raise HTTPException(status_code=501, detail=message) from error
    raise HTTPException(status_code=400, detail=message) from error


@workspace.get("/tree", response_model=dict)
async def get_workspace_tree(
    path: str = Query("/", description="工作区目录路径"),
    recursive: bool = Query(False, description="是否递归返回子目录文件"),
    files_only: bool = Query(False, description="是否仅返回文件"),
    current_user: User = Depends(get_required_user),
):
    return await list_workspace_tree(
        path=path,
        recursive=recursive,
        files_only=files_only,
        current_user=current_user,
    )


@workspace.get("/file", response_model=dict)
async def get_workspace_file(
    path: str = Query(..., description="工作区文件路径"),
    current_user: User = Depends(get_required_user),
):
    return await read_workspace_file_content(path=path, current_user=current_user)


@workspace.get("/knowledge/tree", response_model=dict)
async def get_workspace_knowledge_tree(
    kb_id: str = Query(..., description="知识库 ID"),
    parent_id: str | None = Query(None, description="父文件夹 ID"),
    recursive: bool = Query(False, description="是否递归返回子目录文件"),
    files_only: bool = Query(False, description="是否仅返回文件"),
    current_user: User = Depends(get_required_user),
):
    await _ensure_knowledge_read_access(current_user, kb_id)
    try:
        return await knowledge_base.list_file_tree(
            kb_id=kb_id,
            parent_id=parent_id,
            recursive=recursive,
            files_only=files_only,
        )
    except ValueError as error:
        _raise_knowledge_read_error(error)


@workspace.get("/knowledge/file", response_model=dict)
async def get_workspace_knowledge_file(
    kb_id: str = Query(..., description="知识库 ID"),
    file_id: str = Query(..., description="知识库文件 ID"),
    variant: str = Query("parsed", description="预览模式：parsed、original 或 pdf"),
    current_user: User = Depends(get_required_user),
):
    await _ensure_knowledge_read_access(current_user, kb_id)
    try:
        return await knowledge_base.read_file_preview(kb_id=kb_id, file_id=file_id, variant=variant)
    except ValueError as error:
        _raise_knowledge_read_error(error)


@workspace.get("/knowledge/download")
async def download_workspace_knowledge_file(
    kb_id: str = Query(..., description="知识库 ID"),
    file_id: str = Query(..., description="知识库文件 ID"),
    variant: str = Query("original", description="下载模式：original、parsed 或 pdf"),
    current_user: User = Depends(get_required_user),
):
    await _ensure_knowledge_read_access(current_user, kb_id)
    try:
        data = await knowledge_base.get_file_download(kb_id=kb_id, file_id=file_id, variant=variant)
    except ValueError as error:
        _raise_knowledge_read_error(error)

    filename = data["filename"]
    return StreamingResponse(
        io.BytesIO(data["content"]),
        media_type=data["media_type"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@workspace.put("/file", response_model=dict)
async def update_workspace_file(
    payload: UpdateWorkspaceFileContentRequest,
    current_user: User = Depends(get_required_user),
):
    return await write_workspace_file_content(
        path=payload.path,
        content=payload.content,
        current_user=current_user,
    )


@workspace.delete("/file", response_model=dict)
async def delete_workspace_file_route(
    path: str = Query(..., description="工作区文件或目录路径"),
    current_user: User = Depends(get_required_user),
):
    return await delete_workspace_path(path=path, current_user=current_user)


@workspace.post("/directory", response_model=dict)
async def create_workspace_directory_route(
    payload: CreateWorkspaceDirectoryRequest,
    current_user: User = Depends(get_required_user),
):
    return await create_workspace_directory(
        parent_path=payload.parent_path,
        name=payload.name,
        current_user=current_user,
    )


@workspace.post("/upload", response_model=dict)
async def upload_workspace_files_route(
    parent_path: str = Form(..., description="父目录路径"),
    files: list[UploadFile] = File(..., description="上传文件列表"),
    current_user: User = Depends(get_required_user),
):
    return await upload_workspace_files(parent_path=parent_path, files=files, current_user=current_user)


@workspace.get("/download")
async def download_workspace(
    path: str = Query(..., description="工作区文件路径"),
    variant: str = Query("original", description="下载模式：original 或 pdf"),
    current_user: User = Depends(get_required_user),
):
    return await download_workspace_file(path=path, current_user=current_user, variant=variant)
