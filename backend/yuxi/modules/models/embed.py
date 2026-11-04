import asyncio
import time
from abc import ABC, abstractmethod

import httpx
import requests

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.models.providers.cache import model_cache
from yuxi.modules.models.utils import get_docker_safe_url

EMBEDDING_RATE_LIMIT_MAX_RETRIES = 10
EMBEDDING_TRANSIENT_MAX_RETRIES = 2
EMBEDDING_RETRY_MAX_DELAY_SECONDS = 10.0
EMBEDDING_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class BaseEmbeddingModel(ABC):
    def __init__(
        self,
        *,
        model,
        dimension=None,
        base_url=None,
        api_key=None,
        batch_size=40,
    ):
        """接收供应商已解析的执行配置。"""
        self.model = model
        self.dimension = int(dimension) if dimension not in (None, "") else None
        self.base_url = get_docker_safe_url(base_url)
        self.api_key = api_key
        self.batch_size = int(batch_size or 40)

    @abstractmethod
    def encode(self, message: list[str] | str) -> list[list[float]]:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        raise NotImplementedError("Subclasses must implement this method")

    def batch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []

        for i in range(0, len(messages), batch_size):
            group_msg = messages[i : i + batch_size]
            logger.info(f"Encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
            response = self.encode(group_msg)
            self._require_one_vector_per_input(response, len(group_msg), self.dimension)
            data.extend(response)

        return data

    async def abatch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []

        for i in range(0, len(messages), batch_size):
            group_msg = messages[i : i + batch_size]
            logger.info(f"Async encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
            res = await self.aencode(group_msg)
            self._require_one_vector_per_input(res, len(group_msg), self.dimension)
            data.extend(res)

        return data

    async def test_connection(self) -> tuple[bool, str]:
        try:
            embeddings = await self.aencode(["Hello world"])
            if self.dimension not in (None, ""):
                actual_dimension = len(embeddings[0]) if embeddings else 0
                expected_dimension = int(self.dimension)
                if actual_dimension != expected_dimension:
                    return False, f"Embedding 维度不一致：配置 {expected_dimension}，实际 {actual_dimension}"
            return True, "连接正常"
        except Exception as e:
            error_msg = str(e)
            error_msg += f", maybe you can check the `{self.base_url}` end with /embeddings as examples."
            logger.error(error_msg)
            return False, error_msg

    @staticmethod
    def _extract_embeddings(result: dict, expected: int | None = None, dimension: int | None = None) -> list[list[float]]:
        """只接受逐条对应且维度一致的向量；提供者带 index 时按 index 归位，否则保持响应顺序。"""
        if not isinstance(result, dict) or "data" not in result:
            raise ValueError(f"Embedding failed: Invalid response format {result}")

        items = result["data"]
        if not isinstance(items, list):
            raise ValueError("Embedding response data must be a list")
        if expected is not None:
            BaseEmbeddingModel._require_one_vector_per_input(items, expected)

        for item in items:
            if not isinstance(item, dict) or item.get("embedding") is None:
                raise ValueError("Embedding response contains an invalid item")
            if dimension is not None and len(item["embedding"]) != dimension:
                raise ValueError(
                    f"Embedding response returned a vector of dimension {len(item['embedding'])}"
                    f" for a model configured with dimension {dimension}"
                )

        if not any(item.get("index") is not None for item in items):
            return [item["embedding"] for item in items]

        indexed = {}
        for item in items:
            index = item.get("index")
            if index is None:
                raise ValueError("Embedding response mixes indexed and unindexed items")
            if type(index) is not int or not 0 <= index < len(items):
                raise ValueError("Embedding response contains an out-of-range index")
            if index in indexed:
                raise ValueError("Embedding response contains a duplicate index")
            indexed[index] = item["embedding"]

        return [indexed[index] for index in range(len(items))]

    @staticmethod
    def _require_one_vector_per_input(embeddings: list, expected: int, dimension: int | None = None) -> None:
        """校验响应或批次条数与维度，拒绝部分结果和维度不符的批次。"""
        if len(embeddings) != expected:
            raise ValueError(f"Embedding response returned {len(embeddings)} vectors for {expected} inputs")
        if dimension is not None:
            for embedding in embeddings:
                if len(embedding) != dimension:
                    raise ValueError(
                        f"Embedding response returned a vector of dimension {len(embedding)}"
                        f" for a model configured with dimension {dimension}"
                    )


class OtherEmbedding(BaseEmbeddingModel):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def build_payload(self, message: list[str] | str) -> dict:
        return {"model": self.model, "input": message}

    def encode(self, message: list[str] | str) -> list[list[float]]:
        """供同步解析及向量客户端在线程内调用，共用响应解析和重试策略。"""
        payload = self.build_payload(message)
        retry_index = 0

        self._log_long_inputs(message)

        while True:
            try:
                response = requests.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                response.raise_for_status()
                return self._extract_embeddings(
                    response.json(),
                    1 if isinstance(message, str) else len(message),
                    self.dimension,
                )
            except requests.RequestException as e:
                retry = self._prepare_retry(
                    message,
                    retry_index=retry_index,
                    response=getattr(e, "response", None),
                    error=e,
                )
                if retry:
                    retry_index, delay = retry
                    time.sleep(delay)
                    continue

                logger.error(f"Embedding request failed: {e}, {payload}")
                raise ValueError(f"Embedding request failed: {e}")

    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        """供异步摄取调用，HTTP 等待及重试休眠均允许取消。"""
        payload = self.build_payload(message)
        self._log_long_inputs(message)
        async with httpx.AsyncClient() as client:
            retry_index = 0
            while True:
                try:
                    response = await client.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                    response.raise_for_status()
                    return self._extract_embeddings(
                        response.json(),
                        1 if isinstance(message, str) else len(message),
                        self.dimension,
                    )
                except httpx.HTTPStatusError as e:
                    retry = self._prepare_retry(
                        message,
                        retry_index=retry_index,
                        response=e.response,
                        error=e,
                    )
                    if retry:
                        retry_index, delay = retry
                        await asyncio.sleep(delay)
                        continue
                    raise
                except httpx.RequestError as e:
                    retry = self._prepare_retry(message, retry_index=retry_index, error=e)
                    if retry:
                        retry_index, delay = retry
                        await asyncio.sleep(delay)
                        continue
                    raise ValueError(f"Embedding async request failed: {e}, {payload}, {self.base_url=}")

    @staticmethod
    def _retry_delay_seconds(retry_index: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), EMBEDDING_RETRY_MAX_DELAY_SECONDS)
            except ValueError:
                pass
        return min(float(2 ** (retry_index - 1)), EMBEDDING_RETRY_MAX_DELAY_SECONDS)

    def _prepare_retry(
        self,
        message: list[str] | str,
        *,
        retry_index: int,
        response=None,
        error: Exception | None = None,
    ) -> tuple[int, float] | None:
        status_code = getattr(response, "status_code", None)
        response_text = str(getattr(response, "text", "") or "")
        messages = [message] if isinstance(message, str) else message

        if status_code == 400 and response is not None:
            logger.warning(
                "Embedding request returned 400 Bad Request: "
                f"model={self.model}, base_url={self.base_url}, input_count={len(messages)}, "
                f"input_lengths={[len(item) for item in messages]}, body={response_text[:2000]}"
            )

        if status_code == 429:
            max_retries = EMBEDDING_RATE_LIMIT_MAX_RETRIES
        elif status_code in EMBEDDING_RETRYABLE_STATUS_CODES or status_code is None:
            max_retries = EMBEDDING_TRANSIENT_MAX_RETRIES
        else:
            max_retries = 0
        if retry_index >= max_retries:
            return None

        next_retry_index = retry_index + 1
        retry_after = response.headers.get("Retry-After") if response is not None else None
        delay = self._retry_delay_seconds(next_retry_index, retry_after)
        reason = f"status={status_code}" if status_code is not None else f"error={type(error).__name__}"
        logger.warning(
            "Retrying embedding request: "
            f"{reason}, model={self.model}, base_url={self.base_url}, "
            f"retry={next_retry_index}/{max_retries}, delay={delay:.1f}s, "
            f"input_count={len(messages)}, body={response_text[:1000]}"
        )
        return next_retry_index, delay

    @staticmethod
    def _log_long_inputs(message: list[str] | str, threshold: int = 4000) -> None:
        """调试辅助：记录超过字符阈值的 embedding 输入位置与长度，不输出内容以免泄露用户数据。"""
        messages = [message] if isinstance(message, str) else message
        for idx, text in enumerate(messages):
            if text and len(text) > threshold:
                logger.warning(f"超长 embedding 输入 index={idx}, len={len(text)}")


def select_embedding_model(model_id: str):
    info = model_cache.get_model_info(model_id)
    if not info:
        raise ValueError(f"Unknown embedding model spec: {model_id}")

    if info.model_type != "embedding":
        raise ValueError(f"Model {model_id} is not an embedding model (type={info.model_type})")

    logger.info(f"Selecting embedding model: {model_id} (provider_type={info.provider_type})")
    return OtherEmbedding(
        model=info.model_id,
        base_url=info.base_url,
        api_key=info.api_key,
        dimension=info.dimension,
        batch_size=info.batch_size,
    )
