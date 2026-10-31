"""Agent 定义与专属 Skill 的共同生命周期。"""

import asyncio
import shutil
from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.permissions import is_builtin_agent, user_can_manage_agent, user_can_run_agent
from yuxi.modules.agents.repositories.definitions import DEFAULT_SHARE_CONFIG, AgentRepository
from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
from yuxi.modules.agents.runtime.context import filter_declared_config
from yuxi.modules.agents.services.configuration import merge_agent_config_json, prepare_agent_config_write
from yuxi.modules.extensions.skills.access import validate_skill_dependencies
from yuxi.modules.extensions.skills.bound import publish_bound_skill
from yuxi.modules.extensions.skills.install_draft import prepared_uploaded_skill
from yuxi.modules.extensions.skills.package import parse_skill_dir_metadata
from yuxi.modules.extensions.skills.projection import invalidate_skill_projections, refresh_committed_skill_projections
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import normalize_permission_config, resolve_agent_permission
from yuxi.modules.identity.permissions.resource_permission import ADMIN_ROLES
from yuxi.modules.identity.repositories.resource_grants import validate_shared_grants
from yuxi.modules.identity.repositories.users import UserRepository


async def ensure_default_agent_definition(db, *, created_by: str | None = None) -> Agent:
    """默认定义变化时提交，读取已就绪定义不产生空提交。"""
    agent, changed = await AgentRepository(db).ensure_default_agent(created_by=created_by)
    if changed:
        await db.commit()
    return agent


async def set_default_agent_definition(db, *, agent: Agent, operator: User) -> Agent:
    """应用默认定义写入并由用例提交。"""
    result = await AgentRepository(db).set_default(agent=agent, updated_by=str(operator.uid))
    await db.commit()
    return result


async def create_agent_definition(db, *, operator, skill_upload=None, **fields):
    """提交 Agent、Context 与可选专属包，共享资源通过独立入口创建。"""
    if fields["backend_id"] == "PersonalAssistantAgent":
        raise ValueError("个人助理只能通过专用入口创建")
    backend = get_agent_backend(fields["backend_id"])
    if fields.pop("set_default", False):
        raise ValueError("默认智能体已固定为内置智能助手")
    operator = await UserRepository(db).lock_active_human(operator.uid)
    if operator is None:
        raise PermissionError("必须由有效产品用户创建智能体")

    try:
        config_json, resource_access = await prepare_agent_config_write(
            fields.pop("config_json", None) or {},
            context_schema=backend.context_schema,
            db=db,
            user=operator,
        )
        if fields.get("visibility", "private") == "shared":
            fields["share_config"] = await validate_shared_grants(db, fields.get("share_config") or DEFAULT_SHARE_CONFIG)
        agent = await AgentRepository(db).create(
            **fields,
            config_json=merge_agent_config_json(
                {"context": {}}, config_json, resource_access=resource_access, context_schema=backend.context_schema
            ),
            created_by=operator.uid,
            creator=operator,
        )
        if skill_upload is not None:
            filename, file_bytes = skill_upload
            with prepared_uploaded_skill(filename=filename, file_bytes=file_bytes) as source:
                await publish_bound_skill(
                    db,
                    agent=agent,
                    operator=operator,
                    source=source,
                    source_slug=parse_skill_dir_metadata(source)["slug"],
                )
        else:
            await db.commit()
    except Exception:
        await db.rollback()
        raise
    return agent


async def commit_agent_definition(db, agent, user, *, refresh_binding: bool = True) -> None:
    """定义授权变化时校验绑定依赖并撤下旧投影，再共同提交。"""
    item = await SkillRepository(db).get_by_bound_agent_id(agent.id, for_update=True) if refresh_binding else None
    uids = []
    if item is not None:
        item.bound_agent = agent
        await validate_skill_dependencies(
            db=db,
            parent=item,
            tool_dependencies=item.tool_dependencies,
            mcp_dependencies=item.mcp_dependencies,
            skill_dependencies=item.skill_dependencies,
            available_skills={row.slug: row for row in await SkillRepository(db).list_enabled_readable(user)},
        )
        uids = await invalidate_skill_projections(db, item.slug)
    await db.commit()
    await refresh_committed_skill_projections(uids)


