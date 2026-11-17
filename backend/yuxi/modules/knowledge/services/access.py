"""知识库当前读取权限与调用方选择的交集。"""

from dataclasses import replace
from typing import Any, Literal

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_knowledge_base_permission
from yuxi.modules.knowledge.read_models import KnowledgeBaseSummary
from yuxi.modules.knowledge.services import bases
from yuxi.modules.knowledge.utils.security import redact_sensitive_params


async def visible_knowledge_bases(
    uid: str,
    *,
    selection: Literal["all"] | list[str] = "all",
) -> list[dict[str, Any]]:
    """按当前用户权限解析全部或显式选择的知识库摘要。"""
    if selection != "all" and (
        not isinstance(selection, list) or any(not isinstance(kb_id, str) or not kb_id.strip() for kb_id in selection)
    ):
        raise ValueError('知识库选择必须是"all" 或非空字符串组成的列表')
    if not uid or selection == []:
        return []

    summaries = await get_knowledge_bases_by_uid(uid)
    selected_ids = None if selection == "all" else {kb_id.strip() for kb_id in selection}
    return [
        {"kb_id": item.kb_id, "name": item.name, "description": item.description, "kb_type": item.kb_type}
        for item in summaries
        if selected_ids is None or item.kb_id in selected_ids
    ]


async def get_knowledge_bases_by_uid(uid: str) -> list[KnowledgeBaseSummary]:
    """根据 uid 获取知识库列表"""
    from yuxi.modules.identity.repositories.users import UserRepository

    user_repo = UserRepository()
    user: User | None = await user_repo.get_by_uid(uid)
    if not user:
        logger.warning(f"User not found: {uid}")
        return []
    return await get_knowledge_bases_by_user(user)


async def get_knowledge_bases_by_user(user: User | dict) -> list[KnowledgeBaseSummary]:
    """根据用户权限获取知识库列表"""

    # 构建用户信息字典（支持 User 对象或 dict）
    if isinstance(user, dict):
        user_info = user
    else:
        user_info = {
            "uid": user.uid,
            "role": user.role,
            "department_id": user.department_id,
        }

    user_role = user_info.get("role")
    user_dept = user_info.get("department_id")
    logger.info(f"Getting knowledge_bases for user with role {user_role} and department {user_dept}")

    all_knowledge_bases = await bases.get_knowledge_bases()

    # 超级管理员可以看到所有知识库
    filtered_knowledge_bases: list[KnowledgeBaseSummary] = []
    for knowledge_base in all_knowledge_bases:
        permission = resolve_knowledge_base_permission(user_info, knowledge_base)
        if permission == ResourcePermission.NONE:
            continue
        additional_params = knowledge_base.additional_params
        if permission == ResourcePermission.READ:
            additional_params = redact_sensitive_params(additional_params)
        filtered_knowledge_bases.append(
            replace(
                knowledge_base,
                additional_params=additional_params,
                effective_permission=permission,
            )
        )

    return filtered_knowledge_bases


async def check_accessible(user: dict, kb_id: str) -> bool:
    """检查用户是否可访问知识库；超级管理员直通，库不存在视为不可访问。"""
    # 超级管理员有权访问所有
    if user.get("role") == "superadmin":
        return True

    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        return False

    return resolve_knowledge_base_permission(user, kb) != ResourcePermission.NONE
