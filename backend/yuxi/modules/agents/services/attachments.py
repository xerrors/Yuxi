import asyncio
import hashlib
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.document_parsing import IMAGE_FILE_EXTENSIONS, PDF_FILE_EXTENSIONS
from yuxi.infrastructure.document_parsing.artifacts import local_image_links, resource_path
from yuxi.infrastructure.document_parsing.engines import get_ocr_engines_for_extension
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import StorageError, get_minio_client
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.paths import ensure_user_workspace
from yuxi.modules.workspace.runtime_paths import runtime_path_for_workdir_scope, workdir_scope_from_runtime_path
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.datetime import format_utc_datetime
from yuxi.shared.errors import DomainError, InvalidInputError, InvalidOperationError, ResourceNotFoundError, StateConflictError

MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024
TMP_ATTACHMENT_PREFIX = "tmp/chat_attachments"
TMP_ATTACHMENT_PARSE_EXTENSIONS = (*PDF_FILE_EXTENSIONS, *IMAGE_FILE_EXTENSIONS)
TMP_ATTACHMENT_IMAGE_EXTENSIONS = IMAGE_FILE_EXTENSIONS
TMP_ATTACHMENT_TTL = timedelta(hours=24)


def serialize_attachment(attachment: AgentAttachment, *, thread_id: str) -> dict:
    """从唯一附件记录生成展示字段，URL 由正式路径派生。"""
    return {
        "file_id": attachment.id,
        "file_name": attachment.filename,
        "file_type": attachment.mime_type,
        "file_size": attachment.size_bytes,
        "status": ("parsed" if attachment.path != attachment.original_path else "uploaded")
        if attachment.status == "ready"
        else attachment.status,
        "uploaded_at": format_utc_datetime(attachment.created_at),
        "path": attachment.path,
        "artifact_url": _artifact_url(thread_id, attachment.path) if attachment.path else None,
        "original_path": attachment.original_path,
        "original_artifact_url": _artifact_url(thread_id, attachment.original_path) if attachment.original_path else None,
        "input_id": attachment.input_id,
        "message_id": attachment.message_id,
    }


