from typing import Any

from yuxi.knowledge.base import KnowledgeBase


class ReadOnlyConnectors(KnowledgeBase):
    """只读外部检索连接器基类。

    这类知识库只负责保存连接参数和执行 Query，不承载文档上传、解析、索引和文件预览能力。
    """

    requires_embedding_model = False
    supports_documents = False
    apply_chunk_defaults = False

    @staticmethod
    def _readonly_error() -> ValueError:
        return ValueError("只读检索连接器不支持该操作")

    async def _create_kb_instance(self, kb_id: str, config: dict) -> Any:
        del kb_id, config
        return None

    async def _initialize_kb_instance(self, instance: Any) -> None:
        del instance
        return None

    async def add_file_record(
        self, kb_id: str, item: str, params: dict | None = None, operator_id: str | None = None
    ) -> dict:
        raise self._readonly_error()

    async def parse_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        raise self._readonly_error()

    async def update_file_params(self, kb_id: str, file_id: str, params: dict, operator_id: str | None = None) -> None:
        raise self._readonly_error()

    async def create_folder(self, kb_id: str, folder_name: str, parent_id: str | None = None) -> dict:
        raise self._readonly_error()

    async def move_file(self, kb_id: str, file_id: str, new_parent_id: str | None) -> dict:
        raise self._readonly_error()

    async def delete_folder(self, kb_id: str, folder_id: str) -> None:
        raise self._readonly_error()

    async def index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
    ) -> dict:
        raise self._readonly_error()

    async def update_content(self, kb_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        raise self._readonly_error()

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        raise self._readonly_error()

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        raise self._readonly_error()

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        raise self._readonly_error()

    async def open_file_content(self, kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        del offset, limit
        raise self._readonly_error()

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
        del kb_id, file_id, patterns, use_regex, case_sensitive, max_windows, window_size
        raise self._readonly_error()

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        raise self._readonly_error()

    async def list_file_tree(
        self,
        kb_id: str,
        parent_id: str | None = None,
        recursive: bool = False,
        files_only: bool = False,
    ) -> dict:
        del kb_id, parent_id, recursive, files_only
        raise ValueError("只读检索连接器不支持文件树预览")

    async def read_file_preview(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        raise ValueError("只读检索连接器不支持文件预览")

    async def get_file_download(self, kb_id: str, file_id: str, variant: str = "original") -> dict:
        del kb_id, file_id, variant
        raise ValueError("只读检索连接器不支持文件下载")
