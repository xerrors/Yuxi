import asyncio
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.backends.paths import runtime_path_for_workdir_scope, workdir_scope_from_runtime_path
from yuxi.config.options import system_options
from yuxi.knowledge.parser.capabilities import (
    IMAGE_FILE_EXTENSIONS,
    PDF_FILE_EXTENSIONS,
    get_ocr_engines_for_extension,
)
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.utils.datetime_utils import utc_isoformat
from yuxi.utils.logging_config import logger
from yuxi.utils.upload_utils import read_upload_with_limit

ATTACHMENT_ALLOWED_EXTENSIONS: tuple[str, ...] = ()
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ATTACHMENT_MARKDOWN_CHARS = 32_000  # TODO: 转 MARKDOWN的时候，不应该裁剪
TMP_ATTACHMENT_PREFIX = "tmp/chat_attachments"
TMP_ATTACHMENT_PARSE_EXTENSIONS = (*PDF_FILE_EXTENSIONS, *IMAGE_FILE_EXTENSIONS)
TMP_ATTACHMENT_IMAGE_EXTENSIONS = IMAGE_FILE_EXTENSIONS
TMP_ATTACHMENT_TTL = timedelta(hours=24)


async def _require_user_conversation(conv_repo: ConversationRepository, thread_id: str, uid: str):
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.uid != str(uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return conversation


def _truncate_markdown(markdown: str) -> tuple[str, bool]:
    if len(markdown) <= MAX_ATTACHMENT_MARKDOWN_CHARS:
        return markdown, False

    truncated_content = markdown[: MAX_ATTACHMENT_MARKDOWN_CHARS - 100].rstrip()
    truncated_content = f"{truncated_content}\n\n[内容已截断，超出 {MAX_ATTACHMENT_MARKDOWN_CHARS} 字符限制]"
    return truncated_content, True


def _safe_file_name(file_name: str | None, default: str = "attachment.bin") -> str:
    safe_name = Path(file_name or "").name.replace("/", "_").replace("\\", "_").strip(" .")
    return safe_name or default


def _make_attachment_path(file_name: str) -> str:
    """生成附件在沙盒用户目录中的统一路径。"""
    file_name = _safe_file_name(file_name)
    base_name = file_name
    for ext in [".docx", ".txt", ".html", ".htm", ".pdf", ".md"]:
        if file_name.lower().endswith(ext):
            base_name = file_name[: -len(ext)]
            break

    safe_name = base_name.replace("/", "_").replace("\\", "_")
    return f"{safe_name}.md"


def _artifact_url(thread_id: str, virtual_path: str) -> str:
    return f"/api/chat/thread/{thread_id}/artifacts/{virtual_path.lstrip('/')}"


def _tmp_attachment_prefix(uid: str, tmp_file_id: str) -> str:
    return f"{TMP_ATTACHMENT_PREFIX}/{uid}/{tmp_file_id}"


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


def _normalize_parse_method(file_name: str, parse_method: str | None, default_ocr_engine: str) -> str:
    """按文件类型确定临时附件解析方式。"""
    suffix = Path(file_name).suffix.lower()
    if suffix not in TMP_ATTACHMENT_PARSE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="当前仅支持 PDF 和图片附件解析")

    allowed_methods = get_ocr_engines_for_extension(suffix)
    if suffix in TMP_ATTACHMENT_IMAGE_EXTENSIONS:
        method = parse_method or ("rapid_ocr" if default_ocr_engine == "disable" else default_ocr_engine)
    else:
        method = parse_method or "disable"
        allowed_methods = ("disable", *allowed_methods)

    if method not in allowed_methods:
        allowed = ", ".join(allowed_methods)
        raise HTTPException(status_code=400, detail=f"不支持的解析方法: {method}，可选: {allowed}")
    return method


def serialize_attachment(record: dict, *, thread_id: str) -> dict:
    """输出附件 API 结构，并从路径派生无需持久化的 URL。"""
    path = record.get("path")
    original_path = record.get("original_path")
    return {
        "file_id": record.get("file_id"),
        "file_name": record.get("file_name"),
        "file_type": record.get("file_type"),
        "file_size": record.get("file_size", 0),
        "status": record.get("status", "uploaded"),
        "uploaded_at": record.get("uploaded_at"),
        "path": path,
        "artifact_url": _artifact_url(thread_id, path) if isinstance(path, str) else None,
        "original_path": original_path,
        "original_artifact_url": (_artifact_url(thread_id, original_path) if isinstance(original_path, str) else None),
        "request_id": record.get("request_id"),
    }


async def _write_workdir_file(workdir, path: str, content: bytes) -> None:
    """通过受信任 no-follow 文件边界写入实时 Workdir。"""
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="yuxi-attachment-", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(content)
        await asyncio.to_thread(workdir.copy_file_from_path, path, temp_path)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


