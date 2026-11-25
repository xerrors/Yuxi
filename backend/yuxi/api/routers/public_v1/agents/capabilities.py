"""Thread 范围内的文件、上下文与状态能力。"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db
from yuxi.api.responses.files import render_file_result
from yuxi.api.routers.public_v1.agents.auth import PublicAgentContext, require_public_context
from yuxi.api.routers.public_v1.agents.responses import PUBLIC_ERRORS
from yuxi.api.routers.public_v1.agents.schemas import FileResponse
from yuxi.api.uploads import read_upload_with_limit
from yuxi.modules.agents.services.artifacts import resolve_thread_artifact_view, save_thread_artifact_to_workspace_view
from yuxi.modules.agents.services.attachments import (
    MAX_ATTACHMENT_SIZE_BYTES,
    delete_draft_file,
    delete_thread_attachment_view,
    get_draft_file,
    list_thread_attachments_view,
    upload_draft_file,
)
from yuxi.modules.agents.services.checkpoint_state import get_agent_state_view
from yuxi.modules.agents.services.compression import compress_thread_context as compress_context
from yuxi.modules.agents.services.cooperation import get_cooperation_summary
from yuxi.modules.agents.services.thread_access import require_thread

router = APIRouter(dependencies=[Depends(require_public_context)], responses=PUBLIC_ERRORS)


class SaveArtifact(BaseModel):
    """把 Thread 产物保存到用户工作区。"""

    model_config = ConfigDict(extra="forbid")
    path: str
    destination_path: str | None = None


@router.post("/files", status_code=201, response_model=FileResponse, summary="上传文件草稿")
async def upload_file(
    file: UploadFile = File(...),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """上传不创建 Session 或 Workdir，单文件最多 5 MiB，有效期 24 小时。

    将返回的 id 放入创建请求或消息事件的提交级 yuxi.attachment_file_ids。
    每个 draft 只提交给一个 Input；不要求选择 OCR 或提供存储路径。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="无法识别的文件名")
    try:
        content = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_ATTACHMENT_SIZE_BYTES,
            too_large_message="附件过大，当前仅支持 5 MB 以内的文件",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await upload_draft_file(
        file_content=content,
        filename=file.filename,
        content_type=file.content_type,
        scope=context.scope,
        db=db,
    )
    await db.commit()
    return result


@router.get("/files/{file_id}", response_model=FileResponse, summary="读取未提交文件草稿")
async def retrieve_file(file_id: str, context: PublicAgentContext = Depends(require_public_context), db: AsyncSession = Depends(get_db)):
    """读取当前用户/APP 尚未提交的文件信息，不暴露存储地址。

    不可见返回 404，已过期返回 422，已提交返回 409；正式文件通过 Session attachments 回读。
    """
    return await get_draft_file(file_id=file_id, scope=context.scope, db=db)


@router.delete("/files/{file_id}", summary="删除未提交文件草稿")
async def delete_file(file_id: str, context: PublicAgentContext = Depends(require_public_context), db: AsyncSession = Depends(get_db)):
    """删除未提交的 draft 及派生资源；已提交文件由 Workdir 管理。"""
    return await delete_draft_file(file_id=file_id, scope=context.scope, db=db)


@router.get("/sessions/{session_id}/cooperation")
async def retrieve_cooperation_summary(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取不包含 checkpoint 和结果正文的完整协作摘要。"""
    return await get_cooperation_summary(db=db, scope=context.scope, thread_id=session_id)


@router.get("/sessions/{session_id}/state")
async def retrieve_thread_state(
    session_id: str,
    include_relations: bool = Query(default=True),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """按 Thread 作用域读取 LangGraph 当前状态。"""
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    return await get_agent_state_view(
        thread_id=session_id,
        current_user=context.user,
        db=db,
        include_relations=include_relations,
        app_id=context.scope.app_id,
    )


@router.post("/sessions/{session_id}/compress")
async def compress_thread_context(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """在作用域与空闲条件成立时压缩线程上下文。"""
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    return await compress_context(thread_id=session_id, current_user=context.user, db=db, app_id=context.scope.app_id)


@router.get("/sessions/{session_id}/attachments")
async def list_thread_attachments(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取当前资源用户的 Thread 附件。"""
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    return await list_thread_attachments_view(thread_id=session_id, db=db, current_uid=context.scope.uid, app_id=context.scope.app_id)


@router.delete("/sessions/{session_id}/attachments/{file_id}")
async def delete_thread_attachment(
    session_id: str,
    file_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """拒绝删除仍被待消费 Input 使用的附件。"""
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    return await delete_thread_attachment_view(
        thread_id=session_id,
        file_id=file_id,
        db=db,
        current_uid=context.scope.uid,
        app_id=context.scope.app_id,
    )


@router.post("/sessions/{session_id}/artifacts/save")
async def save_thread_artifact(
    session_id: str,
    payload: SaveArtifact,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """把已授权 Thread 产物保存到用户工作区。"""
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    return await save_thread_artifact_to_workspace_view(
        thread_id=session_id,
        current_uid=context.scope.uid,
        db=db,
        path=payload.path,
        destination_path=payload.destination_path,
        app_id=context.scope.app_id,
    )


@router.get("/sessions/{session_id}/artifacts/{path:path}")
async def retrieve_thread_artifact(
    session_id: str,
    path: str,
    download: bool = Query(default=False),
    preview: bool = Query(default=False),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """下载或预览已授权 Thread 的沙盒产物。"""
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    return render_file_result(
        await resolve_thread_artifact_view(
            thread_id=session_id,
            current_uid=context.scope.uid,
            db=db,
            path=path,
            download=download,
            preview=preview,
            app_id=context.scope.app_id,
        )
    )
