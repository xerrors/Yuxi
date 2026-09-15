"""文档限制与部署能力边界；任务入队时生成不可变快照。"""

import os

from yuxi.config.options import document_limits, invalidate_option_cache
from yuxi.repositories.document_limits_repository import read_limits, replace_limits

MIB = 1024 * 1024


def deployment_limits() -> dict:
    """部署方声明已协调代理与 OCR 的硬上限，不由网页扩大。"""
    return {
        "hard_upload_max_mib": int(os.getenv("DOCUMENT_UPLOAD_HARD_MAX_MIB", "100")),
        "hard_ocr_max_pages": int(os.getenv("DOCUMENT_OCR_HARD_MAX_PAGES", "500")),
    }


def resolve_limits(value: dict) -> dict:
    """校验持久化值并展示部署收紧后的实际值。"""
    caps = deployment_limits()
    if min(caps.values()) < 1:
        raise ValueError("部署硬上限必须是正整数")
    defaults = {
        "upload_max_mib": min(int(os.getenv("DOCUMENT_UPLOAD_MAX_MIB", "100")), caps["hard_upload_max_mib"]),
        "ocr_max_pages": min(int(os.getenv("DOCUMENT_OCR_MAX_PAGES", "500")), caps["hard_ocr_max_pages"]),
    }
    upload = min(int(value.get("upload_max_mib", defaults["upload_max_mib"])), caps["hard_upload_max_mib"])
    pages = min(int(value.get("ocr_max_pages", defaults["ocr_max_pages"])), caps["hard_ocr_max_pages"])
    if min(upload, pages, *defaults.values()) < 1:
        raise ValueError("文档处理限制必须是正整数")
    return {
        **caps,
        "defaults": defaults,
        "upload_max_mib": upload,
        "ocr_max_pages": pages,
        "effective_upload_max_bytes": upload * MIB,
        "revision": int(value.get("revision", 0)),
    }


async def get_document_limits() -> dict:
    """读取所有登录用户可见的有效限制。"""
    return resolve_limits(document_limits.resolve(await read_limits()))


async def snapshot_document_limits() -> dict:
    """只由服务端创建任务快照，不接受客户端参数中的快照。"""
    settings = await get_document_limits()
    return {
        "max_file_bytes": settings["effective_upload_max_bytes"],
        "max_ocr_pages": settings["ocr_max_pages"],
        "version": settings["revision"],
    }


async def save_document_limits(value: dict, revision: int, actor: str) -> dict:
    """拒绝越过部署能力的保存，使用 CAS 避免覆盖其他管理员。"""
    caps = deployment_limits()
    for key, cap in (("upload_max_mib", "hard_upload_max_mib"), ("ocr_max_pages", "hard_ocr_max_pages")):
        if key in value and (type(value[key]) is not int or not 1 <= value[key] <= caps[cap]):
            raise ValueError(f"{key} 必须为 1 至 {caps[cap]} 的整数")
    await replace_limits(value, revision, actor)
    await invalidate_option_cache(document_limits.key)
    return await get_document_limits()
