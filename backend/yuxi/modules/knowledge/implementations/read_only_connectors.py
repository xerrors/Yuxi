from typing import Any

from yuxi.modules.knowledge.base import KnowledgeExecutor


class ReadOnlyConnectors(KnowledgeExecutor):
    """只读外部检索连接器基类。

    这类知识库只负责保存连接参数和执行 Query，不承载文档上传、解析、索引和文件预览能力；
    下方桩方法按基类业务流顺序排列，统一显式拒绝写路径。
    """

    requires_embedding_model = False
    supports_documents = False
    supports_document_read = False
    apply_chunk_defaults = False

    @staticmethod
    def _readonly_error() -> ValueError:
        return ValueError("只读检索连接器不支持该操作")

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
        recover_missing_markdown: bool = False,
    ) -> dict:
        del kb_id, file_id, operator_id, additional_params, processing_job_id, processing_owner, params, recover_missing_markdown
        raise self._readonly_error()

    async def index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
        *,
        embedding_model_spec: str | None,
        additional_params: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        del (
            kb_id,
            file_id,
            operator_id,
            params,
            embedding_model_spec,
            additional_params,
            processing_job_id,
            processing_owner,
        )
        raise self._readonly_error()
