"""知识文档元数据、正文、窗口及原件读取用例，不装配检索执行器。"""

import asyncio
import json
import re
from typing import Any
from urllib.parse import unquote

from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.file_status import status_filter_values
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.knowledge.schemas import FindOutputSchema, FindWindowSchema
from yuxi.modules.knowledge.utils.kb_utils import sanitize_processing_params
from yuxi.shared.datetime import utc_isoformat
from yuxi.shared.errors import ContentTooLargeError, InvalidOperationError
from yuxi.shared.files import detect_media_type

KB_FILE_SEARCH_SCAN_LIMIT = 5000


class UnsupportedDocumentOperation(ValueError):
    """知识库类型不支持文档操作，供 HTTP 保留能力拒绝响应。"""


async def require_document_support(kb_id: str, operation: str) -> None:
    """按持久类型拒绝不支持的文档操作，不装配执行器或运行配置。"""

    from yuxi.modules.knowledge.base import KBNotFoundError
    from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise KBNotFoundError(f"知识库资源 '{kb_id}' 不存在")
    kb_type = (kb.kb_type or "milvus").lower()
    if not KnowledgeBaseFactory.supports_documents(kb_type):
        operation_label = {"open": "文档查看", "find": "文档查找", "download": "文件下载"}.get(operation, operation)
        raise UnsupportedDocumentOperation(f"{kb.name or kb_type} 只支持检索，不支持{operation_label}")


async def get_knowledge_base_document_support(kb_id: str) -> tuple[KnowledgeBaseDetail | None, bool]:
    """返回知识库信息及其是否支持文档全文操作。"""

    from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
    from yuxi.modules.knowledge.services import bases

    kb_info = await bases.get_knowledge_base_info(kb_id)
    if not kb_info:
        return None, False
    return kb_info, KnowledgeBaseFactory.supports_documents(kb_info.kb_type)


