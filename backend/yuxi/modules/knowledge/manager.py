import asyncio
import os
import secrets
import string
import traceback
from collections import Counter
from collections.abc import Awaitable
from dataclasses import replace
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import (
    ResourcePermission,
    normalize_permission_config,
    resolve_knowledge_base_permission,
)
from yuxi.modules.knowledge.base import KBNameConflictError, KBNotFoundError, KnowledgeExecutor
from yuxi.modules.knowledge.cache import (
    cache_kb_config,
    get_cached_kb_config,
    kb_config_cache_lock,
    serialize_kb_config,
)
from yuxi.modules.knowledge.chunking.presets import ChunkConfigError, deep_merge
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.file_status import needs_parse_before_index, status_filter_values
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig, KnowledgeBaseDetail, KnowledgeBaseSummary
from yuxi.modules.knowledge.schemas import FindOutputSchema, OpenOutputSchema
from yuxi.modules.knowledge.services import documents
from yuxi.modules.knowledge.utils.security import redact_sensitive_params
from yuxi.shared.datetime import utc_isoformat

KB_FILE_SEARCH_SCAN_LIMIT = 5000


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
                await self._get_or_create_kb_instance(kb_type)
                logger.info(f"[InitializeKB] {kb_type} 实例已初始化")
            except Exception as e:
                logger.error(f"Failed to initialize {kb_type} knowledge base: {e}")
                logger.error(traceback.format_exc())
                failures.append(f"{kb_type}:{type(e).__name__}")
        if failures:
            raise RuntimeError(f"Used knowledge backends failed to initialize: {', '.join(sorted(failures))}")

    async def _get_or_create_kb_instance(self, kb_type: str) -> KnowledgeExecutor:
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

    # ==== 知识库创建与更新 ====

    async def knowledge_base_name_exists(self, name: str) -> bool:
        """检查知识库名称是否已存在"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        return await KnowledgeBaseRepository().name_exists(name)

    async def create_knowledge_base(
        self,
        name: str,
        description: str,
        kb_type: str = "milvus",
        embedding_model_spec: str | None = None,
        llm_model_spec: str | None = None,
        share_config: dict | None = None,
        created_by: str | None = None,
        created_by_department_id: int | str | None = None,
        **kwargs,
    ) -> KnowledgeBaseDetail:
        """创建知识库：校验类型、名称与共享配置，规范化参数后持久化并返回详情。"""
        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            available_types = list(KnowledgeBaseFactory.get_available_types().keys())
            raise ValueError(f"Unsupported knowledge base type: {kb_type}. Available types: {available_types}")

        if await self.knowledge_base_name_exists(name):
            raise KBNameConflictError(f"知识库名称 '{name}' 已存在，请使用其他名称")

        share_config = self._normalize_share_config(
            share_config,
            user_uid=created_by,
            department_id=created_by_department_id,
        )

        kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
        additional_params = kwargs
        additional_params.setdefault("auto_generate_questions", False)
        if "reranker_config" in additional_params:
            raise ValueError("reranker_config 已移除，请在查询参数中使用 reranker_model spec")
        additional_params = kb_class.normalize_additional_params(additional_params)

        if kb_class.requires_embedding_model:
            if not embedding_model_spec:
                raise ValueError("embedding_model_spec 不能为空")

            from yuxi.modules.models.providers.cache import model_cache

            info = model_cache.get_model_info(embedding_model_spec)
            if not info or info.model_type != "embedding":
                raise ValueError(f"不支持的 embedding 模型: {embedding_model_spec}")
        else:
            embedding_model_spec = None

        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        alphabet = string.ascii_lowercase + string.digits
        while True:
            kb_id = "kb_" + "".join(secrets.choice(alphabet) for _ in range(10))
            if await kb_repo.get_by_kb_id(kb_id) is None:
                break

        query_params = kb_class.get_default_query_params()
        persisted_additional_params = {**additional_params, "stats": self._normalize_knowledge_base_stats(None)}
        await kb_repo.create(
            {
                "kb_id": kb_id,
                "name": name,
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

        logger.info(f"Created {kb_type} knowledge_base: {name} ({kb_id})")
        knowledge_base = await self.get_knowledge_base_info(kb_id)
        if knowledge_base is None:
            raise KBNotFoundError(f"Knowledge base {kb_id} not found after creation")
        return knowledge_base

    async def update_knowledge_base(
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
        """更新知识库名称、描述、参数与共享配置，返回更新后的详情。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_kb_id(kb_id)
        if kb is None:
            raise ValueError(f"知识库 {kb_id} 不存在")

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

            merged_additional_params = kb_class.normalize_additional_params(deep_merge(current_additional_params, additional_params))
            update_data["additional_params"] = merged_additional_params

        if share_config is not None:
            update_data["share_config"] = self._normalize_share_config(
                share_config,
                user_uid=operator_uid,
                department_id=operator_department_id,
            )

        await kb_repo.update(kb_id, update_data)

        knowledge_base = await self.get_knowledge_base_info(kb_id)
        if knowledge_base is None:
            raise KBNotFoundError(f"Knowledge base {kb_id} not found after update")
        return knowledge_base

    def _normalize_share_config(
        self,
        share_config: dict | None,
        *,
        user_uid: str | None = None,
        department_id: int | str | None = None,
    ) -> dict:
        """规范化共享配置：仅接受 version 2，并把创建/操作者并入读取范围。"""
        if share_config is None:
            return {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            }

        if not share_config or share_config.get("version") != 2:
            raise ValueError("知识库共享配置必须使用 version 2")

        strict = user_uid is not None or department_id is not None
        normalized = normalize_permission_config(share_config, strict=strict)
        read_scope = normalized["read_scope"]
        if read_scope is None and strict:
            raise ValueError("知识库必须设置读取范围")
        if read_scope and read_scope["access_level"] == "department" and department_id is not None:
            read_scope["department_ids"] = sorted({*read_scope["department_ids"], int(department_id)})
        elif read_scope and read_scope["access_level"] == "user" and user_uid:
            read_scope["user_uids"] = sorted({*read_scope["user_uids"], str(user_uid)})
        return normalized

    # ==== 知识库删除与外部清理 ====

    async def delete_knowledge_base(self, kb_id: str) -> dict:
        """提交知识库不可见事实；外部存储清理由 durable outbox 重放。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        await KnowledgeBaseRepository().mark_deleted(kb_id)
        return {"message": "删除成功"}

    async def cleanup_deleted_knowledge_base(self, kb_id: str) -> None:
        """仅为已删除知识库执行可重试外部清理。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        repository = KnowledgeBaseRepository()
        record = await repository.get_by_kb_id(kb_id, include_deleted=True)
        if record is None:
            return
        if record.deleted_at is None:
            raise ValueError("Cannot clean a visible knowledge base")
        executor = await self._get_or_create_kb_instance(record.kb_type)
        await executor.cleanup_knowledge_base_resources(kb_id)
        await repository.purge(kb_id)

    async def get_cleanup_executor(self, kb_id: str) -> KnowledgeExecutor | None:
        """清理任务读取 tombstone，不使用可见配置缓存。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        record = await KnowledgeBaseRepository().get_by_kb_id(kb_id, include_deleted=True)
        if record is None:
            return None
        return await self._get_or_create_kb_instance(record.kb_type)

    # ==== 知识库读取与权限过滤 ====

    async def get_knowledge_bases(self) -> list[KnowledgeBaseSummary]:
        """获取所有知识库摘要。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        rows = await kb_repo.get_all()
        all_knowledge_bases: list[KnowledgeBaseSummary] = []
        for row in rows:
            kb_type = row.kb_type or "milvus"
            if not KnowledgeBaseFactory.is_type_supported(kb_type):
                logger.warning(f"Skip unsupported knowledge_base: kb_id={row.kb_id}, kb_type={kb_type}")
                continue

            # 单条记录元数据不合法时只跳过该条，避免一条坏记录隐藏整个列表。
            try:
                knowledge_base = KnowledgeBaseSummary(**self._knowledge_base_read_fields(row))
            except Exception as e:
                logger.warning(f"Skip knowledge_base with invalid metadata: kb_id={row.kb_id}, kb_type={kb_type}: {e}")
                continue
            all_knowledge_bases.append(knowledge_base)
        return all_knowledge_bases

    async def get_knowledge_base_info(self, kb_id: str, include_files: bool = False) -> KnowledgeBaseDetail | None:
        """获取知识库详情。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
        if kb is None:
            return None

        repo = KnowledgeFileRepository()
        files = None
        files_truncated = False
        files_page_size = None
        if include_files:
            files_page_size = 500
            records, total = await repo.list_documents(kb_id=kb_id, page=1, page_size=files_page_size)
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

        file_stats = await repo.get_kb_file_stats(kb_id)
        return KnowledgeBaseDetail(
            **self._knowledge_base_read_fields(kb, stats=file_stats),
            sample_questions=tuple(kb.sample_questions or []),
            files=files,
            files_truncated=files_truncated,
            files_page_size=files_page_size,
        )

    async def get_knowledge_bases_by_uid(self, uid: str) -> list[KnowledgeBaseSummary]:
        """根据 uid 获取知识库列表"""
        from yuxi.modules.identity.repositories.users import UserRepository

        user_repo = UserRepository()
        user: User | None = await user_repo.get_by_uid(uid)
        if not user:
            logger.warning(f"User not found: {uid}")
            return []
        return await self.get_knowledge_bases_by_user(user)

    async def get_knowledge_bases_by_user(self, user: User | dict) -> list[KnowledgeBaseSummary]:
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
        logger.info(f"Getting knowledge_bases for user with role {user_role} and department {user_dept}")

        all_knowledge_bases = await self.get_knowledge_bases()

        # 超级管理员可以看到所有知识库
        filtered_knowledge_bases: list[KnowledgeBaseSummary] = []
        for knowledge_base in all_knowledge_bases:
            permission = resolve_knowledge_base_permission(user_info, knowledge_base)
            if permission == ResourcePermission.NONE:
                continue
            additional_params = knowledge_base.additional_params
            if permission == ResourcePermission.READ:
                additional_params = redact_sensitive_params(additional_params)
            filtered_knowledge_bases.append(
                replace(
                    knowledge_base,
                    additional_params=additional_params,
                    effective_permission=permission,
                )
            )

        return filtered_knowledge_bases

    async def check_accessible(self, user: dict, kb_id: str) -> bool:
        """检查用户是否可访问知识库；超级管理员直通，库不存在视为不可访问。"""
        # 超级管理员有权访问所有
        if user.get("role") == "superadmin":
            return True

        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
        if kb is None:
            return False

        return resolve_knowledge_base_permission(user, kb) != ResourcePermission.NONE

    async def get_accessible_knowledge_base_info_by_uid(self, uid: str, kb_id: str) -> KnowledgeBaseSummary | None:
        """按 uid 获取一个可访问知识库的信息，找不到或无权访问时返回 None。"""
        normalized_kb_id = str(kb_id or "").strip()
        if not normalized_kb_id:
            return None

        knowledge_bases = await self.get_knowledge_bases_by_uid(uid)
        return next((kb for kb in knowledge_bases if kb.kb_id == normalized_kb_id), None)

    def knowledge_base_type_supports_documents(self, kb_type: str | None) -> bool:
        """判断知识库类型是否支持文档全文操作。"""
        normalized_type = (kb_type or "milvus").lower()
        if not KnowledgeBaseFactory.is_type_supported(normalized_type):
            return False
        return KnowledgeBaseFactory.get_kb_class(normalized_type).supports_documents

    async def get_knowledge_base_document_support(self, kb_id: str) -> tuple[KnowledgeBaseDetail | None, bool]:
        """返回知识库信息及其是否支持文档全文操作。"""
        kb_info = await self.get_knowledge_base_info(kb_id)
        if not kb_info:
            return None, False
        return kb_info, self.knowledge_base_type_supports_documents(kb_info.kb_type)

    # ==== 文件写入与目录管理 ====

    async def parse_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        *,
        params: dict | None = None,
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
        recover_missing_markdown: bool = False,
    ) -> dict:
        """解析文件生成 Markdown。"""
        config = await self.get_kb_config(kb_id)
        executor = await self._get_or_create_kb_instance(config.kb_type)
        return await self._run_with_stats_refresh(
            kb_id,
            executor.parse_file(
                kb_id,
                file_id,
                operator_id,
                params=params,
                additional_params=config.additional_params,
                processing_job_id=processing_job_id,
                processing_owner=processing_owner,
                recover_missing_markdown=recover_missing_markdown,
            ),
        )

    async def index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
        *,
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """入库。未解析或没有正文的文件先解析，然后只交给执行器一次。"""
        config = await self.get_kb_config(kb_id)
        executor = await self._get_or_create_kb_instance(config.kb_type)
        meta = await documents.load_file_meta(kb_id, file_id)
        if needs_parse_before_index(meta):
            await self.parse_file(
                kb_id,
                file_id,
                operator_id,
                params=params,
                processing_job_id=processing_job_id,
                processing_owner=processing_owner,
                recover_missing_markdown=True,
            )
        return await self._run_with_stats_refresh(
            kb_id,
            executor.index_file(
                kb_id,
                file_id,
                operator_id,
                params=params,
                embedding_model_spec=config.embedding_model_spec,
                additional_params=config.additional_params,
                processing_job_id=processing_job_id,
                processing_owner=processing_owner,
            ),
        )

    async def create_folder(self, kb_id: str, folder_name: str, parent_id: str | None = None, operator_id: str | None = None) -> dict:
        """创建目录，不加载检索连接或解析配置。"""
        from yuxi.modules.knowledge.services.directories import create_folder

        await self._require_kb_supports_documents(kb_id, "创建目录")
        return await self._run_with_stats_refresh(kb_id, create_folder(kb_id, folder_name, parent_id, operator_id))

    async def move_file(self, kb_id: str, file_id: str, new_parent_id: str | None) -> dict:
        """在目录事务中移动节点。"""
        from yuxi.modules.knowledge.services.directories import change_node

        await self._require_kb_supports_documents(kb_id, "移动文档")
        return await change_node(kb_id, file_id, parent_id=new_parent_id, move=True)

    async def rename_folder(self, kb_id: str, folder_id: str, folder_name: str) -> dict:
        """在目录事务中重命名文件夹。"""
        from yuxi.modules.knowledge.services.directories import change_node

        await self._require_kb_supports_documents(kb_id, "重命名目录")
        return await change_node(kb_id, folder_id, name=folder_name)

    async def delete_folder(self, kb_id: str, folder_id: str) -> None:
        """提交子树删除事实和外部清理意图。"""
        from yuxi.modules.knowledge.services.directories import delete_folder

        await self._require_kb_supports_documents(kb_id, "删除目录")
        await self._run_with_stats_refresh(kb_id, delete_folder(kb_id, folder_id))

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        """提交文件删除事实，不要求其派生索引可用。"""
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        await self._require_kb_supports_documents(kb_id, "删除文档")
        await self._run_with_stats_refresh(kb_id, KnowledgeFileRepository().mark_deleted(kb_id=kb_id, file_id=file_id))

    # ==== 文件列表与读取 ====

    async def list_document_files(
        self,
        kb_id: str,
        *,
        parent_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
        recursive: bool = False,
        files_only: bool = False,
        include_stats: bool = True,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> dict:
        """按目录和筛选条件分页获取轻量文件列表；排序作用于筛选后的全部记录。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
        if kb is None:
            raise KBNotFoundError(f"Knowledge base {kb_id} not found")

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

        list_documents = repo.list_documents(
            kb_id=kb_id,
            parent_id=parent_id,
            status=status,
            page=normalized_page,
            page_size=normalized_page_size,
            recursive=effective_recursive,
            files_only=files_only,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        # 列表与统计互不依赖，并行执行（大知识库下统计全表聚合耗时显著）
        if include_stats:
            (records, total), stats = await asyncio.gather(list_documents, repo.get_kb_file_stats(kb_id))
        else:
            records, total = await list_documents
            stats = None

        folder_ids = [record.file_id for record in records if record.is_folder]
        creator_uids = [record.created_by for record in records if getattr(record, "created_by", None)]
        from yuxi.modules.identity.repositories.users import UserRepository

        child_counts, creators = await asyncio.gather(
            repo.count_children_by_parent_ids(kb_id=kb_id, parent_ids=folder_ids),
            UserRepository().list_by_uids(creator_uids),
        )
        creators = {user.uid: user for user in creators}
        items = [self._file_record_list_item(record, child_counts, creators.get(getattr(record, "created_by", None))) for record in records]

        result = {
            "items": items,
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "has_more": normalized_page * normalized_page_size < total,
            "parent_id": parent_id,
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
        include_parent_id: bool = False,
    ) -> dict:
        """按文件名在一组知识库中搜索文件，并返回分页结果。"""
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        normalized_offset = max(offset or 0, 0)
        normalized_limit = min(max(limit or 300, 1), KB_FILE_SEARCH_SCAN_LIMIT)
        normalized_query = (query or "").strip().lower()
        accepted_statuses = status_filter_values(status)

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
                if include_parent_id:
                    item["parent_id"] = file.parent_id
                all_files.append(item)

        if not use_sql_pagination:
            # 多库才需要跨库按更新时间归并；单库结果已由 DB 按 updated_at desc, file_id asc 排好序。
            all_files.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        paginated_files = all_files if use_sql_pagination else all_files[normalized_offset : normalized_offset + normalized_limit]
        return {
            "files": paginated_files,
            "total": total,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "has_more": normalized_offset + normalized_limit < total,
        }

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
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

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
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        return await KnowledgeFileRepository().list_file_ids_by_exact_statuses(
            kb_id=kb_id,
            statuses=statuses,
            after_file_id=after_file_id,
            limit=limit,
        )

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        """获取文件基本信息（仅元数据）"""
        return await documents.get_file_basic_info(kb_id, file_id)

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        """获取文件内容信息（chunks和lines）"""
        return await documents.get_file_content(kb_id, file_id)

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        """获取文件完整信息（基本信息+内容信息）"""
        return await documents.get_file_info(kb_id, file_id)

    async def open_file_content(self, kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        """按行窗口打开文件解析后的 Markdown 内容"""
        return await documents.open_file_content(kb_id, file_id, offset, limit)

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
        """在文件解析内容中按关键词或正则定位上下文窗口。"""
        return await documents.find_file_content(
            kb_id,
            file_id,
            patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )

    async def get_file_download(self, kb_id: str, file_id: str, variant: str = "original") -> dict:
        """获取文件下载信息，仅支持文档操作的知识库可用。"""
        await self._require_kb_supports_documents(kb_id, "download")
        return await documents.get_file_download(kb_id, file_id, variant)

    # ==== 检索与文档工具入口 ====

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict[str, Any]]:
        """异步查询知识库"""
        config = await self.get_kb_config(kb_id)
        executor = await self._get_or_create_kb_instance(config.kb_type)
        return await executor.aquery(
            query_text,
            kb_id,
            config=config,
            **kwargs,
        )

    async def retrieve(self, kb_id: str, query: str, **options) -> dict:
        """按 kb_id 加载最新运行时元数据并执行检索。"""
        config = await self.get_kb_config(kb_id)
        executor = await self._get_or_create_kb_instance(config.kb_type)
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

    async def update_kb_query_params(self, kb_id: str, params: dict[str, Any]) -> None:
        """合并并持久化知识库查询参数。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb = await KnowledgeBaseRepository().merge_query_params_options(kb_id, params)
        if kb is None:
            raise KBNotFoundError(f"Knowledge base {kb_id} not found")

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
        kb_info, supports_documents = await self.get_knowledge_base_document_support(kb_id)
        if kb_info is None:
            raise KBNotFoundError(f"知识库资源 '{kb_id}' 不存在")
        if supports_documents:
            window = await documents.open_file_content(kb_id, file_id, offset=offset, limit=limit)
        else:
            kb_class = KnowledgeBaseFactory.get_kb_class(kb_info.kb_type)
            if not kb_class.supports_document_read:
                raise ValueError("该知识库只支持检索，不支持文档查看")
            config = await self.get_kb_config(kb_id)
            executor = await self._get_or_create_kb_instance(config.kb_type)
            window = await executor.open_document(kb_id, file_id, config=config, offset=offset, limit=limit)
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
        """按知识库元数据判断是否支持文档全文操作；不支持抛 ValueError。"""
        kb_info, supports_documents = await self.get_knowledge_base_document_support(kb_id)
        if not kb_info:
            raise KBNotFoundError(f"知识库资源 '{kb_id}' 不存在")
        kb_type = kb_info.kb_type.lower()
        if not supports_documents:
            operation_label = {
                "open": "文档查看",
                "find": "文档查找",
                "download": "文件下载",
            }.get(operation, operation)
            raise ValueError(f"{kb_info.name or kb_type} 只支持检索，不支持{operation_label}")

    # ==== 运维与统计 ====

    def get_supported_kb_types(self) -> dict[str, dict]:
        """获取支持的知识库类型"""
        return KnowledgeBaseFactory.get_available_types()

    async def get_statistics(self) -> dict:
        """统计知识库总数、类型分布与文件总数。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        rows = await KnowledgeBaseRepository().get_all()
        return {
            "total_knowledge_bases": len(rows),
            "kb_types": dict(Counter(row.kb_type or "milvus" for row in rows)),
            "total_files": await KnowledgeFileRepository().count_all(),
        }

    # ==== 内部辅助：统计刷新与持久化 ====

    @staticmethod
    def _normalize_knowledge_base_stats(stats: dict | None) -> dict[str, Any]:
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
            return {**normalized, "file_status_counts": {}}

        for key in normalized:
            try:
                normalized[key] = max(int(stats.get(key) or 0), 0)
            except (TypeError, ValueError):
                normalized[key] = 0
        normalized["file_status_counts"] = dict(stats.get("file_status_counts") or {})
        return normalized

    async def _refresh_knowledge_base_stats(self, kb_id: str) -> dict[str, int]:
        """刷新并持久化知识库聚合统计。"""
        from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

        kb = await KnowledgeBaseRepository().refresh_stats(kb_id)
        if kb is None:
            raise KBNotFoundError(f"Knowledge base {kb_id} not found")
        return self._normalize_knowledge_base_stats(kb.additional_params["stats"])

    async def _run_with_stats_refresh(self, kb_id: str, operation: Awaitable[Any]) -> Any:
        """执行文件操作并刷新统计，同时保留原始操作异常。"""
        try:
            result = await operation
        except (Exception, asyncio.CancelledError):
            try:
                await self._refresh_knowledge_base_stats(kb_id)
            except (Exception, asyncio.CancelledError) as refresh_error:
                logger.error(f"Refresh knowledge_base stats after failed operation: kb_id={kb_id}: {refresh_error}")
            raise

        await self._refresh_knowledge_base_stats(kb_id)
        return result

    # ==== 内部辅助：读取投影 ====

    def _knowledge_base_read_fields(
        self,
        row: Any,
        *,
        stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """将知识库记录转换为 Summary 与 Detail 共用的规范字段。"""
        kb_type = row.kb_type or "milvus"
        kb_class = KnowledgeBaseFactory.get_kb_class(kb_type) if KnowledgeBaseFactory.is_type_supported(kb_type) else None
        try:
            additional_params = (
                kb_class.normalize_additional_params(row.additional_params) if kb_class else dict(row.additional_params or {})
            )
        except ChunkConfigError as error:
            # 管理读取保留原值供授权和修复；执行配置仍由 get_kb_config 严格校验。
            additional_params = dict(row.additional_params or {})
            logger.warning(f"Invalid knowledge chunk config: kb_id={row.kb_id}: {error}")
        persisted_stats = additional_params.pop("stats", None)
        normalized_stats = self._normalize_knowledge_base_stats(stats if stats is not None else persisted_stats)

        try:
            share_config = normalize_permission_config(row.share_config)
        except (TypeError, ValueError) as error:
            # 保留损坏原值供授权拒绝及超级管理员修复，读取不能赋予默认全局权限。
            share_config = row.share_config
            logger.warning(f"Invalid knowledge share config: kb_id={row.kb_id}: {error}")

        return {
            "kb_id": row.kb_id,
            "name": row.name,
            "description": row.description,
            "kb_type": kb_type,
            "embedding_model_spec": row.embedding_model_spec,
            "llm_model_spec": row.llm_model_spec,
            "query_params": dict(row.query_params or {}),
            "additional_params": additional_params,
            "share_config": share_config,
            "created_by": row.created_by,
            "created_at": row.created_at,
            **normalized_stats,
        }

    @staticmethod
    def _file_record_list_item(
        record,
        child_counts: dict[str, int] | None = None,
        creator: User | None = None,
    ) -> dict:
        """将文件记录投影为列表条目，附带子项计数与创建者信息。"""
        child_counts = child_counts or {}
        file_id = getattr(record, "file_id")
        child_count = int(child_counts.get(file_id, 0))
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
            "error_message": getattr(record, "error_message", None),
        }
