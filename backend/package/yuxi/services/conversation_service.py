import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.backends.sandbox import (
    ensure_thread_dirs,
    sandbox_uploads_dir,
)
from yuxi.agents.buildin import agent_manager
from yuxi.config import config as app_config
from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.conversation_repository import INVOCATION_CONVERSATION_SOURCES, ConversationRepository
from yuxi.services.mention_search_service import invalidate_mention_cache
from yuxi.services.ocr_service import parse_document
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import AgentRun, User
from yuxi.utils.datetime_utils import format_utc_datetime, utc_isoformat
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import VIRTUAL_PATH_UPLOADS
from yuxi.utils.upload_utils import read_upload_with_limit, write_upload_to_path

ATTACHMENT_ALLOWED_EXTENSIONS: tuple[str, ...] = ()
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ATTACHMENT_MARKDOWN_CHARS = 32_000  # TODO: 转 MARKDOWN的时候，不应该裁剪
TMP_ATTACHMENT_PREFIX = "tmp/chat_attachments"
TMP_ATTACHMENT_PARSE_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif")
TMP_ATTACHMENT_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif")
TMP_ATTACHMENT_OCR_METHODS = tuple(DocumentProcessorFactory.get_available_processors())
TMP_ATTACHMENT_PARSE_METHODS = ("disable", *TMP_ATTACHMENT_OCR_METHODS)


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
    return await write_upload_to_path(
        upload,
        dest,
        max_size_bytes=MAX_ATTACHMENT_SIZE_BYTES,
        too_large_message="附件过大，当前仅支持 5 MB 以内的文件",
    )


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
        markdown = await parse_document(str(temp_path))
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


async def require_user_conversation(conv_repo: ConversationRepository, thread_id: str, uid: str):
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.uid != str(uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return conversation


def _safe_file_name(file_name: str | None, default: str = "attachment.bin") -> str:
    safe_name = Path(file_name or "").name.replace("/", "_").replace("\\", "_").strip(" .")
    return safe_name or default


def _make_upload_virtual_path(file_name: str) -> str:
    return f"{VIRTUAL_PATH_UPLOADS}/{_safe_file_name(file_name)}"


def _make_attachment_path(file_name: str) -> str:
    """生成附件在沙盒用户目录中的统一路径。"""
    file_name = _safe_file_name(file_name)
    # 提取不带扩展名的部分
    base_name = file_name
    for ext in [".docx", ".txt", ".html", ".htm", ".pdf", ".md"]:
        if file_name.lower().endswith(ext):
            base_name = file_name[: -len(ext)]
            break

    # 替换路径分隔符
    safe_name = base_name.replace("/", "_").replace("\\", "_")
    return f"{safe_name}.md"


def _build_attachment_storage_path(*, uid: str, thread_id: str, file_name: str) -> tuple[str, Path]:
    """返回附件虚拟路径和宿主机落盘路径。"""
    relative_name = _make_attachment_path(file_name)
    virtual_path = f"{VIRTUAL_PATH_UPLOADS}/attachments/{relative_name}"

    host_dir = Path(app_config.save_dir) / "threads" / thread_id / "user-data" / "uploads" / "attachments"
    host_dir.mkdir(parents=True, exist_ok=True)
    host_path = host_dir / relative_name

    return virtual_path, host_path


def _artifact_url(thread_id: str, virtual_path: str) -> str:
    return f"/api/chat/thread/{thread_id}/artifacts/{virtual_path.lstrip('/')}"


def _tmp_attachment_prefix(uid: str, tmp_file_id: str) -> str:
    return f"{TMP_ATTACHMENT_PREFIX}/{uid}/{tmp_file_id}"


def _get_tmp_attachment_bucket() -> str:
    return get_minio_client().KB_BUCKETS["documents"]


def _make_tmp_attachment_object(uid: str, file_name: str) -> tuple[str, str]:
    """生成用户隔离的 tmp 对象路径。"""
    tmp_file_id = uuid.uuid4().hex
    safe_name = _safe_file_name(file_name)
    return tmp_file_id, f"{_tmp_attachment_prefix(uid, tmp_file_id)}/original/{safe_name}"


def _make_tmp_parsed_object(uid: str, tmp_file_id: str, file_name: str) -> str:
    stem = Path(_safe_file_name(file_name)).stem or "attachment"
    return f"{_tmp_attachment_prefix(uid, tmp_file_id)}/parsed/{stem}.md"


def _minio_source(bucket_name: str, object_name: str) -> str:
    return f"minio://{bucket_name}/{quote(object_name, safe='/')}"


def _parse_user_tmp_object(object_name: str, uid: str) -> tuple[str, str, str]:
    if not object_name or "\\" in object_name:
        raise HTTPException(status_code=400, detail="无效的临时附件路径")

    user_prefix = f"{TMP_ATTACHMENT_PREFIX}/{uid}/"
    if not object_name.startswith(user_prefix):
        raise HTTPException(status_code=403, detail="无权访问该临时附件")

    parts = object_name[len(user_prefix) :].split("/")
    if len(parts) != 3 or any(not part or part in {".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="无效的临时附件路径")

    return parts[0], parts[1], parts[2]


def _require_tmp_object_section(
    object_name: str,
    uid: str,
    section: str,
    tmp_file_id: str | None = None,
) -> tuple[str, str]:
    current_tmp_file_id, current_section, object_file_name = _parse_user_tmp_object(object_name, uid)
    if current_section != section or (tmp_file_id is not None and current_tmp_file_id != tmp_file_id):
        raise HTTPException(status_code=400, detail="无效的临时附件路径")
    if section == "parsed" and Path(object_file_name).suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="无效的解析附件路径")
    return current_tmp_file_id, object_file_name


def _normalize_parse_method(file_name: str, parse_method: str | None) -> str:
    """按文件类型确定临时附件解析方式。"""

    suffix = Path(file_name).suffix.lower()
    if suffix not in TMP_ATTACHMENT_PARSE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="当前仅支持 PDF 和图片附件解析")

    if suffix in TMP_ATTACHMENT_IMAGE_EXTENSIONS:
        default_engine = app_config.default_ocr_engine
        # 图片没有可直接提取的文本层，系统默认 disable 时仍需回退到本地 OCR。
        method = parse_method or ("rapid_ocr" if default_engine == "disable" else default_engine)
    else:
        method = parse_method or "disable"
    allowed_methods = (
        TMP_ATTACHMENT_OCR_METHODS if suffix in TMP_ATTACHMENT_IMAGE_EXTENSIONS else TMP_ATTACHMENT_PARSE_METHODS
    )

    if method not in allowed_methods:
        allowed = ", ".join(allowed_methods)
        raise HTTPException(status_code=400, detail=f"不支持的解析方法: {method}，可选: {allowed}")
    return method


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
                "request_id": attachment.get("request_id"),
            }
        )
    return uploads


