import asyncio
import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

import aiohttp
import numpy as np

from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url, logger


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


class BaseReranker(ABC):
    def __init__(self, model_name, api_key, base_url, **kwargs):
        self.url = get_docker_safe_url(base_url)
        self.model = model_name
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.session: aiohttp.ClientSession | None = None
        self.timeout = aiohttp.ClientTimeout(total=30)
        self.parameters: dict[str, Any] = dict(kwargs.get("parameters", {}))

    async def _ensure_session(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers, timeout=self.timeout)

    @abstractmethod
    def _build_payload(self, query: str, documents: list[str], max_length: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def _extract_results(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def acompute_score(
        self,
        sentence_pairs: Sequence[Sequence[str]],
        batch_size: int = 32,
        max_length: int = 512,
        normalize: bool = True,
    ) -> list[float]:
        if not sentence_pairs or len(sentence_pairs) < 2:
            return []

        query, sentences = sentence_pairs[0], sentence_pairs[1]
        documents = [sentences] if isinstance(sentences, str) else list(sentences)

        if not documents:
            return []

        await self._ensure_session()

        all_scores: list[float] = []
        batch_size = max(1, int(batch_size))
        total_batches = (len(documents) + batch_size - 1) // batch_size

        for batch_no, start in enumerate(range(0, len(documents), batch_size), start=1):
            batch = documents[start : start + batch_size]
            try:
                scores = await self._batch_rerank(query, batch, max_length=max_length)
                all_scores.extend(scores)
                logger.debug(f"Reranking batch {batch_no}/{total_batches} completed")
            except Exception as exc:
                logger.error(f"Reranking batch {batch_no} failed: {exc}")
                all_scores.extend([0.5] * len(batch))

        if normalize:
            all_scores = [float(sigmoid(score)) for score in all_scores]

        return all_scores

    async def _batch_rerank(self, query: str, documents: Iterable[str], max_length: int) -> list[float]:
        docs = list(documents)
        if not docs:
            return []

        payload = self._build_payload(query, docs, max_length)

        await self._ensure_session()
        assert self.session is not None

        try:
            async with self.session.post(self.url, json=payload) as response:
                response.raise_for_status()
                result: dict[str, Any] = await response.json()
        except TimeoutError as exc:
            total_timeout = self.timeout.total if self.timeout else 0.0
            logger.error(f"Reranking request timeout after {total_timeout:.1f}s")
            raise exc
        except aiohttp.ClientError as exc:
            logger.error(f"Reranking request failed: {exc}")
            raise exc

        results = self._extract_results(result)
        if len(results) != len(docs):
            raise ValueError("Rerank response must contain one result per document")

        scores = [0.0] * len(docs)
        seen = set()
        for entry in results:
            if not isinstance(entry, dict):
                raise ValueError("Rerank response contains an invalid result")
            index = entry.get("index")
            if type(index) is not int or not 0 <= index < len(docs) or index in seen:
                raise ValueError("Rerank response contains an invalid or duplicate index")

            raw_score = entry.get("relevance_score")
            if isinstance(raw_score, bool):
                raise ValueError("Rerank response contains an invalid relevance_score")
            try:
                score = float(raw_score)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Rerank response contains an invalid relevance_score") from None
            if not math.isfinite(score):
                raise ValueError("Rerank response contains a non-finite relevance_score")

            seen.add(index)
            scores[index] = score

        return scores

    def compute_score(self, sentence_pairs, batch_size=256, max_length=512, normalize=False):
        try:
            _ = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.acompute_score(sentence_pairs, batch_size, max_length, normalize))
        raise RuntimeError("compute_score cannot be used while an event loop is running. Use acompute_score instead.")

    async def test_connection(self) -> tuple[bool, str]:
        try:
            scores = await self._batch_rerank("test query", ["test document"], max_length=128)
            if scores:
                return True, "连接正常"
            return False, "响应无效"
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Rerank connection test failed: {error_msg}")
            return False, error_msg
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    def __del__(self) -> None:
        if self.session and not self.session.closed:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                asyncio.run(self.aclose())
                return

            if loop.is_closed():
                asyncio.run(self.aclose())
            elif not loop.is_running():
                loop.run_until_complete(self.aclose())


class OpenAIReranker(BaseReranker):
    def _build_payload(self, query: str, documents: list[str], max_length: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "query": query,
            "documents": documents,
            "max_chunks_per_doc": max_length,
        }

    def _extract_results(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        return list(result.get("results", []))


class DashscopeReranker(BaseReranker):
    def _build_payload(self, query: str, documents: list[str], max_length: int) -> dict[str, Any]:
        params = {"top_n": len(documents), "return_documents": False}
        instruct = self.parameters.get("instruct")
        if instruct:
            params["instruct"] = instruct
        return {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": params,
        }

    def _extract_results(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        return list(result.get("output", {}).get("results", []))


def get_reranker(model_id: str, **kwargs):
    info = model_cache.get_model_info(model_id)
    if not info:
        raise ValueError(f"Unknown reranker model spec: {model_id}")
    if info.model_type != "rerank":
        raise ValueError(f"Model {model_id} is not a rerank model (type={info.model_type})")
    if not info.api_key:
        raise ValueError(f"{info.display_name} api_key is required")

    parameters = dict(info.extra.get("parameters") or {})
    parameters.update(kwargs.pop("parameters", {}) or {})
    reranker_kwargs = {**kwargs, "parameters": parameters}
    protocol = info.extra.get("rerank_protocol") or info.extra.get("protocol")

    if protocol == "dashscope":
        return DashscopeReranker(
            model_name=info.model_id,
            api_key=info.api_key,
            base_url=info.base_url,
            **reranker_kwargs,
        )
    return OpenAIReranker(
        model_name=info.model_id,
        api_key=info.api_key,
        base_url=info.base_url,
        **reranker_kwargs,
    )
