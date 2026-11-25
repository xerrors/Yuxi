import asyncio
import os
import traceback
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.base import KBNotFoundError, KnowledgeExecutor
from yuxi.modules.knowledge.cache import (
    cache_kb_config,
    get_cached_kb_config,
    kb_config_cache_lock,
    serialize_kb_config,
)
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.schemas import OpenOutputSchema
from yuxi.modules.knowledge.services import documents


class KnowledgeBaseManager:
    """
    知识库管理器

    统一管理多种知识库执行器；业务事实来自 PostgreSQL，Redis 仅缓存最小运行配置。
    """

    # ==== 生命周期与执行器装配 ====

    def __init__(self, work_dir: str):
        """初始化知识库管理器并创建工作目录。"""
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)

        # 知识库实例缓存 {kb_type: kb_instance}
        self.kb_instances: dict[str, KnowledgeExecutor] = {}
        self._kb_instance_lock = asyncio.Lock()

    async def initialize(self):
        """异步初始化已使用的知识库类型执行器；配置在每次操作时按需读取。"""
        await self._initialize_existing_kbs()
        logger.info("KnowledgeBaseManager initialized")

    async def _initialize_existing_kbs(self):
        """初始化已存在的知识库实例"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        rows = await kb_repo.get_all()

        kb_types_in_use = set()
        unsupported_types = set()
        for row in rows:
            kb_type = row.kb_type or "milvus"
            if KnowledgeBaseFactory.is_type_supported(kb_type):
                kb_types_in_use.add(kb_type)
            else:
                logger.warning(f"Skip unsupported knowledge base type during initialization: {kb_type}")
                unsupported_types.add(kb_type)

        logger.info(f"[InitializeKB] 发现 {len(kb_types_in_use)} 种知识库类型: {kb_types_in_use}")

        # 为每种使用中的知识库类型创建共享执行器；失败聚合后阻断启动。
        failures = [f"{kb_type}:unsupported" for kb_type in unsupported_types]
        for kb_type in kb_types_in_use:
            try:
                await self.get_executor_for_type(kb_type)
                logger.info(f"[InitializeKB] {kb_type} 实例已初始化")
            except Exception as e:
                logger.error(f"Failed to initialize {kb_type} knowledge base: {e}")
                logger.error(traceback.format_exc())
                failures.append(f"{kb_type}:{type(e).__name__}")
        if failures:
            raise RuntimeError(f"Used knowledge backends failed to initialize: {', '.join(sorted(failures))}")

    async def get_executor_for_type(self, kb_type: str) -> KnowledgeExecutor:
        """获取或创建类型共享知识库实例，双检锁避免并发重复创建。"""
        if kb_type in self.kb_instances:
            return self.kb_instances[kb_type]

        async with self._kb_instance_lock:
            if kb_type in self.kb_instances:
                return self.kb_instances[kb_type]
            kb_work_dir = os.path.join(self.work_dir, f"{kb_type}_data")
            kb_instance = await asyncio.to_thread(KnowledgeBaseFactory.create, kb_type, kb_work_dir)
            self.kb_instances[kb_type] = kb_instance
            logger.info(f"Created {kb_type} knowledge base instance")
            return kb_instance

    # ==== 运行配置与执行器获取 ====

    async def get_kb_config(self, kb_id: str) -> KnowledgeBaseConfig:
        """读取知识库运行配置，Redis 未命中时回源 PostgreSQL；不存在或类型不支持抛 KBNotFoundError。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        snapshot = await get_cached_kb_config(kb_id)
        if snapshot is None:
            try:
                async with kb_config_cache_lock(kb_id):
                    # 等锁期间写请求可能已刷新配置，必须先重新检查缓存。
                    snapshot = await get_cached_kb_config(kb_id)
                    if snapshot is None:
                        kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
                        if kb is None:
                            raise KBNotFoundError(f"Knowledge base {kb_id} not found")
                        await cache_kb_config(kb)
                        snapshot = serialize_kb_config(kb)
            except (RedisConnectionError, RedisTimeoutError) as exc:
                # Redis 只是缓存；连接故障时只读回源，不尝试写入可能已恢复的旧缓存。
                logger.warning(f"Bypass knowledge base cache: kb_id={kb_id}: {exc}")
                kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
                if kb is None:
                    raise KBNotFoundError(f"Knowledge base {kb_id} not found") from exc
                snapshot = serialize_kb_config(kb)

        kb_type = snapshot.get("kb_type") or "milvus"

        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            raise KBNotFoundError(f"Unsupported knowledge base type: {kb_type}")

        kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
        additional_params = kb_class.normalize_additional_params(snapshot.get("additional_params"))
        additional_params.pop("stats", None)
        return KnowledgeBaseConfig(
            kb_id=kb_id,
            kb_type=kb_type,
            embedding_model_spec=snapshot.get("embedding_model_spec"),
            query_params=snapshot.get("query_params") or kb_class.get_default_query_params(),
            additional_params=additional_params,
        )

    # ==== 知识库删除与外部清理 ====

    async def cleanup_deleted_knowledge_base(self, kb_id: str) -> None:
        """仅为已删除知识库执行可重试外部清理。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        repository = KnowledgeBaseRepository()
        record = await repository.get_by_kb_id(kb_id, include_deleted=True)
        if record is None:
            return
        if record.deleted_at is None:
            raise ValueError("Cannot clean a visible knowledge base")
        executor = await self.get_executor_for_type(record.kb_type)
        await executor.cleanup_knowledge_base_resources(kb_id)
        await repository.purge(kb_id)

    async def get_cleanup_executor(self, kb_id: str) -> KnowledgeExecutor | None:
        """清理任务读取 tombstone，不使用可见配置缓存。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        record = await KnowledgeBaseRepository().get_by_kb_id(kb_id, include_deleted=True)
        if record is None:
            return None
        return await self.get_executor_for_type(record.kb_type)

    # ==== 检索与文档工具入口 ====

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict[str, Any]]:
        """异步查询知识库"""
        config = await self.get_kb_config(kb_id)
        executor = await self.get_executor_for_type(config.kb_type)
        return await executor.aquery(
            query_text,
            kb_id,
            config=config,
            **kwargs,
        )

    async def retrieve(self, kb_id: str, query: str, **options) -> dict:
        """按 kb_id 加载最新运行时元数据并执行检索。"""
        config = await self.get_kb_config(kb_id)
        executor = await self.get_executor_for_type(config.kb_type)
        results = await executor.aquery(
            query,
            kb_id,
            config=config,
            **options,
        )
        return executor.build_search_output(kb_id, results)

    async def get_kb_query_params_config(self, kb_id: str) -> dict:
        """获取知识库查询参数定义，并合并当前保存值。"""
        config = await self.get_kb_config(kb_id)
        kb_class = KnowledgeBaseFactory.get_kb_class(config.kb_type)
        params = kb_class.get_query_params_config()
        for option in params.get("options", []):
            key = option.get("key")
            if key in config.query_options:
                option["default"] = config.query_options[key]
        return params

    async def open_document(
        self,
        kb_id: str,
        file_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict:
        """按行窗口打开文件解析后的 Markdown 内容，返回 OpenOutputSchema 字典。

        不支持文档全文操作的知识库（如 dify 只读源）抛 ValueError。
        """
        kb_info, supports_documents = await documents.get_knowledge_base_document_support(kb_id)
        if kb_info is None:
            raise KBNotFoundError(f"知识库资源 '{kb_id}' 不存在")
        if supports_documents:
            window = await documents.open_file_content(kb_id, file_id, offset=offset, limit=limit)
        else:
            kb_class = KnowledgeBaseFactory.get_kb_class(kb_info.kb_type)
            if not kb_class.supports_document_read:
                raise ValueError("该知识库只支持检索，不支持文档查看")
            config = await self.get_kb_config(kb_id)
            executor = await self.get_executor_for_type(config.kb_type)
            window = await executor.open_document(kb_id, file_id, config=config, offset=offset, limit=limit)
        return OpenOutputSchema(kb_id=kb_id, file_id=file_id, **window).model_dump()
