import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any

from yuxi.knowledge.chunking.ragflow_like.presets import (
    ensure_chunk_defaults_in_additional_params,
    resolve_chunk_processing_params,
)
from yuxi.knowledge.utils import sanitize_processing_params
from yuxi.utils import logger
from yuxi.utils.datetime_utils import coerce_any_to_utc_datetime, utc_isoformat


class FileStatus:
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    ERROR_PARSING = "error_parsing"
    INDEXING = "indexing"
    INDEXED = "indexed"
    ERROR_INDEXING = "error_indexing"
    # Legacy status mapping
    DONE = "done"  # Map to INDEXED
    FAILED = "failed"  # Generic failure


class KnowledgeBaseException(Exception):
    """知识库统一异常基类"""

    pass


class KBNotFoundError(KnowledgeBaseException):
    """知识库不存在错误"""

    pass


class KBOperationError(KnowledgeBaseException):
    """知识库操作错误"""

    pass


class KnowledgeBase(ABC):
    """知识库抽象基类，定义统一接口"""

    # 类级别的处理队列，跟踪所有正在处理的文件
    _processing_files = set()
    _processing_lock = None

    def __init__(self, work_dir: str):
        """
        初始化知识库

        Args:
            work_dir: 工作目录
        """
        import threading

        self.work_dir = work_dir
        self.databases_meta: dict[str, dict] = {}
        self.files_meta: dict[str, dict] = {}
        self.benchmarks_meta: dict[str, dict] = {}
        self._metadata_loaded = False  # 标记元数据是否已加载

        # 初始化类级别的锁
        if KnowledgeBase._processing_lock is None:
            KnowledgeBase._processing_lock = threading.Lock()

        os.makedirs(work_dir, exist_ok=True)

        # 注意：不在 __init__ 中加载元数据，由 KnowledgeBaseManager 统一管理加载

    def load_metadata(
        self, global_databases_meta: dict[str, dict], files_meta: dict[str, dict], benchmarks_meta: dict[str, dict]
    ):
        """由 KnowledgeBaseManager 调用，同步加载元数据"""
        # 过滤出当前 kb_type 的知识库
        self.databases_meta = {}
        for db_id, meta in global_databases_meta.items():
            if meta.get("kb_type") == self.kb_type:
                normalized_additional_params = ensure_chunk_defaults_in_additional_params(meta.get("additional_params"))
                self.databases_meta[db_id] = {
                    "name": meta.get("name"),
                    "description": meta.get("description"),
                    "kb_type": meta.get("kb_type"),
                    "embed_info": meta.get("embed_info"),
                    "llm_info": meta.get("llm_info"),
                    "query_params": meta.get("query_params"),
                    "metadata": normalized_additional_params,
                    "created_at": meta.get("created_at"),
                }

        # 过滤文件
        self.files_meta = {}
        for file_id, meta in files_meta.items():
            if meta.get("database_id") in self.databases_meta:
                db_id = meta.get("database_id")
                kb_additional_params = self.databases_meta.get(db_id, {}).get("metadata") or {}
                normalized_meta = dict(meta)
                normalized_meta["processing_params"] = resolve_chunk_processing_params(
                    kb_additional_params=kb_additional_params,
                    file_processing_params=meta.get("processing_params"),
                )
                self.files_meta[file_id] = normalized_meta

        # 过滤评估基准
        self.benchmarks_meta = {}
        for kb_id, benchmarks in benchmarks_meta.items():
            if kb_id in self.databases_meta:
                self.benchmarks_meta[kb_id] = benchmarks

        self._normalize_metadata_state()
        self._metadata_loaded = True
        logger.info(f"{self.kb_type}: 加载了 {len(self.databases_meta)} 个数据库的元数据")

    def _ensure_metadata_loaded(self):
        """确保元数据已加载（延迟加载）"""
        if not self._metadata_loaded:
            logger.warning(f"{self.kb_type}: 元数据尚未加载，请确保 KnowledgeBaseManager 已调用 load_metadata()")

    @staticmethod
    def _normalize_timestamp(value: Any) -> str | None:
        """Convert persisted timestamps to a normalized UTC ISO string."""
        try:
            dt_value = coerce_any_to_utc_datetime(value)
        except (TypeError, ValueError) as exc:  # noqa: BLE001
            logger.warning(f"Invalid timestamp encountered: {value!r} ({exc})")
            return None

        if not dt_value:
            return None
        return utc_isoformat(dt_value)

    def _normalize_metadata_state(self) -> None:
        """Ensure in-memory metadata uses normalized timestamp formats."""
        for meta in self.databases_meta.values():
            if "created_at" in meta:
                normalized = self._normalize_timestamp(meta.get("created_at"))
                if normalized:
                    meta["created_at"] = normalized

        for file_info in self.files_meta.values():
            if "created_at" in file_info:
                normalized = self._normalize_timestamp(file_info.get("created_at"))
                if normalized:
                    file_info["created_at"] = normalized

        for db_benchmarks in self.benchmarks_meta.values():
            for b in db_benchmarks.values():
                if "created_at" in b:
                    normalized = self._normalize_timestamp(b.get("created_at"))
                    if normalized:
                        b["created_at"] = normalized
                if "updated_at" in b:
                    normalized = self._normalize_timestamp(b.get("updated_at"))
                    if normalized:
                        b["updated_at"] = normalized

    @property
    @abstractmethod
    def kb_type(self) -> str:
        """知识库类型标识"""
        pass

    @abstractmethod
    async def _create_kb_instance(self, db_id: str, config: dict) -> Any:
        """
        创建底层知识库实例

        Args:
            db_id: 数据库ID
            config: 配置信息

        Returns:
            底层知识库实例
        """
        pass

    @abstractmethod
    async def _initialize_kb_instance(self, instance: Any) -> None:
        """
        初始化底层知识库实例

        Args:
            instance: 底层知识库实例
        """
        pass

    async def add_file_record(
        self, db_id: str, item: str, params: dict | None = None, operator_id: str | None = None
    ) -> dict:
        """
        Add a file record to metadata (Status: UPLOADED)

        Args:
            db_id: Database ID
            item: File path or URL
            params: Parameters
            operator_id: Operator ID who created the file

        Returns:
            File metadata record
        """
        from yuxi.knowledge.utils.kb_utils import prepare_item_metadata

        params = params or {}
        content_type = params.get("content_type", "file")

        # Prepare metadata
        metadata = await prepare_item_metadata(item, content_type, db_id, params=params)
        file_id = metadata["file_id"]
        kb_additional_params = self.databases_meta.get(db_id, {}).get("metadata") or {}
        metadata["processing_params"] = resolve_chunk_processing_params(
            kb_additional_params=kb_additional_params,
            file_processing_params=metadata.get("processing_params"),
        )

        # Initial status
        metadata["status"] = FileStatus.UPLOADED
        metadata["created_at"] = utc_isoformat()
        if operator_id:
            metadata["created_by"] = operator_id

        # Save to metadata
        self.files_meta[file_id] = metadata
        await self._persist_file(file_id)

        return metadata

    async def parse_file(self, db_id: str, file_id: str, operator_id: str | None = None) -> dict:
        """
        Parse file to Markdown and save to MinIO (Status: PARSING -> PARSED/ERROR_PARSING)

        Args:
            db_id: Database ID
            file_id: File ID
            operator_id: ID of the user performing the operation

        Returns:
            Updated file metadata
        """
        if file_id not in self.files_meta:
            raise ValueError(f"File {file_id} not found")

        file_meta = self.files_meta[file_id]
        current_status = file_meta.get("status")

        # Validate current status - only allow parsing from these states
        allowed_statuses = {
            FileStatus.UPLOADED,
            FileStatus.ERROR_PARSING,
            "failed",  # Legacy status
        }

        if current_status not in allowed_statuses:
            raise ValueError(
                f"Cannot parse file with status '{current_status}'. "
                f"File must be in one of these states: {', '.join(allowed_statuses)}"
            )

        file_path = file_meta.get("path")
        if not file_path:
            raise ValueError(f"File {file_id} has no valid path in metadata")

        # Clear previous error if any
        if "error" in file_meta:
            self.files_meta[file_id].pop("error", None)

        # Update status to PARSING and add to processing queue
        self.files_meta[file_id]["status"] = FileStatus.PARSING
        self.files_meta[file_id]["updated_at"] = utc_isoformat()
        if operator_id:
            self.files_meta[file_id]["updated_by"] = operator_id
        await self._persist_file(file_id)

        # Add to processing queue
        self._add_to_processing_queue(file_id)

        try:
            from yuxi.plugins.parser.unified import Parser

            # Prepare params
            params = file_meta.get("processing_params", {}) or {}
            params["image_bucket"] = "public"
            params["image_prefix"] = f"{db_id}/kb-images"

            markdown_content = await Parser.aparse(
                source=file_path,
                params=params,
            )

            # Save Markdown to MinIO
            markdown_file_path = await self._save_markdown_to_minio(db_id, file_id, markdown_content)

            # Update metadata
            self.files_meta[file_id]["status"] = FileStatus.PARSED
            self.files_meta[file_id]["markdown_file"] = markdown_file_path
            self.files_meta[file_id]["updated_at"] = utc_isoformat()
            if operator_id:
                self.files_meta[file_id]["updated_by"] = operator_id
            await self._persist_file(file_id)

            return self.files_meta[file_id]

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to parse file {file_id}: {error_msg}")

            self.files_meta[file_id]["status"] = FileStatus.ERROR_PARSING
            self.files_meta[file_id]["error"] = error_msg
            self.files_meta[file_id]["updated_at"] = utc_isoformat()
            if operator_id:
                self.files_meta[file_id]["updated_by"] = operator_id
            await self._persist_file(file_id)

            raise

        finally:
            # Remove from processing queue
            self._remove_from_processing_queue(file_id)

    async def update_file_params(self, db_id: str, file_id: str, params: dict, operator_id: str | None = None) -> None:
        """Update file processing params"""
        if file_id not in self.files_meta:
            raise ValueError(f"File {file_id} not found")

        # Skip if no params to update
        if not params:
            return

        current_params = self.files_meta[file_id].get("processing_params", {}) or {}
        kb_additional_params = self.databases_meta.get(db_id, {}).get("metadata") or {}

        logger.debug(f"[update_file_params] file_id={file_id}, current_params={current_params}, new_params={params}")

        current_params = resolve_chunk_processing_params(
            kb_additional_params=kb_additional_params,
            file_processing_params=current_params,
            request_params=params,
        )

        self.files_meta[file_id]["processing_params"] = current_params
        self.files_meta[file_id]["updated_at"] = utc_isoformat()
        if operator_id:
            self.files_meta[file_id]["updated_by"] = operator_id

        logger.debug(f"[update_file_params] file_id={file_id}, updated_params={current_params}")

        await self._persist_file(file_id)

    async def _mark_file_unparsed(self, file_id: str, operator_id: str | None = None) -> None:
        if file_id not in self.files_meta:
            return

        self.files_meta[file_id]["status"] = FileStatus.UPLOADED
        self.files_meta[file_id].pop("markdown_file", None)
        self.files_meta[file_id].pop("error", None)
        self.files_meta[file_id]["updated_at"] = utc_isoformat()
        if operator_id:
            self.files_meta[file_id]["updated_by"] = operator_id
        await self._persist_file(file_id)

    async def _save_markdown_to_minio(self, db_id: str, file_id: str, content: str) -> str:
        """Save markdown content to MinIO and return HTTP URL"""
        from yuxi.storage.minio import get_minio_client

        minio_client = get_minio_client()
        bucket_name = minio_client.KB_BUCKETS["parsed"]
        await asyncio.to_thread(minio_client.ensure_bucket_exists, bucket_name)

        object_name = f"{db_id}/parsed/{file_id}.md"
        data = content.encode("utf-8")

        # Return standard HTTP URL from UploadResult
        upload_result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=data,
        )

        return upload_result.url

    async def _read_markdown_from_minio(self, file_path: str) -> str:
        """Read markdown content from MinIO"""
        from yuxi.knowledge.utils.kb_utils import parse_minio_url
        from yuxi.storage.minio import get_minio_client

        if not file_path.startswith(("http://", "https://")):
            raise ValueError(f"Invalid MinIO path format: {file_path}")

        bucket_name, object_name = parse_minio_url(file_path)
        minio_client = get_minio_client()

        content_bytes = await minio_client.adownload_file(bucket_name, object_name)
        return content_bytes.decode("utf-8")

    def _build_open_file_window(self, content: str, *, offset: int = 0, limit: int = 800) -> dict[str, Any]:
        lines = content.splitlines()
        total_lines = len(lines)
        start = min(max(int(offset), 0), total_lines)
        window_size = min(max(int(limit), 1), 2000)
        selected = lines[start : start + window_size]
        end = start + len(selected)

        return {
            "start_line": start + 1 if selected else 0,
            "end_line": end,
            "total_lines": total_lines,
            "offset": start,
            "window_size": window_size,
            "has_more_before": start > 0,
            "has_more_after": end < total_lines,
            "next_offset": end if end < total_lines else None,
            "content": "\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected)),
        }

    async def open_file_content(self, db_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        """按行窗口打开文件解析后的 Markdown 内容"""
        file_meta = self.files_meta.get(file_id)
        if file_meta is None:
            raise Exception(f"文件不存在: {file_id}")
        if file_meta.get("database_id") != db_id:
            raise Exception(f"文件 {file_id} 不属于知识库 {db_id}")
        if file_meta.get("is_folder"):
            raise Exception(f"文件 {file_id} 是文件夹")

        markdown_file = file_meta.get("markdown_file")
        if not markdown_file:
            raise Exception(f"文件 {file_id} 没有解析后的 Markdown 内容")

        content = await self._read_markdown_from_minio(markdown_file)
        return self._build_open_file_window(content, offset=offset, limit=limit)

    @abstractmethod
    async def index_file(self, db_id: str, file_id: str, operator_id: str | None = None) -> dict:
        """
        Index parsed file (Status: INDEXING -> INDEXED/ERROR_INDEXING)

        Args:
            db_id: Database ID
            file_id: File ID
            operator_id: ID of the user performing the operation

        Returns:
            Updated file metadata
        """
        pass

    async def create_database(
        self,
        database_name: str,
        description: str,
        embed_info: dict | None = None,
        llm_info: dict | None = None,
        **kwargs,
    ) -> dict:
        """
        创建数据库

        Args:
            database_name: 数据库名称
            description: 数据库描述
            embed_info: 嵌入模型信息
            llm_info: LLM配置信息
            **kwargs: 其他配置参数

        Returns:
            数据库信息字典
        """
        from yuxi.utils import hashstr

        kwargs = ensure_chunk_defaults_in_additional_params(kwargs)

        # 从 kwargs 中获取 is_private 配置
        is_private = kwargs.get("is_private", False)
        prefix = "kb_private_" if is_private else "kb_"
        db_id = f"{prefix}{hashstr(database_name, with_salt=True, length=32)}"

        # 创建数据库记录
        # 确保 Pydantic 模型被转换为字典，以便 JSON 序列化
        embed_info_dump = embed_info.model_dump() if hasattr(embed_info, "model_dump") else embed_info
        self.databases_meta[db_id] = {
            "name": database_name,
            "description": description,
            "kb_type": self.kb_type,
            "embed_info": embed_info_dump,
            "llm_info": llm_info.model_dump() if hasattr(llm_info, "model_dump") else llm_info,
            "metadata": kwargs,
            "created_at": utc_isoformat(),
            "query_params": self._get_default_query_params(db_id),
        }
        await self._persist_kb(db_id)

        # 创建工作目录
        working_dir = os.path.join(self.work_dir, db_id)
        os.makedirs(working_dir, exist_ok=True)

        # 返回数据库信息
        db_dict = self.databases_meta[db_id].copy()
        db_dict["db_id"] = db_id
        db_dict["files"] = {}

        return db_dict

    async def delete_database(self, db_id: str) -> dict:
        """
        删除数据库

        Args:
            db_id: 数据库ID

        Returns:
            操作结果
        """
        if db_id in self.databases_meta:
            from yuxi.knowledge.utils.kb_utils import parse_minio_url
            from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
            from yuxi.storage.minio import get_minio_client

            minio_client = get_minio_client()

            # 1. 删除文件元数据中记录的 MinIO 文件
            files_to_delete = [fid for fid, finfo in self.files_meta.items() if finfo.get("database_id") == db_id]
            for file_id in files_to_delete:
                file_path = self.files_meta[file_id].get("path")
                if file_path and file_path.startswith(("http://", "https://")):
                    try:
                        bucket_name, object_name = parse_minio_url(file_path)
                        await minio_client.adelete_file(bucket_name, object_name)
                    except Exception as e:
                        logger.warning(f"Failed to delete MinIO file {file_path}: {e}")

                # 删除解析后的 markdown 文件
                parsed_object = f"{db_id}/parsed/{file_id}.md"
                await minio_client.adelete_file(minio_client.KB_BUCKETS["parsed"], parsed_object)

                del self.files_meta[file_id]

            # 2. 并行删除所有知识库 bucket 中该 db_id 下的文件
            prefix = f"{db_id}/"
            cleanup_buckets = {
                minio_client.KB_BUCKETS["parsed"],
                minio_client.KB_BUCKETS["documents"],
                minio_client.KB_BUCKETS["images"],
            }
            cleanup_tasks = [
                minio_client.adelete_objects_by_prefix(bucket_name, prefix) for bucket_name in cleanup_buckets
            ]
            await asyncio.gather(*cleanup_tasks)

            # 3. 删除数据库记录
            del self.databases_meta[db_id]
            kb_repo = KnowledgeBaseRepository()
            await kb_repo.delete(db_id)
            await self._save_metadata()

        # 删除工作目录
        working_dir = os.path.join(self.work_dir, db_id)
        if os.path.exists(working_dir):
            import shutil

            try:
                shutil.rmtree(working_dir)
            except Exception as e:
                logger.error(f"Error deleting working directory {working_dir}: {e}")

        return {"message": "删除成功"}

    async def create_folder(self, db_id: str, folder_name: str, parent_id: str | None = None) -> dict:
        """Create a folder in the database."""
        import uuid

        folder_id = f"folder-{uuid.uuid4()}"

        self.files_meta[folder_id] = {
            "file_id": folder_id,
            "filename": folder_name,
            "is_folder": True,
            "parent_id": parent_id,
            "database_id": db_id,
            "created_at": utc_isoformat(),
            "status": "done",
            "path": folder_name,
            "file_type": "folder",
        }
        await self._persist_file(folder_id)
        return self.files_meta[folder_id]

    @abstractmethod
    async def update_content(self, db_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        """
        更新内容 - 根据file_ids重新解析文件并更新向量库

        Args:
            db_id: 数据库ID
            file_ids: 文件ID列表
            params: 处理参数

        Returns:
            更新结果列表
        """
        pass

    @abstractmethod
    async def aquery(self, query_text: str, db_id: str, **kwargs) -> list[dict]:
        """
        异步查询知识库

        Args:
            query_text: 查询文本
            db_id: 数据库ID
            **kwargs: 查询参数

        Returns:
            一个包含字典的列表，每个字典代表一个检索到的文档块。
        """
        pass

    @abstractmethod
    def get_query_params_config(self, db_id: str, **kwargs) -> dict:
        """
        获取知识库类型的查询参数配置

        Args:
            db_id: 数据库ID
            **kwargs: 额外参数(如 reranker_names 等)

        Returns:
            dict: {
                "type": "kb_type",
                "options": [
                    {
                        "key": "param_name",
                        "label": "参数名称",
                        "type": "select|number|boolean",
                        "default": default_value,
                        "options": [...],  # 对于 select 类型
                        "description": "参数描述",
                        "min": 1,  # 对于 number 类型
                        "max": 100,
                        "step": 0.1
                    },
                    ...
                ]
            }
        """
        pass

    async def export_data(self, db_id: str, format: str = "zip", **kwargs) -> str:
        pass

    def query(self, query_text: str, db_id: str, **kwargs) -> list[dict]:
        """
        同步查询知识库（兼容性方法）

        Args:
            query_text: 查询文本
            db_id: 数据库ID
            **kwargs: 查询参数

        Returns:
            一个包含字典的列表，每个字典代表一个检索到的文档块。
        """
        import asyncio

        logger.warning("query is deprecated, use aquery instead")
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.aquery(query_text, db_id, **kwargs))
        except RuntimeError:
            return asyncio.run(self.aquery(query_text, db_id, **kwargs))

    def _get_query_params(self, db_id: str) -> dict:
        """从实例元数据中加载查询参数"""
        if db_id in self.databases_meta:
            query_params_meta = self.databases_meta[db_id].get("query_params") or {}
            return query_params_meta.get("options", {})
        return {}

    def _get_default_query_params(self, db_id: str) -> dict[str, Any]:
        """从 get_query_params_config 中提取所有参数的默认值，返回 {"options": {...}}"""
        config = self.get_query_params_config(db_id)
        defaults = {}
        for opt in config.get("options", []):
            if "default" in opt:
                defaults[opt["key"]] = opt["default"]
        return {"options": defaults}

    def get_database_info(self, db_id: str, include_files: bool = True) -> dict | None:
        """
        获取数据库详细信息

        Args:
            db_id: 数据库ID
            include_files: 是否包含文件信息，默认为True（保持向后兼容）

        Returns:
            数据库信息或None
        """
        if db_id not in self.databases_meta:
            return None

        meta = self.databases_meta[db_id].copy()
        meta["db_id"] = db_id

        # 检查并修复异常的processing状态
        self._check_and_fix_processing_status(db_id)

        # 统计文件数量（始终计算，即使不加载文件详情）
        db_file_count = sum(1 for file_info in self.files_meta.values() if file_info.get("database_id") == db_id)
        meta["row_count"] = db_file_count

        # 仅在需要时加载文件详情
        if include_files:
            db_files = {}
            for file_id, file_info in self.files_meta.items():
                if file_info.get("database_id") == db_id:
                    created_at = self._normalize_timestamp(file_info.get("created_at"))
                    db_files[file_id] = {
                        "file_id": file_id,
                        "filename": file_info.get("filename", ""),
                        "path": file_info.get("path", ""),
                        "markdown_file": file_info.get("markdown_file", ""),
                        "type": file_info.get("file_type", ""),
                        "status": file_info.get("status", "done"),
                        "created_at": created_at,
                        "is_folder": file_info.get("is_folder", False),
                        "parent_id": file_info.get("parent_id", None),
                    }

            # 按创建时间倒序排序文件列表
            sorted_files = dict(
                sorted(
                    db_files.items(),
                    key=lambda item: item[1].get("created_at") or "",
                    reverse=True,
                )
            )

            meta["files"] = sorted_files

        meta["status"] = "已连接"
        return meta

    def get_databases(self, include_files: bool = False) -> dict:
        """
        获取所有数据库信息

        Args:
            include_files: 是否包含文件信息，默认False以减少响应大小

        Returns:
            数据库列表
        """
        # 确保元数据已加载（延迟加载机制）
        self._ensure_metadata_loaded()

        databases = []
        for db_id, meta in self.databases_meta.items():
            # 检查并修复异常的processing状态
            self._check_and_fix_processing_status(db_id)

            db_dict = meta.copy()
            db_dict["db_id"] = db_id

            # 统计文件数量（始终计算，即使不加载文件详情）
            db_file_count = sum(1 for file_info in self.files_meta.values() if file_info.get("database_id") == db_id)
            db_dict["row_count"] = db_file_count

            # 仅在需要时加载文件详情
            if include_files:
                db_files = {}
                for file_id, file_info in self.files_meta.items():
                    if file_info.get("database_id") == db_id:
                        created_at = self._normalize_timestamp(file_info.get("created_at"))
                        db_files[file_id] = {
                            "file_id": file_id,
                            "filename": file_info.get("filename", ""),
                            "path": file_info.get("path", ""),
                            "markdown_file": file_info.get("markdown_file", ""),
                            "type": file_info.get("file_type", ""),
                            "status": file_info.get("status", "done"),
                            "created_at": created_at,
                            "is_folder": file_info.get("is_folder", False),
                            "parent_id": file_info.get("parent_id", None),
                        }

                # 按创建时间倒序排序文件列表
                sorted_files = dict(
                    sorted(
                        db_files.items(),
                        key=lambda item: item[1].get("created_at") or "",
                        reverse=True,
                    )
                )

                db_dict["files"] = sorted_files

            db_dict["status"] = "已连接"
            databases.append(db_dict)

        return {"databases": databases}

    @classmethod
    def _add_to_processing_queue(cls, file_id: str) -> None:
        """
        将文件添加到处理队列

        Args:
            file_id: 文件ID
        """
        with cls._processing_lock:
            cls._processing_files.add(file_id)
            logger.debug(f"Added file {file_id} to processing queue")

    @classmethod
    def _remove_from_processing_queue(cls, file_id: str) -> None:
        """
        从处理队列中移除文件

        Args:
            file_id: 文件ID
        """
        with cls._processing_lock:
            cls._processing_files.discard(file_id)
            logger.debug(f"Removed file {file_id} from processing queue")

    @classmethod
    def _is_file_in_processing_queue(cls, file_id: str) -> bool:
        """
        检查文件是否在处理队列中

        Args:
            file_id: 文件ID

        Returns:
            bool: 文件是否在处理队列中
        """
        with cls._processing_lock:
            return file_id in cls._processing_files

    def _check_and_fix_processing_status(self, db_id: str) -> None:
        """
        检查并修复异常的处理中状态
        如果文件状态为处理中但实际不在处理队列中，则修改为相应的错误状态

        Args:
            db_id: 数据库ID
        """
        try:
            status_changed = False

            # 定义需要检查的中间状态及其对应的错误状态
            intermediate_states = {
                FileStatus.PARSING: FileStatus.ERROR_PARSING,
                FileStatus.INDEXING: FileStatus.ERROR_INDEXING,
                "processing": "failed",  # 兼容旧状态
            }

            # 检查该数据库下所有中间状态的文件
            for file_id, file_info in self.files_meta.items():
                if file_info.get("database_id") == db_id:
                    current_status = file_info.get("status")

                    if current_status in intermediate_states:
                        # 检查文件是否真的在处理队列中
                        if not self._is_file_in_processing_queue(file_id):
                            error_status = intermediate_states[current_status]
                            logger.warning(
                                f"File {file_id} has {current_status} status but is not in processing queue, "
                                f"marking as {error_status}"
                            )
                            self.files_meta[file_id]["status"] = error_status
                            self.files_meta[file_id]["error"] = (
                                f"{current_status.capitalize()} interrupted - process not found in queue"
                            )
                            self.files_meta[file_id]["updated_at"] = utc_isoformat()
                            status_changed = True

            # 如果有状态变更，保存元数据
            if status_changed:
                logger.info(f"Fixed interrupted processing status for database {db_id}")

        except Exception as e:
            logger.error(f"Error checking processing status for database {db_id}: {e}")

    async def delete_folder(self, db_id: str, folder_id: str) -> None:
        """
        Recursively delete a folder and its content.

        Args:
            db_id: Database ID
            folder_id: Folder ID to delete
        """
        # Find all children
        children = [
            fid
            for fid, meta in self.files_meta.items()
            if meta.get("database_id") == db_id and meta.get("parent_id") == folder_id
        ]

        for child_id in children:
            child_meta = self.files_meta.get(child_id)
            if child_meta and child_meta.get("is_folder"):
                await self.delete_folder(db_id, child_id)
            else:
                await self.delete_file(db_id, child_id)

        # Delete the folder itself
        # We call delete_file which should handle the actual removal.
        # Implementations should ensure they handle folder deletion gracefully (e.g. skip vector deletion)
        await self.delete_file(db_id, folder_id)

    async def move_file(self, db_id: str, file_id: str, new_parent_id: str | None) -> dict:
        """
        Move a file or folder to a new parent folder.

        Args:
            db_id: Database ID
            file_id: File/Folder ID to move
            new_parent_id: New parent folder ID (None for root)

        Returns:
            dict: Updated metadata
        """
        if file_id not in self.files_meta:
            raise ValueError(f"File {file_id} not found")

        meta = self.files_meta[file_id]
        if meta.get("database_id") != db_id:
            raise ValueError(f"File {file_id} does not belong to database {db_id}")

        # Basic cycle detection for folders
        if meta.get("is_folder") and new_parent_id:
            # Check if new_parent_id is a child of file_id (or is file_id itself)
            if new_parent_id == file_id:
                raise ValueError("Cannot move a folder into itself")

            # Walk up the tree from new_parent_id
            current = new_parent_id
            while current:
                parent_meta = self.files_meta.get(current)
                if not parent_meta:
                    break  # Should not happen if integrity is maintained
                if current == file_id:
                    raise ValueError("Cannot move a folder into its own subfolder")
                current = parent_meta.get("parent_id")

        meta["parent_id"] = new_parent_id
        await self._persist_file(file_id)
        return meta

    @abstractmethod
    async def delete_file(self, db_id: str, file_id: str) -> None:
        """
        删除文件

        Args:
            db_id: 数据库ID
            file_id: 文件ID
        """
        pass

    @abstractmethod
    async def get_file_basic_info(self, db_id: str, file_id: str) -> dict:
        """
        获取文件基本信息（仅元数据）

        Args:
            db_id: 数据库ID
            file_id: 文件ID

        Returns:
            dict: 包含文件基本信息的字典
        """
        pass

    @abstractmethod
    async def get_file_content(self, db_id: str, file_id: str) -> dict:
        """
        获取文件内容信息（chunks和lines）

        Args:
            db_id: 数据库ID
            file_id: 文件ID

        Returns:
            dict: 包含文件内容信息的字典
        """
        pass

    @abstractmethod
    async def get_file_info(self, db_id: str, file_id: str) -> dict:
        """
        获取文件完整信息（基本信息+内容信息）- 保持向后兼容

        Args:
            db_id: 数据库ID
            file_id: 文件ID

        Returns:
            dict: 包含文件信息和chunks的字典
        """
        pass

    def get_db_upload_path(self, db_id: str | None = None) -> str:
        """
        获取数据库上传路径

        Args:
            db_id: 数据库ID，可选

        Returns:
            上传路径
        """
        if db_id:
            uploads_folder = os.path.join(self.work_dir, db_id, "uploads")
            os.makedirs(uploads_folder, exist_ok=True)
            return uploads_folder

        general_uploads = os.path.join(self.work_dir, "uploads")
        os.makedirs(general_uploads, exist_ok=True)
        return general_uploads

    def update_database(self, db_id: str, name: str, description: str, llm_info: dict = None) -> dict:
        """
        更新数据库

        Args:
            db_id: 数据库ID
            name: 新名称
            description: 新描述
            llm_info: LLM配置信息（可选，仅用于 LightRAG 类型知识库）

        Returns:
            更新后的数据库信息
        """
        if db_id not in self.databases_meta:
            raise ValueError(f"数据库 {db_id} 不存在")

        self.databases_meta[db_id]["name"] = name
        self.databases_meta[db_id]["description"] = description

        # 如果提供了 llm_info，则更新（仅针对 LightRAG 类型）
        if llm_info is not None:
            self.databases_meta[db_id]["llm_info"] = llm_info

        asyncio.create_task(self._save_metadata())

        return self.get_database_info(db_id)

    def get_retrievers(self) -> dict[str, dict]:
        """
        获取所有检索器

        Returns:
            检索器字典
        """
        retrievers = {}
        for db_id, meta in self.databases_meta.items():

            def make_retriever(db_id):
                async def retriever(query_text, **kwargs):
                    return await self.aquery(query_text, db_id, agent_call=True, **kwargs)

                return retriever

            retrievers[db_id] = {
                "name": meta["name"],
                "description": meta["description"],
                "retriever": make_retriever(db_id),
                "metadata": meta,
            }
        return retrievers

    async def _load_metadata(self) -> None:
        from yuxi.repositories.evaluation_repository import EvaluationRepository
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        kb_repo = KnowledgeBaseRepository()
        file_repo = KnowledgeFileRepository()
        eval_repo = EvaluationRepository()

        databases = [kb for kb in await kb_repo.get_all() if kb.kb_type == self.kb_type]
        self.databases_meta = {
            kb.db_id: {
                "name": kb.name,
                "description": kb.description,
                "kb_type": kb.kb_type,
                "embed_info": kb.embed_info,
                "llm_info": kb.llm_info,
                "query_params": kb.query_params or self._get_default_query_params(kb.db_id),
                "metadata": ensure_chunk_defaults_in_additional_params(kb.additional_params),
                "created_at": utc_isoformat(kb.created_at) if kb.created_at else utc_isoformat(),
            }
            for kb in databases
        }

        self.files_meta = {}
        for kb in databases:
            kb_additional_params = self.databases_meta.get(kb.db_id, {}).get("metadata") or {}
            for record in await file_repo.list_by_db_id(kb.db_id):
                self.files_meta[record.file_id] = {
                    "file_id": record.file_id,
                    "database_id": record.db_id,
                    "parent_id": record.parent_id,
                    "filename": record.filename,
                    "file_type": record.file_type,
                    "path": record.path,
                    "markdown_file": record.markdown_file,
                    "status": record.status,
                    "content_hash": record.content_hash,
                    "size": record.file_size,
                    "content_type": record.content_type,
                    "processing_params": sanitize_processing_params(
                        resolve_chunk_processing_params(
                            kb_additional_params=kb_additional_params,
                            file_processing_params=record.processing_params,
                        )
                    ),
                    "is_folder": record.is_folder,
                    "error": record.error_message,
                    "created_by": record.created_by,
                    "updated_by": record.updated_by,
                    "created_at": utc_isoformat(record.created_at) if record.created_at else None,
                    "updated_at": utc_isoformat(record.updated_at) if record.updated_at else None,
                    "original_filename": record.original_filename,
                    "minio_url": record.minio_url,
                }

        self.benchmarks_meta = {}
        for kb in databases:
            benchmarks = await eval_repo.list_benchmarks(kb.db_id)
            if not benchmarks:
                continue
            self.benchmarks_meta[kb.db_id] = {}
            for bench in benchmarks:
                self.benchmarks_meta[kb.db_id][bench.benchmark_id] = {
                    "id": bench.benchmark_id,
                    "benchmark_id": bench.benchmark_id,
                    "name": bench.name,
                    "description": bench.description,
                    "db_id": bench.db_id,
                    "question_count": bench.question_count,
                    "has_gold_chunks": bench.has_gold_chunks,
                    "has_gold_answers": bench.has_gold_answers,
                    "benchmark_file": bench.data_file_path,
                    "created_by": bench.created_by,
                    "created_at": utc_isoformat(bench.created_at) if bench.created_at else None,
                    "updated_at": utc_isoformat(bench.updated_at) if bench.updated_at else None,
                }

        logger.info(f"Loaded {self.kb_type} metadata from database for {len(self.databases_meta)} databases")

    async def _save_metadata(self) -> None:
        from yuxi.repositories.evaluation_repository import EvaluationRepository
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        kb_repo = KnowledgeBaseRepository()
        file_repo = KnowledgeFileRepository()
        eval_repo = EvaluationRepository()

        self._normalize_metadata_state()

        for db_id, meta in self.databases_meta.items():
            existing = await kb_repo.get_by_id(db_id)
            payload = {
                "db_id": db_id,
                "name": meta.get("name") or db_id,
                "description": meta.get("description"),
                "kb_type": meta.get("kb_type") or self.kb_type,
                "embed_info": meta.get("embed_info"),
                "llm_info": meta.get("llm_info"),
                "query_params": meta.get("query_params"),
                "additional_params": meta.get("metadata") or {},
            }
            if existing is None:
                await kb_repo.create(payload)
            else:
                await kb_repo.update(
                    db_id,
                    {
                        "name": payload["name"],
                        "description": payload["description"],
                        "kb_type": payload["kb_type"],
                        "embed_info": payload["embed_info"],
                        "llm_info": payload["llm_info"],
                        "query_params": payload["query_params"],
                        "additional_params": payload["additional_params"],
                    },
                )

        for file_id, meta in self.files_meta.items():
            db_id = meta.get("database_id")
            if not db_id:
                continue
            await file_repo.upsert(
                file_id=file_id,
                data={
                    "db_id": db_id,
                    "parent_id": meta.get("parent_id"),
                    "filename": meta.get("filename") or "",
                    "original_filename": meta.get("original_filename"),
                    "file_type": meta.get("file_type"),
                    "path": meta.get("path"),
                    "minio_url": meta.get("minio_url"),
                    "markdown_file": meta.get("markdown_file"),
                    "status": meta.get("status"),
                    "content_hash": meta.get("content_hash"),
                    "file_size": meta.get("size"),
                    "content_type": meta.get("content_type"),
                    "processing_params": sanitize_processing_params(meta.get("processing_params")),
                    "is_folder": meta.get("is_folder", False),
                    "error_message": meta.get("error"),
                    "created_by": str(meta.get("created_by")) if meta.get("created_by") else None,
                    "updated_by": str(meta.get("updated_by")) if meta.get("updated_by") else None,
                },
            )

        for db_id, benchmarks in self.benchmarks_meta.items():
            for benchmark_id, meta in benchmarks.items():
                existing = await eval_repo.get_benchmark(benchmark_id)
                payload = {
                    "benchmark_id": benchmark_id,
                    "db_id": db_id,
                    "name": meta.get("name") or benchmark_id,
                    "description": meta.get("description"),
                    "question_count": int(meta.get("question_count") or 0),
                    "has_gold_chunks": bool(meta.get("has_gold_chunks")),
                    "has_gold_answers": bool(meta.get("has_gold_answers")),
                    "data_file_path": meta.get("benchmark_file"),
                    "created_by": str(meta.get("created_by")) if meta.get("created_by") else None,
                }
                if existing is None:
                    await eval_repo.create_benchmark(payload)

    async def _persist_file(self, file_id: str) -> None:
        """只保存单个文件到数据库，避免全量遍历"""
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        file_repo = KnowledgeFileRepository()

        if file_id not in self.files_meta:
            return

        meta = self.files_meta[file_id]
        db_id = meta.get("database_id")
        if not db_id:
            return

        await file_repo.upsert(
            file_id=file_id,
            data={
                "db_id": db_id,
                "parent_id": meta.get("parent_id"),
                "filename": meta.get("filename") or "",
                "original_filename": meta.get("original_filename"),
                "file_type": meta.get("file_type"),
                "path": meta.get("path"),
                "minio_url": meta.get("minio_url"),
                "markdown_file": meta.get("markdown_file"),
                "status": meta.get("status"),
                "content_hash": meta.get("content_hash"),
                "file_size": meta.get("size"),
                "content_type": meta.get("content_type"),
                "processing_params": sanitize_processing_params(meta.get("processing_params")),
                "is_folder": meta.get("is_folder", False),
                "error_message": meta.get("error"),
                "created_by": str(meta.get("created_by")) if meta.get("created_by") else None,
                "updated_by": str(meta.get("updated_by")) if meta.get("updated_by") else None,
            },
        )

    async def _persist_kb(self, db_id: str) -> None:
        """只保存单个知识库到数据库，避免全量遍历"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()

        if db_id not in self.databases_meta:
            return

        meta = self.databases_meta[db_id]
        existing = await kb_repo.get_by_id(db_id)
        payload = {
            "db_id": db_id,
            "name": meta.get("name") or db_id,
            "description": meta.get("description"),
            "kb_type": meta.get("kb_type") or self.kb_type,
            "embed_info": meta.get("embed_info"),
            "llm_info": meta.get("llm_info"),
            "query_params": meta.get("query_params"),
            "additional_params": meta.get("metadata") or {},
        }

        if existing is None:
            await kb_repo.create(payload)
        else:
            await kb_repo.update(
                db_id,
                {
                    "name": payload["name"],
                    "description": payload["description"],
                    "kb_type": payload["kb_type"],
                    "embed_info": payload["embed_info"],
                    "llm_info": payload["llm_info"],
                    "query_params": payload["query_params"],
                    "additional_params": payload["additional_params"],
                },
            )
