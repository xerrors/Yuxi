"""模型缓存服务 - 基于 Redis 的跨进程模型信息缓存。

本模块将数据库中的 model_providers 表数据序列化到 Redis，
供 API 和 Worker 等多进程同步读取，避免在同步函数中查询异步数据库。

模型 spec 格式: provider_id:model_id（冒号分隔）。model_id 允许包含斜杠。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from yuxi.storage.redis import sync_redis_client
from yuxi.utils.logging_config import logger

REDIS_CACHE_KEY = "yuxi:model_cache"
_CACHE_TTL_SECONDS = 5


@dataclass(frozen=True)
class ModelInfo:
    """不可变的模型信息，供运行时使用。"""

    provider_id: str
    model_id: str
    model_type: str  # chat / embedding / rerank
    display_name: str

    # 运行时配置
    api_key: str
    base_url: str
    provider_type: str  # openai / anthropic / gemini / openrouter

    # 可选配置
    headers: dict[str, str] = field(default_factory=dict)
    # provider 级 extra_json，不是 OpenAI chat 的 extra_body。
    extra: dict[str, Any] = field(default_factory=dict)
    request_body_overrides: dict[str, Any] = field(default_factory=dict)

    # Embedding 专属
    dimension: int | None = None
    batch_size: int = 40

    @property
    def spec(self) -> str:
        return f"{self.provider_id}:{self.model_id}"

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_type": self.model_type,
            "display_name": self.display_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "provider_type": self.provider_type,
            "headers": self.headers,
            "extra": self.extra,
            "request_body_overrides": self.request_body_overrides,
            "dimension": self.dimension,
            "batch_size": self.batch_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelInfo:
        return cls(
            provider_id=data["provider_id"],
            model_id=data["model_id"],
            model_type=data["model_type"],
            display_name=data["display_name"],
            api_key=data["api_key"],
            base_url=data["base_url"],
            provider_type=data["provider_type"],
            headers=data.get("headers", {}),
            extra=data.get("extra", {}),
            request_body_overrides=data.get("request_body_overrides", {}),
            dimension=data.get("dimension"),
            batch_size=data.get("batch_size", 40),
        )


class ModelCache:
    """基于 Redis 的模型缓存，所有写入均走 Redis，保证跨进程一致。"""

    def __init__(self) -> None:
        self._local_cache: dict[str, ModelInfo] | None = None
        self._local_cache_at: float = 0.0

    def _load_cache(self) -> dict[str, ModelInfo]:
        now = time.monotonic()
        if self._local_cache is not None and (now - self._local_cache_at) < _CACHE_TTL_SECONDS:
            return self._local_cache

        try:
            with sync_redis_client() as redis_client:
                raw = redis_client.get(REDIS_CACHE_KEY)
            if not raw:
                self._local_cache = {}
                self._local_cache_at = now
                return {}

            items = json.loads(raw)
            cache = {spec: ModelInfo.from_dict(data) for spec, data in items.items()}
        except Exception as e:
            logger.warning(f"Failed to load model cache from Redis: {e}")
            return {}

        self._local_cache = cache
        self._local_cache_at = now
        return cache

    def _invalidate_local(self) -> None:
        self._local_cache = None
        self._local_cache_at = 0.0

    def get_model_info(self, spec: str) -> ModelInfo | None:
        cache = self._load_cache()
        return cache.get(spec)

    def get_all_specs(self, model_type: str | None = None) -> list[ModelInfo]:
        cache = self._load_cache()
        if model_type is None:
            return list(cache.values())
        return [info for info in cache.values() if info.model_type == model_type]

    def get_specs_grouped_by_provider(self, model_type: str = "chat") -> dict[str, list[ModelInfo]]:
        cache = self._load_cache()
        grouped: dict[str, list[ModelInfo]] = {}
        for info in cache.values():
            if info.model_type != model_type:
                continue
            grouped.setdefault(info.provider_id, []).append(info)
        return grouped

    def rebuild(self, providers: list[Any]) -> None:
        from yuxi.models.providers.service import resolve_api_key

        new_cache: dict[str, ModelInfo] = {}

        for provider in providers:
            if not provider.is_enabled:
                continue

            api_key = resolve_api_key(provider)

            for model in provider.enabled_models or []:
                model_type = model.get("type", "chat")
                base_url = model.get("base_url_override") or self._get_base_url_for_type(provider, model_type)

                info = ModelInfo(
                    provider_id=provider.provider_id,
                    model_id=model["id"],
                    model_type=model_type,
                    display_name=model.get("display_name", model["id"]),
                    api_key=api_key or "",
                    base_url=base_url,
                    provider_type=provider.provider_type,
                    headers=dict(provider.headers_json or {}),
                    extra=dict(provider.extra_json or {}),
                    request_body_overrides=dict(model.get("request_body_overrides") or {}),
                    dimension=model.get("dimension"),
                    batch_size=model.get("batch_size", 40),
                )
                new_cache[info.spec] = info

        self._save_cache(new_cache)
        self._invalidate_local()
        logger.info(f"Model cache rebuilt: {len(new_cache)} models → Redis")

    def _save_cache(self, cache: dict[str, ModelInfo]) -> None:
        try:
            data = {spec: info.to_dict() for spec, info in cache.items()}
            with sync_redis_client() as redis_client:
                redis_client.set(REDIS_CACHE_KEY, json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save model cache to Redis: {e}")

    @staticmethod
    def _get_base_url_for_type(provider: Any, model_type: str) -> str:
        if model_type == "embedding" and provider.embedding_base_url:
            return provider.embedding_base_url
        if model_type == "rerank" and provider.rerank_base_url:
            return provider.rerank_base_url
        return provider.base_url


model_cache = ModelCache()


def resolve_model_spec(spec: str) -> ModelInfo:
    """根据 spec 返回 ModelInfo。"""
    if not spec:
        raise ValueError("model spec 不能为空")

    info = model_cache.get_model_info(spec)
    if info:
        return info

    all_specs = model_cache.get_all_specs()
    available = [item.spec for item in all_specs[:10]]
    raise ValueError(f"未找到模型: '{spec}'。可用模型 ({len(all_specs)}): {available}")
