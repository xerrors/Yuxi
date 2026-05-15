import asyncio
import json
import os
from abc import ABC, abstractmethod

import httpx
import requests

from yuxi import config
from yuxi.services.model_cache import is_v2_spec_format
from yuxi.utils import get_docker_safe_url, hashstr, logger


class BaseEmbeddingModel(ABC):
    def __init__(
        self,
        model=None,
        name=None,
        dimension=None,
        url=None,
        base_url=None,
        api_key=None,
        model_id=None,
        batch_size=40,
    ):
        """
        Args:
            model: 模型名称，冗余设计，同name
            name: 模型名称，冗余设计，同model
            dimension: 维度
            url: 请求URL，冗余设计，同base_url
            base_url: 基础URL，请求URL，冗余设计，同url
            api_key: 请求API密钥
            batch_size: 模型推荐的批量向量化大小
        """
        base_url = base_url or url
        self.model = model or name
        self.dimension = dimension
        self.base_url = get_docker_safe_url(base_url)
        self.api_key = os.getenv(api_key, api_key)
        self.batch_size = int(batch_size or 40)
        self.embed_state = {}

    @abstractmethod
    def encode(self, message: list[str] | str) -> list[list[float]]:
        """同步编码"""
        raise NotImplementedError("Subclasses must implement this method")

    def encode_queries(self, queries: list[str] | str) -> list[list[float]]:
        """等同于encode"""
        return self.encode(queries)

    @abstractmethod
    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        """异步编码"""
        raise NotImplementedError("Subclasses must implement this method")

    async def aencode_queries(self, queries: list[str] | str) -> list[list[float]]:
        """等同于aencode"""
        return await self.aencode(queries)

    def batch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        # logger.info(f"Batch encoding {len(messages)} messages")
        batch_size = batch_size or self.batch_size
        data = []
        task_id = None
        if len(messages) > batch_size:
            task_id = hashstr(messages)
            self.embed_state[task_id] = {"status": "in-progress", "total": len(messages), "progress": 0}

        for i in range(0, len(messages), batch_size):
            group_msg = messages[i : i + batch_size]
            logger.info(f"Encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
            response = self.encode(group_msg)
            data.extend(response)
            if task_id:
                self.embed_state[task_id]["progress"] = i + len(group_msg)

        if task_id:
            self.embed_state[task_id]["status"] = "completed"

        return data

    async def abatch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or self.batch_size
        data = []
        task_id = None
        logger.info(f"[INFO] Embedding abatch_encode: msg_len={len(messages)}, batch_size={batch_size}")
        if len(messages) > batch_size:
            task_id = hashstr(messages)
            self.embed_state[task_id] = {"status": "in-progress", "total": len(messages), "progress": 0}

        # 保留原有逻辑：
        # 使用 asyncio.gather 并发执行所有 embedding 批次请求：
        # tasks = []
        # for i in range(0, len(messages), batch_size):
        #     group_msg = messages[i : i + batch_size]
        #     tasks.append(self.aencode(group_msg))

        # results = await asyncio.gather(*tasks)
        # for res in results:
        #     data.extend(res)

        # if task_id:
        #     self.embed_state[task_id]["progress"] = len(messages)
        #     self.embed_state[task_id]["status"] = "completed"

        # return data

        for i in range(0, len(messages), batch_size):
            group_msg = messages[i : i + batch_size]
            logger.info(f"Async encoding [{i}/{len(messages)}] messages (bsz={batch_size})")
            res = await self.aencode(group_msg)
            data.extend(res)
            if task_id:
                self.embed_state[task_id]["progress"] = i + len(group_msg)

        if task_id:
            self.embed_state[task_id]["status"] = "completed"

        return data

    async def test_connection(self) -> tuple[bool, str]:
        """
        测试embedding模型的连接性

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # 使用简单的测试文本
            test_text = ["Hello world"]
            await self.aencode(test_text)
            return True, "连接正常"
        except Exception as e:
            error_msg = str(e)
            error_msg += f", maybe you can check the `{self.base_url}` end with /embeddings as examples."
            logger.error(error_msg)
            return False, error_msg


class OllamaEmbedding(BaseEmbeddingModel):
    """
    Ollama Embedding Model
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.base_url = self.base_url or get_docker_safe_url("http://localhost:11434/api/embed")

    def encode(self, message: list[str] | str) -> list[list[float]]:
        if isinstance(message, str):
            message = [message]

        payload = {"model": self.model, "input": message}
        try:
            response = requests.post(self.base_url, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            if "embeddings" not in result:
                raise ValueError(f"Ollama Embedding failed: Invalid response format {result}")
            return result["embeddings"]
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"Ollama Embedding request failed: {e}, {payload}")
            raise ValueError(f"Ollama Embedding request failed: {e}")

    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        if isinstance(message, str):
            message = [message]

        payload = {"model": self.model, "input": message}
        async with httpx.AsyncClient() as client:
            try:
                print(f"\n\n\nOllama Embedding request: {payload}\n\n\n")
                response = await client.post(self.base_url, json=payload, timeout=60)
                response.raise_for_status()
                result = response.json()
                if "embeddings" not in result:
                    raise ValueError(f"Ollama Embedding failed: Invalid response format {result}")
                return result["embeddings"]
            except (httpx.RequestError, json.JSONDecodeError) as e:
                raise ValueError(f"Ollama Embedding async request failed: {e}, {payload}, {self.base_url=}")


class OtherEmbedding(BaseEmbeddingModel):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def build_payload(self, message: list[str] | str) -> dict:
        payload = {"model": self.model, "input": message}
        if self.dimension:
            payload["dimensions"] = self.dimension
        if self.batch_size:
            payload["batch_size"] = self.batch_size
        return payload

    def encode(self, message: list[str] | str) -> list[list[float]]:
        if isinstance(message, list) and len(message) > self.batch_size:
            return self.batch_encode(message, batch_size=self.batch_size)
        payload = self.build_payload(message)
        try:
            response = requests.post(self.base_url, json=payload, headers=self.headers, timeout=60)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or "data" not in result:
                raise ValueError(f"Other Embedding failed: Invalid response format {result}")
            return [item["embedding"] for item in result["data"]]
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"Other Embedding request failed: {e}, {payload}")
            raise ValueError(f"Other Embedding request failed: {e}")

    async def aencode(self, message: list[str] | str) -> list[list[float]]:
        if isinstance(message, list) and len(message) > self.batch_size:
            return await self.abatch_encode(message, batch_size=self.batch_size)
        payload = self.build_payload(message)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.base_url, json=payload, headers=self.headers, timeout=60)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or "data" not in result:
                    raise ValueError(f"Other Embedding failed: Invalid response format {result}")
                return [item["embedding"] for item in result["data"]]
            except (httpx.RequestError, json.JSONDecodeError) as e:
                raise ValueError(f"Other Embedding async request failed: {e}, {payload}, {self.base_url=}")
            except httpx.HTTPStatusError as e:
                raise ValueError(f"Other Embedding async request failed: {e}, {payload}, {self.base_url=}")

