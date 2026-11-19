"""知识库字段、文件属性和过滤条件的产品用例。"""

from uuid import uuid4

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.metadata import (
    FIELD_OPERATORS,
    MAX_METADATA_FIELDS,
    MetadataField,
    MetadataFieldCreate,
    MetadataFieldUpdate,
    MetadataFilesPatch,
    validate_attributes,
    validate_filter,
)
from yuxi.modules.knowledge.repositories.metadata import MetadataRepository
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, ResourceNotFoundError, StateConflictError


async def get_metadata_schema(kb_id: str) -> dict:
    """向管理界面和获授权的 MCP 调用方返回当前字段及可用操作。"""
    kb_type, raw_fields = await MetadataRepository().read_schema(kb_id)
    fields = [MetadataField.model_validate(item) for item in raw_fields]
    return {
        "kb_id": kb_id,
        "supported": kb_type == "milvus",
        "fields": [{**field.model_dump(), "operators": [*FIELD_OPERATORS[field.type], "exists", "not_exists"]} for field in fields],
    }


async def create_metadata_field(kb_id: str, uid: str, request: MetadataFieldCreate) -> dict:
    """在知识库事务中新增稳定字段定义。"""
    repo = MetadataRepository()
    async with pg_manager.get_async_session_context() as session:
        kb = await repo.lock_for_write(session, kb_id, uid)
        fields = [MetadataField.model_validate(item) for item in kb.metadata_fields]
        if len(fields) >= MAX_METADATA_FIELDS:
            raise InvalidInputError("每个知识库最多定义 64 个文件属性字段")
        if any(field.name.casefold() == request.name.casefold() for field in fields):
            raise StateConflictError("字段名称已存在")
        field = MetadataField(key="f_" + uuid4().hex, **request.model_dump())
        kb.metadata_fields = [*(item.model_dump() for item in fields), field.model_dump()]
    return field.model_dump()


async def update_metadata_field(kb_id: str, key: str, uid: str, request: MetadataFieldUpdate) -> dict:
    """更新展示名称或选项；已存属性使用的选项不能被隐式移除。"""
    repo = MetadataRepository()
    async with pg_manager.get_async_session_context() as session:
        kb = await repo.lock_for_write(session, kb_id, uid)
        fields = [MetadataField.model_validate(item) for item in kb.metadata_fields]
        previous = next((field for field in fields if field.key == key), None)
        if previous is None:
            raise ResourceNotFoundError("属性字段不存在")
        changes = request.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise InvalidInputError("字段定义不能设置为 null")
        try:
            updated = MetadataField.model_validate({**previous.model_dump(), **changes})
        except ValueError as error:
            raise InvalidInputError(str(error)) from error
        if any(field.key != key and field.name.casefold() == updated.name.casefold() for field in fields):
            raise StateConflictError("字段名称已存在")
        removed = set(previous.options) - set(updated.options)
        if removed and await repo.field_in_use(session, kb_id, key, removed):
            raise StateConflictError("待移除选项仍被文件使用，请先修改或清除相关文件属性")
        kb.metadata_fields = [updated.model_dump() if field.key == key else field.model_dump() for field in fields]
    return updated.model_dump()


async def delete_metadata_field(kb_id: str, key: str, uid: str) -> dict:
    """只删除没有存量属性值的字段定义。"""
    repo = MetadataRepository()
    async with pg_manager.get_async_session_context() as session:
        kb = await repo.lock_for_write(session, kb_id, uid)
        if not any(field["key"] == key for field in kb.metadata_fields):
            raise ResourceNotFoundError("属性字段不存在")
        if await repo.field_in_use(session, kb_id, key):
            raise StateConflictError("字段仍被文件使用，请先显式清除文件属性")
        kb.metadata_fields = [field for field in kb.metadata_fields if field["key"] != key]
    return {"deleted": key}


async def patch_file_attributes(kb_id: str, uid: str, request: MetadataFilesPatch) -> dict:
    """在同一事务内部分更新所有目标文件，任何错误整体回滚。"""
    repo = MetadataRepository()
    async with pg_manager.get_async_session_context() as session:
        kb = await repo.lock_for_write(session, kb_id, uid)
        fields = [MetadataField.model_validate(item) for item in kb.metadata_fields]
        known = {field.key for field in fields}
        if set(request.unset) - known:
            raise InvalidInputError("清除的字段不存在，请刷新字段定义")
        try:
            values = validate_attributes(request.set_values, fields)
        except ValueError as error:
            raise InvalidInputError(str(error)) from error
        files = await repo.lock_files(session, kb_id, request.file_ids)
        results = []
        for file in files:
            updated = {key: value for key, value in file.attributes.items() if key not in request.unset}
            updated.update(values)
            try:
                file.attributes = validate_attributes(updated, fields)
            except ValueError as error:
                raise InvalidInputError(str(error)) from error
            file.updated_by = uid
            results.append({"file_id": file.file_id, "attributes": file.attributes})
    return {"files": results}


async def prepare_metadata_filter(kb_id: str, value):
    """执行前读取实时定义；外部连接器不能忽略不支持的筛选。"""
    if value is None or value == []:
        return None
    kb_type, fields = await MetadataRepository().read_schema(kb_id)
    if kb_type != "milvus":
        raise InvalidOperationError("当前知识库不支持文件属性筛选")
    return validate_filter(value, [MetadataField.model_validate(field) for field in fields])
