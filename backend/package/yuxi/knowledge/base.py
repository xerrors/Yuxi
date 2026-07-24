import asyncio
import mimetypes
import os
import re
import secrets
import string
from abc import ABC, abstractmethod
from typing import Any

from yuxi.knowledge.chunking.ragflow_like.presets import ensure_chunk_defaults_in_additional_params
from yuxi.knowledge.schemas import (
    FindOutputSchema,
    FindWindowSchema,
    SearchOutputSchema,
    SearchResultSchema,
)
from yuxi.knowledge.utils import resolve_processing_params, sanitize_processing_params
from yuxi.services.file_preview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    convert_office_to_pdf,
    detect_media_type,
    is_binary_preview_type,
    is_office_pdf_preview_file,
    render_preview_payload,
    render_preview_too_large_payload,
)
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


INDEXED_STATS_STATUSES = {FileStatus.INDEXED, "done"}


def _should_repair_file_stats(file_meta: dict) -> bool:
    status = file_meta.get("status")
    return status is None or status in INDEXED_STATS_STATUSES


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

    kb_type = ""
    name = ""
    description = ""
    requires_embedding_model = True
    supports_documents = True
    apply_chunk_defaults = True

    def __init__(self, work_dir: str):
        """
        初始化知识库

        Args:
            work_dir: 工作目录
        """
        self.work_dir = work_dir
        self.databases_meta: dict[str, dict] = {}
        self.benchmarks_meta: dict[str, dict] = {}
        self._metadata_loaded = False  # 标记元数据是否已加载

        os.makedirs(work_dir, exist_ok=True)

        # 注意：不在 __init__ 中加载元数据，由 KnowledgeBaseManager 统一管理加载

    def load_metadata(
        self,
        global_databases_meta: dict[str, dict],
        _unused_file_metadata: dict[str, dict],
        benchmarks_meta: dict[str, dict],
    ):
        """由 KnowledgeBaseManager 调用，同步加载元数据"""
        # 过滤出当前 kb_type 的知识库
        self.databases_meta = {}
        for kb_id, meta in global_databases_meta.items():
            if meta.get("kb_type") == self.kb_type:
                normalized_additional_params = self.normalize_additional_params(meta.get("additional_params"))
                self.databases_meta[kb_id] = {
                    "name": meta.get("name"),
                    "description": meta.get("description"),
                    "kb_type": meta.get("kb_type"),
                    "embedding_model_spec": meta.get("embedding_model_spec"),
                    "llm_model_spec": meta.get("llm_model_spec"),
                    "query_params": meta.get("query_params"),
                    "metadata": normalized_additional_params,
                    "created_at": meta.get("created_at"),
                }

        del _unused_file_metadata

        # 文件元数据以 PostgreSQL 为准，不在 KnowledgeBase 实例中缓存。

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

    def _file_record_to_meta(self, record: Any) -> dict:
        kb_additional_params = self.databases_meta.get(record.kb_id, {}).get("metadata") or {}
        return {
            "file_id": record.file_id,
            "kb_id": record.kb_id,
            "parent_id": record.parent_id,
            "filename": record.filename,
            "file_type": record.file_type,
            "path": record.path,
            "markdown_file": record.markdown_file,
            "status": record.status,
            "content_hash": record.content_hash,
            "size": record.file_size,
            "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
            "token_count": int(getattr(record, "token_count", 0) or 0),
            "content_type": record.content_type,
            "processing_params": sanitize_processing_params(
                resolve_processing_params(
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

    @staticmethod
    def _file_meta_to_record_data(meta: dict) -> dict[str, Any]:
        return {
            "kb_id": meta.get("kb_id"),
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
            "chunk_count": int(meta.get("chunk_count") or 0),
            "token_count": int(meta.get("token_count") or 0),
            "content_type": meta.get("content_type"),
            "processing_params": sanitize_processing_params(meta.get("processing_params")),
            "is_folder": meta.get("is_folder", False),
            "error_message": meta.get("error"),
            "created_by": str(meta.get("created_by")) if meta.get("created_by") else None,
            "updated_by": str(meta.get("updated_by")) if meta.get("updated_by") else None,
        }

    async def _load_file_meta(self, kb_id: str, file_id: str, *, refresh: bool = False) -> dict:
        del refresh

        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        record = await KnowledgeFileRepository().get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id:
            raise ValueError(f"File {file_id} not found")

        return self._file_record_to_meta(record)

    def _normalize_metadata_state(self) -> None:
        """Ensure in-memory metadata uses normalized timestamp formats."""
        for meta in self.databases_meta.values():
            if "created_at" in meta:
                normalized = self._normalize_timestamp(meta.get("created_at"))
                if normalized:
                    meta["created_at"] = normalized

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

    @abstractmethod
    async def _create_kb_instance(self, kb_id: str, config: dict) -> Any:
        """
        创建底层知识库实例

        Args:
            kb_id: 数据库ID
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
        self, kb_id: str, item: str, params: dict | None = None, operator_id: str | None = None
    ) -> dict:
        """
        Add a file record to metadata (Status: UPLOADED)

        Args:
            kb_id: Database ID
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
        metadata = await prepare_item_metadata(item, content_type, kb_id, params=params)
        file_id = metadata["file_id"]
        kb_additional_params = self.databases_meta.get(kb_id, {}).get("metadata") or {}
        metadata["processing_params"] = resolve_processing_params(
            kb_additional_params=kb_additional_params,
            file_processing_params=metadata.get("processing_params"),
        )

        # Fallback: fetch file size from MinIO if not provided
        if metadata.get("size") is None and content_type == "file":
            try:
                from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
                from yuxi.storage.minio import get_minio_client

                file_path = metadata.get("path") or item
                if is_minio_url(file_path):
                    bucket_name, obj_name = parse_minio_url(file_path)
                    minio_client = get_minio_client()
                    file_size = await minio_client.astat_file(bucket_name, obj_name)
                    if file_size is not None:
                        metadata["size"] = file_size
            except Exception as exc:
                logger.warning(f"Failed to stat file size from MinIO for {item}: {exc}")

        # Initial status
        metadata["status"] = FileStatus.UPLOADED
        metadata["created_at"] = utc_isoformat()
        if operator_id:
            metadata["created_by"] = operator_id

        await self._persist_file_meta(file_id, metadata)
        await self.refresh_database_stats(kb_id)

        return metadata

    async def parse_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        """
        Parse file to Markdown and save to MinIO (Status: PARSING -> PARSED/ERROR_PARSING)

        Args:
            kb_id: Database ID
            file_id: File ID
            operator_id: ID of the user performing the operation

        Returns:
            Updated file metadata
        """
        # Validate current status - only allow parsing from these states
        allowed_statuses = {
            FileStatus.UPLOADED,
            FileStatus.ERROR_PARSING,
            "failed",  # Legacy status
        }

        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        file_repo = KnowledgeFileRepository()
        claim_data = {"status": FileStatus.PARSING, "error_message": None}
        if operator_id:
            claim_data["updated_by"] = operator_id
        claimed_record = await file_repo.update_fields_if_status(
            kb_id=kb_id,
            file_id=file_id,
            allowed_statuses=allowed_statuses,
            data=claim_data,
        )
        if claimed_record is None:
            current_meta = await self._load_file_meta(kb_id, file_id)
            current_status = current_meta.get("status")
            raise ValueError(
                f"Cannot parse file with status '{current_status}'. "
                f"File must be in one of these states: {', '.join(allowed_statuses)}"
            )

        file_meta = self._file_record_to_meta(claimed_record)
        file_path = file_meta.get("path")
        if not file_path:
            message = f"File {file_id} has no valid path in metadata"
            update_data = {"status": FileStatus.ERROR_PARSING, "error_message": message}
            if operator_id:
                update_data["updated_by"] = operator_id
            await file_repo.update_fields(file_id=file_id, kb_id=kb_id, data=update_data)
            raise ValueError(message)

        try:
            from yuxi.services.ocr_service import parse_document

            # Prepare params
            params = file_meta.get("processing_params", {}) or {}
            params["image_bucket"] = "public"
            params["image_prefix"] = f"{kb_id}/kb-images"

            markdown_content = await parse_document(
                source=file_path,
                params=params,
            )

            # Save Markdown to MinIO
            markdown_file_path = await self._save_markdown_to_minio(kb_id, file_id, markdown_content)

            # Update metadata
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
            }
            if operator_id:
                update_data["updated_by"] = operator_id
            await file_repo.update_fields(file_id=file_id, kb_id=kb_id, data=update_data)

            return file_meta

        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            error_msg = "File parsing was cancelled" if isinstance(e, asyncio.CancelledError) else str(e)
            logger.error(f"Failed to parse file {file_id}: {error_msg}")

            file_meta["status"] = FileStatus.ERROR_PARSING
            file_meta["error"] = error_msg
            file_meta["updated_at"] = utc_isoformat()
            if operator_id:
                file_meta["updated_by"] = operator_id
            update_data = {"status": FileStatus.ERROR_PARSING, "error_message": error_msg}
            if operator_id:
                update_data["updated_by"] = operator_id
            await file_repo.update_fields(file_id=file_id, kb_id=kb_id, data=update_data)

            raise

    async def update_file_params(self, kb_id: str, file_id: str, params: dict, operator_id: str | None = None) -> None:
        """Update file processing params"""
        # Skip if no params to update
        if not params:
            return

        file_meta = await self._load_file_meta(kb_id, file_id)
        current_params = file_meta.get("processing_params", {}) or {}
        kb_additional_params = self.databases_meta.get(kb_id, {}).get("metadata") or {}

        logger.debug(f"[update_file_params] file_id={file_id}, current_params={current_params}, new_params={params}")

        current_params = resolve_processing_params(
            kb_additional_params=kb_additional_params,
            file_processing_params=current_params,
            request_params=params,
        )

        file_meta["processing_params"] = current_params
        file_meta["updated_at"] = utc_isoformat()
        if operator_id:
            file_meta["updated_by"] = operator_id

        logger.debug(f"[update_file_params] file_id={file_id}, updated_params={current_params}")

        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        update_data = {"processing_params": sanitize_processing_params(current_params)}
        if operator_id:
            update_data["updated_by"] = operator_id
        record = await KnowledgeFileRepository().update_fields(file_id=file_id, kb_id=kb_id, data=update_data)
        if record is None:
            raise ValueError(f"File {file_id} not found")

    async def _mark_file_unparsed(self, kb_id: str, file_id: str, operator_id: str | None = None) -> None:
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        update_data = {"status": FileStatus.UPLOADED, "markdown_file": None, "error_message": None}
        if operator_id:
            update_data["updated_by"] = operator_id
        record = await KnowledgeFileRepository().update_fields(file_id=file_id, kb_id=kb_id, data=update_data)
        if record is None:
            raise ValueError(f"File {file_id} not found")

    async def _save_markdown_to_minio(self, kb_id: str, file_id: str, content: str) -> str:
        """Save markdown content to MinIO and return HTTP URL"""
        from yuxi.storage.minio import get_minio_client

        minio_client = get_minio_client()
        bucket_name = minio_client.KB_BUCKETS["parsed"]
        await asyncio.to_thread(minio_client.ensure_bucket_exists, bucket_name)

        object_name = f"{kb_id}/parsed/{file_id}.md"
        data = content.encode("utf-8")

        # Return standard HTTP URL from UploadResult
        upload_result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=data,
        )

        return upload_result.url

    async def _read_minio_bytes(self, file_path: str) -> bytes:
        from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
        from yuxi.storage.minio import get_minio_client

        if not file_path or not is_minio_url(file_path):
            raise ValueError(f"Invalid MinIO path format: {file_path}")

        bucket_name, object_name = parse_minio_url(file_path)
        minio_client = get_minio_client()
        return await minio_client.adownload_file(bucket_name, object_name)

    async def _read_markdown_from_minio(self, file_path: str) -> str:
        """Read markdown content from MinIO"""
        content_bytes = await self._read_minio_bytes(file_path)
        return content_bytes.decode("utf-8")

    async def _get_file_meta(self, kb_id: str, file_id: str) -> dict:
        return await self._load_file_meta(kb_id, file_id)

    @staticmethod
    def _original_file_path(file_meta: dict) -> str | None:
        return file_meta.get("minio_url") or file_meta.get("path")

    def _knowledge_file_entry(self, kb_id: str, file_id: str, file_meta: dict) -> dict:
        is_dir = bool(file_meta.get("is_folder"))
        original_path = self._original_file_path(file_meta)
        path = f"/{file_id}"
        if is_dir:
            path = f"{path}/"
        return {
            "source": "knowledge",
            "kb_id": kb_id,
            "file_id": file_id,
            "parent_id": file_meta.get("parent_id"),
            "path": path,
            "virtual_path": f"/knowledge/{kb_id}/{file_id}",
            "name": file_meta.get("filename") or file_meta.get("original_filename") or file_id,
            "is_dir": is_dir,
            "size": 0 if is_dir else file_meta.get("size") or 0,
            "modified_at": file_meta.get("updated_at") or file_meta.get("created_at") or "",
            "readonly": True,
            "status": file_meta.get("status", "done"),
            "has_original_file": bool(original_path),
            "has_parsed_markdown": bool(file_meta.get("markdown_file")),
        }

    def _sort_file_entries(self, entries: list[dict]) -> list[dict]:
        return sorted(
            entries,
            key=lambda item: (not bool(item.get("is_dir")), str(item.get("name") or "").lower()),
        )

    async def _list_knowledge_children(
        self,
        kb_id: str,
        parent_id: str | None,
        *,
        recursive: bool,
        files_only: bool,
    ) -> list[dict]:
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        children = []
        for record in await KnowledgeFileRepository().list_children(kb_id=kb_id, parent_id=parent_id):
            meta = self._file_record_to_meta(record)
            children.append((record.file_id, meta))

        entries = []
        for file_id, meta in children:
            if not files_only or not meta.get("is_folder"):
                entries.append(self._knowledge_file_entry(kb_id, file_id, meta))
            if recursive and meta.get("is_folder"):
                entries.extend(
                    await self._list_knowledge_children(
                        kb_id,
                        file_id,
                        recursive=True,
                        files_only=files_only,
                    )
                )
        return self._sort_file_entries(entries)

    async def list_file_tree(
        self,
        kb_id: str,
        parent_id: str | None = None,
        recursive: bool = False,
        files_only: bool = False,
    ) -> dict:
        if kb_id not in self.databases_meta:
            raise ValueError(f"Database {kb_id} not found")
        if parent_id:
            parent_meta = await self._get_file_meta(kb_id, parent_id)
            if not parent_meta.get("is_folder"):
                raise ValueError("Parent is not a folder")
        return {
            "entries": await self._list_knowledge_children(
                kb_id,
                parent_id,
                recursive=recursive,
                files_only=files_only,
            ),
            "readonly": True,
        }

    @staticmethod
    def _office_pdf_preview_path(kb_id: str, file_id: str) -> str:
        return f"{kb_id}/preview/{file_id}.pdf"

    async def _ensure_office_pdf_preview(self, kb_id: str, file_id: str, file_meta: dict) -> str:
        from yuxi.storage.minio import get_minio_client

        filename = file_meta.get("filename") or file_meta.get("original_filename") or file_id
        if not is_office_pdf_preview_file(filename):
            raise ValueError("当前文件类型不支持 PDF 预览")

        minio_client = get_minio_client()
        bucket_name = minio_client.KB_BUCKETS["parsed"]
        object_name = self._office_pdf_preview_path(kb_id, file_id)
        if await minio_client.astat_file(bucket_name, object_name) is not None:
            return f"minio://{bucket_name}/{object_name}"

        original_path = self._original_file_path(file_meta)
        if not original_path:
            raise ValueError("文件没有可转换的原始内容")

        raw_content = await self._read_minio_bytes(original_path)
        try:
            pdf_content = await convert_office_to_pdf(filename, raw_content)
        except OfficePreviewConversionError as exc:
            raise ValueError(str(exc)) from exc
        await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=pdf_content,
            content_type="application/pdf",
        )
        return f"minio://{bucket_name}/{object_name}"

    async def _get_minio_file_size(self, file_path: str) -> int | None:
        from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
        from yuxi.storage.minio import get_minio_client

        if not file_path or not is_minio_url(file_path):
            return None
        bucket_name, object_name = parse_minio_url(file_path)
        return await get_minio_client().astat_file(bucket_name, object_name)

    async def read_file_preview(self, kb_id: str, file_id: str) -> dict:
        file_meta = await self._get_file_meta(kb_id, file_id)
        if file_meta.get("is_folder"):
            raise ValueError("Cannot preview a folder")

        filename = file_meta.get("filename") or file_meta.get("original_filename") or file_id
        response = {
            "source": "knowledge",
            "kb_id": kb_id,
            "file_id": file_id,
            "filename": filename,
            "readonly": True,
        }

        original_path = self._original_file_path(file_meta)
        if not original_path:
            return {
                **response,
                "content": None,
                "preview_type": "unsupported",
                "supported": False,
                "message": "文件没有可预览的原始内容",
            }

        file_size = file_meta.get("size")
        if file_size is None:
            file_size = await self._get_minio_file_size(original_path)
        if file_size is not None and int(file_size) > MAX_BINARY_PREVIEW_SIZE_BYTES:
            return {**response, **render_preview_too_large_payload()}

        if is_office_pdf_preview_file(filename):
            preview_path = await self._ensure_office_pdf_preview(kb_id, file_id, file_meta)
            stem = filename.rsplit(".", 1)[0] or file_id
            return {
                **response,
                "content": await self._read_minio_bytes(preview_path),
                "filename": f"{stem}.pdf",
                "media_type": "application/pdf",
                "preview_type": "pdf",
                "supported": True,
                "message": None,
                "binary": True,
            }

        raw_content = await self._read_minio_bytes(original_path)
        if len(raw_content) > MAX_BINARY_PREVIEW_SIZE_BYTES:
            return {**response, **render_preview_too_large_payload()}
        payload = render_preview_payload(filename, raw_content)
        if is_binary_preview_type(payload["preview_type"]) and payload["supported"]:
            return {
                **response,
                "content": raw_content,
                "media_type": detect_media_type(filename, raw_content),
                "preview_type": payload["preview_type"],
                "supported": True,
                "message": None,
                "binary": True,
            }
        return {**response, **payload}

    async def get_file_download(self, kb_id: str, file_id: str, variant: str = "original") -> dict:
        file_meta = await self._get_file_meta(kb_id, file_id)
        if file_meta.get("is_folder"):
            raise ValueError("Cannot download a folder")
        if variant not in {"original", "parsed"}:
            raise ValueError("Unsupported download variant")

        filename = file_meta.get("filename") or file_meta.get("original_filename") or file_id
        if variant == "parsed":
            markdown_file = file_meta.get("markdown_file")
            if not markdown_file:
                raise ValueError("文件尚未生成解析结果")
            return {
                "filename": f"{filename}.parsed.md",
                "content": await self._read_minio_bytes(markdown_file),
                "media_type": "text/markdown; charset=utf-8",
            }

        original_path = self._original_file_path(file_meta)
        if not original_path:
            raise ValueError("文件没有可下载的原始内容")
        media_type = file_meta.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return {
            "filename": filename,
            "content": await self._read_minio_bytes(original_path),
            "media_type": media_type,
        }

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

    @staticmethod
    def build_search_output(kb_id: str, retrieval_results: Any) -> dict[str, Any] | Any:
        if not isinstance(retrieval_results, list):
            return retrieval_results

        results = []
        for index, chunk in enumerate(retrieval_results):
            if not isinstance(chunk, dict):
                continue

            metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
            metadata = {
                key: value
                for key, value in metadata.items()
                if key not in {"filepath", "parsed_path", "path", "markdown_file"}
            }
            file_id = metadata.get("file_id") or chunk.get("file_id") or chunk.get("full_doc_id") or ""
            chunk_id = metadata.get("chunk_id") or chunk.get("chunk_id") or chunk.get("id")
            chunk_index = metadata.get("chunk_index")
            if chunk_index is None:
                chunk_index = chunk.get("chunk_index")
            if chunk_index is not None:
                metadata.setdefault("chunk_index", chunk_index)
            if chunk.get("score") is not None:
                metadata.setdefault("score", chunk.get("score"))
            if chunk.get("distance") is not None:
                metadata.setdefault("distance", chunk.get("distance"))

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

    @staticmethod
    def _build_find_file_windows(
        content: str,
        *,
        patterns: list[str],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ) -> dict[str, Any]:
        patterns = [pattern for pattern in patterns if pattern]
        if not patterns:
            raise ValueError("请提供至少一个 pattern")

        lines = content.splitlines()
        flags = 0 if case_sensitive else re.IGNORECASE
        if use_regex:
            matchers = [re.compile(pattern, flags) for pattern in patterns]

            def line_matches(line: str) -> bool:
                return any(matcher.search(line) for matcher in matchers)

        else:
            normalized_patterns = patterns if case_sensitive else [pattern.lower() for pattern in patterns]

            def line_matches(line: str) -> bool:
                haystack = line if case_sensitive else line.lower()
                return any(pattern in haystack for pattern in normalized_patterns)

        matched_indexes = [index for index, line in enumerate(lines) if line_matches(line)]
        windows: list[FindWindowSchema] = []
        covered_until = -1
        normalized_window_size = min(max(int(window_size), 1), 200)
        half_window = normalized_window_size // 2

        for matched_index in matched_indexes:
            if matched_index < covered_until:
                continue
            start = max(matched_index - half_window, 0)
            end = min(start + normalized_window_size, len(lines))
            start = max(end - normalized_window_size, 0)
            matched_lines = [index + 1 for index in matched_indexes if start <= index < end]
            selected = lines[start:end]
            windows.append(
                FindWindowSchema(
                    start_line=start + 1 if selected else 0,
                    end_line=end,
                    matched_lines=matched_lines,
                    content="\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected)),
                )
            )
            covered_until = end
            if len(windows) >= max_windows:
                break

        return FindOutputSchema(
            kb_id="",
            file_id="",
            semantic=False,
            match_mode="regex" if use_regex else "keyword",
            total_matches=len(matched_indexes),
            windows=windows,
        ).model_dump(exclude={"kb_id", "file_id"})

    async def open_file_content(self, kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        """按行窗口打开文件解析后的 Markdown 内容"""
        try:
            file_meta = await self._load_file_meta(kb_id, file_id)
        except ValueError as exc:
            raise Exception(f"文件不存在: {file_id}") from exc
        if file_meta.get("is_folder"):
            raise Exception(f"文件 {file_id} 是文件夹")

        markdown_file = file_meta.get("markdown_file")
        if not markdown_file:
            raise Exception(f"文件 {file_id} 没有解析后的 Markdown 内容")

        content = await self._read_markdown_from_minio(markdown_file)
        return self._build_open_file_window(content, offset=offset, limit=limit)

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
        try:
            file_meta = await self._load_file_meta(kb_id, file_id)
        except ValueError as exc:
            raise Exception(f"文件不存在: {file_id}") from exc
        if file_meta.get("is_folder"):
            raise Exception(f"文件 {file_id} 是文件夹")

        markdown_file = file_meta.get("markdown_file")
        if not markdown_file:
            raise Exception(f"文件 {file_id} 没有解析后的 Markdown 内容")

        content = await self._read_markdown_from_minio(markdown_file)
        return self._build_find_file_windows(
            content,
            patterns=patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )

    @abstractmethod
    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        """
        Index parsed file (Status: INDEXING -> INDEXED/ERROR_INDEXING)

        Args:
            kb_id: Database ID
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
        embedding_model_spec: str | None = None,
        llm_model_spec: str | None = None,
        record_fields: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict:
        """
        创建数据库

        Args:
            database_name: 数据库名称
            description: 数据库描述
            embedding_model_spec: 嵌入模型 spec
            llm_model_spec: LLM 模型 spec
            record_fields: 首次持久化知识库记录时由上层传入的受控业务字段
            **kwargs: 其他配置参数

        Returns:
            数据库信息字典
        """
        kwargs = self.normalize_additional_params(kwargs)
        kwargs["stats"] = {"file_count": 0, "chunk_count": 0, "token_count": 0}

        alphabet = string.ascii_lowercase + string.digits
        while True:
            kb_id = "kb_" + "".join(secrets.choice(alphabet) for _ in range(10))
            if kb_id not in self.databases_meta:
                break

        self.databases_meta[kb_id] = {
            "name": database_name,
            "description": description,
            "kb_type": self.kb_type,
            "embedding_model_spec": embedding_model_spec,
            "llm_model_spec": llm_model_spec,
            "metadata": kwargs,
            "created_at": utc_isoformat(),
            "query_params": self._get_default_query_params(kb_id),
        }
        await self._persist_kb(kb_id, record_fields=record_fields)

        # 创建工作目录
        working_dir = os.path.join(self.work_dir, kb_id)
        os.makedirs(working_dir, exist_ok=True)

        # 返回数据库信息
        db_dict = self.databases_meta[kb_id].copy()
        db_dict["kb_id"] = kb_id
        db_dict["files"] = {}

        return db_dict

    async def delete_database(self, kb_id: str) -> dict:
        """
        删除数据库

        Args:
            kb_id: 数据库ID

        Returns:
            操作结果
        """
        if kb_id in self.databases_meta:
            from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
            from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
            from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
            from yuxi.storage.minio import get_minio_client

            minio_client = get_minio_client()
            file_repo = KnowledgeFileRepository()

            # 1. 删除文件元数据中记录的 MinIO 文件
            after_file_id = None
            while True:
                records = await file_repo.list_by_kb_id_after(kb_id, after_file_id=after_file_id, limit=500)
                if not records:
                    break
                after_file_id = records[-1].file_id
                for record in records:
                    file_id = record.file_id
                    file_path = record.minio_url or record.path
                    if file_path and is_minio_url(file_path):
                        try:
                            bucket_name, object_name = parse_minio_url(file_path)
                            await minio_client.adelete_file(bucket_name, object_name)
                        except Exception as e:
                            logger.warning(f"Failed to delete MinIO file {file_path}: {e}")

                    # 删除解析后的 markdown 文件
                    parsed_object = f"{kb_id}/parsed/{file_id}.md"
                    await minio_client.adelete_file(minio_client.KB_BUCKETS["parsed"], parsed_object)

            # 2. 并行删除所有知识库 bucket 中该 kb_id 下的文件
            prefix = f"{kb_id}/"
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
            del self.databases_meta[kb_id]
            await file_repo.delete_by_kb_id(kb_id)
            kb_repo = KnowledgeBaseRepository()
            await kb_repo.delete(kb_id)

        # 删除工作目录
        working_dir = os.path.join(self.work_dir, kb_id)
        if os.path.exists(working_dir):
            import shutil

            try:
                shutil.rmtree(working_dir)
            except Exception as e:
                logger.error(f"Error deleting working directory {working_dir}: {e}")

        return {"message": "删除成功"}

    async def create_folder(self, kb_id: str, folder_name: str, parent_id: str | None = None) -> dict:
        """Create a folder in the database."""
        import uuid

        if parent_id:
            parent_meta = await self._load_file_meta(kb_id, parent_id)
            if not parent_meta.get("is_folder"):
                raise ValueError("Parent is not a folder")

        folder_id = f"folder-{uuid.uuid4()}"

        folder_meta = {
            "file_id": folder_id,
            "filename": folder_name,
            "is_folder": True,
            "parent_id": parent_id,
            "kb_id": kb_id,
            "created_at": utc_isoformat(),
            "status": "done",
            "path": folder_name,
            "file_type": "folder",
        }
        await self._persist_file_meta(folder_id, folder_meta)
        return folder_meta

    @abstractmethod
    async def update_content(self, kb_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        """
        更新内容 - 根据file_ids重新解析文件并更新向量库

        Args:
            kb_id: 数据库ID
            file_ids: 文件ID列表
            params: 处理参数

        Returns:
            更新结果列表
        """
        pass

    @abstractmethod
    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        """
        异步查询知识库

        Args:
            query_text: 查询文本
            kb_id: 数据库ID
            **kwargs: 查询参数

        Returns:
            一个包含字典的列表，每个字典代表一个检索到的文档块。
        """
        pass

    @abstractmethod
    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        """
        获取知识库类型的查询参数配置

        Args:
            kb_id: 数据库ID
            **kwargs: 额外参数

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

    async def export_data(self, kb_id: str, format: str = "zip", **kwargs) -> str:
        pass

    def _get_query_params(self, kb_id: str) -> dict:
        """从实例元数据中加载查询参数"""
        if kb_id in self.databases_meta:
            query_params_meta = self.databases_meta[kb_id].get("query_params") or {}
            return query_params_meta.get("options", {})
        return {}

    def _get_default_query_params(self, kb_id: str) -> dict[str, Any]:
        """从 get_query_params_config 中提取所有参数的默认值，返回 {"options": {...}}"""
        config = self.get_query_params_config(kb_id)
        defaults = {}
        for opt in config.get("options", []):
            if "default" in opt:
                defaults[opt["key"]] = opt["default"]
        return {"options": defaults}

    def _build_database_stats(self, kb_id: str) -> dict[str, int]:
        del kb_id
        return self._normalize_database_stats(None)

    @staticmethod
    def _normalize_database_stats(stats: dict | None) -> dict[str, int]:
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

    def _get_database_stats(self, kb_id: str) -> dict[str, int]:
        metadata = self.databases_meta.get(kb_id, {}).get("metadata") or {}
        stats = metadata.get("stats") if isinstance(metadata, dict) else None
        if isinstance(stats, dict):
            return self._normalize_database_stats(stats)
        return self._build_database_stats(kb_id)

    def _set_database_stats(self, kb_id: str, stats: dict[str, int]) -> None:
        if kb_id not in self.databases_meta:
            raise ValueError(f"Database {kb_id} not found")

        metadata = self.databases_meta[kb_id].setdefault("metadata", {})
        metadata["stats"] = self._normalize_database_stats(stats)

    async def refresh_database_stats(self, kb_id: str) -> dict[str, int]:
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        stats = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
        self._set_database_stats(kb_id, stats)
        await self._persist_kb(kb_id)
        return stats

    async def repair_missing_file_stats(self, kb_id: str) -> dict:
        if kb_id not in self.databases_meta:
            raise ValueError(f"Database {kb_id} not found")

        from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
        from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        chunk_repo = KnowledgeChunkRepository()
        file_repo = KnowledgeFileRepository()
        after_file_id = None
        scanned_files = 0
        scanned_indexed_files = 0
        skipped_file_count = 0
        scanned_token_files = 0
        updated_files = 0
        updated_chunk_files = 0
        updated_token_files = 0
        updated_size_files = 0

        while True:
            records = await file_repo.list_by_kb_id_after(
                kb_id,
                after_file_id=after_file_id,
                limit=500,
                files_only=True,
            )
            if not records:
                break
            after_file_id = records[-1].file_id

            indexed_records = [record for record in records if _should_repair_file_stats({"status": record.status})]
            indexed_file_ids = [record.file_id for record in indexed_records]
            indexed_file_id_set = set(indexed_file_ids)
            chunk_counts = await chunk_repo.count_by_file_ids(indexed_file_ids)
            token_file_ids = [record.file_id for record in indexed_records if int(record.token_count or 0) <= 0]
            token_counts = {file_id: 0 for file_id in token_file_ids}
            for chunk in await chunk_repo.list_by_file_ids(token_file_ids):
                token_counts[chunk.file_id] = token_counts.get(chunk.file_id, 0) + count_tokens(chunk.content or "")
            size_updates = await self._fill_missing_file_sizes_for_records(records)

            scanned_files += len(records)
            scanned_indexed_files += len(indexed_file_ids)
            skipped_file_count += len(records) - len(indexed_file_ids)
            scanned_token_files += len(token_file_ids)

            for record in records:
                file_id = record.file_id
                update_data: dict[str, Any] = {}
                if file_id not in indexed_file_id_set:
                    if int(record.chunk_count or 0) != 0:
                        update_data["chunk_count"] = 0
                        updated_chunk_files += 1
                    if int(record.token_count or 0) != 0:
                        update_data["token_count"] = 0
                        updated_token_files += 1
                else:
                    next_chunk_count = int(chunk_counts.get(file_id, 0))
                    if int(record.chunk_count or 0) != next_chunk_count:
                        update_data["chunk_count"] = next_chunk_count
                        updated_chunk_files += 1

                    if file_id in token_counts:
                        next_token_count = int(token_counts[file_id])
                        if record.token_count is None or int(record.token_count or 0) != next_token_count:
                            update_data["token_count"] = next_token_count
                            updated_token_files += 1

                if file_id in size_updates:
                    update_data["file_size"] = size_updates[file_id]
                    updated_size_files += 1

                if update_data:
                    updated_files += 1
                    await file_repo.update_fields(file_id=file_id, kb_id=kb_id, data=update_data)

        stats = await self.refresh_database_stats(kb_id)
        return {
            "status": "success",
            "stats": stats,
            "scanned_files": scanned_files,
            "scanned_indexed_files": scanned_indexed_files,
            "skipped_unindexed_files": skipped_file_count,
            "scanned_token_files": scanned_token_files,
            "updated_files": updated_files,
            "updated_chunk_files": updated_chunk_files,
            "updated_token_files": updated_token_files,
            "updated_size_files": updated_size_files,
        }

    def get_database_info(self, kb_id: str, include_files: bool = True) -> dict | None:
        """
        获取数据库详细信息

        Args:
            kb_id: 数据库ID
            include_files: 是否包含文件信息，默认为True

        Returns:
            数据库信息或None
        """
        if kb_id not in self.databases_meta:
            return None

        meta = self.databases_meta[kb_id].copy()
        meta["kb_id"] = kb_id

        meta["stats"] = self._get_database_stats(kb_id)
        meta["row_count"] = meta["stats"].get("row_count") or meta["stats"].get("file_count") or 0

        if include_files:
            meta["files"] = {}
            meta["files_truncated"] = True

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
        for kb_id, meta in self.databases_meta.items():
            db_dict = meta.copy()
            db_dict["kb_id"] = kb_id
            db_dict["stats"] = self._get_database_stats(kb_id)
            db_dict["row_count"] = db_dict["stats"].get("row_count") or db_dict["stats"].get("file_count") or 0

            if include_files:
                db_dict["files"] = {}
                db_dict["files_truncated"] = True

            db_dict["status"] = "已连接"
            databases.append(db_dict)

        return {"databases": databases}

    async def delete_folder(self, kb_id: str, folder_id: str) -> None:
        """
        Recursively delete a folder and its content.

        Args:
            kb_id: Database ID
            folder_id: Folder ID to delete
        """
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        children = await KnowledgeFileRepository().list_children(kb_id=kb_id, parent_id=folder_id)
        for child in children:
            child_id = child.file_id
            if child.is_folder:
                await self.delete_folder(kb_id, child_id)
            else:
                await self.delete_file(kb_id, child_id)

        # Delete the folder itself
        # We call delete_file which should handle the actual removal.
        # Implementations should ensure they handle folder deletion gracefully (e.g. skip vector deletion)
        await self.delete_file(kb_id, folder_id)

    async def move_file(self, kb_id: str, file_id: str, new_parent_id: str | None) -> dict:
        """
        Move a file or folder to a new parent folder.

        Args:
            kb_id: Database ID
            file_id: File/Folder ID to move
            new_parent_id: New parent folder ID (None for root)

        Returns:
            dict: Updated metadata
        """
        meta = await self._load_file_meta(kb_id, file_id)

        # Basic cycle detection for folders
        if meta.get("is_folder") and new_parent_id:
            # Check if new_parent_id is a child of file_id (or is file_id itself)
            if new_parent_id == file_id:
                raise ValueError("Cannot move a folder into itself")

            # Walk up the tree from new_parent_id
            current = new_parent_id
            while current:
                parent_meta = await self._load_file_meta(kb_id, current)
                if current == new_parent_id and not parent_meta.get("is_folder"):
                    raise ValueError("Parent is not a folder")
                if current == file_id:
                    raise ValueError("Cannot move a folder into its own subfolder")
                current = parent_meta.get("parent_id")
        elif new_parent_id:
            parent_meta = await self._load_file_meta(kb_id, new_parent_id)
            if not parent_meta.get("is_folder"):
                raise ValueError("Parent is not a folder")

        meta["parent_id"] = new_parent_id
        await self._persist_file_meta(file_id, meta)
        return meta

    @abstractmethod
    async def delete_file(self, kb_id: str, file_id: str) -> None:
        """
        删除文件

        Args:
            kb_id: 数据库ID
            file_id: 文件ID
        """
        pass

    @abstractmethod
    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        """
        获取文件基本信息（仅元数据）

        Args:
            kb_id: 数据库ID
            file_id: 文件ID

        Returns:
            dict: 包含文件基本信息的字典
        """
        pass

    @abstractmethod
    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        """
        获取文件内容信息（chunks和lines）

        Args:
            kb_id: 数据库ID
            file_id: 文件ID

        Returns:
            dict: 包含文件内容信息的字典
        """
        pass

    @abstractmethod
    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        """
        获取文件完整信息（基本信息+内容信息）

        Args:
            kb_id: 数据库ID
            file_id: 文件ID

        Returns:
            dict: 包含文件信息和chunks的字典
        """
        pass

    def update_database(
        self,
        kb_id: str,
        name: str,
        description: str,
        llm_model_spec: str | None = None,
        update_llm_model_spec: bool = False,
    ) -> dict:
        """
        更新数据库

        Args:
            kb_id: 数据库ID
            name: 新名称
            description: 新描述
            llm_model_spec: LLM 模型 spec（可选）

        Returns:
            更新后的数据库信息
        """
        if kb_id not in self.databases_meta:
            raise ValueError(f"数据库 {kb_id} 不存在")

        self.databases_meta[kb_id]["name"] = name
        self.databases_meta[kb_id]["description"] = description
        if update_llm_model_spec:
            self.databases_meta[kb_id]["llm_model_spec"] = llm_model_spec

        return self.get_database_info(kb_id)

    def get_retrievers(self) -> dict[str, dict]:
        """
        获取所有检索器

        Returns:
            检索器字典
        """
        retrievers = {}
        for kb_id, meta in self.databases_meta.items():

            def make_retriever(kb_id):
                async def retriever(query_text, **kwargs):
                    results = await self.aquery(query_text, kb_id, agent_call=True, **kwargs)
                    return self.build_search_output(kb_id, results)

                return retriever

            retrievers[kb_id] = {
                "name": meta["name"],
                "description": meta["description"],
                "retriever": make_retriever(kb_id),
                "metadata": meta,
            }
        return retrievers

    async def _load_metadata(self) -> None:
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()

        databases = [kb for kb in await kb_repo.get_all() if kb.kb_type == self.kb_type]
        self.databases_meta = {
            kb.kb_id: {
                "name": kb.name,
                "description": kb.description,
                "kb_type": kb.kb_type,
                "embedding_model_spec": kb.embedding_model_spec,
                "llm_model_spec": kb.llm_model_spec,
                "query_params": kb.query_params or self._get_default_query_params(kb.kb_id),
                "metadata": self.normalize_additional_params(kb.additional_params),
                "created_at": utc_isoformat(kb.created_at) if kb.created_at else utc_isoformat(),
            }
            for kb in databases
        }

        # 文件元数据量可能达到几十万级，启动阶段只加载 KB 级配置。
        # 单文件操作按需查询 PostgreSQL，并在流程内通过局部变量传递。

        self.benchmarks_meta = {}
        self._normalize_metadata_state()
        self._metadata_loaded = True

        logger.info(f"Loaded {self.kb_type} metadata from database for {len(self.databases_meta)} databases")

    async def _fill_missing_file_sizes_for_records(self, records: list[Any]) -> dict[str, int]:
        """为显式修复任务中的缺失 size 文件从 MinIO 补全大小信息。"""
        from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
        from yuxi.storage.minio import get_minio_client

        candidates: list[tuple[str, str]] = []
        for record in records:
            if record.is_folder or record.file_size is not None:
                continue
            file_path = record.minio_url or record.path
            if not file_path or not is_minio_url(file_path):
                continue
            candidates.append((record.file_id, file_path))

        if not candidates:
            return {}

        minio_client = get_minio_client()
        semaphore = asyncio.Semaphore(20)

        async def _stat_file(file_id: str, file_path: str) -> tuple[str, int | None]:
            bucket_name, obj_name = parse_minio_url(file_path)
            try:
                async with semaphore:
                    return file_id, await minio_client.astat_file(bucket_name, obj_name)
            except Exception as exc:
                logger.warning(f"Failed to fill size for {file_id}: {exc}")
                return file_id, None

        updates: dict[str, int] = {}
        for offset in range(0, len(candidates), 100):
            batch = candidates[offset : offset + 100]
            for file_id, file_size in await asyncio.gather(
                *(_stat_file(file_id, file_path) for file_id, file_path in batch)
            ):
                if file_size is not None:
                    updates[file_id] = file_size

        if updates:
            logger.info(f"Filled {len(updates)}/{len(candidates)} missing file sizes from MinIO for {self.kb_type}")
        return updates

    async def _save_metadata(self) -> None:
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()

        self._normalize_metadata_state()

        for kb_id, meta in self.databases_meta.items():
            existing = await kb_repo.get_by_kb_id(kb_id)
            payload = {
                "kb_id": kb_id,
                "name": meta.get("name") or kb_id,
                "description": meta.get("description"),
                "kb_type": meta.get("kb_type") or self.kb_type,
                "embedding_model_spec": meta.get("embedding_model_spec"),
                "llm_model_spec": meta.get("llm_model_spec"),
                "query_params": meta.get("query_params"),
                "additional_params": meta.get("metadata") or {},
            }
            if existing is None:
                await kb_repo.create(payload)

    async def _persist_file_meta(self, file_id: str, meta: dict) -> None:
        """Persist one file metadata record without storing it on the KB instance."""
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        data = self._file_meta_to_record_data(meta)
        if not data.get("kb_id"):
            return
        await KnowledgeFileRepository().upsert(file_id=file_id, data=data)

    async def _persist_kb(self, kb_id: str, record_fields: dict[str, Any] | None = None) -> None:
        """只保存单个知识库到数据库，避免全量遍历"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()

        if kb_id not in self.databases_meta:
            return

        meta = self.databases_meta[kb_id]
        existing = await kb_repo.get_by_kb_id(kb_id)
        payload = {
            "kb_id": kb_id,
            "name": meta.get("name") or kb_id,
            "description": meta.get("description"),
            "kb_type": meta.get("kb_type") or self.kb_type,
            "embedding_model_spec": meta.get("embedding_model_spec"),
            "llm_model_spec": meta.get("llm_model_spec"),
            "query_params": meta.get("query_params"),
            "additional_params": meta.get("metadata") or {},
        }
        if record_fields:
            allowed_fields = {"share_config", "created_by"}
            payload.update({key: value for key, value in record_fields.items() if key in allowed_fields})

        if existing is None:
            await kb_repo.create(payload)
        else:
            update_data = {
                "name": payload["name"],
                "description": payload["description"],
                "kb_type": payload["kb_type"],
                "embedding_model_spec": payload["embedding_model_spec"],
                "llm_model_spec": payload["llm_model_spec"],
                "query_params": payload["query_params"],
                "additional_params": payload["additional_params"],
            }
            if record_fields:
                update_data.update({key: payload[key] for key in allowed_fields if key in payload})
            await kb_repo.update(kb_id, update_data)