async def test_embedding_model_status(model_id: str) -> dict:
    """
    测试指定embedding模型的状态

    Args:
        model_id: 模型ID，格式为 "provider/model_name"

    Returns:
        dict: 包含状态信息的字典
    """
    try:
        support_embed_models = config.embed_model_names.keys()
        if model_id not in support_embed_models:
            return {"model_id": model_id, "status": "unsupported", "message": f"不支持的模型: {model_id}"}

        # 选择并创建模型实例
        model = select_embedding_model(model_id)

        # 测试连接
        success, message = await model.test_connection()

        return {
            "model_id": model_id,
            "status": "available" if success else "unavailable",
            "message": message if not success else "连接正常",
            "dimension": model.dimension,
        }

    except Exception as e:
        logger.warning(f"测试embedding模型状态失败 {model_id}: {e}")
        return {"model_id": model_id, "status": "error", "message": str(e)}


async def test_all_embedding_models_status() -> dict:
    """
    测试所有支持的embedding模型状态

    Returns:
        dict: 包含所有模型状态的字典
    """
    support_embed_models = list(config.embed_model_names.keys())
    results = {}

    # 并发测试所有模型
    tasks = [test_embedding_model_status(model_id) for model_id in support_embed_models]
    model_statuses = await asyncio.gather(*tasks, return_exceptions=True)

    for i, status in enumerate(model_statuses):
        if isinstance(status, Exception):
            model_id = support_embed_models[i]
            results[model_id] = {"model_id": model_id, "status": "error", "message": str(status)}
        else:
            results[status["model_id"]] = status

    return {
        "models": results,
        "total": len(support_embed_models),
        "available": len([m for m in results.values() if m["status"] == "available"]),
    }