async def _store_attachment(
    *,
    workdir,
    file_id: str,
    file_name: str,
    file_type: str | None,
    file_content: bytes,
    parsed_markdown: str | None = None,
) -> dict:
    """将正式附件直接写入实时 Project Workdir。"""
    file_name = _safe_file_name(file_name)
    storage_name = f"{file_id}_{file_name}"
    original_scope = f"/uploads/{storage_name}"
    await _write_workdir_file(workdir, original_scope, file_content)
    original_path = runtime_path_for_workdir_scope(workdir.relative_path, original_scope)
    record = {
        "file_id": file_id,
        "file_name": file_name,
        "file_type": file_type,
        "file_size": len(file_content),
        "status": "uploaded",
        "uploaded_at": utc_isoformat(),
        "path": original_path,
        "original_path": original_path,
    }
    if parsed_markdown is None:
        return record

    markdown_scope = f"/uploads/attachments/{_make_attachment_path(storage_name)}"
    markdown_path = runtime_path_for_workdir_scope(workdir.relative_path, markdown_scope)
    try:
        await _write_workdir_file(workdir, markdown_scope, parsed_markdown.encode("utf-8"))
    except Exception:
        await asyncio.to_thread(workdir.delete, original_scope)
        raise
    record.update(
        {
            "status": "parsed",
            "path": markdown_path,
        }
    )
    return record


async def _rollback_stored_attachments(workdir, records: list[dict]) -> None:
    """尽力删除本批尚未提交的附件文件。"""
    for record in records:
        for path in {record.get("path"), record.get("original_path")}:
            if not isinstance(path, str):
                continue
            try:
                scope = workdir_scope_from_runtime_path(workdir.relative_path, path)
                await asyncio.to_thread(workdir.delete, scope)
            except Exception:
                pass


async def _cleanup_expired_tmp_attachments(minio_client, bucket_name: str, uid: str) -> None:
    """上传时顺手清理当前用户 24 小时前遗留的临时附件。"""
    prefix = f"{TMP_ATTACHMENT_PREFIX}/{uid}/"
    try:
        objects = await minio_client.alist_object_metadata(bucket_name, prefix)
    except StorageError as exc:
        logger.warning("列出过期临时附件失败: uid=%s error=%s", uid, exc)
        return

    latest_by_tmp_id: dict[str, datetime] = {}
    for item in objects:
        object_name = item.get("object_name")
        modified_at = item.get("last_modified")
        if not isinstance(object_name, str) or not isinstance(modified_at, datetime):
            continue
        try:
            tmp_file_id, _, _ = _parse_user_tmp_object(object_name, uid)
        except HTTPException:
            continue
        if modified_at.tzinfo is None:
            modified_at = modified_at.replace(tzinfo=UTC)
        previous = latest_by_tmp_id.get(tmp_file_id)
        if previous is None or modified_at > previous:
            latest_by_tmp_id[tmp_file_id] = modified_at

    cutoff = datetime.now(UTC) - TMP_ATTACHMENT_TTL
    expired_ids = [tmp_file_id for tmp_file_id, modified_at in latest_by_tmp_id.items() if modified_at <= cutoff]
    results = await asyncio.gather(
        *(
            minio_client.adelete_objects_by_prefix(bucket_name, f"{_tmp_attachment_prefix(uid, tmp_file_id)}/")
            for tmp_file_id in expired_ids
        ),
        return_exceptions=True,
    )
    for tmp_file_id, result in zip(expired_ids, results):
        if isinstance(result, Exception):
            logger.warning("清理过期临时附件失败: uid=%s tmp_file_id=%s error=%s", uid, tmp_file_id, result)


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
    bucket_name = minio_client.KB_BUCKETS["documents"]
    try:
        upload_result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=file_content,
            content_type=file.content_type,
        )
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=f"临时附件上传失败: {exc}") from exc
    await _cleanup_expired_tmp_attachments(minio_client, bucket_name, str(current_uid))

    suffix = Path(file_name).suffix.lower()
    if suffix in TMP_ATTACHMENT_PARSE_EXTENSIONS:
        parse_methods = list(get_ocr_engines_for_extension(suffix))
        if suffix in PDF_FILE_EXTENSIONS:
            parse_methods.insert(0, "disable")
    else:
        parse_methods = []

    return {
        "file_name": file_name,
        "file_type": file.content_type,
        "file_size": file_size,
        "object_name": upload_result.object_name,
        "uploaded_at": utc_isoformat(),
        "parse_supported": bool(parse_methods),
        "parse_methods": parse_methods,
    }


