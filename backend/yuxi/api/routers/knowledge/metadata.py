"""知识库文件属性的 Web 管理入口。"""

from fastapi import APIRouter, Depends

from yuxi.api.dependencies.knowledge import require_knowledge_base_manage, require_knowledge_base_read
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.metadata import MetadataFieldCreate, MetadataFieldUpdate, MetadataFilesPatch, MetadataPatch
from yuxi.modules.knowledge.services import metadata

router = APIRouter(prefix="/knowledge-bases/{kb_id}")


@router.get("/metadata-fields")
async def get_fields(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """读取当前字段定义及其可用筛选操作。"""
    return await metadata.get_metadata_schema(kb_id)


@router.post("/metadata-fields")
async def create_field(kb_id: str, payload: MetadataFieldCreate, current_user: User = Depends(require_knowledge_base_manage)):
    """新增一个可选文件属性字段。"""
    return await metadata.create_metadata_field(kb_id, current_user.uid, payload)


@router.patch("/metadata-fields/{key}")
async def update_field(kb_id: str, key: str, payload: MetadataFieldUpdate, current_user: User = Depends(require_knowledge_base_manage)):
    """更新字段显示信息及未被使用的选项。"""
    return await metadata.update_metadata_field(kb_id, key, current_user.uid, payload)


@router.delete("/metadata-fields/{key}")
async def delete_field(kb_id: str, key: str, current_user: User = Depends(require_knowledge_base_manage)):
    """删除没有存量属性值的字段。"""
    return await metadata.delete_metadata_field(kb_id, key, current_user.uid)


@router.patch("/documents/attributes")
async def patch_attributes(kb_id: str, payload: MetadataFilesPatch, current_user: User = Depends(require_knowledge_base_manage)):
    """同库原子修改选中文件的指定属性。"""
    return await metadata.patch_file_attributes(kb_id, current_user.uid, payload)


@router.patch("/documents/{file_id}/attributes")
async def patch_file(kb_id: str, file_id: str, payload: MetadataPatch, current_user: User = Depends(require_knowledge_base_manage)):
    """单文件与批量修改使用相同的属性更新用例。"""
    request = MetadataFilesPatch(file_ids=[file_id], **payload.model_dump(by_alias=True))
    return await metadata.patch_file_attributes(kb_id, current_user.uid, request)
