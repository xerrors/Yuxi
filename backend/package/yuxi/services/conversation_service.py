import uuid
from dataclasses import dataclass
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.backends.sandbox import (
    ensure_thread_dirs,
    sandbox_uploads_dir,
)
from yuxi.agents.buildin import agent_manager
from yuxi.config import config as app_config
from yuxi.plugins.parser import Parser
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.utils.datetime_utils import utc_isoformat
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import VIRTUAL_PATH_UPLOADS

ATTACHMENT_ALLOWED_EXTENSIONS: tuple[str, ...] = ()
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ATTACHMENT_MARKDOWN_CHARS = 32_000  # TODO: 转 MARKDOWN的时候，不应该裁剪


@dataclass(slots=True)
class ConversionResult:
    """Represents the normalized output of an uploaded attachment."""

    file_id: str
    file_name: str
    file_type: str | None
    file_size: int
    markdown: str
    truncated: bool


def _ensure_workdir() -> Path:
    workdir = Path(app_config.save_dir) / "uploads" / "chat_attachments"
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


async def _write_upload_to_disk(upload: UploadFile, dest: Path) -> int:
    await upload.seek(0)
    written = 0
    chunk_size = 1024 * 1024

    async with aiofiles.open(dest, "wb") as buffer:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_ATTACHMENT_SIZE_BYTES:
                raise ValueError("附件过大，当前仅支持 5 MB 以内的文件")
            await buffer.write(chunk)

    return written


def _truncate_markdown(markdown: str) -> tuple[str, bool]:
    if len(markdown) <= MAX_ATTACHMENT_MARKDOWN_CHARS:
        return markdown, False

    truncated_content = markdown[: MAX_ATTACHMENT_MARKDOWN_CHARS - 100].rstrip()
    truncated_content = f"{truncated_content}\n\n[内容已截断，超出 {MAX_ATTACHMENT_MARKDOWN_CHARS} 字符限制]"
    return truncated_content, True


