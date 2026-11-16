"""智能体定义、后端与专属 Skill 路由。"""

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user, get_superadmin_user
from yuxi.api.uploads import read_upload_with_limit
from yuxi.modules.agents.permissions import is_builtin_agent, user_can_access_agent, user_can_manage_agent
from yuxi.modules.agents.runtime.agent_backends import (
    AgentBackendNotFoundError,
    get_agent_backend,
    list_agent_backend_info,
)
from yuxi.modules.agents.services.definitions import (
    create_agent_definition,
    delete_agent_definition,
    ensure_default_agent_definition,
    get_agent_definition_by_slug,
    get_visible_agent_definition,
    list_visible_agent_definitions,
    serialize_agent_definition,
    set_default_agent_definition,
    update_agent_definition,
)
from yuxi.modules.extensions.skills.bound import (
    create_agent_bound_skill,
    get_agent_bound_skill,
    upload_agent_bound_skill,
)
from yuxi.modules.extensions.skills.package import SkillEditConflict
from yuxi.modules.identity.models import User

agent_router = APIRouter(prefix="/agent", tags=["agent"])


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["private", "shared"] = "private"
    name: str
    backend_id: str = "ChatbotAgent"
    slug: str | None = None
    description: str | None = None
    icon: str | None = None
    pics: list[str] | None = None
    config_json: dict | None = None
    share_config: dict | None = None
    set_default: bool = False


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["shared"] | None = None
    name: str | None = None
    backend_id: str | None = None
    description: str | None = None
    icon: str | None = None
    pics: list[str] | None = None
    config_json: dict | None = None
    share_config: dict | None = None


@agent_router.get("/backends")
async def list_agent_backends(current_user: User = Depends(get_required_user)):
    infos = await list_agent_backend_info()
    return {
        "backends": [
            {
                **info,
                "type": "agent_backend",
                "can_create": True,
            }
            for info in infos
        ]
    }