async def list_document_files(
    kb_id: str,
    *,
    parent_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 100,
    recursive: bool = False,
    files_only: bool = False,
    include_stats: bool = True,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> dict:
    """按目录和筛选条件分页获取轻量文件列表；排序作用于筛选后的全部记录。"""

    from yuxi.modules.knowledge.base import KBNotFoundError
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise KBNotFoundError(f"Knowledge base {kb_id} not found")

    repo = KnowledgeFileRepository()
    if parent_id:
        parent_record = await repo.get_by_file_id(parent_id)
        if not parent_record or parent_record.kb_id != kb_id:
            raise ValueError("Parent folder not found")
        if not parent_record.is_folder:
            raise ValueError("Parent is not a folder")

    normalized_page = max(int(page or 1), 1)
    normalized_page_size = min(max(int(page_size or 100), 1), 500)
    effective_recursive = recursive and bool(status and status != "all")

    list_documents = repo.list_documents(
        kb_id=kb_id,
        parent_id=parent_id,
        status=status,
        page=normalized_page,
        page_size=normalized_page_size,
        recursive=effective_recursive,
        files_only=files_only,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    # 列表与统计互不依赖，并行执行（大知识库下统计全表聚合耗时显著）
    if include_stats:
        (records, total), stats = await asyncio.gather(list_documents, repo.get_kb_file_stats(kb_id))
    else:
        records, total = await list_documents
        stats = None

    folder_ids = [record.file_id for record in records if record.is_folder]
    creator_uids = [record.created_by for record in records if getattr(record, "created_by", None)]
    from yuxi.modules.identity.repositories.users import UserRepository

    child_counts, creators = await asyncio.gather(
        repo.count_children_by_parent_ids(kb_id=kb_id, parent_ids=folder_ids),
        UserRepository().list_by_uids(creator_uids),
    )
    creators = {user.uid: user for user in creators}
    items = [_file_record_list_item(record, child_counts, creators.get(getattr(record, "created_by", None))) for record in records]

    result = {
        "items": items,
        "total": total,
        "page": normalized_page,
        "page_size": normalized_page_size,
        "has_more": normalized_page * normalized_page_size < total,
        "parent_id": parent_id,
        "recursive": effective_recursive,
    }
    if stats is not None:
        result["stats"] = stats
    return result


async def search_document_files(
    knowledge_bases: list[dict],
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int = 300,
    status: str | None = None,
    include_parent_id: bool = False,
) -> dict:
    """按文件名在一组知识库中搜索文件，并返回分页结果。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    normalized_offset = max(offset or 0, 0)
    normalized_limit = min(max(limit or 300, 1), KB_FILE_SEARCH_SCAN_LIMIT)
    normalized_query = (query or "").strip().lower()
    accepted_statuses = status_filter_values(status)

    repo = KnowledgeFileRepository()
    all_files = []
    use_sql_pagination = len(knowledge_bases) == 1
    candidate_limit = normalized_limit if use_sql_pagination else normalized_offset + normalized_limit
    query_offset = normalized_offset if use_sql_pagination else 0
    search_results = await asyncio.gather(
        *(
            _search_kb_files(
                repo,
                kb,
                query=normalized_query,
                statuses=accepted_statuses,
                offset=query_offset,
                limit=candidate_limit,
            )
            for kb in knowledge_bases
        )
    )
    total = 0
    for kb, files, kb_total in search_results:
        total += kb_total
        kb_id = kb.get("kb_id")
        for file in files:
            item = {
                "kb_id": kb_id,
                "kb_name": kb.get("name"),
                "file_id": file.file_id,
                "filename": file.filename,
                "file_type": file.file_type,
                "status": file.status,
                "created_at": str(file.created_at) if file.created_at else None,
                "updated_at": str(file.updated_at) if file.updated_at else None,
                "file_size": file.file_size,
            }
            if include_parent_id:
                item["parent_id"] = file.parent_id
            all_files.append(item)

    if not use_sql_pagination:
        # 多库才需要跨库按更新时间归并；单库结果已由 DB 按 updated_at desc, file_id asc 排好序。
        all_files.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    paginated_files = all_files if use_sql_pagination else all_files[normalized_offset : normalized_offset + normalized_limit]
    return {
        "files": paginated_files,
        "total": total,
        "offset": normalized_offset,
        "limit": normalized_limit,
        "has_more": normalized_offset + normalized_limit < total,
    }


async def document_file_exists(kb_id: str, filename: str) -> bool:
    """检查指定知识库中是否存在给定展示文件名或相对路径的文件。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    normalized_filename = filename.strip()
    if not normalized_filename:
        raise ValueError("filename is required")
    return await KnowledgeFileRepository().exists_by_filename(kb_id=kb_id, filename=normalized_filename)


async def list_document_file_ids_by_statuses(
    kb_id: str,
    *,
    statuses: list[str],
    after_file_id: str | None = None,
    limit: int = 500,
) -> list[str]:
    """按文件状态游标分页获取文件 ID，用于后台批量处理任务。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    return await KnowledgeFileRepository().list_file_ids_by_exact_statuses(
        kb_id=kb_id,
        statuses=statuses,
        after_file_id=after_file_id,
        limit=limit,
    )


async def get_file_basic_info(kb_id: str, file_id: str) -> dict:
    """获取文件基本信息（仅元数据）。"""
    return {"meta": await load_file_meta(kb_id, file_id)}


async def get_file_content(kb_id: str, file_id: str) -> dict:
    """获取文件内容信息（chunks 和 lines）。"""
    file_meta = await load_file_meta(kb_id, file_id)
    return await get_file_content_from_meta(file_id, file_meta)


async def get_file_info(kb_id: str, file_id: str) -> dict:
    """获取文件完整信息（基本信息+内容信息）。"""
    file_meta = await load_file_meta(kb_id, file_id)
    content_info = await get_file_content_from_meta(file_id, file_meta)
    return {"meta": file_meta, **content_info}


async def open_original_file(kb_id: str, file_id: str):
    """打开原件对象流，由 HTTP 适配层在流结束后关闭连接。"""
    meta = await load_file_meta(kb_id, file_id)
    if meta.get("is_folder"):
        raise InvalidOperationError("文件夹不支持下载")
    if meta.get("file_type") == "url":
        raise InvalidOperationError("URL 类型文件不支持下载原始文件")
    path = original_file_path(meta)
    if not path or not is_minio_url(path):
        raise InvalidOperationError("文件路径必须是 MinIO URL")
    filename = unquote(meta.get("filename") or "file")
    bucket, object_name = parse_minio_url(path)
    source = await get_minio_client().adownload_response(bucket_name=bucket, object_name=object_name)
    return source, filename, meta.get("content_type") or detect_media_type(filename)


async def get_file_download(kb_id: str, file_id: str, variant: str = "original") -> dict:
    """下载原始或解析后的文件内容。"""
    file_meta = await load_file_meta(kb_id, file_id)
    if file_meta.get("is_folder"):
        raise ValueError("Cannot download a folder")
    if variant not in {"original", "parsed"}:
        raise ValueError("Unsupported download variant")

    filename = file_meta.get("filename") or file_meta.get("original_filename") or file_id
    if variant == "parsed":
        markdown_file = file_meta.get("markdown_file")
        if not markdown_file:
            raise ValueError("文件尚未生成解析结果")
        return {
            "filename": f"{filename}.parsed.md",
            "content": await read_minio_bytes(markdown_file),
            "media_type": "text/markdown; charset=utf-8",
        }

    original_path = original_file_path(file_meta)
    if not original_path:
        raise ValueError("文件没有可下载的原始内容")
    media_type = file_meta.get("content_type") or detect_media_type(filename)
    return {
        "filename": filename,
        "content": await read_minio_bytes(original_path),
        "media_type": media_type,
    }


async def open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
    """按行窗口打开文件解析后的 Markdown 内容"""
    try:
        file_meta = await load_file_meta(kb_id, file_id)
    except ValueError as exc:
        raise ValueError(f"文件不存在: {file_id}") from exc
    if file_meta.get("is_folder"):
        raise ValueError(f"文件 {file_id} 是文件夹")

    markdown_file = file_meta.get("markdown_file")
    if not markdown_file:
        raise ValueError(f"文件 {file_id} 没有解析后的 Markdown 内容")

    content = await read_markdown_from_minio(markdown_file)
    return build_open_file_window(content, offset=offset, limit=limit)


async def find_in_document(
    kb_id: str,
    file_id: str,
    patterns: list[str],
    *,
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
) -> dict:
    """在文件内做关键词或正则定位，返回 FindOutputSchema 字典。

    不支持文档全文操作的知识库（如 dify 只读源）抛 ValueError。
    """
    await require_document_support(kb_id, "find")
    try:
        file_meta = await load_file_meta(kb_id, file_id)
    except ValueError as exc:
        raise ValueError(f"文件不存在: {file_id}") from exc
    if file_meta.get("is_folder"):
        raise ValueError(f"文件 {file_id} 是文件夹")

    markdown_file = file_meta.get("markdown_file")
    if not markdown_file:
        raise ValueError(f"文件 {file_id} 没有解析后的 Markdown 内容")

    content = await read_markdown_from_minio(markdown_file)
    result = build_find_file_windows(
        content,
        patterns=patterns,
        use_regex=use_regex,
        case_sensitive=case_sensitive,
        max_windows=max_windows,
        window_size=window_size,
    )
    return FindOutputSchema(kb_id=kb_id, file_id=file_id, **result).model_dump()


def file_record_to_meta(record: Any) -> dict:
    """将持久化文件记录映射为通用元数据字典。"""
    return {
        "file_id": record.file_id,
        "kb_id": record.kb_id,
        "parent_id": record.parent_id,
        "filename": record.filename,
        "file_type": record.file_type,
        "path": record.path,
        "markdown_file": record.markdown_file,
        "status": record.status,
        "content_hash": record.content_hash,
        "size": record.file_size,
        "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
        "token_count": int(getattr(record, "token_count", 0) or 0),
        "content_type": record.content_type,
        "processing_params": sanitize_processing_params(record.processing_params),
        "is_folder": record.is_folder,
        "error": record.error_message,
        "created_by": record.created_by,
        "updated_by": record.updated_by,
        "created_at": utc_isoformat(record.created_at) if record.created_at else None,
        "updated_at": utc_isoformat(record.updated_at) if record.updated_at else None,
        "original_filename": record.original_filename,
        "minio_url": record.minio_url,
        "generation": int(getattr(record, "generation", 1) or 1),
        "active_generation": int(getattr(record, "active_generation", 1) or 1),
        "building_generation": getattr(record, "building_generation", None),
    }


def file_meta_to_record_data(meta: dict) -> dict[str, Any]:
    """将元数据字典还原为可持久化字段。"""
    return {
        "kb_id": meta.get("kb_id"),
        "parent_id": meta.get("parent_id"),
        "filename": meta.get("filename") or "",
        "original_filename": meta.get("original_filename"),
        "file_type": meta.get("file_type"),
        "path": meta.get("path"),
        "minio_url": meta.get("minio_url"),
        "markdown_file": meta.get("markdown_file"),
        "status": meta.get("status"),
        "content_hash": meta.get("content_hash"),
        "file_size": meta.get("size"),
        "chunk_count": int(meta.get("chunk_count") or 0),
        "token_count": int(meta.get("token_count") or 0),
        "content_type": meta.get("content_type"),
        "processing_params": sanitize_processing_params(meta.get("processing_params")),
        "is_folder": meta.get("is_folder", False),
        "error_message": meta.get("error"),
        "created_by": str(meta.get("created_by")) if meta.get("created_by") else None,
        "updated_by": str(meta.get("updated_by")) if meta.get("updated_by") else None,
    }


async def load_file_meta(kb_id: str, file_id: str) -> dict:
    """读取文件元数据并校验知识库归属，缺失时抛出 ValueError。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    record = await KnowledgeFileRepository().get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise ValueError(f"File {file_id} not found")

    return file_record_to_meta(record)


async def get_file_content_from_meta(file_id: str, file_meta: dict) -> dict:
    """读取完整解析文本，并校验片段行号与文件快照属于同一入库代次。"""
    from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository

    content_info = {"lines": [], "line_location": {"available": False, "reason": "content_unavailable"}}
    location_reason = "document_not_indexed" if file_meta.get("status") not in {"indexed", "done"} else ""
    try:
        chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
        if not location_reason and any(chunk.generation != file_meta.get("active_generation") for chunk in chunks):
            location_reason = "index_changed"
        content_info["lines"] = [
            {
                "id": chunk.chunk_id,
                "content": chunk.content,
                "chunk_order_index": chunk.chunk_index,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "start_char_pos": chunk.start_char_pos,
                "end_char_pos": chunk.end_char_pos,
                "start_token_pos": chunk.start_token_pos,
                "end_token_pos": chunk.end_token_pos,
                "graph_indexed": chunk.graph_indexed,
                "ent_ids": chunk.ent_ids,
                "tags": chunk.tags,
                "extraction_result": chunk.extraction_result,
            }
            for chunk in chunks
        ]
    except Exception as e:
        location_reason = "chunks_unavailable"
        logger.error(f"Failed to get file content from PostgreSQL: {e}")

    if not content_info["lines"]:
        logger.warning(f"No chunks found in PostgreSQL for file {file_id}, file may not have been indexed")

    # Try to read markdown content if available
    if file_meta.get("markdown_file"):
        try:
            content = await read_markdown_from_minio(file_meta["markdown_file"])
            content_info["content"] = content
        except Exception as e:
            logger.error(f"Failed to read markdown file for {file_id}: {e}")

    if content_info.get("content"):
        content_info["line_location"] = {"available": not location_reason, "reason": location_reason or None}
    return content_info


def original_file_path(file_meta: dict) -> str | None:
    """返回原始文件位置：优先 MinIO 地址，回退历史 path。"""
    return file_meta.get("minio_url") or file_meta.get("path")


async def read_minio_bytes(file_path: str) -> bytes:
    """读取 MinIO 对象字节；路径格式非法时显式失败。"""
    if not file_path or not is_minio_url(file_path):
        raise ValueError(f"Invalid MinIO path format: {file_path}")

    bucket_name, object_name = parse_minio_url(file_path)
    minio_client = get_minio_client()
    return await minio_client.adownload_file(bucket_name, object_name)


async def read_markdown_from_minio(file_path: str) -> str:
    """读取 MinIO 中的 Markdown 文本。"""
    content_bytes = await read_minio_bytes(file_path)
    return content_bytes.decode("utf-8")


def build_open_file_window(content: str, *, offset: int = 0, limit: int = 800) -> dict[str, Any]:
    """按行窗口渲染带行号的内容。"""
    lines = content.splitlines()
    total_lines = len(lines)
    start = min(max(int(offset), 0), total_lines)
    window_size = min(max(int(limit), 1), 2000)
    # 预算包含行号、JSON 转义和窗口元数据，不能截断单行正文。
    byte_budget = 64 * 1024
    selected = [f"{start + index + 1:6d}\t{line}" for index, line in enumerate(lines[start : start + window_size])]

    def render(count: int) -> dict[str, Any]:
        """统一装配指定完整行前缀的响应。"""
        end = start + count
        return {
            "start_line": start + 1 if count else 0,
            "end_line": end,
            "total_lines": total_lines,
            "offset": start,
            "window_size": window_size,
            "has_more_before": start > 0,
            "has_more_after": end < total_lines,
            "next_offset": end if end < total_lines else None,
            "content": "\n".join(selected[:count]),
        }

    result = render(0)
    low, high = 1, len(selected)
    while low <= high:
        count = (low + high) // 2
        candidate = render(count)
        # MCP 同时携带文本和结构化结果；留出外层 JSON-RPC 标头空间。
        wire = {"content": [{"type": "text", "text": json.dumps(candidate, ensure_ascii=False)}], "structuredContent": candidate}
        if len(json.dumps(wire, ensure_ascii=True).encode("utf-8")) > byte_budget - 1024:
            high = count - 1
        else:
            result = candidate
            low = count + 1
    if selected and result["end_line"] == start:
        raise ContentTooLargeError("文档单行超过读取窗口字节上限", code="document_line_too_large")
    return result


def build_find_file_windows(
    content: str,
    *,
    patterns: list[str],
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
) -> dict[str, Any]:
    """按匹配行生成带上下文的查找窗口。"""
    patterns = [pattern for pattern in patterns if pattern]
    if not patterns:
        raise ValueError("请提供至少一个 pattern")

    lines = content.splitlines()
    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        try:
            matchers = [re.compile(pattern, flags) for pattern in patterns]
        except re.error as exc:
            raise ValueError(f"无效正则表达式: {exc}") from exc

        def line_matches(line: str) -> bool:
            return any(matcher.search(line) for matcher in matchers)

    else:
        normalized_patterns = patterns if case_sensitive else [pattern.lower() for pattern in patterns]

        def line_matches(line: str) -> bool:
            haystack = line if case_sensitive else line.lower()
            return any(pattern in haystack for pattern in normalized_patterns)

    matched_indexes = [index for index, line in enumerate(lines) if line_matches(line)]
    windows: list[FindWindowSchema] = []
    covered_until = -1
    normalized_window_size = min(max(int(window_size), 1), 200)
    half_window = normalized_window_size // 2

    for matched_index in matched_indexes:
        if matched_index < covered_until:
            continue
        start = max(matched_index - half_window, 0)
        end = min(start + normalized_window_size, len(lines))
        # 窗口右缘触底时整体左移，保证窗口仍是完整 window_size 行。
        start = max(end - normalized_window_size, 0)
        matched_lines = [index + 1 for index in matched_indexes if start <= index < end]
        selected = lines[start:end]
        windows.append(
            FindWindowSchema(
                start_line=start + 1 if selected else 0,
                end_line=end,
                matched_lines=matched_lines,
                content="\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected)),
            )
        )
        covered_until = end
        if len(windows) >= max_windows:
            break

    return FindOutputSchema(
        kb_id="",
        file_id="",
        semantic=False,
        match_mode="regex" if use_regex else "keyword",
        total_matches=len(matched_indexes),
        windows=windows,
    ).model_dump(exclude={"kb_id", "file_id"})


async def _search_kb_files(
    repo,
    kb: dict,
    *,
    query: str | None,
    statuses: set[str] | None,
    offset: int,
    limit: int,
) -> tuple[dict, list, int]:
    """搜索单个知识库文件，kb_id 缺失时返回空列表，供并行 gather 使用。"""
    if not kb.get("kb_id"):
        return kb, [], 0
    files, total = await repo.search_files(
        kb_id=kb["kb_id"],
        filename_query=query,
        statuses=statuses,
        offset=offset,
        limit=limit,
        files_only=True,
    )
    return kb, files, total


def _file_record_list_item(
    record,
    child_counts: dict[str, int] | None = None,
    creator: User | None = None,
) -> dict:
    """将文件记录投影为列表条目，附带子项计数与创建者信息。"""
    child_counts = child_counts or {}
    file_id = getattr(record, "file_id")
    child_count = int(child_counts.get(file_id, 0))
    created_by = getattr(record, "created_by", None)
    created_at_value = getattr(record, "created_at", None)
    updated_at_value = getattr(record, "updated_at", None)
    created_at = utc_isoformat(created_at_value) if created_at_value else None
    updated_at = utc_isoformat(updated_at_value) if updated_at_value else None
    return {
        "file_id": file_id,
        "filename": getattr(record, "filename"),
        "file_type": getattr(record, "file_type", None),
        "status": getattr(record, "status", None) or "uploaded",
        "created_at": created_at,
        "updated_at": updated_at,
        "file_size": int(getattr(record, "file_size", None) or 0),
        "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
        "token_count": int(getattr(record, "token_count", 0) or 0),
        "created_by": created_by,
        "created_by_name": creator.username if creator else created_by,
        "created_by_avatar": creator.to_dict().get("avatar") if creator else None,
        "is_folder": bool(getattr(record, "is_folder", False)),
        "parent_id": getattr(record, "parent_id", None),
        "has_children": child_count > 0,
        "children_count": child_count,
        "has_original_file": bool(getattr(record, "minio_url", None) or getattr(record, "path", None)),
        "has_parsed_markdown": bool(getattr(record, "markdown_file", None)),
        "error_message": getattr(record, "error_message", None),
    }