async def _convert_upload_to_markdown(upload: UploadFile) -> ConversionResult:
    """Persist an UploadFile temporarily, convert it to markdown, and clean up."""
    if not upload.filename:
        raise ValueError("无法识别的文件名")

    file_name = Path(upload.filename).name
    suffix = Path(file_name).suffix.lower()

    if ATTACHMENT_ALLOWED_EXTENSIONS and suffix not in ATTACHMENT_ALLOWED_EXTENSIONS:
        allowed = ", ".join(ATTACHMENT_ALLOWED_EXTENSIONS)
        raise ValueError(f"不支持的文件类型: {suffix or '未知'}，当前仅支持 {allowed}")

    temp_dir = _ensure_workdir()
    temp_path = temp_dir / f"{uuid.uuid4().hex}{suffix}"

    try:
        file_size = await _write_upload_to_disk(upload, temp_path)
        markdown = await Parser.aparse(str(temp_path))
        markdown, truncated = _truncate_markdown(markdown)
        return ConversionResult(
            file_id=uuid.uuid4().hex,
            file_name=file_name,
            file_type=upload.content_type,
            file_size=file_size,
            markdown=markdown,
            truncated=truncated,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Attachment conversion failed: {exc}")
        raise


async def require_user_conversation(conv_repo: ConversationRepository, thread_id: str, user_id: str):
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(user_id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return conversation


def _make_upload_virtual_path(file_name: str) -> str:
    safe_name = file_name.replace("/", "_").replace("\\", "_").strip(" .")
    return f"{VIRTUAL_PATH_UPLOADS}/{safe_name or 'attachment.bin'}"


def _make_attachment_path(file_name: str) -> str:
    """生成附件在沙盒用户目录中的统一路径。"""
    # 提取不带扩展名的部分
    base_name = file_name
    for ext in [".docx", ".txt", ".html", ".htm", ".pdf", ".md"]:
        if file_name.lower().endswith(ext):
            base_name = file_name[: -len(ext)]
            break

    # 替换路径分隔符
    safe_name = base_name.replace("/", "_").replace("\\", "_")
    return f"{safe_name}.md"


def _build_attachment_storage_path(*, user_id: str, thread_id: str, file_name: str) -> tuple[str, Path]:
    """返回附件虚拟路径和宿主机落盘路径。"""
    relative_name = _make_attachment_path(file_name)
    virtual_path = f"{VIRTUAL_PATH_UPLOADS}/attachments/{relative_name}"

    host_dir = Path(app_config.save_dir) / "threads" / thread_id / "user-data" / "uploads" / "attachments"
    host_dir.mkdir(parents=True, exist_ok=True)
    host_path = host_dir / relative_name

    return virtual_path, host_path


def _artifact_url(thread_id: str, virtual_path: str) -> str:
    return f"/api/chat/thread/{thread_id}/artifacts/{virtual_path.lstrip('/')}"


def _build_state_uploads(attachments: list[dict]) -> list[dict]:
    uploads: list[dict] = []
    for attachment in attachments:
        path = attachment.get("path")
        if not isinstance(path, str) or not path.strip():
            continue

        uploads.append(
            {
                "file_id": attachment.get("file_id"),
                "file_name": attachment.get("file_name"),
                "file_type": attachment.get("file_type"),
                "file_size": attachment.get("file_size", 0),
                "status": attachment.get("status", "uploaded"),
                "uploaded_at": attachment.get("uploaded_at"),
                "path": path,
                "artifact_url": attachment.get("artifact_url"),
            }
        )
    return uploads


def _build_state_files(attachments: list[dict]) -> dict[str, dict]:
    files: dict[str, dict] = {}
    for attachment in attachments:
        if attachment.get("status") != "parsed":
            continue

        file_path = attachment.get("file_path")
        markdown = attachment.get("markdown")
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        if not isinstance(markdown, str) or not markdown:
            continue

        uploaded_at = attachment.get("uploaded_at") or utc_isoformat()
        files[file_path] = {
            "content": markdown.splitlines(),
            "created_at": uploaded_at,
            "modified_at": uploaded_at,
        }

    return files


async def _sync_thread_upload_state(
    *,
    thread_id: str,
    user_id: str,
    agent_id: str,
    attachments: list[dict],
) -> None:
    try:
        agent = agent_manager.get_agent(agent_id)
        if not agent:
            logger.warning(f"Skip upload state sync: agent not found ({agent_id})")
            return

        graph = await agent.get_graph()
        config = {"configurable": {"thread_id": thread_id, "user_id": str(user_id)}}

        await graph.aupdate_state(
            config=config,
            values={
                "uploads": _build_state_uploads(attachments),
                "files": _build_state_files(attachments),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to sync upload state for thread {thread_id}: {exc}")


def serialize_attachment(record: dict) -> dict:
    path = record.get("path")
    return {
        "file_id": record.get("file_id"),
        "file_name": record.get("file_name"),
        "file_type": record.get("file_type"),
        "file_size": record.get("file_size", 0),
        "status": record.get("status", "uploaded"),
        "uploaded_at": record.get("uploaded_at"),
        "path": path,
        "artifact_url": record.get("artifact_url"),
        "original_path": record.get("original_path"),
        "original_artifact_url": record.get("original_artifact_url"),
        "minio_url": record.get("minio_url"),
    }


async def _materialize_attachment_files(
    *,
    thread_id: str,
    user_id: str,
    upload: UploadFile,
    file_name: str,
    file_content: bytes,
) -> dict:
    """将原始附件与可选 markdown 副本落盘到线程 user-data。"""
    ensure_thread_dirs(thread_id, user_id)

    upload_virtual_path = _make_upload_virtual_path(file_name)
    uploads_dir = sandbox_uploads_dir(thread_id)
    upload_actual_path = uploads_dir / Path(upload_virtual_path).name
    upload_actual_path.write_bytes(file_content)

    record = {
        "status": "uploaded",
        "path": upload_virtual_path,
        "artifact_url": _artifact_url(thread_id, upload_virtual_path),
        "storage_path": str(upload_actual_path),
        "original_path": upload_virtual_path,
        "original_artifact_url": _artifact_url(thread_id, upload_virtual_path),
        "original_storage_path": str(upload_actual_path),
        "minio_url": None,
    }

    try:
        await upload.seek(0)
        conversion = await _convert_upload_to_markdown(upload)
    except ValueError:
        return record
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Attachment markdown materialization failed for {file_name}: {exc}")
        return record

    markdown_virtual_path, markdown_host_path = _build_attachment_storage_path(
        user_id="",
        thread_id=thread_id,
        file_name=file_name,
    )
    markdown_host_path.write_text(conversion.markdown, encoding="utf-8")

    record.update(
        {
            "status": "parsed",
            "path": markdown_virtual_path,
            "artifact_url": _artifact_url(thread_id, markdown_virtual_path),
            "storage_path": str(markdown_host_path),
            "file_path": markdown_virtual_path,
            "markdown": conversion.markdown,
            "truncated": conversion.truncated,
            "markdown_storage_path": str(markdown_host_path),
        }
    )
    return record


async def create_thread_view(
    *,
    agent_id: str,
    title: str | None,
    metadata: dict | None,
    db: AsyncSession,
    current_user_id: str,
) -> dict:
    thread_id = str(uuid.uuid4())
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.create_conversation(
        user_id=str(current_user_id),
        agent_id=agent_id,
        title=title or "新的对话",
        thread_id=thread_id,
        metadata=metadata,
    )

    return {
        "id": conversation.thread_id,
        "user_id": conversation.user_id,
        "agent_id": conversation.agent_id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "metadata": conversation.extra_metadata or {},
    }


async def list_threads_view(
    *,
    agent_id: str | None,
    db: AsyncSession,
    current_user_id: str,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    conv_repo = ConversationRepository(db)
    conversations = await conv_repo.list_conversations(
        user_id=str(current_user_id),
        agent_id=agent_id,
        status="active",
        limit=limit,
        offset=offset,
    )

    return [
        {
            "id": conv.thread_id,
            "user_id": conv.user_id,
            "agent_id": conv.agent_id,
            "title": conv.title,
            "is_pinned": bool(conv.is_pinned),
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat(),
            "metadata": conv.extra_metadata or {},
        }
        for conv in conversations
    ]


async def delete_thread_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_user_id: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    await require_user_conversation(conv_repo, thread_id, str(current_user_id))
    deleted = await conv_repo.delete_conversation(thread_id, soft_delete=True)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return {"message": "删除成功"}


async def update_thread_view(
    *,
    thread_id: str,
    title: str | None = None,
    is_pinned: bool | None = None,
    db: AsyncSession,
    current_user_id: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    await require_user_conversation(conv_repo, thread_id, str(current_user_id))
    updated_conv = await conv_repo.update_conversation(thread_id, title=title, is_pinned=is_pinned)
    if not updated_conv:
        raise HTTPException(status_code=500, detail="更新失败")
    return {
        "id": updated_conv.thread_id,
        "user_id": updated_conv.user_id,
        "agent_id": updated_conv.agent_id,
        "title": updated_conv.title,
        "is_pinned": bool(updated_conv.is_pinned),
        "created_at": updated_conv.created_at.isoformat(),
        "updated_at": updated_conv.updated_at.isoformat(),
        "metadata": updated_conv.extra_metadata or {},
    }


async def upload_thread_attachment_view(
    *,
    thread_id: str,
    file: UploadFile,
    db: AsyncSession,
    current_user_id: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_user_id))
    if not file.filename:
        raise HTTPException(status_code=400, detail="无法识别的文件名")

    file_name = Path(file.filename).name
    await file.seek(0)
    file_content = await file.read()
    file_size = len(file_content)
    if file_size > MAX_ATTACHMENT_SIZE_BYTES:
        max_size_mb = MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"附件过大，当前仅支持 {max_size_mb} MB 以内的文件")
    materialized = await _materialize_attachment_files(
        thread_id=thread_id,
        user_id=str(conversation.user_id),
        upload=file,
        file_name=file_name,
        file_content=file_content,
    )

    attachment_record = {
        "file_id": uuid.uuid4().hex,
        "file_name": file_name,
        "file_type": file.content_type,
        "file_size": file_size,
        "status": materialized["status"],
        "uploaded_at": utc_isoformat(),
        "path": materialized["path"],
        "artifact_url": materialized["artifact_url"],
        "storage_path": materialized["storage_path"],
        "original_path": materialized["original_path"],
        "original_artifact_url": materialized["original_artifact_url"],
        "original_storage_path": materialized["original_storage_path"],
        "minio_url": materialized["minio_url"],
    }
    for optional_key in ("file_path", "markdown", "truncated", "markdown_storage_path"):
        if optional_key in materialized:
            attachment_record[optional_key] = materialized[optional_key]

    await conv_repo.add_attachment(conversation.id, attachment_record)
    all_attachments = await conv_repo.get_attachments(conversation.id)
    await _sync_thread_upload_state(
        thread_id=thread_id,
        user_id=str(current_user_id),
        agent_id=conversation.agent_id,
        attachments=all_attachments,
    )

    return serialize_attachment(attachment_record)


async def list_thread_attachments_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_user_id: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_user_id))
    attachments = await conv_repo.get_attachments(conversation.id)
    return {
        "attachments": [serialize_attachment(item) for item in attachments],
        "limits": {
            "allowed_extensions": sorted(ATTACHMENT_ALLOWED_EXTENSIONS),
            "max_size_bytes": MAX_ATTACHMENT_SIZE_BYTES,
        },
    }


async def delete_thread_attachment_view(
    *,
    thread_id: str,
    file_id: str,
    db: AsyncSession,
    current_user_id: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_user_id))

    existing_attachments = await conv_repo.get_attachments(conversation.id)
    target_attachment = next((item for item in existing_attachments if item.get("file_id") == file_id), None)

    removed = await conv_repo.remove_attachment(conversation.id, file_id)
    if not removed:
        raise HTTPException(status_code=404, detail="附件不存在或已被删除")

    if target_attachment:
        delete_candidates = {
            str(value).strip()
            for value in (
                target_attachment.get("storage_path"),
                target_attachment.get("original_storage_path"),
                target_attachment.get("markdown_storage_path"),
            )
            if isinstance(value, str) and value.strip()
        }
        for candidate in delete_candidates:
            try:
                file_path = Path(candidate)
                if file_path.exists():
                    file_path.unlink()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to remove attachment file {candidate}: {exc}")

    all_attachments = await conv_repo.get_attachments(conversation.id)
    await _sync_thread_upload_state(
        thread_id=thread_id,
        user_id=str(current_user_id),
        agent_id=conversation.agent_id,
        attachments=all_attachments,
    )

    return {"message": "附件已删除"}


async def get_thread_history_view(
    *,
    thread_id: str,
    current_user_id: str,
    db: AsyncSession,
) -> dict:
    """获取对话历史消息，包含用户反馈状态"""
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(current_user_id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    messages = await conv_repo.get_messages_by_thread_id(thread_id)

    history: list[dict] = []
    role_type_map = {"user": "human", "assistant": "ai", "tool": "tool", "system": "system"}

    for msg in messages:
        user_feedback = None
        if msg.feedbacks:
            for feedback in msg.feedbacks:
                if feedback.user_id == str(current_user_id):
                    user_feedback = {
                        "id": feedback.id,
                        "rating": feedback.rating,
                        "reason": feedback.reason,
                        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                    }
                    break

        msg_dict = {
            "id": msg.id,
            "type": role_type_map.get(msg.role, msg.role),
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "error_type": msg.extra_metadata.get("error_type") if msg.extra_metadata else None,
            "error_message": msg.extra_metadata.get("error_message") if msg.extra_metadata else None,
            "extra_metadata": msg.extra_metadata,
            "message_type": msg.message_type,
            "image_content": msg.image_content,
            "feedback": user_feedback,
        }

        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": str(tc.id),
                    "name": tc.tool_name,
                    "function": {"name": tc.tool_name},
                    "args": tc.tool_input or {},
                    "tool_call_result": {"content": (tc.tool_output or "")} if tc.status == "success" else None,
                    "status": tc.status,
                    "error_message": tc.error_message,
                }
                for tc in msg.tool_calls
            ]

        history.append(msg_dict)

    logger.info(f"Loaded {len(history)} messages with feedback for thread {thread_id}")
    return {"history": history}
