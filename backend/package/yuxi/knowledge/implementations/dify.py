import traceback
from typing import Any

import httpx

from yuxi.knowledge.base import KnowledgeBase
from yuxi.utils import logger


class DifyKB(KnowledgeBase):
    """基于 Dify Dataset Retrieve API 的只读检索知识库实现"""

    def __init__(self, work_dir: str, **kwargs):
        del kwargs
        super().__init__(work_dir)

    @property
    def kb_type(self) -> str:
        return "dify"

    async def _create_kb_instance(self, db_id: str, config: dict) -> Any:
        return None

    async def _initialize_kb_instance(self, instance: Any) -> None:
        return None

    @staticmethod
    def _readonly_error() -> ValueError:
        return ValueError("Dify 知识库为只读检索类型，不支持该操作")

    async def add_file_record(
        self, db_id: str, item: str, params: dict | None = None, operator_id: str | None = None
    ) -> dict:
        raise self._readonly_error()

    async def parse_file(self, db_id: str, file_id: str, operator_id: str | None = None) -> dict:
        raise self._readonly_error()

    async def update_file_params(self, db_id: str, file_id: str, params: dict, operator_id: str | None = None) -> None:
        raise self._readonly_error()

    async def create_folder(self, db_id: str, folder_name: str, parent_id: str | None = None) -> dict:
        raise self._readonly_error()

    async def move_file(self, db_id: str, file_id: str, new_parent_id: str | None) -> dict:
        raise self._readonly_error()

    async def delete_folder(self, db_id: str, folder_id: str) -> None:
        raise self._readonly_error()

    async def index_file(self, db_id: str, file_id: str, operator_id: str | None = None) -> dict:
        raise self._readonly_error()

    async def update_content(self, db_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        raise self._readonly_error()

    async def delete_file(self, db_id: str, file_id: str) -> None:
        raise self._readonly_error()

    async def get_file_basic_info(self, db_id: str, file_id: str) -> dict:
        raise self._readonly_error()

    async def get_file_content(self, db_id: str, file_id: str) -> dict:
        raise self._readonly_error()

    async def open_file_content(self, db_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        del offset, limit
        raise self._readonly_error()

    async def get_file_info(self, db_id: str, file_id: str) -> dict:
        raise self._readonly_error()

    async def aquery(self, query_text: str, db_id: str, agent_call: bool = False, **kwargs) -> list[dict]:
        del agent_call
        metadata = self.databases_meta.get(db_id, {}).get("metadata", {}) or {}
        api_url = str(metadata.get("dify_api_url") or "").strip()
        token = str(metadata.get("dify_token") or "").strip()
        dataset_id = str(metadata.get("dify_dataset_id") or "").strip()

        if not api_url or not token or not dataset_id:
            logger.error(f"Dify config incomplete for db_id={db_id}")
            return []

        query_params = self._get_query_params(db_id)
        merged = {**query_params, **kwargs}

        search_mode = str(merged.get("search_mode", "vector")).lower()
        search_method_map = {
            "vector": "semantic_search",
            "keyword": "keyword_search",
            "hybrid": "hybrid_search",
        }
        search_method = search_method_map.get(search_mode, "semantic_search")

        top_k = int(merged.get("final_top_k", 10))
        top_k = max(top_k, 1)
        score_threshold_enabled = bool(merged.get("score_threshold_enabled", False))
        score_threshold = float(merged.get("similarity_threshold", 0.0))

        payload: dict[str, Any] = {
            "query": query_text,
            "retrieval_model": {
                "search_method": search_method,
                "top_k": top_k,
                # 某些 Dify 部署版本会直接读取该字段，缺失时抛 KeyError
                "reranking_enable": False,
                "score_threshold_enabled": score_threshold_enabled,
            },
        }
        if score_threshold_enabled:
            payload["retrieval_model"]["score_threshold"] = score_threshold

        request_url = f"{api_url.rstrip('/')}/datasets/{dataset_id}/retrieve"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response_json = await self._request_dify(client_payload=payload, request_url=request_url, headers=headers)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Dify query failed for db_id={db_id}: {e}, {traceback.format_exc()}")
            # 一些 Dify 部署版本对 retrieval_model 兼容性较差，失败时降级为仅 query 请求重试一次
            try:
                response_json = await self._request_dify(
                    client_payload={"query": query_text},
                    request_url=request_url,
                    headers=headers,
                )
                logger.warning(f"Dify query fallback to query-only succeeded for db_id={db_id}")
            except Exception as fallback_error:  # noqa: BLE001
                logger.error(
                    f"Dify query fallback failed for db_id={db_id}: {fallback_error}, {traceback.format_exc()}"
                )
                return []

        records = response_json.get("records", []) if isinstance(response_json, dict) else []
        if not isinstance(records, list):
            return []

        results = []
        for record in records:
            if not isinstance(record, dict):
                continue
            segment = record.get("segment") or {}
            if not isinstance(segment, dict):
                continue
            document = segment.get("document") or {}
            if not isinstance(document, dict):
                document = {}

            content = segment.get("content")
            if not content:
                continue

            results.append(
                {
                    "content": content,
                    "score": float(record.get("score") or 0.0),
                    "metadata": {
                        "source": document.get("name") or "Dify",
                        "file_id": document.get("id"),
                        "chunk_id": segment.get("id"),
                        "chunk_index": segment.get("position"),
                    },
                }
            )

        return results

    async def _request_dify(self, client_payload: dict[str, Any], request_url: str, headers: dict[str, str]) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(request_url, json=client_payload, headers=headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                body_preview = response.text[:1000] if response.text else ""
                logger.error(
                    f"Dify HTTP error: status={response.status_code}, url={request_url}, "
                    f"payload_keys={list(client_payload.keys())}, body={body_preview}"
                )
                raise e
            return response.json()

    def get_query_params_config(self, db_id: str, **kwargs) -> dict:
        del db_id, kwargs
        options = [
            {
                "key": "search_mode",
                "label": "检索模式",
                "type": "select",
                "default": "vector",
                "options": [
                    {"value": "vector", "label": "向量检索", "description": "映射为 semantic_search"},
                    {"value": "keyword", "label": "关键词检索", "description": "映射为 keyword_search"},
                    {"value": "hybrid", "label": "混合检索", "description": "映射为 hybrid_search"},
                ],
                "description": "Dify 检索方法映射",
            },
            {
                "key": "final_top_k",
                "label": "最终返回 Chunk 数",
                "type": "number",
                "default": 10,
                "min": 1,
                "max": 100,
                "description": "映射为 Dify retrieval_model.top_k",
            },
            {
                "key": "score_threshold_enabled",
                "label": "启用分数阈值",
                "type": "boolean",
                "default": False,
                "description": "映射为 Dify retrieval_model.score_threshold_enabled",
            },
            {
                "key": "similarity_threshold",
                "label": "分数阈值（0-1）",
                "type": "number",
                "default": 0.0,
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "description": "映射为 Dify retrieval_model.score_threshold",
            },
        ]
        return {"type": "dify", "options": options}
