"""用户个人工作区与工作区知识文件只读视图路由。"""

import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.responses.files import render_file_result
from yuxi.api.uploads import prepare_upload_files
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.services.files import (
    MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
    create_workspace_directory,
    delete_workspace_path,
    download_workspace_file,
    list_workspace_tree,
    read_workspace_file_content,
    search_workspace_files,
    upload_workspace_files,
    write_workspace_file_content,
)

workspace = APIRouter(prefix="/workspace", tags=["workspace"])
workspace_knowledge = APIRouter(prefix="/workspace", tags=["workspace"])


class CreateWorkspaceDirectoryRequest(BaseModel):
    parent_path: str
    name: str


class UpdateWorkspaceFileContentRequest(BaseModel):
    path: str
    content: str


@workspace.get("/tree", response_model=dict)
async def get_workspace_tree(
    path: str = Query("/", description="工作区目录路径"),
    recursive: bool = Query(False, description="是否递归返回子目录文件"),
    files_only: bool = Query(False, description="是否仅返回文件"),
    include_unbound_project_dirs: bool = Query(False, description="Project 选目录时展示未绑定目录"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_workspace_tree(
        path=path,
        recursive=recursive,
        files_only=files_only,
        include_unbound_project_dirs=include_unbound_project_dirs,
        current_user=current_user,
        db=db,
    )


@workspace.get("/search", response_model=dict)
async def search_workspace_files_route(
    query: str = Query(..., description="搜索关键词"),
    current_user: User = Depends(get_required_user),
):
    return await search_workspace_files(query=query, current_user=current_user)


@workspace.get("/file")
async def get_workspace_file(
    path: str = Query(..., description="工作区文件路径"),
    current_user: User = Depends(get_required_user),
):
    return render_file_result(await read_workspace_file_content(path=path, current_user=current_user))


@workspace_knowledge.get("/knowledge/tree", response_model=dict)
async def get_workspace_knowledge_tree(
    kb_id: str = Query(..., description="知识库 ID"),
    parent_id: str | None = Query(None, description="父文件夹 ID"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    recursive: bool = Query(False, description="是否递归返回子目录文件"),
    files_only: bool = Query(False, description="是否仅返回文件"),
    current_user: User = Depends(get_required_user),
):
    from yuxi.modules.knowledge.services import documents as knowledge_documents

    await _ensure_knowledge_read_access(current_user, kb_id)
    await _ensure_knowledge_supports_documents(kb_id)
    try:
        data = await knowledge_documents.list_document_files(
            kb_id=kb_id,
            parent_id=parent_id,
            page=page,
            page_size=page_size,
            recursive=recursive,
            files_only=files_only,
            include_stats=False,
        )
        return {
            "entries": [_workspace_knowledge_entry(kb_id, item) for item in data.get("items", [])],
            "readonly": True,
            "page": data.get("page", page),
            "page_size": data.get("page_size", page_size),
            "total": data.get("total", 0),
            "has_more": bool(data.get("has_more")),
            "parent_id": data.get("parent_id"),
        }
    except ValueError as error:
        _raise_knowledge_read_error(error)


@workspace_knowledge.get("/knowledge/file")
async def get_workspace_knowledge_file(
    kb_id: str = Query(..., description="知识库 ID"),
    file_id: str = Query(..., description="知识库文件 ID"),
    current_user: User = Depends(get_required_user),
):
    await _ensure_knowledge_read_access(current_user, kb_id)
    await _ensure_knowledge_supports_documents(kb_id)
    try:
        from yuxi.modules.knowledge.preview import read_knowledge_file_preview

        return _preview_response(await read_knowledge_file_preview(kb_id=kb_id, file_id=file_id))
    except ValueError as error:
        _raise_knowledge_read_error(error)


@workspace_knowledge.get("/knowledge/download")
async def download_workspace_knowledge_file(
    kb_id: str = Query(..., description="知识库 ID"),
    file_id: str = Query(..., description="知识库文件 ID"),
    variant: str = Query("original", description="下载模式：original 或 parsed"),
    current_user: User = Depends(get_required_user),
):
    from yuxi.modules.knowledge.services import documents as knowledge_documents

    await _ensure_knowledge_read_access(current_user, kb_id)
    try:
        await knowledge_documents.require_document_support(kb_id, "download")
        data = await knowledge_documents.get_file_download(kb_id=kb_id, file_id=file_id, variant=variant)
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
    relative_paths: list[str] | None = Form(None, description="与 files 顺序对齐的相对路径"),
    files: list[UploadFile] = File(..., description="上传文件列表"),
    current_user: User = Depends(get_required_user),
):
    try:
        inputs = await prepare_upload_files(
            files,
            max_size_bytes=MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
            too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await upload_workspace_files(
        parent_path=parent_path,
        files=inputs,
        relative_paths=relative_paths,
        current_user=current_user,
    )


@workspace.get("/download")
async def download_workspace(
    path: str = Query(..., description="工作区文件路径"),
    current_user: User = Depends(get_required_user),
):
    return render_file_result(await download_workspace_file(path=path, current_user=current_user))


async def _ensure_knowledge_read_access(current_user: User, kb_id: str) -> None:
    from yuxi.modules.knowledge.services import access as knowledge_access

    allowed = await knowledge_access.check_accessible(
        {
            "uid": current_user.uid,
            "role": current_user.role,
            "department_id": current_user.department_id,
        },
        kb_id,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")


async def _ensure_knowledge_supports_documents(kb_id: str) -> None:
    from yuxi.modules.knowledge.services import documents as knowledge_documents

    kb_info, supports_documents = await knowledge_documents.get_knowledge_base_document_support(kb_id)
    if not kb_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    if not supports_documents:
        raise HTTPException(status_code=501, detail=f"{kb_info.name or kb_info.kb_type} 不支持文件浏览")


def _raise_knowledge_read_error(error: ValueError) -> None:
    message = str(error) or "知识库文件读取失败"
    if message.startswith("Dify 知识库不支持"):
        raise HTTPException(status_code=501, detail=message) from error
    raise HTTPException(status_code=400, detail=message) from error


def _workspace_knowledge_entry(kb_id: str, item: dict) -> dict:
    is_dir = bool(item.get("is_folder"))
    file_id = item.get("file_id")
    if is_dir:
        path = f"/knowledge/{kb_id}/folder/{file_id}/"
    else:
        path = f"/knowledge/{kb_id}/file/{file_id}"

    return {
        "source": "knowledge",
        "kb_id": kb_id,
        "file_id": file_id,
        "parent_id": item.get("parent_id"),
        "path": path,
        "virtual_path": path,
        "name": item.get("filename") or file_id,
        "is_dir": is_dir,
        "size": 0 if is_dir else int(item.get("file_size") or 0),
        "modified_at": item.get("updated_at") or item.get("created_at") or "",
        "readonly": True,
        "status": item.get("status") or "done",
        "has_original_file": bool(item.get("has_original_file")),
        "has_parsed_markdown": bool(item.get("has_parsed_markdown")),
    }


def _binary_preview_response(data: dict) -> StreamingResponse:
    filename = data.get("filename") or "preview"
    preview_type = data.get("preview_type") or "unsupported"
    return StreamingResponse(
        io.BytesIO(data.get("content") or b""),
        media_type=data.get("media_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Yuxi-Preview-Type": preview_type,
            "X-Yuxi-Preview-Filename": quote(filename),
        },
    )


def _preview_response(data):
    if isinstance(data, dict) and data.get("binary"):
        return _binary_preview_response(data)
    return data
