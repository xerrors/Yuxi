"""知识库上传、处理参数与图片代理 URL 的无状态共享工具。"""

import hashlib
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.chunking.presets import resolve_chunk_processing_params
from yuxi.modules.knowledge.names import normalize_name
from yuxi.shared.datetime import utc_isoformat

# Web 上传请求与持久 Job payload 的既有协议键；只在上传元数据边界读取。
PREPROCESSED_UPLOADS_KEY = "_preprocessed_map"

_DROPPED_PROCESSING_PARAM_KEYS = {
    PREPROCESSED_UPLOADS_KEY,
    "auto_index",
    "content_hashes",
    "file_sizes",
    "source_paths",
    "source_path",
    "parent_id",
    "enable_ocr",
    "ocr_engine_config",
}


def content_parser_id(processing_params: dict | None) -> str:
    """摄取指纹中的解析器身份。未指定时与空引擎视为同一种处理。"""

    return str((processing_params or {}).get("ocr_engine") or "")


def sanitize_processing_params(params: dict | None) -> dict | None:
    """移除不应写入单文件元数据的参数。"""
    if not params:
        return None

    return {key: value for key, value in params.items() if key not in _DROPPED_PROCESSING_PARAM_KEYS}


async def calculate_content_hash(data: bytes | bytearray) -> str:
    """计算文件内容的 SHA-256 哈希值。"""
    return hashlib.sha256(data).hexdigest()


def params_for_uploaded_document(item: str, params: dict) -> dict:
    """将批量上传参数收敛为单个文档的处理参数。"""
    source_paths = params.get("source_paths")
    item_params = dict(params)
    item_params.pop("source_paths", None)
    if isinstance(source_paths, dict) and source_paths.get(item):
        item_params["source_path"] = source_paths[item]
    return item_params


async def prepare_item_metadata(item: str, content_type: str, kb_id: str, params: dict | None = None) -> dict:
    """准备 MinIO 文件元数据；URL 导入需先通过 fetch-url 预处理为 MinIO 文件。"""
    # URL 转 HTML 文件的预处理信息携带原始文件名、对象路径与内容指纹。
    if params and PREPROCESSED_UPLOADS_KEY in params and item in params[PREPROCESSED_UPLOADS_KEY]:
        pre_info = params[PREPROCESSED_UPLOADS_KEY][item]
        filename = pre_info.get("filename", item)  # 通常是原始 URL

        source_url = urlsplit(filename)
        title = unquote(source_url.path.rsplit("/", 1)[-1]) or source_url.hostname or "page"
        # 标题截断后追加 ".html"，保证仍落在 normalize_name 的名称长度上限内。
        filename_display = normalize_name(title[:480] + ".html")

        safe_params = sanitize_processing_params(params) or {}
        # 后续解析必须走文件流程（MinIO 下载 -> HTML 解析），不能再次当作 URL 抓取。
        safe_params["content_type"] = "file"
        # 完整 URL 保存到 JSON 字段，避免数据库字段长度限制。
        safe_params["original_source"] = filename

        return {
            "kb_id": kb_id,
            "filename": filename_display,
            "path": pre_info["path"],
            "file_type": "html",
            "status": "indexing",
            "created_at": utc_isoformat(),
            "file_id": f"file_{uuid4().hex}",
            "content_hash": pre_info["content_hash"],
            "size": pre_info.get("file_size"),
            "parent_id": params.get("parent_id"),
            "processing_params": safe_params,
        }

    if content_type != "file":
        raise ValueError(f"Unsupported content_type: {content_type}")
    if not is_minio_url(item):
        raise ValueError(f"File source must be a MinIO URL: {item}")

    logger.debug(f"Processing MinIO file: {item}")
    _, object_name = parse_minio_url(item)
    filename = object_name.rsplit("/", 1)[-1]

    filename_display = normalize_name(filename)
    file_type = filename_display.rsplit(".", 1)[-1].lower() if "." in filename_display else ""

    # 内容指纹按上传清单逐对象携带；缺失即拒绝，通过后 params 必为非空，后续可直接读取。
    content_hash = None
    if params and "content_hashes" in params and isinstance(params["content_hashes"], dict):
        content_hash = params["content_hashes"].get(item)
    if not content_hash:
        raise ValueError(f"Missing content_hash for file: {item}")

    file_sizes = params.get("file_sizes")
    file_size = file_sizes.get(item) if isinstance(file_sizes, dict) else None

    return {
        "kb_id": kb_id,
        "filename": filename_display,
        "path": item,
        "file_type": file_type,
        "status": "indexing",
        "created_at": utc_isoformat(),
        "file_id": f"file_{uuid4().hex}",
        "content_hash": content_hash,
        "size": file_size,
        "parent_id": params.get("parent_id"),
        "processing_params": sanitize_processing_params(params),
    }


def resolve_processing_params(
    kb_additional_params: dict | None,
    file_processing_params: dict | None,
    request_params: dict | None = None,
) -> dict:
    """合并文件与请求中的处理参数；分块参数按 KB 配置解析并覆盖同名键。"""
    merged_params = sanitize_processing_params(merge_processing_params(file_processing_params, request_params)) or {}
    chunk_params = resolve_chunk_processing_params(
        kb_additional_params=kb_additional_params,
        file_processing_params=file_processing_params,
        request_params=request_params,
    )
    merged_params.update(chunk_params)
    return merged_params


def merge_processing_params(metadata_params: dict | None, request_params: dict | None) -> dict:
    """合并处理参数，请求参数覆盖元数据中的同名键。"""
    merged_params = {**(metadata_params or {}), **(request_params or {})}

    logger.debug(
        "Merged processing params: "
        f"metadata_keys={list(metadata_params.keys()) if metadata_params else []}, "
        f"request_keys={list(request_params.keys()) if request_params else []}, "
        f"merged_keys={list(merged_params.keys())}"
    )
    return merged_params


def build_kb_image_proxy_url(object_name: str) -> str:
    """构建知识库图片的后端鉴权代理 URL。

    图片存放在私有 bucket，前端通过该 URL 请求后端鉴权后读取图片。
    对象名格式为 ``{kb_id}/kb-images/{timestamp}_{filename}``，kb_id 即首段；
    路径参数只保留 ``kb-images/...`` 部分（保留斜杠、编码其余字符）。
    """
    kb_id, separator, relative_path = object_name.partition("/")
    if not kb_id or not separator or not relative_path.startswith("kb-images/"):
        raise ValueError("知识库图片对象名必须符合 {kb_id}/kb-images/{filename} 格式")
    return f"/api/knowledge/knowledge-bases/{kb_id}/images/{quote(relative_path, safe='/')}"