async def upload_draft_file(*, file_content: bytes, filename: str, content_type: str | None, scope: ActorScope, db) -> dict:
    """上传只保存文件内容与 draft 行，不创建 Session 或 Workdir。"""
    if not filename:
        raise InvalidOperationError("无法识别的文件名")
    if len(file_content) > MAX_ATTACHMENT_SIZE_BYTES:
        raise InvalidOperationError("附件过大，当前仅支持 5 MB 以内的文件")
    owner = _tmp_attachment_owner(scope.uid, scope.app_id)
    file_id, object_name = _make_tmp_attachment_object(owner, filename)
    client = get_minio_client()
    now = datetime.now(UTC)
    try:
        await await_io(
            client.aupload_file(
                client.KB_BUCKETS["documents"],
                object_name,
                file_content,
                content_type=content_type or "application/octet-stream",
            )
        )
        attachment = await AttachmentRepository(db).create(
            id=file_id,
            uid=scope.uid,
            app_id=scope.app_id,
            filename=_safe_file_name(filename),
            mime_type=content_type or "application/octet-stream",
            size_bytes=len(file_content),
            created_at=now,
            expires_at=now + TMP_ATTACHMENT_TTL,
            status="draft",
            object_name=object_name,
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        await client.adelete_objects_by_prefix(client.KB_BUCKETS["documents"], f"{_tmp_attachment_prefix(owner, file_id)}/")
        raise
    result = _file_response(attachment)
    await _cleanup_expired_drafts(scope, db)
    return result


async def get_draft_file(*, file_id: str, scope: ActorScope, db, private: bool = False) -> dict:
    """草稿读取只检查所属附件行，私有读取额外给出解析方式。"""
    attachment = await _require_draft(db, file_id, scope)
    result = _file_response(attachment)
    if private:
        suffix = Path(attachment.filename).suffix.lower()
        methods = list(get_ocr_engines_for_extension(suffix)) if suffix in TMP_ATTACHMENT_PARSE_EXTENSIONS else []
        if suffix in PDF_FILE_EXTENSIONS:
            methods.insert(0, "disable")
        result["parse_methods"] = methods
    return result


async def delete_draft_file(*, file_id: str, scope: ActorScope, db) -> dict:
    """持有附件行锁删除未绑定文件，不修改 Workdir。"""
    attachment = await _require_draft(db, file_id, scope, lock=True)
    await _delete_draft_storage(get_minio_client(), attachment)
    await db.delete(attachment)
    await db.commit()
    return {"id": file_id, "object": "file.deleted", "deleted": True}


async def parse_draft_file(*, file_id: str, parse_method: str | None, scope: ActorScope, db) -> dict:
    """私有解析在附件行锁内生成临时目录，发送仍只引用文件 ID。"""
    attachment = await _require_draft(db, file_id, scope, lock=True)
    default_engine = "rapid_ocr"
    if parse_method is None and Path(attachment.filename).suffix.lower() in TMP_ATTACHMENT_IMAGE_EXTENSIONS:
        default_engine = (await system_options.get())["default_ocr_engine"]
    method = _normalize_parse_method(attachment.filename, parse_method, default_engine)
    parsed_name = _make_tmp_parsed_object(_tmp_attachment_owner(scope.uid, scope.app_id), file_id)
    parsed_directory = str(Path(parsed_name).parent)
    await asyncio.to_thread(ensure_user_workspace, scope.uid)
    workspace = Workspace(scope.uid)
    client = get_minio_client()
    try:
        from yuxi.modules.documents.service import parse

        with tempfile.TemporaryDirectory(prefix="yuxi-chat-parse-") as temporary:
            result = await parse(
                _minio_source(client.KB_BUCKETS["documents"], attachment.object_name),
                Path(temporary) / "parsed",
                params={"ocr_engine": method},
            )
            for relative in (*result.resources, "document.md"):
                await await_io(
                    asyncio.to_thread(
                        workspace.upload_authorized_file_from_path,
                        f"/{parsed_directory}/{relative}",
                        str(result.directory / relative),
                        overwrite=False,
                    )
                )
        attachment.parsed_source = parsed_name
        attachment.parse_method = method
        await db.commit()
    except BaseException as exc:
        try:
            await asyncio.to_thread(workspace.delete_authorized_path, f"/{parsed_directory}", root="/")
        except FileNotFoundError:
            pass
        if isinstance(exc, (asyncio.CancelledError, DomainError)):
            raise
        logger.warning("Draft attachment parse failed: id={} error={}", file_id, exc)
        raise InvalidOperationError("附件解析失败") from exc
    return {**_file_response(attachment), "parse_method": method}


async def stage_input_attachments(*, db, scope: ActorScope, input_item, file_ids: list[str]) -> None:
    """接收事务按固定锁顺序绑定提交级附件，尚不准备文件。"""
    if (input_item.uid, input_item.app_id) != (scope.uid, scope.app_id):
        raise ValueError("附件绑定目标与接收身份不一致")
    unique_ids = set(file_ids)
    if len(unique_ids) != len(file_ids):
        raise InvalidInputError("同一文件不能重复绑定")
    attachments = await AttachmentRepository(db).lock_for_scope(unique_ids, scope.uid, scope.app_id)
    if len(attachments) != len(unique_ids):
        raise ResourceNotFoundError("文件草稿不存在或不可访问")
    client = get_minio_client()
    for attachment in attachments:
        _check_draft(attachment)
        size = await client.astat_file(client.KB_BUCKETS["documents"], attachment.object_name)
        if size != attachment.size_bytes:
            raise InvalidInputError("附件草稿内容不存在或已变化")
        attachment.input_id = input_item.id
        attachment.status = "preparing"
    await db.flush()


async def prepare_input_attachments(*, db, agent_session, input_item, binding) -> bool:
    """唯一准备函数从绑定行写入正式目录，准备失败保留来源。"""
    attachments = await AttachmentRepository(db).list_for_input(input_item.id, lock=True)
    preparing = [attachment for attachment in attachments if attachment.status == "preparing"]
    if not preparing:
        return True
    from yuxi.modules.workspace.services.bindings import ensure_session_workdir_available

    try:
        async with db.begin_nested():
            await ensure_session_workdir_available(agent_session=agent_session, uid=agent_session.uid, db=db, workdir_binding=binding)
            workdir = Workdir.open_existing(agent_session.uid, binding.workdir_path)
            client = get_minio_client()
            for attachment in preparing:
                directory = f"/uploads/attachments/{attachment.id}"
                try:
                    await asyncio.to_thread(workdir.delete, directory)
                except FileNotFoundError:
                    pass
                content = await client.adownload_file(
                    client.KB_BUCKETS["documents"],
                    attachment.object_name,
                    max_bytes=MAX_ATTACHMENT_SIZE_BYTES,
                )
                if len(content) != attachment.size_bytes:
                    raise ValueError("附件来源字节数与上传记录不符")
                parsed_source = (
                    Workdir(str(Path(attachment.parsed_source).parent), Workspace(agent_session.uid)) if attachment.parsed_source else None
                )
                record = await _store_attachment(
                    workdir=workdir,
                    file_id=attachment.id,
                    file_name=attachment.filename,
                    file_content=content,
                    parsed_source=parsed_source,
                )
                attachment.path = record["path"]
                attachment.original_path = record["original_path"]
                attachment.status = "ready"
                attachment.error = None
            await db.flush()
    except Exception:
        for attachment in preparing:
            await db.refresh(attachment)
            attachment.error = "附件准备失败，等待恢复重试"
        await db.flush()
        logger.exception("附件准备失败，保留 Input 等待重试: {}", input_item.id)
        return False
    return True


async def cleanup_prepared_sources(*, db, thread_id: str) -> None:
    """就绪事务提交后清理临时内容，失败保留位置供现有恢复循环重试。"""
    attachments = await AttachmentRepository(db).prepared_sources(thread_id)
    if not attachments:
        return
    client = get_minio_client()
    for attachment in attachments:
        try:
            await _delete_draft_storage(client, attachment)
        except (StorageError, OSError):
            logger.exception("就绪附件临时内容清理失败: {}", attachment.id)
            continue
        attachment.object_name = None
        attachment.parsed_source = None
    await db.flush()


async def list_thread_attachments_view(*, thread_id: str, db: AsyncSession, current_uid: str, app_id: str | None = None) -> dict:
    """附件列表直接读取该 Session 的附件关系。"""
    await require_thread(db=db, scope=ActorScope(uid=str(current_uid), app_id=app_id), thread_id=thread_id)
    attachments = await AttachmentRepository(db).list_for_thread(thread_id, str(current_uid), app_id)
    return {
        "attachments": [serialize_attachment(item, thread_id=thread_id) for item in attachments],
        "limits": {
            "allowed_extensions": [],  # 上传不按后缀限制；解析能力在实际解析入口校验。
            "max_size_bytes": MAX_ATTACHMENT_SIZE_BYTES,
        },
    }


async def delete_thread_attachment_view(
    *, thread_id: str, file_id: str, db: AsyncSession, current_uid: str, app_id: str | None = None
) -> dict:
    """在 Session 与附件行锁内检查执行边界，再删除正式文件和附件关系。"""
    agent_session = await require_thread(db=db, scope=ActorScope(uid=str(current_uid), app_id=app_id), thread_id=thread_id, lock=True)
    attachment = await AttachmentRepository(db).get_for_scope(file_id, str(current_uid), app_id, lock=True)
    if attachment is None or attachment.input_id is None:
        raise ResourceNotFoundError("附件不存在或已被删除")
    input_item = await AgentInputRepository(db).get_for_scope(
        input_id=attachment.input_id,
        thread_id=thread_id,
        uid=str(current_uid),
        app_id=app_id,
    )
    if input_item is None:
        raise ResourceNotFoundError("附件不存在或已被删除")
    if input_item.status == "pending" or attachment.status != "ready":
        raise StateConflictError("附件正在被输入使用，暂时不能删除")
    if await AgentRunRepository(db).get_active_run_by_thread_for_user(
        agent_slug=agent_session.agent_id,
        thread_id=thread_id,
        uid=str(current_uid),
    ):
        raise StateConflictError("对话正在运行，暂时不能删除附件")
    from yuxi.modules.workspace.services.bindings import resolve_authorized_session_workdir

    binding = await resolve_authorized_session_workdir(agent_session=agent_session, uid=str(current_uid), db=db, app_id=app_id)
    paths = {attachment.original_path}
    if attachment.path != attachment.original_path:
        paths.add(str(Path(attachment.path).parent))
    scopes = [workdir_scope_from_runtime_path(binding.workdir.relative_path, path) for path in paths]
    if attachment.object_name:
        await _delete_draft_storage(get_minio_client(), attachment)
    await db.delete(attachment)
    await db.commit()
    for scope in scopes:
        try:
            await asyncio.to_thread(binding.workdir.delete, scope)
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("附件记录已删除，Workdir 内容清理失败: session={} scope={}", thread_id, scope)
    return {"message": "附件已删除"}


async def _require_draft(db, file_id, scope, *, lock=False) -> AgentAttachment:
    """草稿状态来自显式行状态，归属与过期在真实读取边界校验。"""
    attachment = await AttachmentRepository(db).get_for_scope(file_id, scope.uid, scope.app_id, lock=lock)
    if attachment is None:
        raise ResourceNotFoundError("文件草稿不存在或不可访问")
    _check_draft(attachment)
    return attachment


def _check_draft(attachment) -> None:
    """已绑定文件不允许再解析、删除草稿或绑定另一输入。"""
    if attachment.status != "draft":
        raise StateConflictError("文件已提交，请重新上传以用于另一条消息")
    if attachment.expires_at <= datetime.now(UTC):
        raise InvalidInputError("文件草稿已过期，请重新上传")


def _file_response(attachment) -> dict:
    """公开草稿资源只包含引用和展示信息。"""
    return {
        "id": attachment.id,
        "object": "file",
        "filename": attachment.filename,
        "bytes": attachment.size_bytes,
        "mime_type": attachment.mime_type,
        "created_at": attachment.created_at.timestamp(),
        "expires_at": attachment.expires_at.timestamp(),
        "status": "parsed" if attachment.parsed_source else "draft",
    }


async def _delete_draft_storage(client, attachment) -> None:
    """临时存储只保存原文件及解析内容，位置由附件行拥有。"""
    prefix = _tmp_attachment_prefix(_tmp_attachment_owner(attachment.uid, attachment.app_id), attachment.id)
    await client.adelete_objects_by_prefix(client.KB_BUCKETS["documents"], f"{prefix}/")
    try:
        await asyncio.to_thread(Workspace(attachment.uid).delete_authorized_path, f"/{prefix}", root="/")
    except FileNotFoundError:
        pass


async def _cleanup_expired_drafts(scope, db) -> None:
    """上传时只清理数据库领取的到期草稿，绑定文件不在候选集合。"""
    client = get_minio_client()
    for attachment in await AttachmentRepository(db).expired_drafts(scope.uid, scope.app_id):
        try:
            await _delete_draft_storage(client, attachment)
        except (StorageError, OSError):
            logger.exception("过期草稿清理失败: {}", attachment.id)
            continue
        await db.delete(attachment)
    await db.commit()


def _tmp_attachment_owner(uid: str, app_id: str | None) -> str:
    """让同一用户的产品与不同 APP 临时对象互不可用。"""
    if app_id is None:
        return str(uid)
    digest = hashlib.sha256(app_id.encode()).hexdigest()
    return f"{uid}-app-{digest}"


def _safe_file_name(file_name: str | None, default: str = "attachment.bin") -> str:
    safe_name = Path(file_name or "").name.replace("/", "_").replace("\\", "_").strip(" .")
    return safe_name or default


def _artifact_url(thread_id: str, virtual_path: str) -> str:
    return f"/api/v1/agents/sessions/{thread_id}/artifacts/{quote(virtual_path.lstrip('/'), safe='/')}"


def _tmp_attachment_prefix(uid: str, tmp_file_id: str) -> str:
    return f"{TMP_ATTACHMENT_PREFIX}/{uid}/{tmp_file_id}"


def _make_tmp_attachment_object(uid: str, file_name: str) -> tuple[str, str]:
    """生成用户隔离的 tmp 对象路径。"""
    tmp_file_id = uuid.uuid4().hex
    safe_name = _safe_file_name(file_name)
    return tmp_file_id, f"{_tmp_attachment_prefix(uid, tmp_file_id)}/original/{safe_name}"


def _make_tmp_parsed_object(uid: str, tmp_file_id: str) -> str:
    """生成用户隔离的本地解析入口标识。"""
    return f"{_tmp_attachment_prefix(uid, tmp_file_id)}/parsed/{uuid.uuid4().hex}/document.md"


def _minio_source(bucket_name: str, object_name: str) -> str:
    return f"minio://{bucket_name}/{quote(object_name, safe='/')}"


def _normalize_parse_method(file_name: str, parse_method: str | None, default_ocr_engine: str) -> str:
    """按文件类型确定临时附件解析方式。"""
    suffix = Path(file_name).suffix.lower()
    if suffix not in TMP_ATTACHMENT_PARSE_EXTENSIONS:
        raise InvalidOperationError("当前仅支持 PDF 和图片附件解析")

    allowed_methods = get_ocr_engines_for_extension(suffix)
    if suffix in TMP_ATTACHMENT_IMAGE_EXTENSIONS:
        method = parse_method or ("rapid_ocr" if default_ocr_engine == "disable" else default_ocr_engine)
    else:
        method = parse_method or "disable"
        allowed_methods = ("disable", *allowed_methods)

    if method not in allowed_methods:
        allowed = ", ".join(allowed_methods)
        raise InvalidOperationError(f"不支持的解析方法: {method}，可选: {allowed}")
    return method


async def _write_workdir_file(workdir, path: str, content: bytes) -> None:
    """通过受信任 no-follow 文件边界写入实时 Workdir。"""
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="yuxi-attachment-", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(content)
        await await_io(asyncio.to_thread(workdir.copy_file_from_path, path, temp_path))
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
    file_content: bytes,
    parsed_source: Workdir | None = None,
) -> dict:
    """将正式附件直接写入实时 Project Workdir。"""
    file_name = _safe_file_name(file_name)
    storage_name = f"{file_id}_{file_name}"
    original_scope = f"/uploads/{storage_name}"
    directory_scope = f"/uploads/attachments/{file_id}"
    markdown_scope = f"{directory_scope}/document.md"
    markdown_path = runtime_path_for_workdir_scope(workdir.relative_path, markdown_scope)
    try:
        await _write_workdir_file(workdir, original_scope, file_content)
        original_path = runtime_path_for_workdir_scope(workdir.relative_path, original_scope)
        record = {"path": original_path, "original_path": original_path}
        if parsed_source is None:
            return record

        # 客户端只能选择入口；每个文件仍在源与目标 owning filesystem boundary 校验。
        markdown = await asyncio.to_thread(parsed_source.read_file, "/document.md", 100 * 1024 * 1024)
        for link in local_image_links(markdown.decode("utf-8")):
            relative = resource_path(unquote(link))
            await asyncio.to_thread(parsed_source.read_file, f"/{relative}", 100 * 1024 * 1024)
        await await_io(asyncio.to_thread(workdir.copy_directory_from, parsed_source, directory_scope, max_file_bytes=100 * 1024 * 1024))
    except BaseException:
        try:
            await asyncio.to_thread(workdir.delete, original_scope)
        except FileNotFoundError:
            pass
        try:
            await asyncio.to_thread(workdir.delete, directory_scope)
        except FileNotFoundError:
            pass
        raise
    record["path"] = markdown_path
    return record
