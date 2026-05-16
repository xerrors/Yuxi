import asyncio
import os

from yuxi.knowledge.base import KBNotFoundError, KnowledgeBase
from yuxi.knowledge.chunking.ragflow_like.presets import (
    deep_merge,
    ensure_chunk_defaults_in_additional_params,
)
from yuxi.knowledge.factory import KnowledgeBaseFactory
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat


class KnowledgeBaseManager:
    """
    知识库管理器

    统一管理多种类型的知识库实例，直接通过 Repository 访问数据库，不维护冗余缓存。
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

        # 元数据锁
        self._metadata_lock = asyncio.Lock()

    async def initialize(self):
        """异步初始化"""
        # 初始化已存在的知识库实例
        self._initialize_existing_kbs()
        logger.info("KnowledgeBaseManager initialized")

    async def _load_all_metadata(self):
        """异步加载所有元数据 - 保留兼容性的空方法，现在由 KB 实例自行加载"""
        pass

    def _initialize_existing_kbs(self):
        """初始化已存在的知识库实例"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        async def _async_init():
            kb_repo = KnowledgeBaseRepository()
            rows = await kb_repo.get_all()

            kb_types_in_use = set()
            for row in rows:
                kb_type = row.kb_type or "lightrag"
                kb_types_in_use.add(kb_type)

            logger.info(f"[InitializeKB] 发现 {len(kb_types_in_use)} 种知识库类型: {kb_types_in_use}")

            # 为每种使用中的知识库类型创建实例并加载元数据
            for kb_type in kb_types_in_use:
                try:
                    kb_instance = self._get_or_create_kb_instance(kb_type)
                    # 让 KB 实例自行加载元数据
                    await kb_instance._load_metadata()
                    logger.info(f"[InitializeKB] {kb_type} 实例已初始化")
                except Exception as e:
                    logger.error(f"Failed to initialize {kb_type} knowledge base: {e}")
                    import traceback

                    logger.error(traceback.format_exc())

        # 在事件循环中运行异步初始化
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_async_init())
        except RuntimeError:
            asyncio.run(_async_init())

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

    async def move_file(self, db_id: str, file_id: str, new_parent_id: str | None) -> dict:
        """
        移动文件/文件夹
        """
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.move_file(db_id, file_id, new_parent_id)

    async def _get_kb_for_database(self, db_id: str) -> KnowledgeBase:
        """
        根据数据库ID获取对应的知识库实例

        Args:
            db_id: 数据库ID

        Returns:
            知识库实例

        Raises:
            KBNotFoundError: 数据库不存在或知识库类型不支持
        """
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(db_id)

        if kb is None:
            raise KBNotFoundError(f"Database {db_id} not found")

        kb_type = kb.kb_type or "lightrag"

        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            raise KBNotFoundError(f"Unsupported knowledge base type: {kb_type}")

        return self._get_or_create_kb_instance(kb_type)

    def _get_kb_for_database_sync(self, db_id: str) -> KnowledgeBase:
        """同步版本的 _get_kb_for_database，用于兼容同步调用"""
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self._get_kb_for_database(db_id))
        except RuntimeError:
            return asyncio.run(self._get_kb_for_database(db_id))

    # =============================================================================
    # 统一的外部接口 - 与原始 LightRagBasedKB 兼容
    # =============================================================================

    async def aget_kb(self, db_id: str) -> KnowledgeBase:
        """异步获取知识库实例

        Args:
            db_id: 数据库ID

        Returns:
            知识库实例
        """
        return await self._get_kb_for_database(db_id)

    def get_kb(self, db_id: str) -> KnowledgeBase:
        """同步获取知识库实例（兼容性方法，用于同步上下文）

        Args:
            db_id: 数据库ID

        Returns:
            知识库实例
        """
        return self._get_kb_for_database_sync(db_id)

    async def get_databases(self) -> dict:
        """获取所有数据库信息"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        rows = await kb_repo.get_all()
        all_databases = []
        metadata_reloaded_types: set[str] = set()
        for row in rows:
            kb_type = row.kb_type or "lightrag"
            kb_instance = self._get_or_create_kb_instance(kb_type)
            db_info = kb_instance.get_database_info(row.db_id, include_files=False)
            if not db_info and kb_type not in metadata_reloaded_types:
                try:
                    await kb_instance._load_metadata()
                    metadata_reloaded_types.add(kb_type)
                except Exception as e:
                    logger.warning(f"Failed to reload metadata for kb_type={kb_type}: {e}")
                db_info = kb_instance.get_database_info(row.db_id, include_files=False)

            if not db_info:
                logger.warning(f"Skip database due to missing metadata: db_id={row.db_id}, kb_type={kb_type}")
                continue

            # 补充 share_config 和 additional_params
            db_info["share_config"] = row.share_config or {"is_shared": True, "accessible_departments": []}
            db_info["additional_params"] = ensure_chunk_defaults_in_additional_params(row.additional_params)
            all_databases.append(db_info)
        return {"databases": all_databases}

    async def check_accessible(self, user: dict, db_id: str) -> bool:
        """检查用户是否有权限访问数据库

        Args:
            user: 用户信息字典
            db_id: 数据库ID

        Returns:
            bool: 是否有权限
        """
        # 超级管理员有权访问所有
        if user.get("role") == "superadmin":
            return True

        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(db_id)
        if kb is None:
            return False

        share_config = kb.share_config or {}
        is_shared = share_config.get("is_shared", True)

        # 如果是全员共享，则有权限
        if is_shared:
            return True

        # 检查部门权限
        user_department_id = user.get("department_id")
        accessible_departments = share_config.get("accessible_departments", [])

        if user_department_id is None:
            return False

        # 转换为整数进行比较（前端可能传递字符串，后端存储为整数）
        try:
            user_department_id = int(user_department_id)
            accessible_departments = [int(d) for d in accessible_departments]
        except (ValueError, TypeError):
            return False

        return user_department_id in accessible_departments

    async def get_databases_by_raw_id(self, user_id: int) -> dict:
        """根据用户ID获取知识库列表（原始ID版本，兼容旧接口）"""
        from yuxi.repositories.user_repository import UserRepository

        # 通过数据库获取用户信息
        user_repo = UserRepository()
        user: User | None = await user_repo.get_by_id(id=int(user_id))
        if not user:
            logger.warning(f"User not found: {user_id}")
            return {"databases": []}
        return await self.get_databases_by_user(user)

    async def get_databases_by_user_id(self, user_id: str) -> dict:
        """根据用户ID获取知识库列表（字符串ID版本）"""
        from yuxi.repositories.user_repository import UserRepository

        # 通过数据库获取用户信息
        user_repo = UserRepository()
        user: User | None = await user_repo.get_by_user_id(user_id)
        if not user:
            logger.warning(f"User not found: {user_id}")
            return {"databases": []}
        return await self.get_databases_by_user(user)

    async def get_databases_by_user(self, user: User | dict) -> dict:
        """根据用户权限获取知识库列表"""

        # 构建用户信息字典（支持 User 对象或 dict）
        if isinstance(user, dict):
            user_info = user
        else:
            user_info = {
                "role": user.role,
                "department_id": user.department_id,
            }

        user_role = user_info.get("role")
        user_dept = user_info.get("department_id")
        logger.info(f"Getting databases for user with role {user_role} and department {user_dept}")

        all_databases = (await self.get_databases()).get("databases", [])

        # 超级管理员可以看到所有知识库
        if user_info.get("role") == "superadmin":
            return {"databases": all_databases}

        filtered_databases = []

        for db in all_databases:
            db_id = db.get("db_id")
            if not db_id:
                continue

            if await self.check_accessible(user_info, db_id):
                filtered_databases.append(db)

        return {"databases": filtered_databases}

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

    async def create_folder(self, db_id: str, folder_name: str, parent_id: str = None) -> dict:
        """Create a folder in the database."""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.create_folder(db_id, folder_name, parent_id)

    async def create_database(
        self,
        database_name: str,
        description: str,
        kb_type: str = "lightrag",
        embed_info: dict | None = None,
        share_config: dict | None = None,
        **kwargs,
    ) -> dict:
        """
        创建数据库

        Args:
            database_name: 数据库名称
            description: 数据库描述
            kb_type: 知识库类型，默认为lightrag
            embed_info: 嵌入模型信息
            share_config: 共享配置
            **kwargs: 其他配置参数，包括chunk_size和chunk_overlap

        Returns:
            数据库信息字典
        """
        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            available_types = list(KnowledgeBaseFactory.get_available_types().keys())
            raise ValueError(f"Unsupported knowledge base type: {kb_type}. Available types: {available_types}")

        # 检查名称是否已存在
        if await self.database_name_exists(database_name):
            raise ValueError(f"知识库名称 '{database_name}' 已存在，请使用其他名称")

        # 默认共享配置
        if share_config is None:
            share_config = {"is_shared": True, "accessible_departments": []}

        kwargs = ensure_chunk_defaults_in_additional_params(kwargs)

        kb_instance = self._get_or_create_kb_instance(kb_type)
        db_info = await kb_instance.create_database(database_name, description, embed_info, **kwargs)
        db_id = db_info["db_id"]

        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        updated = await kb_repo.update(db_id, {"share_config": share_config})
        if updated is None:
            await kb_repo.create(
                {
                    "db_id": db_id,
                    "name": database_name,
                    "description": description,
                    "kb_type": kb_type,
                    "embed_info": embed_info,
                    "llm_info": db_info.get("llm_info"),
                    "additional_params": kwargs.copy(),
                    "share_config": share_config,
                }
            )

        logger.info(f"Created {kb_type} database: {database_name} ({db_id}) with {kwargs}")
        db_info["share_config"] = share_config
        return db_info

    async def delete_database(self, db_id: str) -> dict:
        """删除数据库"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        try:
            kb_instance = await self._get_kb_for_database(db_id)
            result = await kb_instance.delete_database(db_id)

            # 删除数据库记录
            kb_repo = KnowledgeBaseRepository()
            await kb_repo.delete(db_id)

            return result
        except KBNotFoundError as e:
            logger.warning(f"Database {db_id} not found during deletion: {e}")
            return {"message": "删除成功"}

    async def add_file_record(
        self, db_id: str, item: str, params: dict | None = None, operator_id: str | None = None
    ) -> dict:
        """Add file record to metadata"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.add_file_record(db_id, item, params, operator_id)

    async def parse_file(self, db_id: str, file_id: str, operator_id: str | None = None) -> dict:
        """Parse file to Markdown"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.parse_file(db_id, file_id, operator_id)

    async def index_file(
        self, db_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None
    ) -> dict:
        """Index parsed file"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.index_file(db_id, file_id, operator_id, params=params)

    async def update_file_params(self, db_id: str, file_id: str, params: dict, operator_id: str | None = None) -> None:
        """Update file processing params"""
        kb_instance = await self._get_kb_for_database(db_id)
        await kb_instance.update_file_params(db_id, file_id, params, operator_id)

    async def aquery(self, query_text: str, db_id: str, **kwargs) -> str:
        """异步查询知识库"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.aquery(query_text, db_id, **kwargs)

    async def export_data(self, db_id: str, format: str = "zip", **kwargs) -> str:
        """导出知识库数据"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.export_data(db_id, format=format, **kwargs)

    def query(self, query_text: str, db_id: str, **kwargs) -> str:
        """同步查询知识库（兼容性方法）"""
        kb_instance = self._get_kb_for_database_sync(db_id)
        return kb_instance.query(query_text, db_id, **kwargs)

    async def get_database_info(self, db_id: str) -> dict | None:
        """获取数据库详细信息"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(db_id)
        if kb is None:
            return None

        try:
            kb_instance = await self._get_kb_for_database(db_id)
            db_info = kb_instance.get_database_info(db_id)
        except KBNotFoundError:
            db_info = {
                "db_id": db_id,
                "name": kb.name,
                "description": kb.description,
                "kb_type": kb.kb_type,
                "files": {},
                "row_count": 0,
                "status": "已连接",
            }

        # 添加数据库中的附加字段
        db_info["additional_params"] = ensure_chunk_defaults_in_additional_params(kb.additional_params)
        db_info["share_config"] = kb.share_config or {"is_shared": True, "accessible_departments": []}
        db_info["mindmap"] = kb.mindmap
        db_info["sample_questions"] = kb.sample_questions or []
        db_info["query_params"] = kb.query_params

        return db_info

    async def delete_folder(self, db_id: str, folder_id: str) -> None:
        """递归删除文件夹"""
        kb_instance = await self._get_kb_for_database(db_id)
        await kb_instance.delete_folder(db_id, folder_id)

    async def delete_file(self, db_id: str, file_id: str) -> None:
        """删除文件"""
        kb_instance = await self._get_kb_for_database(db_id)
        await kb_instance.delete_file(db_id, file_id)

    async def update_content(self, db_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        """更新内容（重新分块）"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.update_content(db_id, file_ids, params or {})

    async def get_file_basic_info(self, db_id: str, file_id: str) -> dict:
        """获取文件基本信息（仅元数据）"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.get_file_basic_info(db_id, file_id)

    async def get_file_content(self, db_id: str, file_id: str) -> dict:
        """获取文件内容信息（chunks和lines）"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.get_file_content(db_id, file_id)

    async def open_file_content(self, db_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        """按行窗口打开文件解析后的 Markdown 内容"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.open_file_content(db_id, file_id, offset, limit)

    async def get_file_info(self, db_id: str, file_id: str) -> dict:
        """获取文件完整信息（基本信息+内容信息）- 保持向后兼容"""
        kb_instance = await self._get_kb_for_database(db_id)
        return await kb_instance.get_file_info(db_id, file_id)

    def get_db_upload_path(self, db_id: str | None = None) -> str:
        """获取数据库上传路径"""
        if db_id:
            try:
                kb_instance = self._get_kb_for_database_sync(db_id)
                return kb_instance.get_db_upload_path(db_id)
            except KBNotFoundError:
                # 如果数据库不存在，创建通用上传路径
                pass

        # 通用上传路径
        general_uploads = os.path.join(self.work_dir, "uploads")
        os.makedirs(general_uploads, exist_ok=True)
        return general_uploads

    async def file_name_existed_in_db(self, db_id: str | None, file_name: str | None) -> bool:
        """检查指定数据库中是否存在同名的文件"""
        if not db_id or not file_name:
            return False
        try:
            kb_instance = await self._get_kb_for_database(db_id)
        except KBNotFoundError:
            return False

        for file_info in kb_instance.files_meta.values():
            if file_info.get("database_id") != db_id:
                continue
            if file_info.get("status") == "failed":
                continue
            if file_info.get("file_name") == file_name:
                return True

        return False

    async def get_same_name_files(self, db_id: str, filename: str) -> list[dict]:
        """获取同一知识库中同名文件列表
        基于原始文件名直接比较
        返回基础信息：文件名、大小、上传时间

        Args:
            db_id: 数据库ID
            filename: 要检测的文件名（原始文件名）

        Returns:
            同名文件列表，每项包含：
            - filename: 文件名
            - size: 文件大小
            - created_at: 上传时间
            - file_id: 文件ID（用于下载）
        """
        if not db_id or not filename:
            return []
        try:
            kb_instance = await self._get_kb_for_database(db_id)
        except KBNotFoundError:
            return []

        same_name_files = []
        for file_id, file_info in kb_instance.files_meta.items():
            if file_info.get("database_id") != db_id:
                continue
            if file_info.get("status") == "failed":
                continue

            # 直接比较文件名（现在就是原始文件名）
            current_filename = file_info.get("filename", "")

            if current_filename.lower() == filename.lower():
                same_name_files.append(
                    {
                        "file_id": file_id,
                        "filename": current_filename,
                        "size": file_info.get("size", 0),
                        "created_at": file_info.get("created_at", ""),
                        "content_hash": file_info.get("content_hash", ""),
                    }
                )

        # 按上传时间降序排序
        same_name_files.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return same_name_files

    async def file_existed_in_db(self, db_id: str | None, content_hash: str | None) -> bool:
        """检查指定数据库中是否存在相同内容哈希的文件"""
        if not db_id or not content_hash:
            return False

        try:
            kb_instance = await self._get_kb_for_database(db_id)
        except KBNotFoundError:
            return False

        for file_info in kb_instance.files_meta.values():
            if file_info.get("database_id") != db_id:
                continue
            if file_info.get("status") == "failed":
                continue
            if file_info.get("content_hash") == content_hash:
                return True

        return False

    async def update_database(
        self,
        db_id: str,
        name: str,
        description: str,
        llm_info: dict = None,
        additional_params: dict | None = None,
        share_config: dict | None = None,
    ) -> dict:
        """更新数据库"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(db_id)
        if kb is None:
            raise ValueError(f"数据库 {db_id} 不存在")

        kb_instance = await self._get_kb_for_database(db_id)
        kb_instance.update_database(db_id, name, description, llm_info)

        # 准备更新数据
        update_data: dict = {
            "name": name,
            "description": description,
        }
        if llm_info is not None:
            update_data["llm_info"] = llm_info

        if additional_params is not None:
            merged_additional_params = ensure_chunk_defaults_in_additional_params(
                deep_merge(kb.additional_params or {}, additional_params)
            )
            update_data["additional_params"] = merged_additional_params
            if db_id in kb_instance.databases_meta:
                kb_instance.databases_meta[db_id]["metadata"] = merged_additional_params

        if share_config is not None:
            update_data["share_config"] = share_config

        # 保存到数据库
        await kb_repo.update(db_id, update_data)

        return await self.get_database_info(db_id)

    def get_retrievers(self) -> dict[str, dict]:
        """获取所有检索器"""
        all_retrievers = {}

        # 收集所有知识库的检索器
        for kb_instance in self.kb_instances.values():
            retrievers = kb_instance.get_retrievers()
            all_retrievers.update(retrievers)

        return all_retrievers

    # =============================================================================
    # 管理器特有的方法
    # =============================================================================

    def get_supported_kb_types(self) -> dict[str, dict]:
        """获取支持的知识库类型"""
        return KnowledgeBaseFactory.get_available_types()

    def get_kb_instance_info(self) -> dict[str, dict]:
        """获取知识库实例信息"""
        info = {}
        for kb_type, kb_instance in self.kb_instances.items():
            info[kb_type] = {
                "work_dir": kb_instance.work_dir,
                "database_count": len(kb_instance.databases_meta),
                "file_count": len(kb_instance.files_meta),
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
            kb_type = row.kb_type or "lightrag"
            if kb_type not in stats["kb_types"]:
                stats["kb_types"][kb_type] = 0
            stats["kb_types"][kb_type] += 1

        # 统计文件总数
        file_repo = KnowledgeFileRepository()
        files = await file_repo.get_all()
        stats["total_files"] = len(files)

        return stats

    # =============================================================================
    # 兼容性方法 - 为了支持现有的 graph_router.py
    # =============================================================================

    async def _get_lightrag_instance(self, db_id: str):
        """
        获取 LightRAG 实例（兼容性方法）

        Args:
            db_id: 数据库ID

        Returns:
            LightRAG 实例，如果数据库不是 lightrag 类型则返回 None

        Raises:
            ValueError: 如果数据库不存在或不是 lightrag 类型
        """
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(db_id)

        if kb is None:
            logger.error(f"Database {db_id} not found in global metadata")
            return None

        kb_type = kb.kb_type or "lightrag"
        if kb_type != "lightrag":
            logger.error(f"Database {db_id} is not a LightRAG type (actual type: {kb_type})")
            raise ValueError(f"Database {db_id} is not a LightRAG knowledge base")

        kb_instance = await self._get_kb_for_database(db_id)

        if not hasattr(kb_instance, "_get_lightrag_instance"):
            logger.error(f"Knowledge base instance for {db_id} is not LightRagKB")
            return None

        return await kb_instance._get_lightrag_instance(db_id)

    async def is_lightrag_database(self, db_id: str) -> bool:
        """
        检查数据库是否是 LightRAG 类型

        Args:
            db_id: 数据库ID

        Returns:
            是否是 LightRAG 类型的数据库
        """
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_id(db_id)
        if kb is None:
            return False
        return (kb.kb_type or "lightrag") == "lightrag"

    async def get_lightrag_databases(self) -> list[dict]:
        """
        获取所有 LightRAG 类型的数据库

        Returns:
            LightRAG 数据库列表
        """
        all_databases = (await self.get_databases())["databases"]
        return [db for db in all_databases if db.get("kb_type", "lightrag") == "lightrag"]

    # =============================================================================
    # 数据一致性检测方法
    # =============================================================================

    async def detect_data_inconsistencies(self) -> dict:
        """
        检测向量数据库中存在但在 metadata 中缺失的数据

        Returns:
            包含不一致信息的字典，按知识库类型分组
        """
        inconsistencies = {
            "milvus": {"missing_collections": [], "missing_files": []},
            "total_missing_collections": 0,
            "total_missing_files": 0,
        }

        logger.info("开始检测向量数据库与元数据的一致性...")

        # 检测 Milvus 数据不一致
        if "milvus" in self.kb_instances:
            try:
                milvus_inconsistencies = await self._detect_milvus_inconsistencies()
                inconsistencies["milvus"] = milvus_inconsistencies
                inconsistencies["total_missing_collections"] += len(milvus_inconsistencies["missing_collections"])
                inconsistencies["total_missing_files"] += len(milvus_inconsistencies["missing_files"])
            except Exception as e:
                logger.error(f"检测 Milvus 数据不一致时出错: {e}")

        # 输出检测结果到日志
        self._log_inconsistencies(inconsistencies)

        return inconsistencies

    async def _detect_milvus_inconsistencies(self) -> dict:
        """检测 Milvus 中的数据不一致"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        inconsistencies = {"missing_collections": [], "missing_files": []}

        milvus_kb = self.kb_instances["milvus"]

        try:
            from pymilvus import utility

            # 获取 Milvus 中所有实际的集合
            actual_collection_names = set(utility.list_collections(using=milvus_kb.connection_alias))

            # 从数据库获取所有已知的数据库ID
            kb_repo = KnowledgeBaseRepository()
            rows = await kb_repo.get_all()
            all_known_db_ids = {row.db_id for row in rows}

            lightrag_suffixes = ["_chunks", "_relationships", "_entities"]

            # 找出存在于 Milvus 但不在 metadata 中的集合
            # missing_collections = actual_collection_names - metadata_collection_names
            for collection_name in actual_collection_names:
                # 跳过一些系统集合
                if not collection_name.startswith("kb_"):
                    continue

                # 检查集合是否属于已知数据库
                is_known = False

                # 1. 精确匹配 (Milvus 类型的知识库)
                if collection_name in all_known_db_ids:
                    is_known = True
                # 2. 后缀匹配 (LightRAG 类型的知识库)
                else:
                    for suffix in lightrag_suffixes:
                        if collection_name.endswith(suffix):
                            potential_db_id = collection_name[: -len(suffix)]
                            if potential_db_id in all_known_db_ids:
                                is_known = True
                                break

                # 如果是已知集合，跳过
                if is_known:
                    continue

                # 如果是未知集合，记录下来
                collection_info = {"collection_name": collection_name, "detected_at": utc_isoformat()}

                # 尝试获取集合的基本信息
                try:
                    from pymilvus import Collection

                    collection = Collection(name=collection_name, using=milvus_kb.connection_alias)
                    collection_info["count"] = collection.num_entities
                    collection_info["description"] = collection.description
                except Exception as e:
                    logger.warning(f"无法获取集合 {collection_name} 的详细信息: {e}")
                    collection_info["count"] = "unknown"

                inconsistencies["missing_collections"].append(collection_info)
                logger.warning(
                    f"发现 Milvus 中存在但 metadata 中缺失的集合: {collection_name} "
                    f"(实体数: {collection_info['count']})"
                )

            # 获取 metadata 中记录的数据库ID（仅 Milvus 类型，用于检查文件一致性）
            metadata_collection_names = set(milvus_kb.databases_meta.keys())

            # 检查文件级别的不一致（针对已知的数据库）
            for db_id in metadata_collection_names:
                try:
                    if utility.has_collection(db_id, using=milvus_kb.connection_alias):
                        from pymilvus import Collection

                        collection = Collection(name=db_id, using=milvus_kb.connection_alias)
                        actual_count = collection.num_entities

                        # 获取 metadata 中记录的文件数量
                        metadata_files_count = sum(
                            1 for file_info in milvus_kb.files_meta.values() if file_info.get("database_id") == db_id
                        )

                        # 如果向量数据库中有数据但 metadata 中没有文件记录，可能存在文件缺失
                        if actual_count > 0 and metadata_files_count == 0:
                            inconsistencies["missing_files"].append(
                                {
                                    "database_id": db_id,
                                    "vector_count": actual_count,
                                    "metadata_files_count": metadata_files_count,
                                    "detected_at": utc_isoformat(),
                                }
                            )
                            logger.warning(
                                f"发现数据库 {db_id} 在 Milvus 中有 {actual_count} 条向量数据，"
                                "但 metadata 中没有文件记录"
                            )

                except Exception as e:
                    logger.debug(f"检查数据库 {db_id} 的文件一致性时出错: {e}")

        except Exception as e:
            logger.error(f"检测 Milvus 数据不一致时出错: {e}")

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
                    f"    - 数据库: {file_info['database_id']}, 向量数: {file_info['vector_count']}, "
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
