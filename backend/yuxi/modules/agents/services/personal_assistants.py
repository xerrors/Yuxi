"""Web 产品个人助理的创建与设置。"""

import asyncio
import uuid

from sqlalchemy import select

from yuxi.bootstrap.capabilities import personal_assistant_enabled
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.personal_assistants import PersonalAssistant
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import PersonalAssistantContext
from yuxi.modules.agents.services.configuration import merge_agent_config_json, prepare_agent_config_write
from yuxi.modules.identity.models import User
from yuxi.modules.identity.repositories.users import UserRepository
from yuxi.modules.models.providers.cache import model_cache
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.paths import ensure_user_workspace
from yuxi.modules.workspace.services.projects import create_project_record
from yuxi.shared.errors import InvalidInputError, ResourceNotFoundError

PERSONAL_WORKDIR = "agents/assistant"


async def personal_assistant_view(db, uid: str) -> dict:
    """读取启用功能的用户唯一绑定与当前模型可用状态。"""
    _require_enabled()
    row = await db.scalar(select(PersonalAssistant).where(PersonalAssistant.uid == uid))
    assistant = None
    spec = str((await system_options.get(db))["personal_assistant_model"] or "").strip()
    if row is not None:
        agent = await db.get(Agent, row.agent_id)
        session = await SessionRepository(db).get_session_by_thread_id(row.main_thread_id)
        spec = session.config_snapshot.get("model") or (agent.config_json.get("context") or {}).get("model") or spec
        assistant = {
            "id": f"assistant:{uid}",
            "agent_id": agent.slug,
            "name": agent.name,
            "icon": agent.to_dict()["icon"],
            "config_json": agent.config_json,
            "thread_id": row.main_thread_id,
            "project_id": row.project_id,
        }
    info = model_cache.get_model_info(spec) if spec else None
    error = (
        None if info is not None and info.model_type == "chat" else ("请联系管理员配置个人助理模型" if not spec else "个人助理模型不可用")
    )
    return {"assistant": assistant, "model_available": error is None, "model_error": error}


async def create_personal_assistant(db, *, user, name: str, avatar: str | None) -> dict:
    """在用户锁内原子创建定义、项目、主会话与登记；重试返回既有绑定。"""
    _require_enabled()
    name = _normalize_name(name)
    _validate_avatar(avatar)
    operator = await db.scalar(select(User).where(User.uid == user.uid, User.is_deleted == 0, User.user_kind == "human").with_for_update())
    if operator is None:
        raise ResourceNotFoundError("用户不存在")
    existing = await db.get(PersonalAssistant, operator.uid)
    if existing is not None:
        return await personal_assistant_view(db, operator.uid)

    await asyncio.to_thread(_ensure_assistant_workdir, operator.uid)
    agent = await AgentRepository(db).create(
        name=name,
        backend_id="PersonalAssistantAgent",
        slug=f"assistant-{uuid.uuid4().hex}",
        icon=avatar,
        config_json={"context": {"system_prompt": ""}},
        created_by=operator.uid,
        creator=operator,
    )
    project = await create_project_record(
        uid=operator.uid,
        name=None,
        directory_mode="linked",
        selection_status="implicit",
        workdir_path=PERSONAL_WORKDIR,
        db=db,
    )
    session = await SessionRepository(db).add_session(
        uid=operator.uid,
        agent_id=agent.slug,
        title=name,
        project_id=project.id,
        config_snapshot={
            "model": "",
            "system_prompt": "",
            "personal_assistant_name": name,
            "tool_approval_mode": "default",
        },
        metadata={"source": "personal_assistant", "channel": "web"},
    )
    db.add(PersonalAssistant(uid=operator.uid, agent_id=agent.id, project_id=project.id, main_thread_id=session.thread_id))
    await db.commit()
    return await personal_assistant_view(db, operator.uid)


async def update_personal_assistant(db, *, user, updates: dict) -> dict:
    """只更新用户可管理的名称、头像和个性化提示词。"""
    _require_enabled()
    row = await db.get(PersonalAssistant, user.uid)
    if row is None:
        raise ResourceNotFoundError("个人助理不存在")
    agent = await db.scalar(select(Agent).where(Agent.id == row.agent_id).with_for_update())
    operator = await UserRepository(db).lock_active_human(user.uid)
    if operator is None:
        raise ResourceNotFoundError("个人助理不存在")
    # 与协作创建保持 Agent → Session 锁序，再读取将要更新的会话配置。
    session = await SessionRepository(db).lock_session_by_thread_id(row.main_thread_id)
    if "name" in updates:
        updates["name"] = _normalize_name(updates["name"])
    if "icon" in updates:
        _validate_avatar(updates["icon"])
    if updates.get("config_json") is not None:
        config, access = await prepare_agent_config_write(
            updates["config_json"], context_schema=PersonalAssistantContext, db=db, user=operator
        )
        updates["config_json"] = merge_agent_config_json(
            agent.config_json, config, resource_access=access, context_schema=PersonalAssistantContext
        )
    await AgentRepository(db).update_record(agent, **updates, updated_by=operator.uid)
    snapshot = dict(session.config_snapshot)
    if "name" in updates:
        snapshot["personal_assistant_name"] = agent.name
    if updates.get("config_json") is not None:
        snapshot["system_prompt"] = agent.config_json["context"]["system_prompt"]
    session.config_snapshot = snapshot
    await db.commit()
    return await personal_assistant_view(db, operator.uid)


async def personal_task_summary(db, *, uid: str) -> dict:
    """查询当前用户助理创建的会话，状态复用普通协作摘要。"""
    from yuxi.modules.agents.services.cooperation import tree_snapshot

    _require_enabled()
    registration = await db.get(PersonalAssistant, uid)
    if registration is None:
        raise ResourceNotFoundError("个人助理不存在")
    caller = await SessionRepository(db).get_session_by_thread_id(registration.main_thread_id)
    return await tree_snapshot(db, caller, all_sessions=True, created_by_thread_id=caller.thread_id)


def _ensure_assistant_workdir(uid: str) -> None:
    """在用户工作区 no-follow 边界下幂等创建专用目录。"""
    ensure_user_workspace(uid)
    workspace = Workspace(uid)
    try:
        workspace.create_authorized_directory("/agents", "assistant", root="/agents")
    except FileExistsError:
        workspace.list_authorized_directory("/agents/assistant", root="/agents/assistant")


def _normalize_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise InvalidInputError("名称长度必须为 1 至 100")
    return name.strip()


def _validate_avatar(avatar: str | None) -> None:
    if not isinstance(avatar, str) or not avatar.strip() or len(avatar) > 255:
        raise InvalidInputError("头像必须为非空字符串，长度不能超过 255")


def _require_enabled() -> None:
    if not personal_assistant_enabled():
        raise ResourceNotFoundError("个人助理功能未启用")
