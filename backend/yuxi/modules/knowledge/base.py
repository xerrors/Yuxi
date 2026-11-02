import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import uuid4

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.chunking.presets import ensure_chunk_defaults_in_additional_params
from yuxi.modules.knowledge.file_status import FileStatus
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.schemas import SearchOutputSchema, SearchResultSchema
from yuxi.modules.knowledge.services import documents
from yuxi.modules.knowledge.utils.kb_utils import build_kb_image_proxy_url
from yuxi.shared.datetime import utc_isoformat


class KnowledgeBaseException(Exception):
    """知识库统一异常基类"""

    pass


class KBNotFoundError(KnowledgeBaseException):
    """知识库不存在错误"""

    pass


class KBNameConflictError(KnowledgeBaseException):
    """知识库名称冲突错误。"""

    pass


class KBEmbeddingModelMissingError(KnowledgeBaseException):
    """提供不含内部配置或凭据的缺少向量模型提示。"""

    public_message = "知识库未配置 Embedding 模型，无法入库。请联系管理员修复模型配置后重试。"

    def __init__(self):
        """使用固定的可公开消息。"""
        super().__init__(self.public_message)


class KnowledgeExecutor(ABC):
    """知识库执行器基类：定义解析、索引、检索、外部阅读与投影清理的契约和共享流程。"""

    kb_type = ""
    name = ""
    description = ""
    requires_embedding_model = True
    supports_documents = True
    supports_document_read = True
    apply_chunk_defaults = True

    def __init__(self, work_dir: str):
        """初始化知识库并确保工作目录存在。"""
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)

    # ==== 类型配置契约 ====

    @classmethod
    def get_create_params_config(cls) -> dict[str, Any]:
        """获取创建知识库时的类型特定参数配置。"""
        return {"options": []}

    @classmethod
    def validate_additional_params(cls, additional_params: dict | None) -> dict:
        """校验并规范化类型特定配置。"""
        return dict(additional_params or {})

    @classmethod
    def normalize_additional_params(cls, additional_params: dict | None) -> dict:
        """规范化 additional_params，仅文档型知识库补充分块默认值。"""
        params = cls.validate_additional_params(additional_params)
        if cls.apply_chunk_defaults:
            return ensure_chunk_defaults_in_additional_params(params)
        return params

    # ==== 文件写入流程：登记 → 解析 → 索引 ====

    async def parse_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        *,
        params: dict | None = None,
        additional_params: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """解析全过程持有共享投影锁，撤销必须等待全部产物写入结束。"""
        from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

        async with knowledge_projection_lock(kb_id, shared=True):
            return await self._parse_file(
                kb_id,
                file_id,
                operator_id,
                additional_params=additional_params,
                params=params,
                processing_job_id=processing_job_id,
                processing_owner=processing_owner,
            )

    async def _parse_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        *,
        params: dict | None = None,
        additional_params: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """解析原件生成 Markdown 并写入 MinIO（状态 PARSING → PARSED/ERROR_PARSING）。"""
        allowed_statuses = {
            FileStatus.UPLOADED,
            FileStatus.ERROR_PARSING,
        }

        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        file_repo = KnowledgeFileRepository()
        # 条件更新同时匹配执行权；接管发生后更新落空，据此按取消让位。
        owner_filter = (
            {"processing_job_id": processing_job_id, "processing_owner": processing_owner}
            if processing_job_id is not None and processing_owner is not None
            else {}
        )

        async def mark_parse_failed(error_message: str) -> None:
            """把 PARSING 文件落盘为 ERROR_PARSING；执行权被接管时以取消让位。"""
            update_data = {
                "status": FileStatus.ERROR_PARSING,
                "error_message": error_message,
                "processing_job_id": None,
                "processing_owner": None,
            }
            if operator_id:
                update_data["updated_by"] = operator_id
            updated_record = await file_repo.update_fields_if_status(
                file_id=file_id,
                kb_id=kb_id,
                allowed_statuses={FileStatus.PARSING},
                data=update_data,
                **owner_filter,
            )
            if updated_record is None and processing_owner is not None:
                raise asyncio.CancelledError("File processing owner was lost")

        claim_data = {
            "status": FileStatus.PARSING,
            "error_message": None,
            "processing_job_id": processing_job_id,
            "processing_owner": processing_owner,
        }
        if operator_id:
            claim_data["updated_by"] = operator_id
        claimed_record = await file_repo.update_fields_if_status(
            kb_id=kb_id,
            file_id=file_id,
            allowed_statuses=allowed_statuses,
            data=claim_data,
            processing_params=params,
            kb_additional_params=additional_params,
        )
        if claimed_record is None:
            current_meta = await documents.load_file_meta(kb_id, file_id)
            current_status = current_meta.get("status")
            raise ValueError(
                f"Cannot parse file with status '{current_status}'. File must be in one of these states: {', '.join(allowed_statuses)}"
            )

        file_meta = documents.file_record_to_meta(claimed_record)
        file_path = file_meta.get("path")
        if not file_path:
            message = f"File {file_id} has no valid path in metadata"
            await mark_parse_failed(message)
            raise ValueError(message)

        attempt_id = uuid4().hex
        previous_markdown = file_meta.get("markdown_file")
        try:
            from yuxi.modules.documents.service import parse_to_hosted_markdown

            params = dict(file_meta.get("processing_params") or {})

            markdown_content = await parse_to_hosted_markdown(
                source=file_path,
                params=params,
                image_bucket=get_minio_client().KB_BUCKETS["images"],
                image_prefix=f"{kb_id}/kb-images/{file_id}/{attempt_id}/",
                url_builder=build_kb_image_proxy_url,
            )

            markdown_file_path = await self.save_markdown_artifact(kb_id, file_id, attempt_id, markdown_content)

            # 发布解析结果仍走 PARSING 条件更新；失败说明执行权被接管，按取消让位。
            file_meta["status"] = FileStatus.PARSED
            file_meta["markdown_file"] = markdown_file_path
            file_meta["error"] = None
            file_meta["updated_at"] = utc_isoformat()
            if operator_id:
                file_meta["updated_by"] = operator_id
            update_data = {
                "status": FileStatus.PARSED,
                "markdown_file": markdown_file_path,
                "error_message": None,
                "processing_job_id": None,
                "processing_owner": None,
            }
            if operator_id:
                update_data["updated_by"] = operator_id
            updated_record = await file_repo.update_fields_if_status(
                file_id=file_id,
                kb_id=kb_id,
                allowed_statuses={FileStatus.PARSING},
                data=update_data,
                **owner_filter,
            )
            if updated_record is None:
                raise asyncio.CancelledError("File processing owner was lost")

        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                # 先摘除取消标记，让 ERROR_PARSING 有机会落盘，随后仍重新抛出取消。
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            try:
                if not await self.markdown_attempt_is_referenced(kb_id, file_id, attempt_id):
                    await self._cleanup_parse_attempt(kb_id, file_id, attempt_id)
            except Exception as cleanup_error:
                logger.warning("无法确认解析发布事实，保留本次产物: kb={} file={} error={}", kb_id, file_id, cleanup_error)
            error_msg = "File parsing was cancelled" if isinstance(e, asyncio.CancelledError) else str(e)
            logger.error(f"Failed to parse file {file_id}: {error_msg}")
            await mark_parse_failed(error_msg)
            raise

        # 发布成功后回收上一次尝试的产物；此处的取消不回滚已发布结果。
        if previous_markdown:
            _, previous_object = parse_minio_url(previous_markdown)
            previous_attempt = Path(previous_object).parent.name
            await self._cleanup_parse_attempt(kb_id, file_id, previous_attempt)
        return file_meta

    @abstractmethod
    async def index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        *,
        embedding_model_spec: str | None,
        additional_params: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """索引已解析文件（状态 INDEXING → INDEXED/ERROR_INDEXING）。"""
        pass

    # ==== 文件读取 ====

    async def open_document(
        self,
        kb_id: str,
        file_id: str,
        *,
        config: KnowledgeBaseConfig,
        offset: int = 0,
        limit: int = 800,
    ) -> dict:
        """按本次连接配置打开正文；本地文档复用既有窗口读取。"""
        del config
        return await documents.open_file_content(kb_id, file_id, offset=offset, limit=limit)

    # ==== 检索 ====

    @abstractmethod
    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        **kwargs,
    ) -> list[dict]:
        """异步查询知识库，返回命中的 chunk 字典列表。"""
        pass

    @classmethod
    @abstractmethod
    def get_query_params_config(cls) -> dict:
        """返回知识库类型的查询参数配置（{"type", "options": [{key,label,type,default,options,description,...}]}）。"""
        pass

    @classmethod
    def get_default_query_params(cls) -> dict[str, Any]:
        """从 get_query_params_config 中提取所有参数的默认值，返回 {"options": {...}}"""
        config = cls.get_query_params_config()
        defaults = {}
        for opt in config.get("options", []):
            if "default" in opt:
                defaults[opt["key"]] = opt["default"]
        return {"options": defaults}

    @staticmethod
    def build_search_output(kb_id: str, retrieval_results: Any) -> dict[str, Any] | Any:
        """把检索命中规范化为 SearchOutputSchema；非列表结果原样返回。"""
        if not isinstance(retrieval_results, list):
            return retrieval_results

        results = []
        for index, chunk in enumerate(retrieval_results):
            if not isinstance(chunk, dict):
                continue

            metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
            # 检索元数据不得泄露沙盒与对象存储内部路径。
            metadata = {key: value for key, value in metadata.items() if key not in {"filepath", "parsed_path", "path", "markdown_file"}}
            file_id = metadata.get("file_id") or chunk.get("file_id") or chunk.get("full_doc_id") or ""
            chunk_id = metadata.get("chunk_id") or chunk.get("chunk_id") or chunk.get("id")
            chunk_index = metadata.get("chunk_index")
            if chunk_index is None:
                chunk_index = chunk.get("chunk_index")
            if chunk_index is not None:
                metadata.setdefault("chunk_index", chunk_index)
            for field in ("score", "score_type", "distance", "rerank_score", "highlights"):
                if chunk.get(field) is not None:
                    metadata.setdefault(field, chunk[field])

            results.append(
                SearchResultSchema(
                    id=str(chunk_id or f"{file_id}:{index + 1}"),
                    kb_id=str(kb_id),
                    file_id=str(file_id or ""),
                    content=str(chunk.get("content") or ""),
                    metadata=metadata,
                )
            )

        return SearchOutputSchema(kb_id=str(kb_id), results=results).model_dump()

    # ==== 目录与文件节点操作 ====

    # ==== 知识库级清理与运维 ====

    async def cleanup_file_resources(self, kb_id: str, file_id: str) -> None:
        """删除单个文档拥有的全部解析产物。"""
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        client = get_minio_client()
        record = await KnowledgeFileRepository().get_by_file_id(file_id, include_deleted=True)
        if record is not None:
            if record.kb_id != kb_id:
                raise ValueError("File cleanup cannot cross knowledge bases")
            source = record.minio_url or record.path
            if source and is_minio_url(source):
                bucket, key = parse_minio_url(source)
                if bucket != client.KB_BUCKETS["documents"] or not key.startswith(f"{kb_id}/"):
                    raise ValueError("File source is outside the owning knowledge base")
                await client.adelete_file(bucket, key)
        await asyncio.gather(
            client.adelete_objects_by_prefix(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/"),
            client.adelete_objects_by_prefix(client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/"),
            client.adelete_file(client.KB_BUCKETS["parsed"], f"{kb_id}/preview/{file_id}.pdf"),
        )

    async def cleanup_knowledge_base_resources(self, kb_id: str) -> dict:
        """清理知识库关联的文件与存储资源。"""
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        minio_client = get_minio_client()
        file_repo = KnowledgeFileRepository()

        # 调用方已经提交 KB/File tombstone；失败必须阻止 PG 硬删除。
        prefix = f"{kb_id}/"
        cleanup_buckets = {
            minio_client.KB_BUCKETS["parsed"],
            minio_client.KB_BUCKETS["documents"],
            minio_client.KB_BUCKETS["images"],
        }
        cleanup_tasks = [minio_client.adelete_objects_by_prefix(bucket_name, prefix) for bucket_name in cleanup_buckets]
        await asyncio.gather(*cleanup_tasks)

        # 删除知识库的文件记录；知识库主记录由 Manager 统一删除。
        await file_repo.delete_by_kb_id(kb_id)

        # 删除工作目录
        working_dir = os.path.join(self.work_dir, kb_id)
        if os.path.exists(working_dir):
            try:
                shutil.rmtree(working_dir)
            except Exception as e:
                logger.error(f"Error deleting working directory {working_dir}: {e}")

        return {"message": "删除成功"}

    # ==== 内部辅助：记录与元数据映射 ====

    # ==== 内部辅助：内容读取与窗口构建 ====

    # ==== 内部辅助：解析产物写入与回收 ====

    @staticmethod
    async def markdown_attempt_is_referenced(kb_id: str, file_id: str, attempt_id: str) -> bool:
        """回收前从持久正文指针确认本次产物是否已发布，查询失败交由调用方保留对象。"""
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        current = await KnowledgeFileRepository().get_by_file_id(file_id, include_deleted=True)
        return bool(
            current is not None
            and current.kb_id == kb_id
            and current.markdown_file
            and parse_minio_url(current.markdown_file)
            == (get_minio_client().KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/{attempt_id}/document.md")
        )

    async def save_markdown_artifact(self, kb_id: str, file_id: str, attempt_id: str, content: str) -> str:
        """将 Markdown 写入 MinIO 并返回对象 URL。"""
        minio_client = get_minio_client()
        bucket_name = minio_client.KB_BUCKETS["parsed"]
        await asyncio.to_thread(minio_client.ensure_bucket_exists, bucket_name)

        object_name = f"{kb_id}/parsed/{file_id}/{attempt_id}/document.md"
        data = content.encode("utf-8")

        upload_result = await await_io(
            minio_client.aupload_file(
                bucket_name=bucket_name,
                object_name=object_name,
                data=data,
            )
        )

        return upload_result.url

    async def _cleanup_parse_attempt(self, kb_id: str, file_id: str, attempt_id: str) -> None:
        """尽力回收指定解析尝试，清理失败形成可观察日志。"""
        client = get_minio_client()
        results = await asyncio.gather(
            client.adelete_objects_by_prefix(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/{attempt_id}/"),
            client.adelete_objects_by_prefix(client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/{attempt_id}/"),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("解析产物清理失败: kb={} file={} attempt={} error={}", kb_id, file_id, attempt_id, result)
