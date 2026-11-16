"""知识库原生媒体处理设置。"""

from sqlalchemy import select

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.cache import delete_cached_kb_config, kb_config_cache_lock
from yuxi.modules.knowledge.media.repository import require_media_manage
from yuxi.modules.knowledge.media.schemas import DocumentImageAutoIndexSettings
from yuxi.modules.knowledge.models import KnowledgeBase
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.models.media import available_media_models
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, ResourceNotFoundError

DOCUMENT_IMAGE_AUTO_INDEX_KEY = "document_image_auto_index"


async def get_document_image_auto_index_settings(kb_id: str) -> DocumentImageAutoIndexSettings:
    """读取知识库级文档图片自动索引设置。"""
    async with pg_manager.get_async_session_context() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id, KnowledgeBase.deleted_at.is_(None)))
        if kb is None:
            raise ResourceNotFoundError("知识库不存在")
        _require_media_knowledge_base(kb)
        raw = (kb.additional_params or {}).get(DOCUMENT_IMAGE_AUTO_INDEX_KEY) or {}
        return DocumentImageAutoIndexSettings.model_validate(raw)


async def update_document_image_auto_index_settings(
    kb_id: str, operator_id: str, settings: DocumentImageAutoIndexSettings
) -> DocumentImageAutoIndexSettings:
    """校验权限与兼容模型后保存知识库级自动索引设置。"""
    async with kb_config_cache_lock(kb_id):
        await delete_cached_kb_config(kb_id)
        async with pg_manager.get_async_session_context() as session:
            kb = await DirectoryRepository().lock(session, kb_id)
            _require_media_knowledge_base(kb)
            await require_media_manage(session, kb, operator_id)
            if settings.enabled:
                available = {item["value"] for item in available_media_models()["embedding_model"]}
                if settings.embedding_model not in available:
                    raise InvalidInputError("请选择已启用且兼容的图像向量模型")
            additional_params = dict(kb.additional_params or {})
            additional_params[DOCUMENT_IMAGE_AUTO_INDEX_KEY] = settings.model_dump()
            kb.additional_params = additional_params
    return settings


def _require_media_knowledge_base(kb: KnowledgeBase) -> None:
    """原生图片索引仅支持文档型 Milvus 知识库。"""
    if kb.kb_type != "milvus":
        raise InvalidOperationError("原生媒体仅支持本地 Milvus 知识库", code="unsupported_media_store")