@agent_router.get("/backends/{backend_id}")
async def get_agent_backend_detail(
    backend_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        backend = get_agent_backend(backend_id)
    except AgentBackendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    info = await backend.get_info(user_role=current_user.role, db=db, user=current_user)
    return {**info, "backend_id": backend_id, "type": "agent_backend"}


@agent_router.get("")
async def list_agents(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    items = await list_visible_agent_definitions(db, user=current_user)
    backend_info_cache: dict[tuple[str, bool, str], dict] = {}
    agents = [await _serialize_agent(db, item, current_user, backend_info_cache=backend_info_cache) for item in items]
    return {"agents": agents}


@agent_router.get("/default")
async def get_default_agent(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    item = await ensure_default_agent_definition(db)
    if not item or not user_can_access_agent(current_user, item):
        raise HTTPException(status_code=404, detail="默认智能体不可访问")
    return {"agent": await _serialize_agent(db, item, current_user, include_configurable_items=True)}


@agent_router.post("")
async def create_agent(payload: AgentCreate, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """创建定义与 Context，保留 JSON 创建契约。"""
    return await _create_agent_response(db, current_user, payload)


@agent_router.post("/with-skill")
async def create_agent_with_skill(
    agent: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """附带单 Skill ZIP 的创建入口，共用 JSON 模型和创建事务。"""
    try:
        payload = AgentCreate.model_validate_json(agent)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="智能体创建参数无效") from exc
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="请上传 Skill ZIP 文件")
    try:
        file_bytes = await read_upload_with_limit(file, max_size_bytes=10 * 1024 * 1024, too_large_message="ZIP 文件不能超过 10 MiB")
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return await _create_agent_response(db, current_user, payload, skill_upload=(file.filename, file_bytes))


@agent_router.get("/{agent_id}")
async def get_agent(agent_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    agent_slug = agent_id  # 兼容既有路径参数名；这里实际是 Agent.slug。
    item = await get_visible_agent_definition(db, user=current_user, slug=agent_slug)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return {"agent": await _serialize_agent(db, item, current_user, include_configurable_items=True)}


@agent_router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """更新可管理的智能体定义。"""
    agent_slug = agent_id  # 兼容既有路径参数名；这里实际是 Agent.slug。
    item = await get_visible_agent_definition(db, user=current_user, slug=agent_slug)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if not user_can_manage_agent(current_user, item):
        raise HTTPException(status_code=403, detail="不能编辑非自己创建的智能体")

    try:
        updated = await update_agent_definition(
            db,
            item,
            name=payload.name,
            backend_id=payload.backend_id,
            description=payload.description,
            icon=payload.icon,
            pics=payload.pics,
            config_json=payload.config_json,
            share_config=payload.share_config,
            visibility=payload.visibility,
            updated_by=str(current_user.uid),
            updater=current_user,
            fields_set=payload.model_fields_set,
        )
    except AgentBackendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": await _serialize_agent(db, updated, current_user, include_configurable_items=True)}


@agent_router.delete("/{agent_id}")
async def delete_agent(agent_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """删除自有非内置智能体。"""
    agent_slug = agent_id  # 兼容既有路径参数名；这里实际是 Agent.slug。
    item = await get_visible_agent_definition(db, user=current_user, slug=agent_slug)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if not user_can_manage_agent(current_user, item):
        raise HTTPException(status_code=403, detail="不能删除非自己创建的智能体")
    if is_builtin_agent(item):
        raise HTTPException(status_code=409, detail="内置智能体不能删除")
    try:
        await delete_agent_definition(db, agent=item, user=current_user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True}


@agent_router.post("/{agent_id}/set_default")
async def set_agent_default(
    agent_id: str,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """设置平台默认智能体（超级管理员）。"""
    agent_slug = agent_id  # 兼容既有路径参数名；这里实际是 Agent.slug。
    item = await get_agent_definition_by_slug(db, slug=agent_slug)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    try:
        updated = await set_default_agent_definition(db, agent=item, operator=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": await _serialize_agent(db, updated, current_user, include_configurable_items=True)}


@agent_router.get("/{agent_id}/self-skill")
async def get_self_skill(agent_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """读取专属 Skill 与整包修订。"""
    try:
        return await get_agent_bound_skill(db, agent_slug=agent_id, operator=current_user)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@agent_router.post("/{agent_id}/self-skill")
async def create_self_skill(agent_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """按 Agent 管理权限幂等创建操作指南。"""
    try:
        return await create_agent_bound_skill(db, agent_slug=agent_id, operator=current_user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@agent_router.post("/{agent_id}/self-skill/upload")
async def upload_self_skill(
    agent_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """首次导入专属 Skill，已有绑定返回冲突。"""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="请上传 Skill ZIP 文件")
    file_bytes = await file.read(10 * 1024 * 1024 + 1)
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="ZIP 文件不能超过 10 MiB")
    try:
        return await upload_agent_bound_skill(
            db,
            agent_slug=agent_id,
            filename=file.filename,
            file_bytes=file_bytes,
            operator=current_user,
        )
    except SkillEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _serialize_agent(
    db,
    item,
    user: User,
    *,
    include_configurable_items: bool = False,
    backend_info_cache: dict[tuple[str, bool, str], dict] | None = None,
) -> dict:
    """序列化智能体定义，后端缺失时返回 404。"""
    try:
        data = await serialize_agent_definition(
            db,
            item,
            user=user,
            include_configurable_items=include_configurable_items,
            backend_info_cache=backend_info_cache,
        )
    except AgentBackendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return data


async def _create_agent_response(db, user, payload, *, skill_upload=None):
    """将同一创建用例的领域错误与结果映射到 HTTP。"""
    try:
        item = await create_agent_definition(db, operator=user, skill_upload=skill_upload, **payload.model_dump())
    except AgentBackendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        # YAML parser 的后续行含文件片段；只展示失败原因，不回显内容。
        detail = {"code": "agent_creation_invalid", "message": str(exc).splitlines()[0]}
        raise HTTPException(status_code=422, detail=detail) from exc
    return {"agent": await _serialize_agent(db, item, user, include_configurable_items=True)}
