"""Public Session 创建、读取与归档入口。"""

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db
from yuxi.api.routers.public_v1.agents.auth import PublicAgentContext, require_public_context
from yuxi.api.routers.public_v1.agents.responses import PUBLIC_ERRORS, SSE_RESPONSE
from yuxi.api.routers.public_v1.agents.schemas import (
    SessionCreate,
    SessionList,
    SessionResponse,
    SessionUpdate,
    input_messages_to_domain,
)
from yuxi.modules.agents.services.inputs import create_thread, thread_id_for_creation
from yuxi.modules.agents.services.threads import (
    archive_thread,
    get_queue_snapshot,
    get_session_resource,
    list_session_page,
    mark_thread_viewed,
    search_threads,
    update_thread,
)

router = APIRouter(dependencies=[Depends(require_public_context)], responses=PUBLIC_ERRORS)


@router.get("/sessions", response_model=SessionList)
async def list_public_threads(
    agent_id: str | None = Query(default=None),
    after: str | None = Query(default=None),
    order: Literal["asc", "desc"] = Query(default="desc"),
    archived: bool = Query(default=False),
    is_pinned: bool | None = Query(default=None, description="按 Yuxi 置顶状态筛选，不改变游标排序。"),
    limit: int = Query(default=50, ge=1, le=100),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """按用户、APP 与稳定创建顺序分页读取会话资源。"""
    snapshots, has_more = await list_session_page(
        db=db,
        scope=context.scope,
        agent_slug=agent_id,
        after=after,
        order=order,
        limit=limit,
        archived=archived,
        is_pinned=is_pinned,
    )
    data = snapshots
    return {
        "object": "list",
        "data": data,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
        "has_more": has_more,
    }


@router.post(
    "/sessions",
    status_code=201,
    summary="创建智能体会话",
    response_model=SessionResponse,
    responses={201: SSE_RESPONSE},
)
async def create_public_thread(
    payload: SessionCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """创建空会话或原子接收首批输入。

    提供 Idempotency-Key，重复相同意图返回同一会话和回执，不重复执行。
    返回 201 证明持久创建或幂等重放。yuxi.receipt 只返回 Input 目标；
    查询 Input 消费归属后读取 Turn 的明确结果。
    stream=true 返回长期 SSE，先发送 agent.session.created；客户端主动关闭订阅。
    """
    if payload.stream and payload.input is None:
        raise HTTPException(status_code=422, detail="空 Thread 不能以流式创建")
    result = await create_thread(
        db=db,
        scope=context.scope,
        agent_slug=payload.agent_id,
        thread_id=thread_id_for_creation(context.scope, idempotency_key),
        idempotency_key=idempotency_key,
        project_id=payload.project_id,
        title=payload.title,
        messages=input_messages_to_domain(payload.input) if payload.input else None,
        attachment_file_ids=payload.yuxi.attachment_file_ids,
        mcps=payload.yuxi.mcps,
        model_spec=payload.agent.model if payload.agent else None,
        tool_approval_mode=payload.tool_approval_mode,
        source="public_api",
        channel="api" if context.api_key else "web",
    )
    response = await get_session_resource(db=db, scope=context.scope, thread_id=result["thread_id"], receipt=result)
    if not payload.stream:
        return response
    from yuxi.api.routers.public_v1.agents.events import public_stream_response

    await db.close()
    return public_stream_response(
        scope=context.scope,
        thread_id=result["thread_id"],
        after_cursor=None,
        initial_event=response,
    )


@router.get("/sessions/search")
async def search_public_threads(
    q: str = Query(..., min_length=1, max_length=200),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """仅在当前 APP 命名空间搜索会话标题与可见历史消息。"""
    result = await search_threads(
        query=q,
        agent_slug=agent_id,
        db=db,
        scope=context.scope,
        limit=limit,
        offset=offset,
    )
    return result


@router.get("/sessions/{session_id}", summary="读取会话状态", response_model=SessionResponse)
async def retrieve_public_thread(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取持久会话与最近一轮工作。

    status=idle 不证明上一轮成功；通过 yuxi.current_turn 定位 Turn，读取整轮状态和最终结果。
    """
    result = await get_session_resource(db=db, scope=context.scope, thread_id=session_id)
    return result


@router.post("/sessions/{session_id}/viewed", response_model=SessionResponse)
async def mark_public_thread_viewed(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """在作用域校验后记录用户已查看的当前执行段。"""
    await mark_thread_viewed(thread_id=session_id, scope=context.scope, db=db)
    return await get_session_resource(db=db, scope=context.scope, thread_id=session_id)


@router.post("/sessions/{session_id}", response_model=SessionResponse)
async def update_public_thread(
    session_id: str,
    payload: SessionUpdate,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """更新会话展示字段和后续输入配置，已接收输入及当前 Turn 保持原配置。"""
    await update_thread(
        db=db,
        scope=context.scope,
        thread_id=session_id,
        title=payload.yuxi.title,
        is_pinned=payload.yuxi.is_pinned,
        tool_approval_mode=payload.yuxi.tool_approval_mode,
        model_spec=payload.agent.model if payload.agent else None,
    )
    return await get_session_resource(db=db, scope=context.scope, thread_id=session_id)


@router.post("/sessions/{session_id}/archive", response_model=SessionResponse)
async def archive_public_thread(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """检查在途工作后归档 Thread，保留历史事实。"""
    await archive_thread(db=db, scope=context.scope, thread_id=session_id)
    return await get_session_resource(db=db, scope=context.scope, thread_id=session_id)


@router.get("/sessions/{session_id}/queue")
async def retrieve_public_queue(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取当前 Thread 的持久 follow-up 队列。"""
    return await get_queue_snapshot(db=db, scope=context.scope, thread_id=session_id)