async def parse_tmp_attachment_view(
    *,
    object_name: str,
    parse_method: str | None,
    current_uid: str,
) -> dict:
    """解析用户 tmp 附件并把 markdown 写回 tmp。"""
    minio_client = get_minio_client()
    bucket_name = minio_client.KB_BUCKETS["documents"]

    tmp_file_id, safe_name = _require_tmp_object_section(object_name, str(current_uid), "original")
    default_ocr_engine = "rapid_ocr"
    if parse_method is None and Path(safe_name).suffix.lower() in TMP_ATTACHMENT_IMAGE_EXTENSIONS:
        default_ocr_engine = (await system_options.get())["default_ocr_engine"]
    method = _normalize_parse_method(safe_name, parse_method, default_ocr_engine)

    try:
        from yuxi.services.ocr_service import parse_document

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
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Tmp attachment parse failed for {safe_name}: {exc}")
        raise HTTPException(status_code=400, detail=f"附件解析失败: {exc}") from exc

    return {
        "parsed_object_name": upload_result.object_name,
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
    conversation = await _require_user_conversation(conv_repo, thread_id, str(current_uid))
    from yuxi.services.workdir_service import resolve_authorized_conversation_workdir

    binding = await resolve_authorized_conversation_workdir(
        conversation=conversation,
        uid=str(current_uid),
        db=db,
    )
    workdir = binding.workdir
    minio_client = get_minio_client()
    bucket_name = minio_client.KB_BUCKETS["documents"]
    added_records: list[dict] = []
    confirmed_tmp_ids: list[str] = []
    try:
        for item in attachments:
            object_name = str(item.get("object_name") or "")
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

            file_id = uuid.uuid4().hex
            attachment_record = await _store_attachment(
                workdir=workdir,
                file_id=file_id,
                file_name=file_name,
                file_type=item.get("file_type"),
                file_content=file_content,
                parsed_markdown=parsed_markdown,
            )
            added_records.append(attachment_record)
            confirmed_tmp_ids.append(tmp_file_id)
    except Exception:
        await _rollback_stored_attachments(workdir, added_records)
        raise

    try:
        await conv_repo.add_attachments(conversation.id, added_records)
        await db.commit()
    except Exception:
        await db.rollback()
        await _rollback_stored_attachments(workdir, added_records)
        raise

    delete_results = await asyncio.gather(
        *(
            minio_client.adelete_objects_by_prefix(
                bucket_name,
                f"{_tmp_attachment_prefix(str(current_uid), tmp_file_id)}/",
            )
            for tmp_file_id in confirmed_tmp_ids
        ),
        return_exceptions=True,
    )
    for tmp_file_id, result in zip(confirmed_tmp_ids, delete_results):
        if isinstance(result, Exception):
            logger.warning("清理已确认临时附件失败: tmp_file_id=%s error=%s", tmp_file_id, result)

    return {"attachments": [serialize_attachment(item, thread_id=thread_id) for item in added_records]}


async def list_thread_attachments_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    """列出指定对话线程的附件。"""
    conv_repo = ConversationRepository(db)
    conversation = await _require_user_conversation(conv_repo, thread_id, str(current_uid))
    attachments = await conv_repo.get_attachments(conversation.id)
    return {
        "attachments": [serialize_attachment(item, thread_id=thread_id) for item in attachments],
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
    """删除指定对话线程的附件。"""
    conv_repo = ConversationRepository(db)
    conversation = await _require_user_conversation(conv_repo, thread_id, str(current_uid))
    from yuxi.services.workdir_service import resolve_authorized_conversation_workdir

    binding = await resolve_authorized_conversation_workdir(
        conversation=conversation,
        uid=str(current_uid),
        db=db,
    )
    workdir = binding.workdir

    existing_attachments = await conv_repo.lock_attachments(conversation.id)
    target_attachment = next((item for item in existing_attachments if item.get("file_id") == file_id), None)
    if target_attachment is None:
        raise HTTPException(status_code=404, detail="附件不存在或已被删除")

    request_id = target_attachment.get("request_id")
    if isinstance(request_id, str) and request_id:
        request = await AgentRunRequestRepository(db).get_by_request_id(request_id)
        if request and request.status == "queued":
            raise HTTPException(status_code=409, detail="附件正在被请求使用，暂时不能删除")

    active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
        agent_slug=conversation.agent_id,
        conversation_thread_id=thread_id,
        uid=str(current_uid),
    )
    if active_run:
        raise HTTPException(status_code=409, detail="对话正在运行，暂时不能删除附件")

    removed = await conv_repo.remove_attachment(conversation.id, file_id)
    if not removed:
        raise HTTPException(status_code=404, detail="附件不存在或已被删除")

    await db.commit()

    for path in {target_attachment.get("path"), target_attachment.get("original_path")}:
        if not isinstance(path, str):
            continue
        try:
            scope = workdir_scope_from_runtime_path(workdir.relative_path, path)
            await asyncio.to_thread(workdir.delete, scope)
        except FileNotFoundError:
            pass
        except Exception:
            # PostgreSQL 已经移除 shipping 引用；残留文件仍留在用户可见 Workdir，后续可显式清理。
            logger.warning("附件元数据已删除，但 Workdir 文件清理失败: thread=%s path=%s", thread_id, path)

    return {"message": "附件已删除"}
