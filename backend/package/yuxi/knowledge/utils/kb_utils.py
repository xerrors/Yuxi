import hashlib
import time

from yuxi.knowledge.chunking.ragflow_like.presets import resolve_chunk_processing_params
from yuxi.utils import hashstr, logger
from yuxi.utils.datetime_utils import utc_isoformat

_DROPPED_PROCESSING_PARAM_KEYS = {
    "_preprocessed_map",
    "auto_index",
    "content_hashes",
    "file_sizes",
    "enable_ocr",
    "ocr_engine_config",
}


def sanitize_processing_params(params: dict | None) -> dict | None:
    """移除不应写入单文件元数据的参数。"""
    if not params:
        return None

    return {key: value for key, value in params.items() if key not in _DROPPED_PROCESSING_PARAM_KEYS}


def resolve_processing_params(
    kb_additional_params: dict | None,
    file_processing_params: dict | None,
    request_params: dict | None = None,
) -> dict:
    """合并文件、请求中的 OCR 和分块参数。"""

    merged_params = sanitize_processing_params(merge_processing_params(file_processing_params, request_params)) or {}
    chunk_params = resolve_chunk_processing_params(
        kb_additional_params=kb_additional_params,
        file_processing_params=file_processing_params,
        request_params=request_params,
    )
    merged_params.update(chunk_params)
    return merged_params


async def calculate_content_hash(data: bytes | bytearray) -> str:
    """计算文件内容的 SHA-256 哈希值。"""
    sha256 = hashlib.sha256()
    sha256.update(data)
    return sha256.hexdigest()


async def prepare_item_metadata(item: str, content_type: str, kb_id: str, params: dict | None = None) -> dict:
    """
    准备 MinIO 文件元数据；URL 导入需先通过 fetch-url 预处理为 MinIO 文件。

    Args:
        item: MinIO URL
        content_type: 内容类型，目前仅支持 "file"
        kb_id: 数据库ID
        params: 处理参数，可选
    """
    # 检查是否有预处理信息 (针对 URL 转 HTML 文件的情况)
    if params and "_preprocessed_map" in params and item in params["_preprocessed_map"]:
        pre_info = params["_preprocessed_map"][item]

        # 使用预处理信息
        filename = pre_info.get("filename", item)  # 通常是原始 URL

        # 截断文件名以适应数据库限制 (512 chars)，保留部分后缀信息如果可能
        if len(filename) > 500:
            filename_display = filename[:400] + "..." + filename[-90:]
        else:
            filename_display = filename

        file_type = "html"  # 强制转换为 html 类型，以便后续作为文件处理
        item_path = pre_info["path"]  # MinIO path
        content_hash = pre_info["content_hash"]

        # 使用 item(url) 生成 ID，保证同一 URL 即使多次添加 ID 也不同（配合 time）
        # 或者我们应该基于 hash？不，基于 time 更符合上传逻辑
        file_id = f"file_{hashstr(item + str(time.time()), 6)}"

        metadata = {
            "kb_id": kb_id,
            "filename": filename_display,
            "path": item_path,
            "file_type": file_type,
            "status": "indexing",
            "created_at": utc_isoformat(),
            "file_id": file_id,
            "content_hash": content_hash,
            "size": pre_info.get("file_size"),
            "parent_id": params.get("parent_id"),
        }

        if params:
            safe_params = sanitize_processing_params(params) or {}
            # 覆盖 content_type 为 file，确保后续解析走文件流程（MinIO 下载 -> HTML 解析）
            # 而不是再次尝试作为 URL 抓取
            safe_params["content_type"] = "file"
            safe_params["original_source"] = item  # 保存完整 URL 到 JSON 字段，避免数据库字段长度限制
            metadata["processing_params"] = safe_params

        return metadata

    if content_type == "file":
        if not is_minio_url(item):
            raise ValueError(f"File source must be a MinIO URL: {item}")

        logger.debug(f"Processing MinIO file: {item}")
        _, object_name = parse_minio_url(item)
        filename = object_name.rsplit("/", 1)[-1]

        import re

        timestamp_pattern = r"^(.+)_(\d{13})(\.[^.]+)$"
        match = re.match(timestamp_pattern, filename)
        filename_display = match.group(1) + match.group(3) if match else filename
        source_path = _normalize_source_path(params.get("source_path")) if params else None
        if source_path:
            filename_display = source_path

        file_type = filename_display.rsplit(".", 1)[-1].lower() if "." in filename_display else ""
        item_path = item

        content_hash = None
        if params and "content_hashes" in params and isinstance(params["content_hashes"], dict):
            content_hash = params["content_hashes"].get(item)

        if not content_hash:
            raise ValueError(f"Missing content_hash for file: {item}")

        file_sizes = params.get("file_sizes") if params else None
        if not isinstance(file_sizes, dict):
            file_sizes = {}
        file_size = file_sizes.get(item)
        file_id = f"file_{hashstr(str(item_path) + str(time.time()), 6)}"

    else:
        raise ValueError(f"Unsupported content_type: {content_type}")

    metadata = {
        "kb_id": kb_id,
        "filename": filename_display,  # 使用显示用的文件名
        "path": item_path,
        "file_type": file_type,
        "status": "indexing",
        "created_at": utc_isoformat(),
        "file_id": file_id,
        "content_hash": content_hash,
        "size": file_size,
        "parent_id": params.get("parent_id") if params else None,
    }

    # 保存处理参数到元数据
    if params:
        metadata["processing_params"] = sanitize_processing_params(params)

    return metadata


