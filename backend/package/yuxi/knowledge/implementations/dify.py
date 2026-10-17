import traceback
from typing import Any

import httpx

from yuxi.knowledge.implementations.read_only_connectors import ReadOnlyConnectors
from yuxi.knowledge.read_models import KnowledgeBaseConfig
from yuxi.utils import logger

DIFY_REQUIRED_PARAMS = ("dify_api_url", "dify_token", "dify_dataset_id")


class DifyKB(ReadOnlyConnectors):
    """基于 Dify Dataset Retrieve API 的只读检索知识库实现"""

    kb_type = "dify"
    name = "Dify"
    description = "连接 Dify Dataset 的只读检索知识库"

    def __init__(self, work_dir: str, **kwargs):
        del kwargs
        super().__init__(work_dir)

    @classmethod
    def get_create_params_config(cls) -> dict[str, Any]:
        return {
            "options": [
                {
                    "key": "dify_api_url",
                    "label": "Dify API URL",
                    "type": "text",
                    "required": True,
                    "placeholder": "例如: https://api.dify.ai/v1",
                    "description": "Dify API 地址，必须以 /v1 结尾",
                },
                {
                    "key": "dify_token",
                    "label": "Dify Token",
                    "type": "password",
                    "required": True,
                    "placeholder": "请输入 Dify API Token",
                },
                {
                    "key": "dify_dataset_id",
                    "label": "Dataset ID",
                    "type": "text",
                    "required": True,
                    "placeholder": "请输入 Dify dataset_id",
                },
            ]
        }

    @classmethod
    def validate_additional_params(cls, additional_params: dict | None) -> dict:
        params = dict(additional_params or {})
        missing_fields = [field for field in DIFY_REQUIRED_PARAMS if not str(params.get(field) or "").strip()]
        if missing_fields:
            raise ValueError(f"Dify 参数缺失: {', '.join(missing_fields)}")

        params["dify_api_url"] = str(params.get("dify_api_url") or "").strip()
        params["dify_token"] = str(params.get("dify_token") or "").strip()
        params["dify_dataset_id"] = str(params.get("dify_dataset_id") or "").strip()
        if not params["dify_api_url"].endswith("/v1"):
            raise ValueError("Dify api_url 必须以 /v1 结尾")
        return params

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        raise_on_error: bool = False,
        agent_call: bool = False,
        **kwargs,
    ) -> list[dict]:
        del agent_call
        additional_params = config.additional_params
        api_url = str(additional_params.get("dify_api_url") or "").strip()
        token = str(additional_params.get("dify_token") or "").strip()
        dataset_id = str(additional_params.get("dify_dataset_id") or "").strip()

        if not api_url or not token or not dataset_id:
            logger.error(f"Dify config incomplete for kb_id={kb_id}")
            if raise_on_error:
                raise ValueError(f"Dify 配置缺失，无法查询 kb_id={kb_id}")
            return []

        merged = {**config.query_options, **kwargs}

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
            logger.error(f"Dify query failed for kb_id={kb_id}: {e}, {traceback.format_exc()}")
            # 一些 Dify 部署版本对 retrieval_model 兼容性较差，失败时降级为仅 query 请求重试一次
            try:
                response_json = await self._request_dify(
                    client_payload={"query": query_text},
                    request_url=request_url,
                    headers=headers,
                )
                logger.warning(f"Dify query fallback to query-only succeeded for kb_id={kb_id}")
            except Exception as fallback_error:  # noqa: BLE001
                logger.error(
                    f"Dify query fallback failed for kb_id={kb_id}: {fallback_error}, {traceback.format_exc()}"
                )
                if raise_on_error:
                    raise e
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

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        del kb_id, kwargs
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