def get_embedding_model_info_by_id(model_id: str) -> dict:
    """
    通过模型ID获取Embedding模型的标准化配置信息（统一入口，V1/V2 自动识别）。

    V1 格式: provider/model_name（如 "siliconflow/BAAI/bge-m3"），从 config.embed_model_names 查找
    V2 格式: provider_id:model_id（如 "siliconflow:BAAI/bge-m3"），从 model_cache 查找

    Returns:
        dict: 包含 base_url、api_key、name、dimension、model_id、batch_size 等字段的配置字典。
              api_key 已从环境变量解析为实际值。
    """
    # V2 spec 检测
    if isinstance(model_id, str) and is_v2_spec_format(model_id):
        from yuxi.services.model_cache import model_cache

        info = model_cache.get_model_info(model_id)
        if info:
            logger.info(f"Loaded v2 embedding model info for {model_id}")
            return {
                "name": info.display_name,
                "dimension": info.dimension,
                "base_url": info.base_url,
                "api_key": info.api_key,
                "model_id": info.spec,
                "batch_size": info.batch_size,
            }

    support_embed_models = config.embed_model_names.keys()
    assert model_id in support_embed_models, f"Unsupported embed model: {model_id}, only support {support_embed_models}"

    embed_config = config.embed_model_names[model_id].model_dump()

    # 解析 api_key：如果值本身是环境变量名，则从环境变量获取实际值
    embed_config["api_key"] = os.getenv(embed_config["api_key"]) or embed_config["api_key"]

    logger.info(f"Loaded embedding model info for {model_id}")
    return embed_config


def select_embedding_model(model_id):
    """选择 Embedding 模型（V1/V2 自动识别）。

    V1 格式: provider/model_name（斜杠分隔）
    V2 格式: provider_id:model_id（冒号分隔）
    """
    # V2 spec 检测：第一个特殊字符为冒号且存在于缓存中
    if isinstance(model_id, str) and is_v2_spec_format(model_id):
        from yuxi.services.model_cache import model_cache

        if model_cache.is_v2_spec(model_id):
            return select_embedding_model_v2(model_id)

    provider, model_name = model_id.split("/", 1) if model_id else ("", "")
    logger.info(f"Loading embedding model {model_id}")
    if provider == "local":
        raise ValueError("Local embedding model is not supported, please use other embedding models")

    embed_config = get_embedding_model_info_by_id(model_id)

    if provider == "ollama":
        model = OllamaEmbedding(**embed_config)
    else:
        model = OtherEmbedding(**embed_config)

    return model


def select_embedding_model_v2(spec: str):
    """根据 v2 spec（provider_id:model_id）选择 Embedding 模型。

    v2 spec 格式使用冒号分隔，如: siliconflow:BAAI/bge-m3
    数据来源为数据库中的 model_providers 表，通过全局缓存访问。
    """
    from yuxi.services.model_cache import model_cache

    info = model_cache.get_model_info(spec)
    if not info:
        raise ValueError(f"Unknown v2 embedding model spec: {spec}")

    if info.model_type != "embedding":
        raise ValueError(f"Model {spec} is not an embedding model (type={info.model_type})")

    logger.info(f"Selecting v2 embedding model: {spec} (provider_type={info.provider_type})")

    if info.provider_type == "ollama":
        return OllamaEmbedding(
            model=info.model_id,
            base_url=info.base_url,
            api_key=info.api_key,
            dimension=info.dimension,
            batch_size=info.batch_size,
        )
    else:
        return OtherEmbedding(
            model=info.model_id,
            base_url=info.base_url,
            api_key=info.api_key,
            dimension=info.dimension,
            batch_size=info.batch_size,
        )


async def test_embedding_model_status_by_spec(spec: str) -> dict:
    """根据 full spec 测试 Embedding 模型状态（自动识别 V1/V2）。

    V1 spec 格式: provider/model_name（斜杠分隔）
    V2 spec 格式: provider_id:model_id（冒号分隔）
    """
    try:
        if is_v2_spec_format(spec):
            from yuxi.services.model_cache import model_cache

            if model_cache.is_v2_spec(spec):
                model = select_embedding_model_v2(spec)
            else:
                return {"spec": spec, "status": "unsupported", "message": f"不支持的 V2 模型: {spec}"}
        else:
            model = select_embedding_model(spec)

        success, message = await model.test_connection()
        return {
            "spec": spec,
            "status": "available" if success else "unavailable",
            "message": "连接正常" if success else message,
        }
    except Exception as e:
        logger.warning(f"测试 Embedding 模型状态失败 {spec}: {e}")
        return {"spec": spec, "status": "error", "message": str(e)}
