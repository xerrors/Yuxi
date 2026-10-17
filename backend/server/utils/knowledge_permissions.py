"""将知识库领域权限校验适配为 FastAPI 依赖。"""

from fastapi import Depends, HTTPException

from server.utils.auth_middleware import get_admin_user, get_required_user
from yuxi.knowledge.read_models import KnowledgeBaseDetail
from yuxi.knowledge.runtime import knowledge_base
from yuxi.permissions import (
    ResourcePermission,
    ResourcePermissionDenied,
    require_knowledge_base_permission,
)
from yuxi.storage.postgres.models_business import User


async def ensure_knowledge_base_permission(
    kb_id: str,
    current_user: User,
    required: ResourcePermission,
) -> KnowledgeBaseDetail:
    """加载知识库并校验当前用户的有效资源权限。"""

    db_info = await knowledge_base.get_database_info(kb_id)
    if not db_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    try:
        require_knowledge_base_permission(current_user, db_info, required)
    except ResourcePermissionDenied as error:
        raise HTTPException(status_code=403, detail="无权操作该知识库") from error
    return db_info


async def require_knowledge_base_read(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验登录用户对指定知识库的读取权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.READ)
    return current_user


async def require_knowledge_base_content_edit(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验登录用户对指定知识库的文档与文件夹编辑权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.MANAGE)
    return current_user


async def require_knowledge_base_manage(
    kb_id: str,
    current_user: User = Depends(get_admin_user),
) -> User:
    """校验管理员对指定知识库的管理权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.MANAGE)
    return current_user
