from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.agent_invocation_service import (
    create_agent_call_run_view,
    create_agent_eval_run_view,
    get_agent_call_run_result_view,
)
from yuxi.storage.postgres.models_business import User

from server.utils.auth_middleware import get_db, get_required_user

agent_invocation_router = APIRouter(prefix="/agent-invocation", tags=["agent-invocation"])


class AgentCallRunCreate(BaseModel):
    agent_slug: str = Field(..., description="要调用的智能体 slug")
    messages: list[dict[str, Any]] = Field(..., description="消息列表，取最后一条 user 消息作为输入")
    stream: bool = Field(False, description="暂不支持流式，传 true 会返回 422")
    agent_call_meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent Call 元数据；不允许通过 context 覆盖 Agent 运行上下文",
    )
    thread_id: str | None = Field(None, description="可选会话线程 ID，不传则自动创建临时线程")
    request_id: str | None = Field(None, description="可选请求幂等 ID，不传则自动生成")
    model_spec: str | None = Field(None, description="可选模型覆盖")
    async_mode: bool = Field(False, description="是否只创建运行并立即返回 run_id")


class AgentCallRunResultRequest(BaseModel):
    run_id: str = Field(..., description="AgentRun ID")
    agent_slug: str | None = Field(None, description="可选，传入时校验 run 归属")


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


@agent_invocation_router.post("/agent-call/runs")
async def create_agent_call_run(
    payload: AgentCallRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建外部系统 Agent 调用 run，并按 async_mode 决定是否等待最终结果。"""
    return await create_agent_call_run_view(
        agent_slug=payload.agent_slug,
        messages=payload.messages,
        agent_call_meta=payload.agent_call_meta,
        requested_thread_id=payload.thread_id,
        request_id=payload.request_id,
        model_spec=payload.model_spec,
        async_mode=payload.async_mode,
        stream=payload.stream,
        current_user=current_user,
        db=db,
    )


@agent_invocation_router.post("/agent-call/runs/result")
async def get_agent_call_run_result(
    payload: AgentCallRunResultRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取外部 Agent 调用 run 的 OpenAI-compatible 结果结构。"""
    return await get_agent_call_run_result_view(
        run_id=payload.run_id,
        agent_slug=payload.agent_slug,
        current_uid=str(current_user.uid),
        db=db,
    )


@agent_invocation_router.post("/eval/runs")
async def create_agent_eval_run(
    payload: AgentEvalRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """运行一次 CLI/Langfuse Agent 评估样例，并阻塞等待最终输出。"""
    return await create_agent_eval_run_view(
        query=payload.query,
        agent_slug=payload.agent_slug,
        evaluation=payload.evaluation.model_dump(exclude_none=True),
        meta=dict(payload.meta or {}),
        image_content=payload.image_content,
        model_spec=payload.model_spec,
        current_user=current_user,
        db=db,
    )
