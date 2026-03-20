import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.config import config as app_config
from yuxi.plugins.parser import Parser
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.minio.client import get_minio_client
from yuxi.utils.datetime_utils import utc_isoformat
from yuxi.utils.logging_config import logger

# 附件存储桶名称
ATTACHMENTS_BUCKET = "user"
ATTACHMENT_ALLOWED_EXTENSIONS: tuple[str, ...] = (".txt", ".md", ".docx", ".html", ".htm")
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ATTACHMENT_MARKDOWN_CHARS = 32_000


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

    if suffix not in ATTACHMENT_ALLOWED_EXTENSIONS:
        allowed = ", ".join(ATTACHMENT_ALLOWED_EXTENSIONS)
        raise ValueError(f"不支持的文件类型: {suffix or '未知'}，当前仅支持 {allowed}")

    temp_dir = _ensure_workdir()
    temp_path = temp_dir / f"{uuid.uuid4().hex}{suffix}"

    try:
        file_size = await _write_upload_to_disk(upload, temp_path)
        markdown = await Parser.aparse(str(temp_path), source_type="file")
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
        logger.error("Attachment conversion failed: %s", exc)
        raise


async def require_user_conversation(conv_repo: ConversationRepository, thread_id: str, user_id: str):
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(user_id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return conversation


def _make_attachment_path(file_name: str) -> str:
    """生成附件在文件系统中的路径（无需 thread_id，state 已隔离）

    统一使用 .md 扩展名，因为文件内容已经是 Markdown 格式
    """
    # 提取不带扩展名的部分
    base_name = file_name
    for ext in [".docx", ".txt", ".html", ".htm", ".pdf", ".md"]:
        if file_name.lower().endswith(ext):
            base_name = file_name[: -len(ext)]
            break

    # 替换路径分隔符
    safe_name = base_name.replace("/", "_").replace("\\", "_")
    return f"/attachments/{safe_name}.md"


def _build_state_files(attachments: list[dict]) -> dict:
    files = {}
    for attachment in attachments:
        if attachment.get("status") != "parsed":
            continue

        file_path = attachment.get("file_path")
        markdown = attachment.get("markdown")
        if not file_path or not markdown:
            continue

        now = datetime.now(UTC).isoformat()
        files[file_path] = {
            "content": markdown.split("\n"),
            "created_at": attachment.get("uploaded_at", now),
            "modified_at": attachment.get("uploaded_at", now),
        }
    return files


async def _sync_thread_attachment_state(
    *,
    thread_id: str,
    user_id: str,
    agent_id: str,
    attachments: list[dict],
) -> None:
    try:
        agent = agent_manager.get_agent(agent_id)
        if not agent:
            logger.warning(f"Skip attachment state sync: agent not found ({agent_id})")
            return

        graph = await agent.get_graph()
        config = {"configurable": {"thread_id": thread_id, "user_id": str(user_id)}}

        # 先获取现有 state，保留非附件文件
        state = await graph.aget_state(config)
        state_values = getattr(state, "values", {}) if state else {}
        existing_files = state_values.get("files", {}) if isinstance(state_values, dict) else {}
        if not isinstance(existing_files, dict):
            existing_files = {}

        # 仅对 /attachments 命名空间做增量更新，避免覆盖 agent 运行期生成的其它文件。
        next_attachment_files = _build_state_files(attachments)
        prev_attachment_paths = {
            path for path in existing_files.keys() if isinstance(path, str) and path.startswith("/attachments/")
        }
        next_attachment_paths = set(next_attachment_files.keys())

        file_updates: dict[str, dict | None] = {**next_attachment_files}
        for removed_path in prev_attachment_paths - next_attachment_paths:
            file_updates[removed_path] = None

        # 使用 Command 确保 reducer 被正确应用
        await graph.aupdate_state(
            config=config,
            values={
                "attachments": attachments,
                "files": file_updates,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to sync attachment state for thread {thread_id}: {e}")


def serialize_attachment(record: dict) -> dict:
    """序列化附件记录，返回给前端"""
    return {
        "file_id": record.get("file_id"),
        "file_name": record.get("file_name"),
        "file_type": record.get("file_type"),
        "file_size": record.get("file_size", 0),
        "status": record.get("status", "parsed"),
        "uploaded_at": record.get("uploaded_at"),
        "truncated": record.get("truncated", False),
        "minio_url": record.get("minio_url"),  # 仅用于前端下载
    }


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

    try:
        conversion = await _convert_upload_to_markdown(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"附件解析失败: {exc}")
        raise HTTPException(status_code=500, detail="附件解析失败，请稍后重试") from exc

    # 生成文件路径
    file_path = _make_attachment_path(conversion.file_name)

    # 上传源文件到 MinIO（用于前端下载）
    minio_url = None
    try:
        file_content = await file.read()
        await file.seek(0)
        client = get_minio_client()
        safe_filename = conversion.file_name.replace("/", "_").replace("\\", "_")
        object_name = f"user/{current_user_id}/chat_attachments/{thread_id}/{safe_filename}"
        result = client.upload_file(
            bucket_name=ATTACHMENTS_BUCKET,
            object_name=object_name,
            data=file_content,
        )
        minio_url = result.url
        logger.info(f"Uploaded attachment to MinIO: {object_name}")
    except Exception as e:
        logger.error(f"Failed to upload attachment to MinIO: {e}")
        # 继续处理，不因为上传失败而中断

    attachment_record = {
        "file_id": conversion.file_id,
        "file_name": conversion.file_name,
        "file_type": conversion.file_type,
        "file_size": conversion.file_size,
        "status": "parsed",
        "markdown": conversion.markdown,
        "uploaded_at": utc_isoformat(),
        "truncated": conversion.truncated,
        "file_path": file_path,  # 用于 StateBackend，前端不返回此字段
        "minio_url": minio_url,  # 暂未使用
    }
    await conv_repo.add_attachment(conversation.id, attachment_record)
    all_attachments = await conv_repo.get_attachments(conversation.id)
    await _sync_thread_attachment_state(
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
    removed = await conv_repo.remove_attachment(conversation.id, file_id)
    if not removed:
        raise HTTPException(status_code=404, detail="附件不存在或已被删除")
    all_attachments = await conv_repo.get_attachments(conversation.id)
    await _sync_thread_attachment_state(
        thread_id=thread_id,
        user_id=str(current_user_id),
        agent_id=conversation.agent_id,
        attachments=all_attachments,
    )
    return {"message": "附件已删除"}
