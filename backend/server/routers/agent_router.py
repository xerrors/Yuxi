from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import filter_config_by_role
from yuxi.repositories.agent_repository import (
    AgentRepository,
    is_builtin_agent,
    user_can_access_agent,
    user_can_manage_agent,
)
from yuxi.services.agent_run_service import (
    cancel_agent_run_view,
    create_agent_run_view,
    get_active_run_by_thread,
    get_agent_run_result_view,
    get_agent_run_view,
    stream_agent_run_events,
)
from yuxi.services.agent_eval_run_service import run_agent_eval
from yuxi.storage.postgres.models_business import User

agent_router = APIRouter(prefix="/agent", tags=["agent"])


class AgentCreate(BaseModel):
    name: str
    backend_id: str = "ChatbotAgent"
    slug: str | None = None
    description: str | None = None
    icon: str | None = None
    pics: list[str] | None = None
    config_json: dict | None = None
    share_config: dict | None = None
    is_subagent: bool | None = None
    set_default: bool = False


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    pics: list[str] | None = None
    config_json: dict | None = None
    share_config: dict | None = None
    is_subagent: bool | None = None


class AgentRunCreate(BaseModel):
    query: str | None = Field(None, description="用户输入的问题")
    agent_id: str = Field(..., description="智能体 ID")
    thread_id: str = Field(..., description="会话线程 ID")
    meta: dict = Field(default_factory=dict, description="可选，请求追踪信息，例如 request_id")
    image_content: str | None = Field(None, description="可选，base64 图片内容")
    model_spec: str | None = Field(None, description="可选，对话级模型覆盖，优先级高于智能体配置")
    resume: Any | None = Field(None, description="可选，恢复 interrupted run 的输入")
    parent_run_id: str | None = Field(None, description="可选，被恢复的 run ID")
    resume_request_id: str | None = Field(None, description="可选，resume 幂等键")


class AgentEvaluationContext(BaseModel):
    dataset_name: str | None = Field(None, description="Langfuse dataset 名称")
    dataset_item_id: str | None = Field(None, description="Langfuse dataset item ID")
    experiment_name: str | None = Field(None, description="Langfuse experiment/run 名称")


class AgentEvalRunCreate(BaseModel):
    query: str = Field(..., description="评估样例输入")
    agent_slug: str = Field(..., description="要运行的智能体 slug")
    evaluation: AgentEvaluationContext = Field(default_factory=AgentEvaluationContext, description="评估上下文")
    meta: dict = Field(default_factory=dict, description="可选，请求追踪信息，例如 request_id、attachment_file_ids")
    image_content: str | None = Field(None, description="可选，base64 图片内容")
    model_spec: str | None = Field(None, description="可选，对话级模型覆盖，优先级高于智能体配置")


def _backend_info(info: dict) -> dict:
    data = dict(info)
    data["backend_id"] = data.pop("id", None)
    data["type"] = "agent_backend"
    return data


def _filter_agent_config_json(backend_id: str, config_json: dict | None, role: str | None) -> dict:
    backend = agent_manager.get_agent(backend_id)
    context_schema = backend.context_schema if backend else None
    return filter_config_by_role(config_json or {}, role, context_schema=context_schema)


async def _serialize_agent(
    repo: AgentRepository,
    item,
    user: User,
    *,
    include_configurable_items: bool = False,
    backend_info_cache: dict[tuple[str, bool, str], dict] | None = None,
) -> dict:
    data = await repo.serialize(
        item,
        user=user,
        include_configurable_items=include_configurable_items,
        backend_info_cache=backend_info_cache,
    )
    data["config_json"] = _filter_agent_config_json(item.backend_id, data.get("config_json"), user.role)
    return data


@agent_router.get("/backends")
async def list_agent_backends(current_user: User = Depends(get_required_user)):
    infos = await agent_manager.get_agents_info(include_configurable_items=False)
    return {"backends": [_backend_info(info) for info in infos]}


