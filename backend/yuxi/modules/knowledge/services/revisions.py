"""登记当前文档来源；对象读取与 PostgreSQL 提交不伪装成跨库事务。"""

import asyncio
import hashlib
import json

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.modules.knowledge.revision_identity import body_revision, media_revision, original_revision, source_object_uri
from yuxi.modules.knowledge.services.structure import document_images
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, ResourceNotFoundError, StateConflictError


async def prepare_document_revision(file_meta: dict, *, content: bytes | None = None) -> dict:
    """核对当前正文对象并准备来源映射；可传入已成功上传的 Markdown 字节。"""
    client = get_minio_client()
    kb_id, file_id = file_meta["kb_id"], file_meta["file_id"]
    markdown_file = file_meta.get("markdown_file")
    if not markdown_file:
        raise ResourceNotFoundError("当前正文不存在", code="archive_source_missing")
    markdown = owned_object(kb_id, markdown_file)
    size = await client.astat_file(markdown["bucket"], markdown["object_name"])
    if size is None:
        raise ResourceNotFoundError("当前正文对象不存在", code="archive_source_missing")
    if content is None:
        content = await client.adownload_file(markdown["bucket"], markdown["object_name"])
    if size != len(content):
        raise InvalidOperationError("正文对象长度与发布字节不一致", code="archive_source_changed")
    structure = document_images(content.decode("utf-8"), kb_id=kb_id, file_id=file_id)
    original = await _original_info(file_meta)
    assets, missing, seen = [], [], set()
    for image in structure:
        if not image["readable"]:
            missing.append(
                {
                    "reference_hash": hashlib.sha256(image["reference"].encode()).hexdigest(),
                    "line": image["line"],
                    "reason": image["reason"],
                }
            )
            continue
        asset_id = image["asset_id"]
        if asset_id in seen:
            continue
        seen.add(asset_id)
        asset_object = {"bucket": client.KB_BUCKETS["images"], "object_name": f"{kb_id}/{asset_id}"}
        asset_size = await client.astat_file(asset_object["bucket"], asset_object["object_name"])
        if asset_size is None:
            missing.append({"asset_id": asset_id, "line": image["line"], "reason": "object_missing"})
        else:
            size, digest = await object_checksum(asset_object)
            assets.append(
                {"asset_id": asset_id, "kind": "image", "object": asset_object, "line": image["line"], "size": size, "sha256": digest}
            )
    return {
        "revision": body_revision(kb_id, file_id, markdown_file),
        "kb_id": kb_id,
        "file_id": file_id,
        "filename": file_meta["filename"],
        "kind": "document",
        "markdown_object": markdown,
        **original,
        "assets": assets,
        "missing_assets": missing,
        "content_hash": hashlib.sha256(content).hexdigest(),
        "content_size": len(content),
        "created_by": None,
    }


async def prepare_media_revision(file_meta: dict, manifest_uri: str) -> dict:
    """核对当前媒体清单和对象指纹，切段与时间码由媒体 Producer 拥有。"""
    original = await _original_info(file_meta)
    if not original["original_available"]:
        raise ResourceNotFoundError("原件不可用，不能发布新的媒体产物", code="archive_source_missing")
    kb_id, file_id = file_meta["kb_id"], file_meta["file_id"]
    manifest_object = owned_object(kb_id, manifest_uri)
    if not manifest_object["object_name"].startswith(f"{kb_id}/media/{file_id}/"):
        raise InvalidOperationError("媒体清单不属于当前文件", code="invalid_archive_object")
    data = await get_minio_client().adownload_file(manifest_object["bucket"], manifest_object["object_name"], max_bytes=2 * 1024 * 1024)
    try:
        manifest = json.loads(data, parse_constant=_reject_nonfinite_json)
    except (ValueError, UnicodeError) as error:
        raise InvalidInputError("媒体清单必须是有效 JSON") from error
    if not isinstance(manifest, dict) or (manifest.get("kb_id"), manifest.get("file_id")) != (kb_id, file_id):
        raise InvalidInputError("媒体清单的文件身份无效")
    if (manifest.get("source_revision"), manifest.get("source_sha256")) != (original["source_revision"], original["original_hash"]):
        raise StateConflictError("媒体清单不匹配当前原件", code="stale_source")
    basis = manifest.get("basis_document_revision")
    if basis is not None and (not file_meta.get("markdown_file") or basis != body_revision(kb_id, file_id, file_meta["markdown_file"])):
        raise StateConflictError("媒体清单依据的正文已变化", code="stale_source")
    items = manifest.get("assets")
    if not isinstance(items, list) or not items:
        raise InvalidInputError("媒体清单需要实际资产")
    assets, seen = [], set()
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("asset_id"), str)
            or not item["asset_id"]
            or not isinstance(item.get("kind"), str)
            or not item["kind"].strip()
        ):
            raise InvalidInputError("媒体资产标识与类型无效")
        if item["asset_id"] in seen:
            raise InvalidInputError("媒体资产标识不能重复")
        seen.add(item["asset_id"])
        ref = item.get("object")
        require_owned_object(kb_id, ref)
        prefixes = tuple(f"{kb_id}/{kind}/{file_id}/" for kind in ("media", "kb-images", "parsed", "original"))
        if ref != original["original_object"] and not ref["object_name"].startswith(prefixes):
            raise InvalidOperationError("媒体资产不属于当前文件", code="invalid_archive_object")
        size, digest = await object_checksum(ref)
        if item.get("sha256") != digest or ("size" in item and item["size"] != size):
            raise StateConflictError("媒体资产内容与清单不一致", code="immutable_source_changed")
        assets.append({**item, "size": size})
    return {
        "revision": media_revision(kb_id, file_id, original["source_revision"], manifest_uri),
        "kb_id": kb_id,
        "file_id": file_id,
        "filename": file_meta["filename"],
        "kind": "media",
        **original,
        "basis_document_revision": basis,
        "manifest_object": manifest_object,
        "markdown_object": None,
        "content_hash": hashlib.sha256(data).hexdigest(),
        "content_size": len(data),
        "assets": assets,
        "missing_assets": [],
        "created_by": None,
    }