async def update_agent_definition(db, agent, **kwargs):
    """锁内准备配置与共享补丁，绑定依赖与定义共同提交。"""
    try:
        repo = AgentRepository(db)
        agent, operator = await repo.lock_for_management(agent_id=agent.id, user=kwargs.pop("updater", None))
        backend = get_agent_backend(agent.backend_id)
        share_config = kwargs.get("share_config")
        sharing = agent.visibility == "private" and kwargs.get("visibility") == "shared"
        if sharing:
            if operator.role not in ADMIN_ROLES:
                raise PermissionError("仅管理员可共享智能体")
            if share_config is None:
                raise ValueError("共享智能体必须指定共享权限")
        if share_config is not None and agent.visibility == "private" and not sharing:
            raise ValueError("私有智能体不接受共享授权")
        if share_config is not None and is_builtin_agent(agent):
            raise ValueError("内置定义不接受共享配置写入")
        if kwargs.get("config_json") is not None:
            config, access = await prepare_agent_config_write(
                kwargs["config_json"], context_schema=backend.context_schema, db=db, user=operator
            )
            kwargs["config_json"] = merge_agent_config_json(
                agent.config_json, config, resource_access=access, context_schema=backend.context_schema
            )
        if share_config is not None:
            kwargs["share_config"] = await validate_shared_grants(db, share_config)
        agent = await repo.update_record(agent, **kwargs)
        await commit_agent_definition(db, agent, operator, refresh_binding=share_config is not None or kwargs.get("visibility") is not None)
        return agent

    except Exception:
        await db.rollback()
        raise


async def delete_agent_definition(db, *, agent, user) -> None:
    """沿用活跃执行拒绝规则，提交删除后清理已经定位的专属内容。"""
    # repository 先持有 Agent/Thread 锁并证明无活跃执行，随后由 FK 删除绑定行。
    from yuxi.modules.extensions.skills.bound import lock_manageable_agent

    agent, user = await lock_manageable_agent(db, agent.slug, user)
    item = await SkillRepository(db).get_by_bound_agent_id(agent.id, for_update=True)
    await AgentRepository(db).delete(agent=agent, user=user)
    if item is None:
        await db.commit()
        return
    slug = item.slug
    packages = get_skill_data_dir() / "packages" / slug
    owned = list(packages.iterdir()) if packages.is_dir() else []
    uids = await invalidate_skill_projections(db, slug)
    await db.commit()
    for root in owned:
        try:
            await asyncio.to_thread(shutil.rmtree, root)
        except FileNotFoundError:
            pass  # 无引用目录可能已被其他清理完成。
    await refresh_committed_skill_projections(uids)


async def serialize_agent_definition(
    db,
    agent: Agent,
    *,
    user: User,
    include_configurable_items: bool = False,
    backend_info_cache: dict[tuple[str, bool, str], dict] | None = None,
) -> dict[str, Any]:
    """装配治理权限、后端元数据与配置项。"""
    data = agent.to_dict()
    if agent.backend_id == "PersonalAssistantAgent":
        data["is_personal_assistant"] = True
        include_configurable_items = False

    data["share_config_invalid"] = False
    try:
        data["share_config"] = normalize_permission_config(agent.share_config)
    except (TypeError, ValueError) as exc:
        if user.role != "superadmin":
            raise
        # 管理者需要读取原始坏配置进行修复，不将它解释为有效共享范围。
        data["share_config"] = agent.share_config
        data["share_config_invalid"] = True
        logger.warning("Invalid agent share config exposed for repair: slug={}, error={}", agent.slug, str(exc))
    permission = resolve_agent_permission(user, agent)
    is_builtin = is_builtin_agent(agent)
    data["can_manage"] = user_can_manage_agent(user, agent)
    data["can_run"] = user_can_run_agent(user, agent)
    data["can_share"] = data["can_manage"] and not is_builtin and (agent.visibility == "shared" or user.role in ADMIN_ROLES)
    data["can_transfer"] = agent.visibility == "shared" and user.role == "superadmin" and not is_builtin
    data["effective_permission"] = permission.value
    data["is_builtin"] = is_builtin
    data["permission_locked"] = is_builtin

    from yuxi.modules.agents.runtime.agent_backends import get_agent_backend

    backend = get_agent_backend(agent.backend_id)
    data["config_json"] = filter_declared_config(data.get("config_json") or {}, context_schema=backend.context_schema)
    cache_key = (agent.backend_id, include_configurable_items, user.role)
    backend_info = backend_info_cache.get(cache_key) if backend_info_cache is not None else None
    if backend_info is None:
        backend_info = await backend.get_info(
            include_configurable_items=include_configurable_items,
            user_role=user.role,
            db=db if include_configurable_items else None,
            user=user if include_configurable_items else None,
        )
        if backend_info_cache is not None:
            backend_info_cache[cache_key] = backend_info
    data["metadata"] = backend_info.get("metadata", {})
    if include_configurable_items:
        data["configurable_items"] = backend_info.get("configurable_items", {})
    return data


async def list_visible_agent_definitions(db, *, user: User) -> list[Agent]:
    """返回当前用户可见 Agent，并确保默认定义已就绪。"""
    await ensure_default_agent_definition(db)
    return await AgentRepository(db).list_visible(user=user)


async def get_visible_agent_definition(db, *, user: User, slug: str) -> Agent | None:
    """按运行期可见性读取 Agent；不存在或不可见返回 None。"""
    return await AgentRepository(db).get_visible_by_slug(slug=slug, user=user, for_run=False)


async def get_agent_definition_by_slug(db, *, slug: str) -> Agent | None:
    """按标识读取 Agent 定义，供只读管理入口使用。"""
    return await AgentRepository(db).get_by_slug(slug)
