"""将知识库领域权限校验适配为 FastAPI 依赖。"""

from fastapi import Depends, HTTPException

from yuxi.api.dependencies.auth import get_admin_user
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import (
    ResourcePermission,
    ResourcePermissionDenied,
    require_knowledge_base_permission,
)
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.knowledge.services import bases as knowledge_bases


async def ensure_knowledge_base_permission(
    kb_id: str,
    current_user: User,
    required: ResourcePermission,
) -> KnowledgeBaseDetail:
    """加载知识库并校验当前用户的有效资源权限。"""

    kb_info = await knowledge_bases.get_knowledge_base_info(kb_id)
    if not kb_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    try:
        require_knowledge_base_permission(current_user, kb_info, required)
    except ResourcePermissionDenied as error:
        raise HTTPException(status_code=403, detail="无权操作该知识库") from error
    return kb_info


async def require_knowledge_base_read(
    kb_id: str,
    current_user: User = Depends(get_admin_user),
) -> User:
    """校验管理员对指定知识库的读取权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.READ)
    return current_user


async def require_knowledge_base_manage(
    kb_id: str,
    current_user: User = Depends(get_admin_user),
) -> User:
    """校验管理员对指定知识库的管理权限。"""

    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.MANAGE)
    return current_user
