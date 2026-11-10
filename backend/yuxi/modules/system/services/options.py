"""配置值读取、校验和表单输出；定义汇总不参与运行时读取。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.redis import get_async_redis_client
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.repositories.options import get_option, save_option_value

OPTION_CACHE_PREFIX = "yuxi:config_option:"
OPTION_CACHE_VERSION_PREFIX = "yuxi:config_option_version:"
OPTION_CACHE_TTL_SECONDS = 300
_URL_ADAPTER = TypeAdapter(HttpUrl)


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

        from yuxi.infrastructure.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as session:
            stored = await self._load_stored_value(session)

        if self.cacheable:
            await _save_cached_value(self.key, stored, cache_version)
        return self.resolve(stored)

    def resolve(self, stored: dict[str, Any]) -> dict[str, Any]:
        """按数据库、环境变量、默认值顺序解析有效配置。"""
        resolved = {}
        for field in self.fields:
            field_key = field["key"]
            environment_value = os.getenv(field.get("environment", ""))
            if field.get("type") == "list[str]":
                if field_key in stored:
                    resolved[field_key] = stored[field_key]
                elif environment_value:
                    resolved[field_key] = [item.strip() for item in environment_value.split(",") if item.strip()]
                else:
                    resolved[field_key] = field.get("default", [])
                continue

            if field.get("type") == "integer":
                resolved[field_key] = normalize_option_value(field, stored.get(field_key, field.get("default")))
                continue

            stored_value = stored.get(field_key)
            resolved[field_key] = stored_value or environment_value or field.get("default")
        return resolved

    @property
    def fields(self) -> list[dict[str, Any]]:
        return list(self.params.get("fields") or [])

    @property
    def cacheable(self) -> bool:
        return not any(field.get("sensitive") for field in self.fields)

    async def _load_stored_value(self, db: AsyncSession) -> dict[str, Any]:
        """从指定事务读取原始配置值。"""
        record = await get_option(db, self.key)
        if record is None:
            raise ValueError(f"配置项不存在: {self.key}")
        return dict(record.value or {})


def serialize_option(record: ConfigOption) -> dict[str, Any]:
    """返回原始配置与非敏感有效值；密钥仅公开来源和脱敏预览。"""

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
    public_fields = [field for field in _fields(record) if not field.get("sensitive")]
    public_definition = Option(record.key, record.name, record.description, {"fields": public_fields})
    return {
        "key": record.key,
        "name": record.name,
        "description": record.description,
        "params": record.params or {},
        "value": value,
        "effective_value": public_definition.resolve(value),
        "sensitive_configured": sensitive_configured,
        "sensitive_state": sensitive_state,
    }


async def save_configuration(db: AsyncSession, key: str, value: dict[str, Any], updated_by: str) -> ConfigOption | None:
    """提交管理员配置后失效缓存，失败时回滚调用方事务。"""
    try:
        record = await update_option_value(db, key, value, updated_by)
        if record is None:
            return None
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    await invalidate_option_cache(key)
    return record


async def update_option_value(
    db: AsyncSession,
    key: str,
    value: dict[str, Any],
    updated_by: str,
) -> ConfigOption | None:
    record = await get_option(db, key, for_update=True)
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
    return await save_option_value(db, record, updated, updated_by)


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


def normalize_option_value(field: dict[str, Any], value: Any) -> Any:
    """在配置持久化边界解析字段类型与有效范围。"""
    if field.get("type") == "integer":
        minimum = field.get("min", 0)
        if type(value) is not int or value < minimum:
            hint = "非负整数" if minimum <= 0 else f"不小于 {minimum} 的整数"
            raise ValueError(f"{field['key']} 必须是{hint}")
        return value

    if field.get("type") == "list[str]":
        if not isinstance(value, list):
            raise ValueError("配置值必须是列表")
        if not all(isinstance(item, str) for item in value):
            raise ValueError("配置值必须是字符串列表")
        return value

    normalized = str(value or "").strip()
    if "choices" in field and normalized not in field["choices"]:
        raise ValueError(f"不支持的 {field['key']}: {normalized}")
    if field.get("type") == "url" and normalized:
        return str(_URL_ADAPTER.validate_python(normalized))
    if field.get("type") == "ocr_engine" and normalized:
        from yuxi.infrastructure.document_parsing.engines import get_ocr_engine_ids

        if normalized not in {"disable", *get_ocr_engine_ids()}:
            raise ValueError(f"不支持的默认 OCR 引擎: {normalized}")
    return normalized


def _fields(record: ConfigOption) -> list[dict[str, Any]]:
    return list((record.params or {}).get("fields") or [])


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
