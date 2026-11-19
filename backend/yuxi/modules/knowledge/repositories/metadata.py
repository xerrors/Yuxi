"""文件属性查询、写入锁及 JSONB 条件编译。"""

from sqlalchemy import and_, func, literal, or_, select, type_coerce
from sqlalchemy.dialects.postgresql import JSONB

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_knowledge_base_permission
from yuxi.modules.knowledge.metadata import MetadataFilter, MetadataGroup
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.shared.errors import InvalidOperationError, PermissionDeniedError, ResourceNotFoundError


class MetadataRepository:
    """元数据与文件共用知识库写锁，事务由领域用例拥有。"""

    async def read_schema(self, kb_id: str) -> tuple[str, list[dict]]:
        """读取实时字段定义，不缓存业务字段目录。"""
        async with pg_manager.get_async_session_context() as session:
            row = (
                await session.execute(
                    select(KnowledgeBase.kb_type, KnowledgeBase.metadata_fields).where(
                        KnowledgeBase.kb_id == kb_id, KnowledgeBase.deleted_at.is_(None)
                    )
                )
            ).one_or_none()
            if row is None:
                raise ResourceNotFoundError("知识库不存在")
            return row.kb_type, row.metadata_fields

    async def lock_for_write(self, session, kb_id: str, uid: str) -> KnowledgeBase:
        """获得知识库锁后复核当前操作者的管理权及本地文档能力。"""
        kb = await DirectoryRepository().lock(session, kb_id)
        user = await session.scalar(select(User).where(User.uid == uid, User.is_deleted == 0))
        if user is None or resolve_knowledge_base_permission(user, kb) != ResourcePermission.MANAGE:
            raise PermissionDeniedError("无权管理该知识库的文件属性")
        if kb.kb_type != "milvus":
            raise InvalidOperationError("当前知识库不支持本地文件属性管理")
        return kb

    async def lock_files(self, session, kb_id: str, file_ids: list[str]) -> list[KnowledgeFile]:
        """锁定同库有效文件，任一 ID 不可用时整批拒绝。"""
        files = list(
            (
                await session.scalars(
                    select(KnowledgeFile)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.file_id.in_(file_ids),
                        KnowledgeFile.deleted_at.is_(None),
                        KnowledgeFile.is_folder.is_(False),
                    )
                    .order_by(KnowledgeFile.file_id)
                    .with_for_update()
                )
            ).all()
        )
        if len(files) != len(file_ids):
            raise ResourceNotFoundError("部分文件不存在、不属于当前知识库或是目录")
        return files

    async def field_in_use(self, session, kb_id: str, key: str, options: set[str] | None = None) -> bool:
        """字段或选项仍被有效文件使用时，拒绝删除定义。"""
        attributes = type_coerce(KnowledgeFile.attributes, JSONB)
        condition = attributes.has_key(key)
        if options is not None:
            condition = or_(*(attributes.contains({key: option}) | attributes.contains({key: [option]}) for option in options))
        return bool(
            await session.scalar(
                select(
                    select(KnowledgeFile.id)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.deleted_at.is_(None),
                        KnowledgeFile.is_folder.is_(False),
                        condition,
                    )
                    .exists()
                )
            )
        )


def metadata_predicate(expression: MetadataFilter):
    """编译已通过领域校验的条件，只产生绑定参数，不拼接字段或值。"""
    if isinstance(expression, MetadataGroup):
        children = [metadata_predicate(child) for child in expression.conditions]
        return and_(*children) if expression.operator == "and" else or_(*children)
    attributes = type_coerce(KnowledgeFile.attributes, JSONB)
    item = attributes[expression.field]
    present = attributes.has_key(expression.field) & (func.jsonb_typeof(item) != "null")
    operator, value = expression.operator, expression.value
    if operator == "exists":
        return present
    if operator == "not_exists":
        return ~attributes.has_key(expression.field) | (func.jsonb_typeof(item) == "null")
    if operator == "contains_any":
        return or_(*(attributes.contains({expression.field: [entry]}) for entry in value))
    if operator == "contains_all":
        return attributes.contains({expression.field: value})
    if operator == "eq":
        return attributes.contains({expression.field: value})
    expected = literal(value, type_=JSONB)
    comparisons = {
        "eq": item == expected,
        "ne": item != expected,
        "gt": item > expected,
        "gte": item >= expected,
        "lt": item < expected,
        "lte": item <= expected,
    }
    expected_type = "boolean" if type(value) is bool else "number" if type(value) in {int, float} else "string"
    return and_(present, func.jsonb_typeof(item) == expected_type, comparisons[operator])
