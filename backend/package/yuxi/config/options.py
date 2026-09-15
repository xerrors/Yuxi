"""通用配置项定义、持久化、校验和运行时解析。

系统代码维护 `params`，管理员只修改 `value`。本模块只支持受控的基础字段，
不提供任意动态组件或可执行协议；OCR 只是第一批消费者。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import ConfigOption
from yuxi.storage.redis import get_async_redis_client
from yuxi.utils.logging_config import logger

OPTION_CACHE_PREFIX = "yuxi:config_option:"
OPTION_CACHE_VERSION_PREFIX = "yuxi:config_option_version:"
OPTION_CACHE_TTL_SECONDS = 300
SYSTEM_OPTIONS_MIGRATION_VERSION_PARAM = "migration_version"


@dataclass(frozen=True, slots=True)
class Option:
    """由代码定义并持久化到 PostgreSQL 的管理员配置项。"""

    key: str
    name: str
    description: str
    params: dict[str, Any]

    async def get(self, db: AsyncSession | None = None) -> dict[str, Any]:
        if db is not None:
            return self.resolve(await self._load_stored_value(db))

        cache_version = None
        if self.cacheable:
            cached = await _load_cached_value(self.key)
            if cached is not None:
                return self.resolve(cached)
            cache_version = await _load_cache_version(self.key)

        from yuxi.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as session:
            stored = await self._load_stored_value(session)

        if self.cacheable:
            await _save_cached_value(self.key, stored, cache_version)
        return self.resolve(stored)

    async def _load_stored_value(self, db: AsyncSession) -> dict[str, Any]:
        """从指定事务读取原始配置值。"""
        record = await get_option(db, self.key)
        if record is None:
            raise ValueError(f"配置项不存在: {self.key}")
        return dict(record.value or {})

    def resolve(self, stored: dict[str, Any]) -> dict[str, Any]:
        """按数据库、环境变量、默认值顺序解析有效配置。"""
        resolved = {}
        for field in self.fields:
            field_key = field["key"]
            if field_key in stored and field.get("type") == "list[str]":
                resolved[field_key] = stored[field_key]
                continue

            stored_value = stored.get(field_key)
            environment_value = os.getenv(field.get("environment", ""))
            resolved[field_key] = stored_value or environment_value or field.get("default")
        return resolved

    @property
    def fields(self) -> list[dict[str, Any]]:
        return list(self.params.get("fields") or [])

    @property
    def cacheable(self) -> bool:
        return not any(field.get("sensitive") for field in self.fields)


system_options = Option(
    key="system_options",
    name="系统配置",
    description="API 与 worker 共用的管理员配置。",
    params={
        "internal": True,
        "fields": [
            {
                "key": "default_model",
                "label": "默认对话模型",
                "type": "model",
                "default": "siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash",
            },
            {
                "key": "fast_model",
                "label": "快速响应模型",
                "type": "model",
                "default": "siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash",
            },
            {
                "key": "embed_model",
                "label": "默认 Embedding 模型",
                "type": "model",
                "default": "siliconflow-cn:Pro/BAAI/bge-m3",
            },
            {
                "key": "reranker",
                "label": "默认 Re-Ranker 模型",
                "type": "model",
                "default": "siliconflow-cn:Pro/BAAI/bge-reranker-v2-m3",
            },
            {
                "key": "default_ocr_engine",
                "label": "默认 OCR 解析引擎",
                "type": "ocr_engine",
                "default": "rapid_ocr",
            },
        ],
    },
)


mineru_ocr_host_opts = Option(
    key="mineru_ocr_host_opts",
    name="MinerU 服务",
    description="配置自托管 MinerU 服务地址。",
    params={
        "fields": [
            {
                "key": "server_url",
                "label": "服务地址",
                "type": "url",
                "environment": "MINERU_API_URI",
                "placeholder": "http://mineru-api:30001",
                "help": "留空时读取 MINERU_API_URI。",
            }
        ]
    },
)

mineru_official_api_opts = Option(
    key="mineru_official_api_opts",
    name="MinerU Official",
    description="配置 MinerU 官方云服务凭证。",
    params={
        "fields": [
            {
                "key": "api_key",
                "label": "API Key",
                "type": "password",
                "environment": "MINERU_API_KEY",
                "sensitive": True,
                "help": "留空时读取 MINERU_API_KEY，建议优先使用环境变量。",
            }
        ]
    },
)

pp_structure_v3_ocr_host_opts = Option(
    key="pp_structure_v3_ocr_host_opts",
    name="PP-Structure-V3 服务",
    description="配置自托管 PaddleX 服务地址。",
    params={
        "fields": [
            {
                "key": "server_url",
                "label": "服务地址",
                "type": "url",
                "environment": "PADDLEX_URI",
                "placeholder": "http://paddlex:8080",
                "help": "留空时读取 PADDLEX_URI。",
            }
        ]
    },
)

paddleocr_api_opts = Option(
    key="paddleocr_api_opts",
    name="PaddleOCR API",
    description="PaddleOCR-VL 和 PP-OCRv6 共用此配置。",
    params={
        "fields": [
            {
                "key": "api_url",
                "label": "API 地址",
                "type": "url",
                "environment": "PADDLEOCR_API_URL",
                "placeholder": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
                "help": "留空时读取 PADDLEOCR_API_URL。",
            },
            {
                "key": "api_token",
                "label": "Access Token",
                "type": "password",
                "environment": "PADDLEOCR_API_TOKEN",
                "sensitive": True,
                "help": "留空时读取 PADDLEOCR_API_TOKEN，建议优先使用环境变量。",
            },
        ]
    },
)

remote_skill_source_policy = Option(
    key="remote_skill_source_policy",
    name="远程 Skill 来源",
    description="配置允许远程安装 Skill 的来源域名。",
    params={
        "fields": [
            {
                "key": "allowed_hosts",
                "label": "允许的来源域名",
                "type": "list[str]",
                "default": ["github.com", "modelscope.cn"],
                "help": "仅精确匹配域名；保存空列表会关闭远程安装。",
            }
        ]
    },
)

document_limits = Option(
    key="document_limits",
    name="文档处理限制",
    description="专用文档限制服务维护的配置。",
    params={
        "internal": True,
        "fields": [
            {"key": "upload_max_mib", "type": "integer", "default": 100, "environment": "DOCUMENT_UPLOAD_MAX_MIB"},
            {"key": "ocr_max_pages", "type": "integer", "default": 500, "environment": "DOCUMENT_OCR_MAX_PAGES"},
            {"key": "revision", "type": "integer", "default": 0},
        ],
    },
)

OPTION_DEFINITIONS = {
    option.key: option
    for option in (
        mineru_ocr_host_opts,
        mineru_official_api_opts,
        pp_structure_v3_ocr_host_opts,
        paddleocr_api_opts,
        remote_skill_source_policy,
        system_options,
        document_limits,
    )
}

_URL_ADAPTER = TypeAdapter(HttpUrl)


async def ensure_options_in_db(db: AsyncSession) -> list[ConfigOption]:
    """幂等同步系统定义，保留管理员已经保存的值。"""

    if db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(94721801)"))

    result = await db.execute(select(ConfigOption).where(ConfigOption.key.in_(OPTION_DEFINITIONS)))
    existing = {record.key: record for record in result.scalars().all()}
    synced = []
    for key, definition in OPTION_DEFINITIONS.items():
        record = existing.get(key)
        if record is None:
            record = ConfigOption(
                key=key,
                name=definition.name,
                description=definition.description,
                params=definition.params,
                value={},
                created_by="system",
                updated_by="system",
            )
            db.add(record)
        else:
            record.name = definition.name
            record.description = definition.description
            params = dict(definition.params)
            if definition.key == system_options.key:
                params[SYSTEM_OPTIONS_MIGRATION_VERSION_PARAM] = int(
                    (record.params or {}).get(SYSTEM_OPTIONS_MIGRATION_VERSION_PARAM) or 0
                )
            record.params = params
        synced.append(record)
    await db.flush()
    return synced


async def list_options(db: AsyncSession) -> list[ConfigOption]:
    result = await db.execute(
        select(ConfigOption)
        .where(ConfigOption.key.not_in([system_options.key, document_limits.key]))
        .order_by(ConfigOption.id)
    )
    return list(result.scalars().all())


async def get_option(db: AsyncSession, key: str) -> ConfigOption | None:
    statement = select(ConfigOption).where(ConfigOption.key == key).execution_options(populate_existing=True)
    result = await db.execute(statement)
    return result.scalar_one_or_none()


def serialize_option(record: ConfigOption) -> dict[str, Any]:
    """返回表单定义和值；密钥只返回来源和脱敏预览。"""

    value = dict(record.value or {})
    sensitive_configured = {}
    sensitive_state = {}
    for field in _fields(record):
        if not field.get("sensitive"):
            continue
        field_key = field["key"]
        stored_value = str(value.get(field_key) or "")
        environment_value = os.getenv(field.get("environment", ""))
        if stored_value:
            state = {
                "source": "database",
                "configured": True,
                "preview": _mask_sensitive_value(stored_value),
            }
        elif environment_value:
            state = {"source": "environment", "configured": True, "preview": None}
        else:
            state = {"source": "none", "configured": False, "preview": None}
        sensitive_state[field_key] = state
        sensitive_configured[field_key] = state["configured"]
        value[field_key] = ""
    return {
        "key": record.key,
        "name": record.name,
        "description": record.description,
        "params": record.params or {},
        "value": value,
        "sensitive_configured": sensitive_configured,
        "sensitive_state": sensitive_state,
    }


async def update_option_value(
    db: AsyncSession,
    key: str,
    value: dict[str, Any],
    updated_by: str,
) -> ConfigOption | None:
    record = await db.scalar(select(ConfigOption).where(ConfigOption.key == key).with_for_update())
    if record is None:
        return None

    fields = {field["key"]: field for field in _fields(record)}
    unknown = set(value) - set(fields)
    if unknown:
        raise ValueError(f"未知配置字段: {', '.join(sorted(unknown))}")

    updated = dict(record.value or {})
    for field_key, raw_value in value.items():
        field = fields[field_key]
        updated[field_key] = normalize_option_value(field, raw_value)
    record.value = updated
    record.updated_by = updated_by
    await db.flush()
    return record


async def invalidate_option_cache(key: str) -> None:
    """数据库提交后删除 Option 缓存。"""
    try:
        redis = await get_async_redis_client()
        await redis.eval(
            """
            redis.call('INCR', KEYS[1])
            return redis.call('DEL', KEYS[2])
            """,
            2,
            f"{OPTION_CACHE_VERSION_PREFIX}{key}",
            f"{OPTION_CACHE_PREFIX}{key}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to invalidate option cache {key}: {exc}")


def _fields(record: ConfigOption) -> list[dict[str, Any]]:
    return list((record.params or {}).get("fields") or [])


def normalize_option_value(field: dict[str, Any], value: Any) -> Any:
    if field.get("type") == "list[str]":
        if not isinstance(value, list):
            raise ValueError("配置值必须是列表")
        if not all(isinstance(item, str) for item in value):
            raise ValueError("配置值必须是字符串列表")
        return value

    normalized = str(value or "").strip()
    if field.get("type") == "url" and normalized:
        return str(_URL_ADAPTER.validate_python(normalized))
    if field.get("type") == "ocr_engine" and normalized:
        from yuxi.knowledge.parser.capabilities import get_ocr_engine_ids

        if normalized not in {"disable", *get_ocr_engine_ids()}:
            raise ValueError(f"不支持的默认 OCR 引擎: {normalized}")
    return normalized


async def _load_cached_value(key: str) -> dict[str, Any] | None:
    try:
        redis = await get_async_redis_client()
        raw = await redis.get(f"{OPTION_CACHE_PREFIX}{key}")
        value = json.loads(raw) if raw else None
        return value if isinstance(value, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to load option cache {key}: {exc}")
        return None


async def _load_cache_version(key: str) -> str | None:
    try:
        redis = await get_async_redis_client()
        await redis.set(f"{OPTION_CACHE_VERSION_PREFIX}{key}", "0", nx=True)
        return str(await redis.get(f"{OPTION_CACHE_VERSION_PREFIX}{key}") or "0")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to load option cache version {key}: {exc}")
        return None


async def _save_cached_value(key: str, value: dict[str, Any], expected_version: str | None) -> None:
    if expected_version is None:
        return
    try:
        redis = await get_async_redis_client()
        version_key = f"{OPTION_CACHE_VERSION_PREFIX}{key}"
        await redis.eval(
            """
            if redis.call('GET', KEYS[1]) == ARGV[1] then
                return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
            end
            return nil
            """,
            2,
            version_key,
            f"{OPTION_CACHE_PREFIX}{key}",
            expected_version,
            json.dumps(value, ensure_ascii=False),
            OPTION_CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to save option cache {key}: {exc}")


def _mask_sensitive_value(value: str) -> str:
    if len(value) == 1:
        return "*******"
    if len(value) <= 4:
        return f"{value[0]}*******{value[-1]}"
    return f"{value[:2]}*******{value[-2:]}"
