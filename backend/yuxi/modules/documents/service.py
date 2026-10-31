"""OCR 方法选择、运行时配置和健康检测。"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.document_parsing import OCR_FILE_EXTENSIONS, ParseOptions, ParseResult
from yuxi.infrastructure.document_parsing.engines import ENGINE_SPECS, get_engine, get_engine_spec
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.documents.options import DOCUMENT_OPTIONS, OCR_ENGINE_OPTIONS
from yuxi.modules.models.providers.service import resolve_deepseek_ocr_credentials
from yuxi.modules.system.options import system_options
from yuxi.modules.system.repositories.options import get_option, save_option_value


async def migrate_mineru_configuration(db: AsyncSession) -> bool:
    """在启动配置事务中迁移已持久化的旧档位，保留其他字段与审计归属。"""
    record = await get_option(db, "mineru_ocr_host_opts", for_update=True)
    if record is None:
        return False
    values = record.value or {}
    if not {"backend", "effort"}.intersection(values):
        return False
    migrated = dict(values)
    backend = migrated.pop("backend", "hybrid-engine")
    effort = migrated.pop("effort", "medium")
    if "tier" not in migrated:
        if backend not in {"hybrid-engine", "vlm-engine"} or effort not in {"medium", "high"}:
            raise ValueError("MinerU 旧 backend/effort 配置无法迁移，请显式选择 tier")
        migrated["tier"] = "advanced" if backend == "hybrid-engine" and effort == "high" else "standard"
    await save_option_value(db, record, migrated, record.updated_by)
    return True


async def get_ocr_options(db: AsyncSession | None = None) -> dict[str, Any]:
    options = await system_options.get(db)
    return {
        "default_engine": options["default_ocr_engine"],
        "option_keys": [option.key for option in DOCUMENT_OPTIONS],
        "engines": [
            {
                "engine_id": engine_id,
                "service_name": capability.service_name,
                "display_name": capability.display_name,
                "supported_extensions": list(capability.supported_extensions),
            }
            for engine_id, capability in ENGINE_SPECS.items()
        ],
    }


def resolve_ocr_engine_id(engine_id: str | None, default_engine: str) -> str:
    resolved = str(engine_id or default_engine).strip() or default_engine
    if resolved == "disable":
        return resolved
    if resolved not in ENGINE_SPECS:
        raise ValueError(f"不支持的 OCR 引擎: {resolved}")
    return resolved


async def resolve_parse_options(
    params: dict[str, Any] | None = None,
    db: AsyncSession | None = None,
) -> ParseOptions:
    """一次解析默认选择与配置，直接产生本次转换选项。"""
    resolved = dict(params or {})
    configured_engine = resolved.pop("ocr_engine", None)
    if configured_engine is None:
        default_engine = (await system_options.get(db))["default_ocr_engine"]
    else:
        default_engine = str(configured_engine)
    engine_id = resolve_ocr_engine_id(configured_engine, default_engine)
    resolved.pop("ocr_engine_config", None)

    if engine_id == "disable":
        kwargs = {}
    elif db is None:
        from yuxi.infrastructure.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as session:
            kwargs = await _build_processor_kwargs(session, engine_id)
    else:
        kwargs = await _build_processor_kwargs(db, engine_id)

    resolved.pop("_ocr_processor_kwargs", None)
    return ParseOptions(ocr_engine=engine_id, processor_kwargs=kwargs, params=resolved)


async def parse(
    source: str,
    output_dir: str | Path,
    params: dict[str, Any] | None = None,
    db: AsyncSession | None = None,
) -> ParseResult:
    """解析本地或 MinIO 输入，返回完整本地产物，不发布图片。"""
    suffix = Path(source.split("?", 1)[0]).suffix.lower()
    if suffix in OCR_FILE_EXTENSIONS:
        options = await resolve_parse_options(params, db)
        if options.ocr_engine != "disable" and suffix not in get_engine_spec(options.ocr_engine).supported_extensions:
            raise ValueError(f"OCR 引擎 {options.ocr_engine} 不支持文件类型 {suffix}")
    else:
        options = ParseOptions(params=dict(params or {}))
    from yuxi.infrastructure.document_parsing.parser import parse as parse_local
    from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url

    if not is_minio_url(source):
        return await parse_local(source, output_dir, options)
    from yuxi.infrastructure.minio import get_minio_client

    with tempfile.TemporaryDirectory(prefix="yuxi-parse-input-") as directory:
        local_path = Path(directory) / f"source{suffix}"
        bucket, object_name = parse_minio_url(source)
        content = await get_minio_client().adownload_file(bucket, object_name)
        await asyncio.to_thread(local_path.write_bytes, content)
        return await parse_local(local_path, output_dir, options)


async def parse_to_hosted_markdown(
    source: str,
    *,
    image_bucket: str,
    image_prefix: str,
    url_builder: Callable[[str], str],
    params: dict[str, Any] | None = None,
    db: AsyncSession | None = None,
) -> str:
    """解析并发布图片；上传位置与访问 URL 由已授权调用方提供。"""
    from yuxi.modules.documents.assets import upload_resources

    with tempfile.TemporaryDirectory(prefix="yuxi-hosted-markdown-") as directory:
        result = await parse(source, Path(directory) / "parsed", params, db)
        return await upload_resources(result, image_bucket, image_prefix, url_builder)


async def check_all_ocr_health(db: AsyncSession) -> dict[str, Any]:
    """使用当前有效配置并行检查所有 OCR 方法。"""

    configured = []
    results = {}
    for engine_id in ENGINE_SPECS:
        try:
            kwargs = await _build_processor_kwargs(db, engine_id)
            configured.append((engine_id, kwargs))
        except Exception:
            logger.exception("OCR {} 配置检查失败", engine_id)
            results[engine_id] = {"status": "error"}

    async def check(engine_id: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            result = await asyncio.to_thread(get_engine(engine_id, **kwargs).check_health)
        except Exception:
            logger.exception("OCR {} 健康检查失败", engine_id)
            result = {"status": "error"}
        return engine_id, result

    checked = await asyncio.gather(*(check(engine_id, kwargs) for engine_id, kwargs in configured))
    results.update(checked)

    # 登录用户仅消费可用性，提供者 message/details 可能包含服务地址或凭据。
    public_messages = {
        "healthy": "服务可用",
        "configured": "凭据已配置，将在解析时验证",
        "unhealthy": "服务响应异常",
        "unavailable": "服务不可用，请联系管理员",
        "timeout": "服务连接超时",
        "error": "健康检查失败，请联系管理员",
    }
    for engine_id, result in results.items():
        status = result["status"]
        if status not in public_messages:
            logger.error("OCR {} 返回未知健康状态", engine_id)
            status = "error"
        results[engine_id] = {"status": status, "message": public_messages[status], "details": {}}
    return results


async def _build_processor_kwargs(db: AsyncSession, engine_id: str) -> dict[str, Any]:
    """配置绑定归文档域；模型供应商仍拥有 DeepSeek 的凭据。"""
    if engine_id == "deepseek_ocr":
        return await resolve_deepseek_ocr_credentials(db)
    option = OCR_ENGINE_OPTIONS.get(engine_id)
    return await option.get(db) if option is not None else {}