async def _sync_thread_upload_state(
    *,
    thread_id: str,
    uid: str,
    agent_id: str,
    backend_id: str | None,
    attachments: list[dict],
) -> None:
    try:
        agent = agent_manager.get_agent(backend_id or agent_id)
        if not agent:
            logger.warning(f"Skip upload state sync: agent not found ({agent_id})")
            return

        graph = await agent.get_graph()
        config = {"configurable": {"thread_id": thread_id, "uid": str(uid)}}

        await graph.aupdate_state(
            config=config,
            values={
                "uploads": _build_state_uploads(attachments),
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
        "request_id": record.get("request_id"),
    }


async def _materialize_attachment_files(
    *,
    thread_id: str,
    uid: str,
    upload: UploadFile,
    file_name: str,
    file_content: bytes,
) -> dict:
    """将原始附件与可选 markdown 副本落盘到线程 user-data。"""
    ensure_thread_dirs(thread_id, uid)

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
        uid=uid,
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


def _materialize_tmp_attachment_files(
    *,
    thread_id: str,
    uid: str,
    file_id: str,
    file_name: str,
    file_content: bytes,
    parsed_markdown: str | None = None,
    truncated: bool = False,
) -> dict:
    """将 tmp 附件复制到线程目录，不主动删除 tmp 对象。"""
    ensure_thread_dirs(thread_id, uid)

    storage_name = f"{file_id}_{file_name}"
    upload_virtual_path = _make_upload_virtual_path(storage_name)
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

    if parsed_markdown is None:
        return record

    markdown_virtual_path, markdown_host_path = _build_attachment_storage_path(
        uid=uid,
        thread_id=thread_id,
        file_name=storage_name,
    )
    markdown_host_path.write_text(parsed_markdown, encoding="utf-8")
    record.update(
        {
            "status": "parsed",
            "path": markdown_virtual_path,
            "artifact_url": _artifact_url(thread_id, markdown_virtual_path),
            "storage_path": str(markdown_host_path),
            "file_path": markdown_virtual_path,
            "markdown": parsed_markdown,
            "truncated": truncated,
            "markdown_storage_path": str(markdown_host_path),
        }
    )
    return record


async def create_thread_view(
    *,
    agent_slug: str,
    title: str | None,
    metadata: dict | None,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    user_result = await db.execute(select(User).where(User.uid == str(current_uid)))
    current_user = user_result.scalar_one_or_none()
    if not current_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    agent_repo = AgentRepository(db)
    agent_item = await agent_repo.get_visible_by_slug(slug=agent_slug, user=current_user)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    thread_id = str(uuid.uuid4())
    conv_repo = ConversationRepository(db)
    thread_metadata = dict(metadata or {})
    thread_metadata["backend_id"] = agent_item.backend_id
    conversation = await conv_repo.create_conversation(
        uid=str(current_uid),
        agent_id=agent_item.slug,
        title=title or "新的对话",
        thread_id=thread_id,
        metadata=thread_metadata,
    )

    return {
        "id": conversation.thread_id,
        "uid": conversation.uid,
        "agent_id": conversation.agent_id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "metadata": conversation.extra_metadata or {},
    }


async def list_threads_view(
    *,
    agent_slug: str | None,
    db: AsyncSession,
    current_uid: str,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    conv_repo = ConversationRepository(db)
    conversations = await conv_repo.list_conversations(
        uid=str(current_uid),
        agent_id=agent_slug,
        status="active",
        limit=limit,
        offset=offset,
        exclude_sources=INVOCATION_CONVERSATION_SOURCES,
    )

    return [
        {
            "id": conv.thread_id,
            "uid": conv.uid,
            "agent_id": conv.agent_id,
            "title": conv.title,
            "is_pinned": bool(conv.is_pinned),
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat(),
            "metadata": conv.extra_metadata or {},
        }
        for conv in conversations
    ]


async def search_threads_view(
    *,
    query: str,
    agent_id: str | None,
    db: AsyncSession,
    current_uid: str,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return {"items": [], "has_more": False, "limit": limit, "offset": offset}

    conv_repo = ConversationRepository(db)
    search_items, has_more = await conv_repo.search_conversations_by_message_content(
        uid=str(current_uid),
        agent_id=agent_id,
        query=normalized_query,
        limit=limit,
        offset=offset,
        exclude_sources=INVOCATION_CONVERSATION_SOURCES,
    )

    items = []
    for item in search_items:
        conv = item["conversation"]
        snippets = [
            {
                "message_id": snippet.get("message_id"),
                "content": snippet.get("content") or "",
                "created_at": format_utc_datetime(snippet.get("created_at")),
            }
            for snippet in item.get("snippets", [])
        ]
        items.append(
            {
                "id": conv.thread_id,
                "thread_id": conv.thread_id,
                "uid": conv.uid,
                "agent_id": conv.agent_id,
                "title": conv.title,
                "is_pinned": bool(conv.is_pinned),
                "created_at": format_utc_datetime(conv.created_at),
                "updated_at": format_utc_datetime(conv.updated_at),
                "metadata": conv.extra_metadata or {},
                "matched_count": item.get("matched_count", 0),
                "message_id": item.get("message_id"),
                "latest_match_at": format_utc_datetime(item.get("latest_match_at")),
                "snippets": snippets,
            }
        )

    return {"items": items, "has_more": has_more, "limit": limit, "offset": offset}


async def delete_thread_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    await require_user_conversation(conv_repo, thread_id, str(current_uid))
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
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    await require_user_conversation(conv_repo, thread_id, str(current_uid))
    updated_conv = await conv_repo.update_conversation(thread_id, title=title, is_pinned=is_pinned)
    if not updated_conv:
        raise HTTPException(status_code=500, detail="更新失败")
    return {
        "id": updated_conv.thread_id,
        "uid": updated_conv.uid,
        "agent_id": updated_conv.agent_id,
        "title": updated_conv.title,
        "is_pinned": bool(updated_conv.is_pinned),
        "created_at": updated_conv.created_at.isoformat(),
        "updated_at": updated_conv.updated_at.isoformat(),
        "metadata": updated_conv.extra_metadata or {},
    }


async def upload_tmp_attachment_view(*, file: UploadFile, current_uid: str) -> dict:
    """上传附件到用户隔离的 MinIO tmp 路径。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="无法识别的文件名")

    file_name = _safe_file_name(file.filename)
    try:
        file_content = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_ATTACHMENT_SIZE_BYTES,
            too_large_message="附件过大，当前仅支持 5 MB 以内的文件",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    file_size = len(file_content)
    tmp_file_id, object_name = _make_tmp_attachment_object(str(current_uid), file_name)
    minio_client = get_minio_client()
    bucket_name = _get_tmp_attachment_bucket()
    try:
        upload_result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=file_content,
            content_type=file.content_type,
        )
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=f"临时附件上传失败: {exc}") from exc

    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        parse_methods = list(TMP_ATTACHMENT_PARSE_METHODS)
    elif suffix in TMP_ATTACHMENT_IMAGE_EXTENSIONS:
        parse_methods = list(TMP_ATTACHMENT_OCR_METHODS)
    else:
        parse_methods = []

    return {
        "tmp_file_id": tmp_file_id,
        "file_name": file_name,
        "file_type": file.content_type,
        "file_size": file_size,
        "bucket_name": upload_result.bucket_name,
        "object_name": upload_result.object_name,
        "minio_url": upload_result.url,
        "uploaded_at": utc_isoformat(),
        "parse_supported": bool(parse_methods),
        "parse_methods": parse_methods,
    }


async def parse_tmp_attachment_view(
    *,
    object_name: str,
    file_name: str,
    parse_method: str | None,
    bucket_name: str | None,
    current_uid: str,
) -> dict:
    """解析用户 tmp 附件并把 markdown 写回 tmp。"""
    minio_client = get_minio_client()
    expected_bucket = _get_tmp_attachment_bucket()
    bucket_name = bucket_name or expected_bucket
    if bucket_name != expected_bucket:
        raise HTTPException(status_code=400, detail="无效的临时附件 bucket")

    tmp_file_id, safe_name = _require_tmp_object_section(object_name, str(current_uid), "original")
    method = _normalize_parse_method(safe_name, parse_method)

    try:
        markdown = await parse_document(_minio_source(bucket_name, object_name), params={"ocr_engine": method})
        markdown, truncated = _truncate_markdown(markdown)
        parsed_object_name = _make_tmp_parsed_object(str(current_uid), tmp_file_id, safe_name)
        upload_result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=parsed_object_name,
            data=markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=f"读取临时附件失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Tmp attachment parse failed for {safe_name}: {exc}")
        raise HTTPException(status_code=400, detail=f"附件解析失败: {exc}") from exc

    return {
        "tmp_file_id": tmp_file_id,
        "file_name": safe_name,
        "bucket_name": upload_result.bucket_name,
        "object_name": object_name,
        "parsed_object_name": upload_result.object_name,
        "parsed_minio_url": upload_result.url,
        "parse_method": method,
        "status": "parsed",
        "truncated": truncated,
    }


async def confirm_tmp_thread_attachments_view(
    *,
    thread_id: str,
    attachments: list[dict],
    db: AsyncSession,
    current_uid: str,
) -> dict:
    """将选中的 tmp 附件正式关联到对话线程。"""
    if not attachments:
        raise HTTPException(status_code=400, detail="请选择要添加的附件")

    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
    minio_client = get_minio_client()
    expected_bucket = _get_tmp_attachment_bucket()
    prepared_items: list[dict] = []

    for item in attachments:
        object_name = str(item.get("object_name") or "")
        bucket_name = str(item.get("bucket_name") or expected_bucket)
        if bucket_name != expected_bucket:
            raise HTTPException(status_code=400, detail="无效的临时附件 bucket")

        tmp_file_id, file_name = _require_tmp_object_section(object_name, str(current_uid), "original")
        try:
            file_content = await minio_client.adownload_file(bucket_name, object_name)
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=f"读取临时附件失败: {exc}") from exc

        if len(file_content) > MAX_ATTACHMENT_SIZE_BYTES:
            max_size_mb = MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)
            raise HTTPException(status_code=400, detail=f"附件过大，当前仅支持 {max_size_mb} MB 以内的文件")

        parsed_markdown = None
        parsed_object_name = str(item.get("parsed_object_name") or "")
        if parsed_object_name:
            _require_tmp_object_section(parsed_object_name, str(current_uid), "parsed", tmp_file_id)
            expected_parsed_object = _make_tmp_parsed_object(str(current_uid), tmp_file_id, file_name)
            if parsed_object_name != expected_parsed_object:
                raise HTTPException(status_code=400, detail="解析附件路径无效")
            try:
                parsed_bytes = await minio_client.adownload_file(bucket_name, parsed_object_name)
                parsed_markdown = parsed_bytes.decode("utf-8")
            except StorageError as exc:
                raise HTTPException(status_code=400, detail=f"读取解析附件失败: {exc}") from exc
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=400, detail="解析附件内容不是有效的 Markdown 文本") from exc

        prepared_items.append(
            {
                "file_name": file_name,
                "file_type": item.get("file_type"),
                "file_content": file_content,
                "parsed_markdown": parsed_markdown,
                "truncated": bool(item.get("truncated")),
            }
        )

    added_records: list[dict] = []
    for prepared in prepared_items:
        file_id = uuid.uuid4().hex
        materialized = _materialize_tmp_attachment_files(
            thread_id=thread_id,
            uid=str(conversation.uid),
            file_id=file_id,
            file_name=prepared["file_name"],
            file_content=prepared["file_content"],
            parsed_markdown=prepared["parsed_markdown"],
            truncated=prepared["truncated"],
        )
        attachment_record = {
            "file_id": file_id,
            "file_name": prepared["file_name"],
            "file_type": prepared["file_type"],
            "file_size": len(prepared["file_content"]),
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
        added_records.append(attachment_record)

    await conv_repo.add_attachments(conversation.id, added_records)
    all_attachments = await conv_repo.get_attachments(conversation.id)
    await _sync_thread_upload_state(
        thread_id=thread_id,
        uid=str(current_uid),
        agent_id=conversation.agent_id,
        backend_id=(conversation.extra_metadata or {}).get("backend_id"),
        attachments=all_attachments,
    )
    await invalidate_mention_cache(thread_id)

    return {"attachments": [serialize_attachment(item) for item in added_records]}


async def upload_thread_attachment_view(
    *,
    thread_id: str,
    file: UploadFile,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
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
        uid=str(conversation.uid),
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
        uid=str(current_uid),
        agent_id=conversation.agent_id,
        backend_id=(conversation.extra_metadata or {}).get("backend_id"),
        attachments=all_attachments,
    )

    await invalidate_mention_cache(thread_id)

    return serialize_attachment(attachment_record)


async def list_thread_attachments_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
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
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))

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
        uid=str(current_uid),
        agent_id=conversation.agent_id,
        backend_id=(conversation.extra_metadata or {}).get("backend_id"),
        attachments=all_attachments,
    )

    await invalidate_mention_cache(thread_id)

    return {"message": "附件已删除"}


async def get_thread_history_view(
    *,
    thread_id: str,
    current_uid: str,
    db: AsyncSession,
) -> dict:
    """获取对话历史消息，包含用户反馈状态"""
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.uid != str(current_uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    messages = await conv_repo.get_messages_by_thread_id(thread_id)
    messages = [
        message
        for message in messages
        if not (message.role == "user" and message.delivery_status in {"queued", "cancelled", "rejected"})
    ]

    run_ids_in_messages = {msg.run_id for msg in messages if msg.run_id}
    run_created_at: dict[str, Any] = {}
    if run_ids_in_messages:
        run_result = await db.execute(
            select(AgentRun.id, AgentRun.created_at)
            .where(AgentRun.id.in_(run_ids_in_messages))
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        run_created_at = {run_id: created_at for run_id, created_at in run_result.all()}
    messages.sort(
        key=lambda message: (
            run_created_at.get(message.run_id) or message.created_at,
            0 if message.role == "user" else 1,
            message.created_at,
            message.id,
        )
    )
    message_request_ids = set()
    for msg in messages:
        request_id = (msg.extra_metadata or {}).get("request_id")
        if msg.role == "user" and request_id:
            message_request_ids.add(str(request_id))
    attachments_by_request_id: dict[str, list[dict]] = {}
    if message_request_ids:
        for attachment in await conv_repo.get_attachments(conversation.id):
            request_id = attachment.get("request_id")
            if not request_id or str(request_id) not in message_request_ids:
                continue
            attachments_by_request_id.setdefault(str(request_id), []).append(serialize_attachment(attachment))

    history: list[dict] = []
    role_type_map = {"user": "human", "assistant": "ai", "tool": "tool", "system": "system"}

    for msg in messages:
        user_feedback = None
        if msg.feedbacks:
            for feedback in msg.feedbacks:
                if feedback.uid == str(current_uid):
                    user_feedback = {
                        "id": feedback.id,
                        "rating": feedback.rating,
                        "reason": feedback.reason,
                        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                    }
                    break

        extra_metadata = dict(msg.extra_metadata or {})
        request_id = extra_metadata.get("request_id")
        if msg.role == "user" and request_id and not extra_metadata.get("attachments"):
            extra_metadata["attachments"] = attachments_by_request_id.get(str(request_id), [])

        msg_dict = {
            "id": msg.id,
            "type": role_type_map.get(msg.role, msg.role),
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "run_id": msg.run_id,
            "request_id": msg.request_id,
            "delivery_status": msg.delivery_status,
            "error_type": extra_metadata.get("error_type"),
            "error_message": extra_metadata.get("error_message"),
            "extra_metadata": extra_metadata,
            "message_type": msg.message_type,
            "image_content": msg.image_content,
            "feedback": user_feedback,
        }

        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.langgraph_tool_call_id or str(tc.id),
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