@agent_router.get("/backends/{backend_id}")
async def get_agent_backend(
    backend_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    backend = agent_manager.get_agent(backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail=f"智能体后端 {backend_id} 不存在")
    return _backend_info(await backend.get_info(user_role=current_user.role, db=db, user=current_user))


@agent_router.get("")
async def list_agents(
    include_subagents: bool = Query(False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    repo = AgentRepository(db)
    await repo.ensure_default_agent()
    items = await repo.list_visible(user=current_user, include_subagents=include_subagents)
    backend_info_cache: dict[tuple[str, bool, str], dict] = {}
    agents = [await _serialize_agent(repo, item, current_user, backend_info_cache=backend_info_cache) for item in items]
    return {"agents": agents}


@agent_router.get("/default")
async def get_default_agent(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    item = await repo.ensure_default_agent()
    if not item or not user_can_access_agent(current_user, item):
        raise HTTPException(status_code=404, detail="默认智能体不可访问")
    return {"agent": await _serialize_agent(repo, item, current_user, include_configurable_items=True)}


@agent_router.post("")
async def create_agent(
    payload: AgentCreate, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    if not agent_manager.get_agent(payload.backend_id):
        raise HTTPException(status_code=404, detail=f"智能体后端 {payload.backend_id} 不存在")
    if payload.set_default:
        raise HTTPException(status_code=422, detail="默认智能体已固定为内置智能助手")

    repo = AgentRepository(db)
    try:
        item = await repo.create(
            name=payload.name,
            slug=payload.slug,
            backend_id=payload.backend_id,
            description=payload.description,
            icon=payload.icon,
            pics=payload.pics,
            config_json=_filter_agent_config_json(payload.backend_id, payload.config_json, current_user.role),
            share_config=payload.share_config,
            is_default=payload.set_default,
            is_subagent=payload.is_subagent,
            created_by=str(current_user.uid),
            creator=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": await _serialize_agent(repo, item, current_user, include_configurable_items=True)}


@agent_router.get("/{agent_id}")
async def get_agent(agent_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    repo = AgentRepository(db)
    item = await repo.get_visible_by_slug(slug=agent_id, user=current_user, include_subagents=True)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return {"agent": await _serialize_agent(repo, item, current_user, include_configurable_items=True)}


@agent_router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    repo = AgentRepository(db)
    item = await repo.get_visible_by_slug(slug=agent_id, user=current_user, include_subagents=True)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if not user_can_manage_agent(current_user, item):
        raise HTTPException(status_code=403, detail="不能编辑非自己创建的智能体")

    try:
        fields_set = payload.model_fields_set
        if "description" in fields_set and payload.description is None:
            item.description = None
        if "icon" in fields_set and payload.icon is None:
            item.icon = None

        updated = await repo.update(
            item,
            name=payload.name,
            description=payload.description,
            icon=payload.icon,
            pics=payload.pics,
            config_json=_filter_agent_config_json(item.backend_id, payload.config_json, current_user.role)
            if payload.config_json is not None
            else None,
            share_config=payload.share_config,
            is_subagent=payload.is_subagent,
            updated_by=str(current_user.uid),
            updater=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": await _serialize_agent(repo, updated, current_user, include_configurable_items=True)}


@agent_router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    repo = AgentRepository(db)
    item = await repo.get_visible_by_slug(slug=agent_id, user=current_user, include_subagents=True)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if not user_can_manage_agent(current_user, item):
        raise HTTPException(status_code=403, detail="不能删除非自己创建的智能体")
    if is_builtin_agent(item):
        raise HTTPException(status_code=409, detail="内置智能体不能删除")
    await repo.delete(agent=item)
    return {"success": True}


@agent_router.post("/{agent_id}/set_default")
async def set_agent_default(
    agent_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    repo = AgentRepository(db)
    item = await repo.get_by_slug(agent_id)
    if not item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    try:
        updated = await repo.set_default(agent=item, updated_by=str(current_user.uid))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent": await _serialize_agent(repo, updated, current_user, include_configurable_items=True)}


@agent_router.post("/runs")
async def create_agent_run(
    payload: AgentRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_agent_run_view(
        query=payload.query,
        agent_id=payload.agent_id,
        thread_id=payload.thread_id,
        meta=dict(payload.meta or {}),
        image_content=payload.image_content,
        model_spec=payload.model_spec,
        current_uid=str(current_user.uid),
        db=db,
        resume=payload.resume,
        parent_run_id=payload.parent_run_id,
        resume_request_id=payload.resume_request_id,
    )


@agent_router.post("/eval/runs")
async def create_agent_eval_run(
    payload: AgentEvalRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await run_agent_eval(
        query=payload.query,
        agent_slug=payload.agent_slug,
        evaluation=payload.evaluation.model_dump(exclude_none=True),
        meta=dict(payload.meta or {}),
        image_content=payload.image_content,
        model_spec=payload.model_spec,
        current_user=current_user,
        db=db,
    )


@agent_router.get("/runs/{run_id}")
async def get_agent_run(
    run_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    return await get_agent_run_view(run_id=run_id, current_uid=str(current_user.uid), db=db)


@agent_router.get("/runs/{run_id}/result")
async def get_agent_run_result(
    run_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    return await get_agent_run_result_view(run_id=run_id, current_uid=str(current_user.uid), db=db)


@agent_router.post("/runs/{run_id}/cancel")
async def cancel_agent_run(
    run_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    return await cancel_agent_run_view(run_id=run_id, current_uid=str(current_user.uid), db=db)


@agent_router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after_seq: str = "0-0",
    verbose: bool = Query(default=True, description="是否返回完整事件载荷；false 时仅返回 UI/客户端消费所需字段"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_required_user),
):
    cursor = last_event_id or after_seq
    return StreamingResponse(
        stream_agent_run_events(run_id=run_id, after_seq=cursor, current_uid=str(current_user.uid), verbose=verbose),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@agent_router.get("/thread/{thread_id}/active_run")
async def get_thread_active_run(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_active_run_by_thread(thread_id=thread_id, current_uid=str(current_user.uid), db=db)
