import asyncio
import os
import secrets
import string
from collections.abc import Awaitable
from dataclasses import replace
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from yuxi.knowledge.base import KBNameConflictError, KBNotFoundError, KnowledgeBase
from yuxi.knowledge.cache import (
    cache_kb_config,
    get_cached_kb_config,
    kb_config_cache_lock,
    serialize_kb_config,
)
from yuxi.knowledge.chunking.ragflow_like.presets import deep_merge
from yuxi.knowledge.factory import KnowledgeBaseFactory
from yuxi.knowledge.read_models import (
    KnowledgeBaseConfig,
    KnowledgeBaseDetail,
    KnowledgeBaseSummary,
)
from yuxi.knowledge.schemas import FindOutputSchema, OpenOutputSchema
from yuxi.knowledge.utils.security import redact_sensitive_params
from yuxi.permissions import ResourcePermission, normalize_permission_config, resolve_knowledge_base_permission
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat

KB_FILE_SEARCH_SCAN_LIMIT = 5000


class KnowledgeBaseManager:
    """
    知识库管理器

    统一管理多种知识库执行器；业务事实来自 PostgreSQL，Redis 仅缓存最小运行配置。
    """

    def __init__(self, work_dir: str):
        """
        初始化知识库管理器

        Args:
            work_dir: 工作目录
        """
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)

        # 知识库实例缓存 {kb_type: kb_instance}
        self.kb_instances: dict[str, KnowledgeBase] = {}

    async def initialize(self):
        """异步初始化"""
        # 初始化已使用的知识库类型执行器；配置在每次操作时按需读取。
        await self._initialize_existing_kbs()
        logger.info("KnowledgeBaseManager initialized")

    async def _initialize_existing_kbs(self):
        """初始化已存在的知识库实例"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

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

        # 为每种使用中的知识库类型创建共享执行器。
        failures = [f"{kb_type}:unsupported" for kb_type in unsupported_types]
        for kb_type in kb_types_in_use:
            if not KnowledgeBaseFactory.is_type_supported(kb_type):
                logger.warning(f"[InitializeKB] Skip initialization for unsupported knowledge base type: {kb_type}")
                continue
            try:
                self._get_or_create_kb_instance(kb_type)
                logger.info(f"[InitializeKB] {kb_type} 实例已初始化")
            except Exception as e:
                logger.error(f"Failed to initialize {kb_type} knowledge base: {e}")
                import traceback

                logger.error(traceback.format_exc())
                failures.append(f"{kb_type}:{type(e).__name__}")
        if failures:
            raise RuntimeError(f"Used knowledge backends failed to initialize: {', '.join(sorted(failures))}")

    def _get_or_create_kb_instance(self, kb_type: str) -> KnowledgeBase:
        """
        获取或创建知识库实例

        Args:
            kb_type: 知识库类型

        Returns:
            知识库实例
        """
        if kb_type in self.kb_instances:
            return self.kb_instances[kb_type]

        # 创建新的知识库实例
        kb_work_dir = os.path.join(self.work_dir, f"{kb_type}_data")
        kb_instance = KnowledgeBaseFactory.create(kb_type, kb_work_dir)

        self.kb_instances[kb_type] = kb_instance
        logger.info(f"Created {kb_type} knowledge base instance")
        return kb_instance

    async def move_file(self, kb_id: str, file_id: str, new_parent_id: str | None) -> dict:
        """移动文件或文件夹。"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.move_file(kb_id, file_id, new_parent_id)

    async def rename_folder(self, kb_id: str, folder_id: str, folder_name: str) -> dict:
        """重命名真实文件夹。"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.rename_folder(kb_id, folder_id, folder_name)

    async def get_kb_config(self, kb_id: str) -> KnowledgeBaseConfig:
        """读取知识库运行配置，Redis 未命中时回源 PostgreSQL。

        Args:
            kb_id: 数据库ID

        Returns:
            规范化后的知识库运行配置

        Raises:
            KBNotFoundError: 数据库不存在或知识库类型不支持
        """
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        snapshot = await get_cached_kb_config(kb_id)
        if snapshot is None:
            try:
                async with kb_config_cache_lock(kb_id):
                    # 等锁期间写请求可能已刷新配置，必须先重新检查缓存。
                    snapshot = await get_cached_kb_config(kb_id)
                    if snapshot is None:
                        kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
                        if kb is None:
                            raise KBNotFoundError(f"Database {kb_id} not found")
                        await cache_kb_config(kb)
                        snapshot = serialize_kb_config(kb)
            except (RedisConnectionError, RedisTimeoutError) as exc:
                # Redis 只是缓存；连接故障时只读回源，不尝试写入可能已恢复的旧缓存。
                logger.warning(f"Bypass knowledge base cache: kb_id={kb_id}: {exc}")
                kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
                if kb is None:
                    raise KBNotFoundError(f"Database {kb_id} not found") from exc
                snapshot = serialize_kb_config(kb)

        kb_type = snapshot.get("kb_type") or "milvus"

        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            raise KBNotFoundError(f"Unsupported knowledge base type: {kb_type}")

        executor = self._get_or_create_kb_instance(kb_type)
        additional_params = executor.normalize_additional_params(snapshot.get("additional_params"))
        additional_params.pop("stats", None)
        return KnowledgeBaseConfig(
            kb_id=kb_id,
            kb_type=kb_type,
            embedding_model_spec=snapshot.get("embedding_model_spec"),
            query_params=snapshot.get("query_params") or executor.get_default_query_params(kb_id),
            additional_params=additional_params,
        )

    async def get_kb_executor(self, kb_id: str) -> KnowledgeBase:
        """获取知识库类型执行器。"""
        config = await self.get_kb_config(kb_id)
        return self._get_or_create_kb_instance(config.kb_type)

    # =============================================================================
    # 统一的外部接口
    # =============================================================================

    def _normalize_share_config(
        self,
        share_config: dict | None,
        *,
        user_uid: str | None = None,
        department_id: int | str | None = None,
    ) -> dict:
        if share_config is None:
            return {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            }

        if share_config and share_config.get("version") == 2:
            normalized = normalize_permission_config(
                share_config,
                strict=user_uid is not None or department_id is not None,
            )
            if normalized["read_scope"] is None and (user_uid is not None or department_id is not None):
                raise ValueError("知识库必须设置读取范围")
            read_scope = normalized["read_scope"]
            if read_scope and read_scope["access_level"] == "department" and department_id is not None:
                read_scope["department_ids"] = sorted({*read_scope["department_ids"], int(department_id)})
            elif read_scope and read_scope["access_level"] == "user" and user_uid:
                read_scope["user_uids"] = sorted({*read_scope["user_uids"], str(user_uid)})
            return normalized

        raise ValueError("知识库共享配置必须使用 version 2")

    @staticmethod
    def _normalize_database_stats(stats: dict | None) -> dict[str, int]:
        """规范化知识库聚合统计字段。"""
        normalized = {
            "file_count": 0,
            "folder_count": 0,
            "row_count": 0,
            "total_size": 0,
            "chunk_count": 0,
            "token_count": 0,
            "pending_parse_count": 0,
            "pending_index_count": 0,
            "processing_count": 0,
        }
        if not isinstance(stats, dict):
            return normalized

        for key in normalized:
            try:
                normalized[key] = max(int(stats.get(key) or 0), 0)
            except (TypeError, ValueError):
                normalized[key] = 0
        return normalized

    async def _refresh_database_stats(
        self,
        kb_id: str,
        stats: dict[str, int] | None = None,
    ) -> dict[str, int]:
        """刷新并持久化知识库聚合统计。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        if stats is None:
            stats = await self._get_database_file_stats(kb_id)
        normalized_stats = self._normalize_database_stats(stats)
        kb = await KnowledgeBaseRepository().update_stats(kb_id, normalized_stats)
        if kb is None:
            raise KBNotFoundError(f"Database {kb_id} not found")
        return normalized_stats

    async def _run_with_stats_refresh(self, kb_id: str, operation: Awaitable[Any]) -> Any:
        """执行文件操作并刷新统计，同时保留原始操作异常。"""
        try:
            result = await operation
        except (Exception, asyncio.CancelledError):
            try:
                await self._refresh_database_stats(kb_id)
            except (Exception, asyncio.CancelledError) as refresh_error:
                logger.error(f"Refresh database stats after failed operation: kb_id={kb_id}: {refresh_error}")
            raise

        await self._refresh_database_stats(kb_id)
        return result

    def _database_read_fields(
        self,
        row: Any,
        *,
        stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """将知识库记录转换为 Summary 与 Detail 共用的规范字段。"""
        kb_type = row.kb_type or "milvus"
        kb_class = (
            KnowledgeBaseFactory.get_kb_class(kb_type) if KnowledgeBaseFactory.is_type_supported(kb_type) else None
        )
        additional_params = (
            kb_class.normalize_additional_params(row.additional_params)
            if kb_class
            else dict(row.additional_params or {})
        )
        persisted_stats = additional_params.pop("stats", None)
        normalized_stats = self._normalize_database_stats(stats if stats is not None else persisted_stats)

        return {
            "kb_id": row.kb_id,
            "name": row.name,
            "description": row.description,
            "kb_type": kb_type,
            "embedding_model_spec": row.embedding_model_spec,
            "llm_model_spec": row.llm_model_spec,
            "query_params": dict(row.query_params or {}),
            "additional_params": additional_params,
            "share_config": self._normalize_share_config(row.share_config),
            "created_by": row.created_by,
            "created_at": row.created_at,
            **normalized_stats,
        }

    async def get_databases(self) -> list[KnowledgeBaseSummary]:
        """获取所有知识库摘要。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        rows = await kb_repo.get_all()
        all_databases: list[KnowledgeBaseSummary] = []
        for row in rows:
            kb_type = row.kb_type or "milvus"
            if not KnowledgeBaseFactory.is_type_supported(kb_type):
                logger.warning(f"Skip unsupported database: kb_id={row.kb_id}, kb_type={kb_type}")
                continue

            # 单条记录元数据不合法时只跳过该条，避免一条坏记录隐藏整个列表。
            try:
                database = KnowledgeBaseSummary(**self._database_read_fields(row))
            except Exception as e:
                logger.warning(f"Skip database with invalid metadata: kb_id={row.kb_id}, kb_type={kb_type}: {e}")
                continue
            all_databases.append(database)
        return all_databases

    @staticmethod
    def _database_info_accessible(user: dict, db_info: Any) -> bool:
        return resolve_knowledge_base_permission(user, db_info) != ResourcePermission.NONE

    async def check_accessible(self, user: dict, kb_id: str) -> bool:
        """检查用户是否有权限访问数据库

        Args:
            user: 用户信息字典
            kb_id: 数据库ID

        Returns:
            bool: 是否有权限
        """
        # 超级管理员有权访问所有
        if user.get("role") == "superadmin":
            return True

        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_kb_id(kb_id)
        if kb is None:
            return False

        return self._database_info_accessible(user, kb)

    async def get_accessible_database_info_by_uid(self, uid: str, kb_id: str) -> KnowledgeBaseSummary | None:
        """按 uid 获取一个可访问知识库的信息，找不到或无权访问时返回 None。"""
        normalized_kb_id = str(kb_id or "").strip()
        if not normalized_kb_id:
            return None

        databases = await self.get_databases_by_uid(uid)
        for database in databases:
            if database.kb_id == normalized_kb_id:
                return database
        return None

    def database_type_supports_documents(self, kb_type: str | None) -> bool:
        """判断知识库类型是否支持文档全文操作。"""
        normalized_type = (kb_type or "milvus").lower()
        if not KnowledgeBaseFactory.is_type_supported(normalized_type):
            return False
        return KnowledgeBaseFactory.get_kb_class(normalized_type).supports_documents

    async def get_database_document_support(self, kb_id: str) -> tuple[KnowledgeBaseDetail | None, bool]:
        """返回知识库信息及其是否支持文档全文操作。"""
        db_info = await self.get_database_info(kb_id)
        if not db_info:
            return None, False
        return db_info, self.database_type_supports_documents(db_info.kb_type)

    async def get_databases_by_uid(self, uid: str) -> list[KnowledgeBaseSummary]:
        """根据 uid 获取知识库列表"""
        from yuxi.repositories.user_repository import UserRepository

        # 通过数据库获取用户信息
        user_repo = UserRepository()
        user: User | None = await user_repo.get_by_uid(uid)
        if not user:
            logger.warning(f"User not found: {uid}")
            return []
        return await self.get_databases_by_user(user)

    async def get_databases_by_user(self, user: User | dict) -> list[KnowledgeBaseSummary]:
        """根据用户权限获取知识库列表"""

        # 构建用户信息字典（支持 User 对象或 dict）
        if isinstance(user, dict):
            user_info = user
        else:
            user_info = {
                "uid": user.uid,
                "role": user.role,
                "department_id": user.department_id,
            }

        user_role = user_info.get("role")
        user_dept = user_info.get("department_id")
        logger.info(f"Getting databases for user with role {user_role} and department {user_dept}")

        all_databases = await self.get_databases()

        # 超级管理员可以看到所有知识库
        filtered_databases: list[KnowledgeBaseSummary] = []
        for database in all_databases:
            permission = resolve_knowledge_base_permission(user_info, database)
            if permission == ResourcePermission.NONE:
                continue
            additional_params = database.additional_params
            if permission == ResourcePermission.READ:
                additional_params = redact_sensitive_params(additional_params)
            filtered_databases.append(
                replace(
                    database,
                    additional_params=additional_params,
                    effective_permission=permission,
                )
            )

        return filtered_databases

    async def database_name_exists(self, database_name: str) -> bool:
        """检查知识库名称是否已存在"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
        from yuxi.storage.postgres.manager import pg_manager

        # 确保 pg_manager 已初始化
        if not pg_manager._initialized:
            pg_manager.initialize()

        kb_repo = KnowledgeBaseRepository()
        rows = await kb_repo.get_all()
        for row in rows:
            if (row.name or "").lower() == database_name.lower():
                return True
        return False

    async def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        parent_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict:
        """创建文件夹并刷新统计。"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await self._run_with_stats_refresh(
            kb_id,
            kb_instance.create_folder(kb_id, folder_name, parent_id, operator_id),
        )

    async def create_database(
        self,
        database_name: str,
        description: str,
        kb_type: str = "milvus",
        embedding_model_spec: str | None = None,
        llm_model_spec: str | None = None,
        share_config: dict | None = None,
        created_by: str | None = None,
        created_by_department_id: int | str | None = None,
        **kwargs,
    ) -> KnowledgeBaseDetail:
        """
        创建数据库

        Args:
            database_name: 数据库名称
            description: 数据库描述
            kb_type: 知识库类型，默认为 milvus
            embedding_model_spec: 嵌入模型 spec
            llm_model_spec: LLM 模型 spec
            share_config: 共享配置
            created_by: 创建者 uid
            created_by_department_id: 创建者部门 ID
            **kwargs: 其他配置参数

        Returns:
            数据库信息字典
        """
        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            available_types = list(KnowledgeBaseFactory.get_available_types().keys())
            raise ValueError(f"Unsupported knowledge base type: {kb_type}. Available types: {available_types}")

        if await self.database_name_exists(database_name):
            raise KBNameConflictError(f"知识库名称 '{database_name}' 已存在，请使用其他名称")

        share_config = self._normalize_share_config(
            share_config,
            user_uid=created_by,
            department_id=created_by_department_id,
        )

        kb_instance = self._get_or_create_kb_instance(kb_type)
        additional_params = kwargs
        additional_params.setdefault("auto_generate_questions", False)
        if "reranker_config" in additional_params:
            raise ValueError("reranker_config 已移除，请在查询参数中使用 reranker_model spec")
        additional_params = kb_instance.normalize_additional_params(additional_params)

        if kb_instance.requires_embedding_model:
            if not embedding_model_spec:
                raise ValueError("embedding_model_spec 不能为空")

            from yuxi.models.providers.cache import model_cache

            info = model_cache.get_model_info(embedding_model_spec)
            if not info or info.model_type != "embedding":
                raise ValueError(f"不支持的 embedding 模型: {embedding_model_spec}")
        else:
            embedding_model_spec = None

        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        alphabet = string.ascii_lowercase + string.digits
        while True:
            kb_id = "kb_" + "".join(secrets.choice(alphabet) for _ in range(10))
            if await kb_repo.get_by_kb_id(kb_id) is None:
                break

        query_params = kb_instance.get_default_query_params(kb_id)
        persisted_additional_params = {**additional_params, "stats": self._normalize_database_stats(None)}
        await kb_repo.create(
            {
                "kb_id": kb_id,
                "name": database_name,
                "description": description,
                "kb_type": kb_type,
                "embedding_model_spec": embedding_model_spec,
                "llm_model_spec": llm_model_spec,
                "query_params": query_params,
                "additional_params": persisted_additional_params,
                "share_config": share_config,
                "created_by": created_by,
            }
        )
        os.makedirs(os.path.join(kb_instance.work_dir, kb_id), exist_ok=True)

        logger.info(f"Created {kb_type} database: {database_name} ({kb_id}) with {additional_params}")
        database = await self.get_database_info(kb_id)
        if database is None:
            raise KBNotFoundError(f"Database {kb_id} not found after creation")
        return database

    async def delete_database(self, kb_id: str) -> dict:
        """删除数据库"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        try:
            kb_instance = await self.get_kb_executor(kb_id)
            result = await kb_instance.cleanup_database_resources(kb_id)
            await KnowledgeBaseRepository().delete(kb_id)
            return result
        except KBNotFoundError as e:
            logger.warning(f"Database {kb_id} not found during deletion: {e}")
            return {"message": "删除成功"}

    async def add_file_record(
        self, kb_id: str, item: str, params: dict | None = None, operator_id: str | None = None
    ) -> dict:
        """Add file record to metadata"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        return await self._run_with_stats_refresh(
            kb_id,
            executor.add_file_record(
                kb_id,
                item,
                params,
                operator_id,
                additional_params=config.additional_params,
            ),
        )

    async def parse_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        *,
        processing_task_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """Parse file to Markdown"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        return await self._run_with_stats_refresh(
            kb_id,
            executor.parse_file(
                kb_id,
                file_id,
                operator_id,
                additional_params=config.additional_params,
                processing_task_id=processing_task_id,
                processing_owner=processing_owner,
            ),
        )

    async def index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
        *,
        processing_task_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """Index parsed file"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        return await self._run_with_stats_refresh(
            kb_id,
            executor.index_file(
                kb_id,
                file_id,
                operator_id,
                params=params,
                embedding_model_spec=config.embedding_model_spec,
                additional_params=config.additional_params,
                processing_task_id=processing_task_id,
                processing_owner=processing_owner,
            ),
        )

    async def update_file_params(self, kb_id: str, file_id: str, params: dict, operator_id: str | None = None) -> None:
        """Update file processing params"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        await executor.update_file_params(
            kb_id,
            file_id,
            params,
            operator_id,
            additional_params=config.additional_params,
        )

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> str:
        """异步查询知识库"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        return await executor.aquery(
            query_text,
            kb_id,
            config=config,
            **kwargs,
        )

    async def get_kb_query_params_config(self, kb_id: str) -> dict:
        """获取知识库查询参数定义，并合并当前保存值。"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        params = executor.get_query_params_config(kb_id=kb_id)
        for option in params.get("options", []):
            key = option.get("key")
            if key in config.query_options:
                option["default"] = config.query_options[key]
        return params

    async def update_kb_query_params(self, kb_id: str, params: dict[str, Any]) -> None:
        """合并并持久化知识库查询参数。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb = await KnowledgeBaseRepository().merge_query_params_options(kb_id, params)
        if kb is None:
            raise KBNotFoundError(f"Database {kb_id} not found")

    async def export_data(self, kb_id: str, format: str = "zip", **kwargs) -> str:
        """导出知识库数据"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.export_data(kb_id, format=format, **kwargs)

    @staticmethod
    def _file_record_list_item(
        record,
        child_counts: dict[str, int] | None = None,
        creator: User | None = None,
    ) -> dict:
        child_counts = child_counts or {}
        file_id = getattr(record, "file_id")
        child_count = int(getattr(record, "virtual_children_count", 0) or child_counts.get(file_id, 0))
        created_by = getattr(record, "created_by", None)
        created_at_value = getattr(record, "created_at", None)
        updated_at_value = getattr(record, "updated_at", None)
        created_at = utc_isoformat(created_at_value) if created_at_value else None
        updated_at = utc_isoformat(updated_at_value) if updated_at_value else None
        return {
            "file_id": file_id,
            "filename": getattr(record, "filename"),
            "file_type": getattr(record, "file_type", None),
            "status": getattr(record, "status", None) or "uploaded",
            "created_at": created_at,
            "updated_at": updated_at,
            "file_size": int(getattr(record, "file_size", None) or 0),
            "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
            "token_count": int(getattr(record, "token_count", 0) or 0),
            "created_by": created_by,
            "created_by_name": creator.username if creator else created_by,
            "created_by_avatar": creator.to_dict().get("avatar") if creator else None,
            "is_folder": bool(getattr(record, "is_folder", False)),
            "parent_id": getattr(record, "parent_id", None),
            "has_children": child_count > 0,
            "children_count": child_count,
            "has_original_file": bool(getattr(record, "minio_url", None) or getattr(record, "path", None)),
            "has_parsed_markdown": bool(getattr(record, "markdown_file", None)),
            "is_virtual_folder": bool(getattr(record, "is_virtual_folder", False)),
            "path_prefix": getattr(record, "path_prefix", None),
        }

    async def _get_database_file_stats(self, kb_id: str) -> dict[str, int]:
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        return await KnowledgeFileRepository().get_kb_file_stats(kb_id)

    async def get_database_info(self, kb_id: str, include_files: bool = False) -> KnowledgeBaseDetail | None:
        """获取知识库详情。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_kb_id(kb_id)
        if kb is None:
            return None

        files = None
        files_truncated = False
        files_page_size = None
        if include_files:
            from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

            repo = KnowledgeFileRepository()
            records, total = await repo.list_documents(kb_id=kb_id, page=1, page_size=500)
            files = {
                record.file_id: {
                    "file_id": record.file_id,
                    "filename": record.filename,
                    "path": getattr(record, "path", "") or "",
                    "markdown_file": getattr(record, "markdown_file", "") or "",
                    "type": getattr(record, "file_type", "") or "",
                    "status": getattr(record, "status", None) or "uploaded",
                    "created_at": utc_isoformat(record.created_at) if getattr(record, "created_at", None) else None,
                    "is_folder": bool(getattr(record, "is_folder", False)),
                    "parent_id": getattr(record, "parent_id", None),
                    "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
                    "token_count": int(getattr(record, "token_count", 0) or 0),
                }
                for record in records
            }
            files_truncated = total > len(records)
            files_page_size = 500

        file_stats = await self._get_database_file_stats(kb_id)
        return KnowledgeBaseDetail(
            **self._database_read_fields(kb, stats=file_stats),
            mindmap=kb.mindmap,
            sample_questions=tuple(kb.sample_questions or []),
            files=files,
            files_truncated=files_truncated,
            files_page_size=files_page_size,
        )

    async def list_document_files(
        self,
        kb_id: str,
        *,
        parent_id: str | None = None,
        path_prefix: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
        recursive: bool = False,
        files_only: bool = False,
        include_stats: bool = True,
    ) -> dict:
        """按目录和筛选条件分页获取轻量文件列表。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
        if kb is None:
            raise KBNotFoundError(f"Database {kb_id} not found")

        repo = KnowledgeFileRepository()
        if parent_id:
            parent_record = await repo.get_by_file_id(parent_id)
            if not parent_record or parent_record.kb_id != kb_id:
                raise ValueError("Parent folder not found")
            if not parent_record.is_folder:
                raise ValueError("Parent is not a folder")

        normalized_page = max(int(page or 1), 1)
        normalized_page_size = min(max(int(page_size or 100), 1), 500)
        effective_recursive = recursive and bool(status and status != "all")

        # 列表与统计互不依赖，并行执行（大知识库下统计全表聚合耗时显著）
        if include_stats:
            (records, total), stats = await asyncio.gather(
                repo.list_documents(
                    kb_id=kb_id,
                    parent_id=parent_id,
                    path_prefix=path_prefix,
                    status=status,
                    page=normalized_page,
                    page_size=normalized_page_size,
                    recursive=effective_recursive,
                    files_only=files_only,
                ),
                repo.get_kb_file_stats(kb_id),
            )
        else:
            records, total = await repo.list_documents(
                kb_id=kb_id,
                parent_id=parent_id,
                path_prefix=path_prefix,
                status=status,
                page=normalized_page,
                page_size=normalized_page_size,
                recursive=effective_recursive,
                files_only=files_only,
            )
            stats = None

        folder_ids = [record.file_id for record in records if record.is_folder]
        creator_uids = [record.created_by for record in records if getattr(record, "created_by", None)]
        from yuxi.repositories.user_repository import UserRepository

        child_counts, creators = await asyncio.gather(
            repo.count_children_by_parent_ids(kb_id=kb_id, parent_ids=folder_ids),
            UserRepository().list_by_uids(creator_uids),
        )
        creators = {user.uid: user for user in creators}
        items = [
            self._file_record_list_item(record, child_counts, creators.get(getattr(record, "created_by", None)))
            for record in records
        ]
        normalize_path_prefix = getattr(repo, "_normalize_path_prefix", lambda value: value or "")

        result = {
            "items": items,
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "has_more": normalized_page * normalized_page_size < total,
            "parent_id": parent_id,
            "path_prefix": normalize_path_prefix(path_prefix),
            "recursive": effective_recursive,
        }
        if stats is not None:
            result["stats"] = stats
        return result

    async def search_document_files(
        self,
        knowledge_bases: list[dict],
        *,
        query: str | None = None,
        offset: int = 0,
        limit: int = 300,
        status: str | None = None,
        include_is_folder: bool = False,
        include_parent_id: bool = False,
    ) -> dict:
        """按文件名在一组知识库中搜索文件，并返回分页结果。"""
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        normalized_offset = max(offset or 0, 0)
        normalized_limit = min(max(limit or 300, 1), KB_FILE_SEARCH_SCAN_LIMIT)
        normalized_query = (query or "").strip().lower()
        accepted_statuses = self._normalize_file_status_filter(status)

        repo = KnowledgeFileRepository()
        all_files = []
        use_sql_pagination = len(knowledge_bases) == 1
        candidate_limit = normalized_limit if use_sql_pagination else normalized_offset + normalized_limit
        query_offset = normalized_offset if use_sql_pagination else 0
        search_results = await asyncio.gather(
            *(
                self._search_kb_files(
                    repo,
                    kb,
                    query=normalized_query,
                    statuses=accepted_statuses,
                    offset=query_offset,
                    limit=candidate_limit,
                )
                for kb in knowledge_bases
            )
        )
        total = 0
        for kb, files, kb_total in search_results:
            total += kb_total
            kb_id = kb.get("kb_id")
            for file in files:
                item = {
                    "kb_id": kb_id,
                    "kb_name": kb.get("name"),
                    "file_id": file.file_id,
                    "filename": file.filename,
                    "file_type": file.file_type,
                    "status": file.status,
                    "created_at": str(file.created_at) if file.created_at else None,
                    "updated_at": str(file.updated_at) if file.updated_at else None,
                    "file_size": file.file_size,
                }
                if include_is_folder:
                    item["is_folder"] = bool(file.is_folder)
                if include_parent_id:
                    item["parent_id"] = file.parent_id
                all_files.append(item)

        if not use_sql_pagination:
            # 多库才需要跨库按更新时间归并；单库结果已由 DB 按 updated_at desc, file_id asc 排好序。
            all_files.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        paginated_files = (
            all_files if use_sql_pagination else all_files[normalized_offset : normalized_offset + normalized_limit]
        )
        return {
            "files": paginated_files,
            "total": total,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "has_more": normalized_offset + normalized_limit < total,
        }

    @staticmethod
    def _normalize_file_status_filter(status: str | None) -> set[str] | None:
        if not status or status == "all":
            return None
        return {
            "indexed": {"indexed", "done"},
            "error_indexing": {"error_indexing", "failed"},
        }.get(status, {status})

    @staticmethod
    async def _search_kb_files(
        repo,
        kb: dict,
        *,
        query: str | None,
        statuses: set[str] | None,
        offset: int,
        limit: int,
    ) -> tuple[dict, list, int]:
        """搜索单个知识库文件，kb_id 缺失时返回空列表，供并行 gather 使用。"""
        if not kb.get("kb_id"):
            return kb, [], 0
        files, total = await repo.search_files(
            kb_id=kb["kb_id"],
            filename_query=query,
            statuses=statuses,
            offset=offset,
            limit=limit,
            files_only=True,
        )
        return kb, files, total

    async def document_file_exists(self, kb_id: str, filename: str) -> bool:
        """检查指定知识库中是否存在给定展示文件名或相对路径的文件。"""
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        normalized_filename = filename.strip()
        if not normalized_filename:
            raise ValueError("filename is required")
        return await KnowledgeFileRepository().exists_by_filename(kb_id=kb_id, filename=normalized_filename)

    async def list_document_file_ids_by_statuses(
        self,
        kb_id: str,
        *,
        statuses: list[str],
        after_file_id: str | None = None,
        limit: int = 500,
    ) -> list[str]:
        """按文件状态游标分页获取文件 ID，用于后台批量处理任务。"""
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        return await KnowledgeFileRepository().list_file_ids_by_exact_statuses(
            kb_id=kb_id,
            statuses=statuses,
            after_file_id=after_file_id,
            limit=limit,
        )

    async def delete_folder(self, kb_id: str, folder_id: str) -> None:
        """递归删除文件夹"""
        kb_instance = await self.get_kb_executor(kb_id)
        await self._run_with_stats_refresh(kb_id, kb_instance.delete_folder(kb_id, folder_id))

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        """删除文件"""
        kb_instance = await self.get_kb_executor(kb_id)
        await self._run_with_stats_refresh(kb_id, kb_instance.delete_file(kb_id, file_id))

    async def update_content(self, kb_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        """更新内容（重新分块）"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        return await self._run_with_stats_refresh(
            kb_id,
            executor.update_content(
                kb_id,
                file_ids,
                params or {},
                embedding_model_spec=config.embedding_model_spec,
                additional_params=config.additional_params,
            ),
        )

    async def repair_missing_file_stats(self, kb_id: str) -> dict:
        """修复历史文件缺失的 Chunk/Token 统计，并刷新知识库聚合统计。"""
        kb_instance = await self.get_kb_executor(kb_id)
        result = await kb_instance.repair_missing_file_stats(kb_id)
        result["stats"] = await self._refresh_database_stats(kb_id, result["stats"])
        return result

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        """获取文件基本信息（仅元数据）"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.get_file_basic_info(kb_id, file_id)

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        """获取文件内容信息（chunks和lines）"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.get_file_content(kb_id, file_id)

    async def open_file_content(self, kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        """按行窗口打开文件解析后的 Markdown 内容"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.open_file_content(kb_id, file_id, offset, limit)

    async def find_file_content(
        self,
        kb_id: str,
        file_id: str,
        patterns: list[str],
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ) -> dict:
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.find_file_content(
            kb_id,
            file_id,
            patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        """获取文件完整信息（基本信息+内容信息）"""
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.get_file_info(kb_id, file_id)

    async def list_file_tree(
        self,
        kb_id: str,
        parent_id: str | None = None,
        recursive: bool = False,
        files_only: bool = False,
    ) -> dict:
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.list_file_tree(kb_id, parent_id, recursive, files_only)

    async def get_file_download(self, kb_id: str, file_id: str, variant: str = "original") -> dict:
        await self._require_kb_supports_documents(kb_id, "download")
        kb_instance = await self.get_kb_executor(kb_id)
        return await kb_instance.get_file_download(kb_id, file_id, variant)

    async def file_name_existed_in_db(self, kb_id: str | None, file_name: str | None) -> bool:
        """检查指定数据库中是否存在同名的文件"""
        if not kb_id or not file_name:
            return False
        return await self.document_file_exists(kb_id, file_name)

    async def get_same_name_files(self, kb_id: str, filename: str) -> list[dict]:
        """获取同一知识库中同名文件列表
        基于原始文件名直接比较
        返回基础信息：文件名、大小、上传时间

        Args:
            kb_id: 数据库ID
            filename: 要检测的文件名（原始文件名）

        Returns:
            同名文件列表，每项包含：
            - filename: 文件名
            - size: 文件大小
            - created_at: 上传时间
            - file_id: 文件ID（用于下载）
        """
        if not kb_id or not filename:
            return []

        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        records = await KnowledgeFileRepository().list_same_name_files(kb_id=kb_id, filename=filename)
        return [
            {
                "file_id": record.file_id,
                "filename": record.filename,
                "size": int(record.file_size or 0),
                "created_at": utc_isoformat(record.created_at) if record.created_at else "",
                "content_hash": record.content_hash or "",
            }
            for record in records
        ]

    async def file_existed_in_db(self, kb_id: str | None, content_hash: str | None) -> bool:
        """检查指定数据库中是否存在相同内容哈希的文件"""
        if not kb_id or not content_hash:
            return False

        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        return await KnowledgeFileRepository().exists_by_content_hash(kb_id=kb_id, content_hash=content_hash)

    async def update_database(
        self,
        kb_id: str,
        name: str,
        description: str,
        llm_model_spec: str | None = None,
        update_llm_model_spec: bool = False,
        additional_params: dict | None = None,
        share_config: dict | None = None,
        operator_uid: str | None = None,
        operator_department_id: int | str | None = None,
    ) -> KnowledgeBaseDetail:
        """更新数据库"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_kb_id(kb_id)
        if kb is None:
            raise ValueError(f"数据库 {kb_id} 不存在")

        kb_type = kb.kb_type or "milvus"
        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            raise ValueError(f"不支持的知识库类型: {kb_type}")
        kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)

        update_data: dict = {
            "name": name,
            "description": description,
        }
        if update_llm_model_spec:
            update_data["llm_model_spec"] = llm_model_spec

        if additional_params is not None:
            current_additional_params = kb.additional_params or {}
            current_graph_config = current_additional_params.get("graph_build_config") or {}
            if current_graph_config.get("locked") and "graph_build_config" in additional_params:
                raise ValueError("图谱抽取配置已锁定，请使用图谱重置接口重新配置")

            merged_additional_params = kb_class.normalize_additional_params(
                deep_merge(current_additional_params, additional_params)
            )
            update_data["additional_params"] = merged_additional_params

        if share_config is not None:
            update_data["share_config"] = self._normalize_share_config(
                share_config,
                user_uid=operator_uid,
                department_id=operator_department_id,
            )

        # 保存到数据库
        await kb_repo.update(kb_id, update_data)

        database = await self.get_database_info(kb_id)
        if database is None:
            raise KBNotFoundError(f"Database {kb_id} not found after update")
        return database

    async def retrieve(self, kb_id: str, query: str, **options) -> dict:
        """按 kb_id 加载最新运行时元数据并执行检索。"""
        config = await self.get_kb_config(kb_id)
        executor = self._get_or_create_kb_instance(config.kb_type)
        results = await executor.aquery(
            query,
            kb_id,
            config=config,
            agent_call=True,
            **options,
        )
        return executor.build_search_output(kb_id, results)

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
        await self._require_kb_supports_documents(kb_id, "open")
        window = await self.open_file_content(kb_id, file_id, offset=offset, limit=limit)
        return OpenOutputSchema(kb_id=kb_id, file_id=file_id, **window).model_dump()

    async def find_in_document(
        self,
        kb_id: str,
        file_id: str,
        patterns: list[str],
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ) -> dict:
        """在文件内做关键词或正则定位，返回 FindOutputSchema 字典。

        不支持文档全文操作的知识库（如 dify 只读源）抛 ValueError。
        """
        await self._require_kb_supports_documents(kb_id, "find")
        result = await self.find_file_content(
            kb_id,
            file_id,
            patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )
        return FindOutputSchema(kb_id=kb_id, file_id=file_id, **result).model_dump()

    async def _require_kb_supports_documents(self, kb_id: str, operation: str) -> None:
        """按数据库元数据判断是否支持文档全文操作；不支持抛 ValueError。"""
        db_info, supports_documents = await self.get_database_document_support(kb_id)
        if not db_info:
            raise KBNotFoundError(f"知识库资源 '{kb_id}' 不存在")
        kb_type = db_info.kb_type.lower()
        if not supports_documents:
            operation_label = {
                "open": "文档查看",
                "find": "文档查找",
                "download": "文件下载",
            }.get(operation, operation)
            raise ValueError(f"{db_info.name or kb_type} 只支持检索，不支持{operation_label}")

    # =============================================================================
    # 管理器特有的方法
    # =============================================================================

    def get_supported_kb_types(self) -> dict[str, dict]:
        """获取支持的知识库类型"""
        return KnowledgeBaseFactory.get_available_types()

    async def get_kb_instance_info(self) -> dict[str, dict]:
        """获取知识库实例信息"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        counts: dict[str, int] = {}
        for row in await KnowledgeBaseRepository().get_all():
            kb_type = row.kb_type or "milvus"
            counts[kb_type] = counts.get(kb_type, 0) + 1

        info = {}
        for kb_type, kb_instance in self.kb_instances.items():
            info[kb_type] = {
                "work_dir": kb_instance.work_dir,
                "database_count": counts.get(kb_type, 0),
                "file_metadata_source": "database",
            }
        return info

    async def get_statistics(self) -> dict:
        """获取统计信息"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        kb_repo = KnowledgeBaseRepository()
        rows = await kb_repo.get_all()

        stats = {"total_databases": len(rows), "kb_types": {}, "total_files": 0}

        # 按知识库类型统计
        for row in rows:
            kb_type = row.kb_type or "milvus"
            if kb_type not in stats["kb_types"]:
                stats["kb_types"][kb_type] = 0
            stats["kb_types"][kb_type] += 1

        stats["total_files"] = await KnowledgeFileRepository().count_all()

        return stats

    # =============================================================================
    # 数据一致性检测方法
    # =============================================================================

    async def detect_data_inconsistencies(self) -> dict:
        """协调各类型执行器检测外部资源与主数据的不一致。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        inconsistencies = {
            "milvus": {"missing_collections": [], "missing_files": []},
            "total_missing_collections": 0,
            "total_missing_files": 0,
        }
        logger.info("开始检测向量数据库与元数据的一致性...")

        if "milvus" in self.kb_instances:
            try:
                rows = await KnowledgeBaseRepository().get_all()
                known_kb_ids = {row.kb_id for row in rows}
                managed_kb_ids = {row.kb_id for row in rows if (row.kb_type or "milvus") == "milvus"}
                milvus_inconsistencies = await self.kb_instances["milvus"].detect_data_inconsistencies(
                    known_kb_ids,
                    managed_kb_ids,
                )
                inconsistencies["milvus"] = milvus_inconsistencies
                inconsistencies["total_missing_collections"] = len(milvus_inconsistencies["missing_collections"])
                inconsistencies["total_missing_files"] = len(milvus_inconsistencies["missing_files"])
            except Exception as e:
                logger.error(f"检测 Milvus 数据不一致时出错: {e}")

        self._log_inconsistencies(inconsistencies)
        return inconsistencies

    def _log_inconsistencies(self, inconsistencies: dict) -> None:
        """将不一致检测结果输出到日志"""
        total_missing_collections = inconsistencies["total_missing_collections"]
        total_missing_files = inconsistencies["total_missing_files"]

        if total_missing_collections == 0 and total_missing_files == 0:
            logger.info("数据一致性检测完成，未发现不一致情况")
            return

        logger.warning("=" * 80)
        logger.warning("数据一致性检测完成，发现以下不一致情况：")
        logger.warning("=" * 80)

        # Milvus 不一致情况
        milvus_missing = inconsistencies["milvus"]["missing_collections"]
        milvus_files_missing = inconsistencies["milvus"]["missing_files"]
        if milvus_missing or milvus_files_missing:
            logger.warning("Milvus 不一致情况：")
            logger.warning(f"  缺失集合数量: {len(milvus_missing)}")
            for collection_info in milvus_missing:
                logger.warning(f"    - 集合: {collection_info['collection_name']}, 实体数: {collection_info['count']}")
            logger.warning(f"  缺失文件记录数量: {len(milvus_files_missing)}")
            for file_info in milvus_files_missing:
                logger.warning(
                    f"    - 数据库: {file_info['kb_id']}, 向量数: {file_info['vector_count']}, "
                    f"元数据文件数: {file_info['metadata_files_count']}"
                )

        logger.warning("=" * 80)
        logger.warning(f"总计：缺失集合 {total_missing_collections} 个，缺失文件记录 {total_missing_files} 个")
        logger.warning("建议：检查这些不一致的数据，必要时进行数据清理或元数据修复")
        logger.warning("=" * 80)

    async def manual_consistency_check(self) -> dict:
        """
        手动触发数据一致性检测

        Returns:
            检测结果字典
        """
        logger.info("手动触发数据一致性检测...")
        return await self.detect_data_inconsistencies()