async def object_checksum(ref: dict) -> tuple[int, str]:
    """流式计算真实对象大小和 SHA256，不把整段媒体装入内存。"""
    response = await get_minio_client().adownload_response(ref["bucket"], ref["object_name"])

    def consume():
        """消费完整对象后关闭连接，取消须等待当前 I/O 收敛。"""
        size, digest = 0, hashlib.sha256()
        try:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
            return size, digest.hexdigest()
        finally:
            response.close()
            response.release_conn()

    return await await_io(asyncio.to_thread(consume))


def owned_object(kb_id: str, uri: str) -> dict:
    """对象映射只能属于当前知识库的技术存储桶，不能混用宿主路径或外网。"""
    if not is_minio_url(uri):
        raise InvalidOperationError("来源映射只支持已发布的知识库对象", code="invalid_archive_object")
    bucket, object_name = parse_minio_url(uri)
    ref = {"bucket": bucket, "object_name": object_name}
    require_owned_object(kb_id, ref)
    return ref


def require_owned_object(kb_id: str, ref: object) -> None:
    """清单 JSON 与持久对象映射在实际存储边界复核桶和知识库归属。"""
    if not isinstance(ref, dict) or not isinstance(ref.get("bucket"), str) or not isinstance(ref.get("object_name"), str):
        raise InvalidOperationError("来源对象身份无效", code="invalid_archive_object")
    bucket, object_name = ref["bucket"], ref["object_name"]
    if (
        bucket not in get_minio_client().KB_BUCKETS.values()
        or not object_name.startswith(f"{kb_id}/")
        or "\\" in object_name
        or "\x00" in object_name
        or any(part in {".", ".."} for part in object_name.split("/"))
    ):
        raise InvalidOperationError("来源对象不属于当前知识库", code="invalid_archive_object")


async def _original_info(file_meta: dict) -> dict:
    """原件存在才计算真实 hash；缺失状态与正文可读性保持分开。"""
    uri = source_object_uri(file_meta.get("minio_url"), file_meta.get("path"), file_meta.get("file_type"))
    try:
        ref = owned_object(file_meta["kb_id"], uri) if uri else None
    except InvalidOperationError:
        # 旧文件可能只剩可读正文；越界原件不能被下载，也不能阻止保留真实正文。
        ref = None
    available = ref is not None and await get_minio_client().astat_file(ref["bucket"], ref["object_name"]) is not None
    size, digest = await object_checksum(ref) if available else (None, None)
    expected = file_meta.get("content_hash")
    if available and isinstance(expected, str) and len(expected) == 64 and expected != digest:
        raise StateConflictError("原件内容与文件指纹不一致", code="immutable_source_changed")
    return {
        "original_object": ref,
        "original_available": bool(available),
        "original_hash": digest,
        "original_size": size,
        "source_revision": original_revision(file_meta["kb_id"], file_meta["file_id"], uri) if uri else None,
    }


def _reject_nonfinite_json(_value):
    """非标准 JSON 常量不能进入不可变媒体清单。"""
    raise ValueError("媒体清单不能包含非有限数字")
