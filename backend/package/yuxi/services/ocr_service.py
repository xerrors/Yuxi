"""OCR 方法选择、运行时配置和健康检测。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.config.options import (
    mineru_ocr_host_opts,
    mineru_official_api_opts,
    paddleocr_api_opts,
    pp_structure_v3_ocr_host_opts,
    system_options,
)
from yuxi.knowledge.parser.capabilities import OCR_FILE_EXTENSIONS, PARSER_CAPABILITIES, get_parser_capability
from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.models.providers.service import get_model_provider_by_id, resolve_api_key


async def get_ocr_options(db: AsyncSession | None = None) -> dict[str, Any]:
    options = await system_options.get(db)
    return {
        "default_engine": options["default_ocr_engine"],
        "engines": [
            {
                "engine_id": engine_id,
                "service_name": capability.service_name,
                "display_name": capability.display_name,
                "supported_extensions": list(capability.supported_extensions),
            }
            for engine_id, capability in PARSER_CAPABILITIES.items()
        ],
    }


def resolve_ocr_engine_id(engine_id: str | None, default_engine: str) -> str:
    resolved = str(engine_id or default_engine).strip() or default_engine
    if resolved == "disable":
        return resolved
    if resolved not in PARSER_CAPABILITIES:
        raise ValueError(f"不支持的 OCR 引擎: {resolved}")
    return resolved


async def resolve_ocr_task_params(
    params: dict[str, Any] | None = None,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    resolved = dict(params or {})
    configured_engine = resolved.get("ocr_engine")
    if configured_engine is None:
        default_engine = (await system_options.get(db))["default_ocr_engine"]
    else:
        default_engine = str(configured_engine)
    engine_id = resolve_ocr_engine_id(configured_engine, default_engine)
    resolved["ocr_engine"] = engine_id
    resolved.pop("ocr_engine_config", None)

    if engine_id == "disable":
        kwargs = {}
    elif db is None:
        from yuxi.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as session:
            kwargs = await _build_processor_kwargs(session, engine_id)
    else:
        kwargs = await _build_processor_kwargs(db, engine_id)

    resolved["_ocr_processor_kwargs"] = kwargs
    return resolved


async def parse_document(
    source: str,
    params: dict[str, Any] | None = None,
    db: AsyncSession | None = None,
    *,
    document_limits: dict | None = None,
) -> str:
    """使用当前运行时配置将文件解析为 Markdown。

    这是业务代码唯一应调用的文档解析入口。函数负责区分应用层配置解析和
    底层文件转换：对于 PDF 与图片等 OCR 文件，先确定最终 OCR 引擎，再从
    数据库 Options、环境变量或模型供应商中解析该引擎的构造参数；对于普通
    文本、Office、表格等文件，参数保持原样并直接交给统一解析器。

    底层 parser 只接收已经准备好的 ``ocr_engine`` 和
    ``_ocr_processor_kwargs``，不查询数据库，也不关心配置值来自何处。调用方
    不应直接调用 ``yuxi.knowledge.parser.unified`` 中的内部解析入口，否则会
    绕过数据库配置、环境变量回退和默认 OCR 引擎解析。

    Args:
        source: 本地文件路径或系统支持的 MinIO 文件地址。
        params: 文件解析参数。可以包含 ``ocr_engine``、图片存储位置和各解析器
            支持的业务参数；未指定 OCR 引擎时使用系统默认值。
        db: 可选的异步数据库会话。已有事务的调用方可以传入以复用会话；未传入
            时仅在 OCR 配置解析需要查询数据库时创建独立会话。

    Returns:
        解析后的 Markdown 文本。

    Raises:
        ValueError: OCR 引擎无效、图片禁用 OCR 或文件类型不受支持。
        DocumentProcessorException: OCR 或文档解析器执行失败。
        StorageError: MinIO 文件读取失败。
    """

    from yuxi.knowledge.parser.document_limits import validate_document_limits

    limits = validate_document_limits(document_limits)
    resolved_params = dict(params or {})
    resolved_params.pop("_document_limits", None)
    suffix = Path(source.split("?", 1)[0]).suffix.lower()
    if suffix in OCR_FILE_EXTENSIONS:
        resolved_params = await resolve_ocr_task_params(resolved_params, db)
        engine_id = resolved_params["ocr_engine"]
        if engine_id != "disable" and suffix not in get_parser_capability(engine_id).supported_extensions:
            raise ValueError(f"OCR 引擎 {engine_id} 不支持文件类型 {suffix}")

    from yuxi.knowledge.parser.unified import parse_resolved_document

    if limits is not None:
        resolved_params["_document_limits"] = limits
    return await parse_resolved_document(source=source, params=resolved_params)


async def check_all_ocr_health(db: AsyncSession) -> dict[str, Any]:
    """使用当前有效配置并行检查所有 OCR 方法。"""

    configured = []
    results = {}
    for engine_id in PARSER_CAPABILITIES:
        try:
            kwargs = await _build_processor_kwargs(db, engine_id)
            configured.append((engine_id, kwargs))
        except Exception as exc:
            results[engine_id] = {"status": "error", "message": str(exc), "details": {}}

    async def check(engine_id: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            result = await asyncio.to_thread(DocumentProcessorFactory.check_health, engine_id, **kwargs)
        except Exception as exc:
            result = {"status": "error", "message": str(exc), "details": {}}
        return engine_id, result

    checked = await asyncio.gather(*(check(engine_id, kwargs) for engine_id, kwargs in configured))
    results.update(checked)
    return results


async def _build_processor_kwargs(db: AsyncSession, engine_id: str) -> dict[str, Any]:
    if engine_id == "mineru_ocr":
        opts = await mineru_ocr_host_opts.get(db)
        return {"server_url": opts["server_url"]} if opts["server_url"] else {}
    if engine_id == "mineru_official":
        opts = await mineru_official_api_opts.get(db)
        return {"api_key": opts["api_key"]} if opts["api_key"] else {}
    if engine_id == "pp_structure_v3_ocr":
        opts = await pp_structure_v3_ocr_host_opts.get(db)
        return {"server_url": opts["server_url"]} if opts["server_url"] else {}
    if engine_id == "deepseek_ocr":
        provider = await get_model_provider_by_id(db, "siliconflow-cn")
        api_key = resolve_api_key(provider) if provider and provider.is_enabled else None
        if not api_key:
            raise ValueError("siliconflow-cn 模型供应商凭证不可用")
        return {
            "api_key": api_key,
            "api_url": f"{provider.base_url.rstrip('/')}/chat/completions",
        }
    if engine_id in {"paddleocr_vl_1_6", "paddleocr_pp_ocrv6"}:
        opts = await paddleocr_api_opts.get(db)
        return {key: value for key, value in opts.items() if value}
    return {}