def _normalize_source_path(value: object) -> str | None:
    """归一化客户端传入的上传源路径，仅用于知识库文件树中的展示文件名。

    source_path 用来保留 CLI 目录上传时的相对层级。这里不会把它当作真实
    存储路径使用：反斜杠会转成斜杠，开头的 "./" 会被去掉，绝对路径和
    ".." 父目录跳转会被拒绝。
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return None
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    display_path = "/".join(parts)
    if len(display_path) > 512:
        raise ValueError("source_path is too long")
    return display_path


def merge_processing_params(metadata_params: dict | None, request_params: dict | None) -> dict:
    """
    合并处理参数：优先使用请求参数，缺失时使用元数据中的参数

    Args:
        metadata_params: 元数据中保存的参数
        request_params: 请求中提供的参数

    Returns:
        dict: 合并后的参数
    """
    merged_params = {}

    # 首先使用元数据中的参数作为默认值
    if metadata_params:
        merged_params.update(metadata_params)

    # 然后使用请求参数覆盖（如果提供）
    if request_params:
        merged_params.update(request_params)

    logger.debug(
        "Merged processing params: "
        f"metadata_keys={list(metadata_params.keys()) if metadata_params else []}, "
        f"request_keys={list(request_params.keys()) if request_params else []}, "
        f"merged_keys={list(merged_params.keys())}"
    )
    return merged_params


def is_minio_url(file_path: str) -> bool:
    """检测是否是本系统生成的 MinIO 存储 URL。"""
    from urllib.parse import urlparse

    parsed_url = urlparse(file_path)
    if parsed_url.scheme == "minio":
        return bool(parsed_url.netloc and parsed_url.path.lstrip("/"))

    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return False

    path_parts = parsed_url.path.lstrip("/").split("/", 1)
    if len(path_parts) != 2:
        return False

    from yuxi.storage.minio.client import MinIOClient

    known_buckets = set(MinIOClient.KB_BUCKETS.values()) | MinIOClient.PUBLIC_READ_BUCKETS
    return path_parts[0] in known_buckets


def parse_minio_url(file_path: str) -> tuple[str, str]:
    """
    解析MinIO URL，提取bucket名称和对象名称

    支持标准 HTTP/HTTPS URL 格式：
    - http(s)://host/bucket-name/path/to/object

    Args:
        file_path: MinIO文件URL (http:// 或 https://)

    Returns:
        tuple[str, str]: (bucket_name, object_name)

    Raises:
        ValueError: 如果无法解析URL
    """
    try:
        from urllib.parse import unquote, urlparse

        # 解析URL
        parsed_url = urlparse(file_path)

        # 对于 minio:// 协议，bucket名称在netloc中
        if parsed_url.scheme == "minio":
            bucket_name = parsed_url.netloc
            object_name = unquote(parsed_url.path.lstrip("/"))
        else:
            # 对于 http/https 协议，bucket名称在path的第一部分
            object_name = parsed_url.path.lstrip("/")
            path_parts = object_name.split("/", 1)
            if len(path_parts) > 1:
                bucket_name = path_parts[0]
                object_name = unquote(path_parts[1])
            else:
                raise ValueError(f"无法解析MinIO URL中的bucket名称: {file_path}")

        logger.debug(f"Parsed MinIO URL: bucket_name={bucket_name}, object_name={object_name}")
        return bucket_name, object_name

    except Exception as e:
        logger.error(f"Failed to parse MinIO URL {file_path}: {e}")
        raise ValueError(f"无法解析MinIO URL: {file_path}")
