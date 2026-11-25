"""Knowledge 文件的 MinIO 预览与持久化 Office 缓存。"""

from __future__ import annotations

from yuxi.infrastructure.file_preview import OfficeRender, prepare_file_preview, preview_too_large
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import ObjectSizeLimitError, get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.infrastructure.office_conversion import (
    OfficeConversionError,
    convert_office_to_pdf,
    is_office_pdf_convertible,
)
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, PreviewResult


async def read_knowledge_file_preview(kb_id: str, file_id: str, *, office_render: OfficeRender = "source") -> dict:
    """从 Knowledge metadata 与 MinIO 对象生成只读文件预览。"""
    file_record = await KnowledgeFileRepository().get_by_file_id(file_id)
    # kb_id 不匹配与不存在同样按“文件不存在”处理，避免泄露其他知识库的文件存在性。
    if file_record is None or file_record.kb_id != kb_id:
        raise ValueError(f"File {file_id} not found")
    if file_record.is_folder:
        raise ValueError("Cannot preview a folder")

    filename = file_record.filename or file_record.original_filename or file_id
    response = {
        "source": "knowledge",
        "kb_id": kb_id,
        "file_id": file_id,
        "filename": filename,
        "readonly": True,
    }
    original_path = file_record.minio_url or file_record.path
    if not original_path:
        return {
            **response,
            "content": None,
            "preview_type": "unsupported",
            "supported": False,
            "message": "文件没有可预览的原始内容",
        }

    file_size = file_record.file_size
    # 元数据未记录大小时以真实对象大小兜底，防止绕过预览上限。
    if file_size is None:
        file_size = await _get_minio_file_size(original_path)
    if file_size is not None and file_size > MAX_FILE_PREVIEW_BYTES:
        return {**response, **preview_too_large().payload()}

    # Office 预览与直读都以预览上限从 MinIO 读取源对象，元数据低估大小时在下载期兜底拒绝。
    # 默认返回原文件；docx/pptx 只有显式 pdf 回退才进入 LibreOffice 转换与缓存。
    try:
        if office_render == "pdf" and is_office_pdf_convertible(filename):
            pdf_content = await _read_office_pdf_preview(kb_id, file_id, filename, original_path)
            # 隐藏文件名（如 “.docx”）去掉扩展名后 stem 为空，此时退回 file_id 命名。
            response["filename"] = f"{filename.rsplit('.', 1)[0] or file_id}.pdf"
            result = PreviewResult(pdf_content, "pdf", True, media_type="application/pdf")
        else:
            raw_content = await _read_minio_bytes(original_path)
            result = prepare_file_preview(filename, raw_content, office_source=True)
    except ObjectSizeLimitError:
        return {**response, **preview_too_large().payload()}

    # 字节结果经路由流式输出，payload() 只承载文本。
    if isinstance(result.content, bytes):
        if result.preview_type == "office":
            response["filename"] = result.filename
        return {
            **response,
            "content": result.content,
            "media_type": result.media_type,
            "preview_type": result.preview_type,
            "supported": result.supported,
            "message": result.message,
            "binary": True,
        }
    return {**response, **result.payload()}


async def _read_office_pdf_preview(
    kb_id: str,
    file_id: str,
    filename: str,
    original_path: str,
) -> bytes:
    """转换及缓存持有投影读锁，删除清理必须等待对象写入结束。"""
    async with knowledge_projection_lock(kb_id, shared=True):
        file = await KnowledgeFileRepository().get_by_file_id(file_id)
        if file is None or file.kb_id != kb_id:
            raise ValueError(f"File {file_id} not found")
        minio_client = get_minio_client()
        bucket_name = minio_client.KB_BUCKETS["parsed"]
        object_name = f"{kb_id}/preview/{file_id}.pdf"
        if await minio_client.astat_file(bucket_name, object_name) is not None:
            return await minio_client.adownload_file(bucket_name, object_name)

        raw_content = await _read_minio_bytes(original_path)
        try:
            pdf_content = await convert_office_to_pdf(filename, raw_content)
        except OfficeConversionError as exc:
            raise ValueError(str(exc)) from exc

        # 转换期间允许登记 tombstone；发布前复核，排他清理仍由投影锁串行化。
        file = await KnowledgeFileRepository().get_by_file_id(file_id)
        if file is None or file.kb_id != kb_id:
            raise ValueError(f"File {file_id} not found")
        await await_io(
            minio_client.aupload_file(
                bucket_name=bucket_name,
                object_name=object_name,
                data=pdf_content,
                content_type="application/pdf",
            )
        )
        return pdf_content


async def _get_minio_file_size(file_path: str) -> int | None:
    bucket_name, object_name = _parse_minio_path(file_path)
    return await get_minio_client().astat_file(bucket_name, object_name)


async def _read_minio_bytes(file_path: str) -> bytes:
    bucket_name, object_name = _parse_minio_path(file_path)
    return await get_minio_client().adownload_file(bucket_name, object_name, max_bytes=MAX_FILE_PREVIEW_BYTES)


def _parse_minio_path(file_path: str) -> tuple[str, str]:
    if not file_path or not is_minio_url(file_path):
        raise ValueError(f"Invalid MinIO path format: {file_path}")
    return parse_minio_url(file_path)
