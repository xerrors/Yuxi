"""读取知识库源对象并准备只读预览。"""

from __future__ import annotations

from yuxi.infrastructure.file_preview import prepare_file_preview, preview_too_large
from yuxi.infrastructure.minio import ObjectSizeLimitError, get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, PreviewResult


async def read_knowledge_file_preview(kb_id: str, file_id: str) -> PreviewResult | dict:
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

    # 元数据低估时仍在读取对象时执行预览上限。
    try:
        raw_content = await _read_minio_bytes(original_path)
    except ObjectSizeLimitError:
        return {**response, **preview_too_large().payload()}

    result = prepare_file_preview(filename, raw_content)
    if isinstance(result.content, bytes):
        return result
    return {**response, **result.payload()}


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
