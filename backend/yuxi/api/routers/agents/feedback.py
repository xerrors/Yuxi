"""当前用户的结果评价与会话反馈读取协议。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.feedback import list_feedback, remove_user_feedback, save_user_feedback
from yuxi.modules.identity.models import User

router = APIRouter(prefix="/agent/threads", tags=["agent"], include_in_schema=False)


class FeedbackRecord(BaseModel):
    """已经提交的评价或待复盘线索。"""

    id: str
    run_id: str
    source: Literal["user", "agent"]
    rating: Literal["like", "dislike"] | None
    reason: str | None
    created_at: str
    updated_at: str


class FeedbackEntry(FeedbackRecord):
    """反馈列表的精简执行关联，不公开消息正文或 manifest。"""

    agent_id: str
    agent_name: str | None
    session_id: str
    turn_id: str
    run_status: str


class FeedbackPage(BaseModel):
    """按时间与 ID 游标分页的反馈记录。"""

    items: list[FeedbackEntry]
    has_more: bool
    last_id: str | None


class FeedbackAgentOption(BaseModel):
    """反馈范围内出现过的智能体，名称在智能体行缺失时仍可展示。"""

    agent_id: str
    name: str


class FeedbackAgentOptions(BaseModel):
    """集中反馈筛选使用的智能体选项。"""

    agents: list[FeedbackAgentOption]


async def require_web_feedback_scope(request: Request, user: User = Depends(get_required_user)) -> ActorScope:
    """反馈只接受产品登录用户，不扩展 Public 或 API Key 能力。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="反馈仅支持 Web 登录用户")
    return ActorScope(uid=str(user.uid), app_id=None, is_superadmin=user.role == "superadmin")


class UserFeedbackInput(BaseModel):
    """替换最终回答的完整当前评价。"""

    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)
    rating: Literal["like", "dislike"]
    reason: str | None = None


@router.put("/{thread_id}/turns/{turn_id}/feedback", summary="保存最终回答的用户评价", response_model=FeedbackRecord)
async def put_feedback(
    thread_id: str,
    turn_id: str,
    payload: UserFeedbackInput,
    scope: ActorScope = Depends(require_web_feedback_scope),
    db: AsyncSession = Depends(get_db),
):
    """只接受自身产品会话的最终结果，提交后返回持久记录。"""
    return await save_user_feedback(db=db, scope=scope, thread_id=thread_id, turn_id=turn_id, **payload.model_dump())


@router.delete("/{thread_id}/turns/{turn_id}/feedback", status_code=204, summary="撤销当前用户评价")
async def delete_feedback(
    thread_id: str,
    turn_id: str,
    run_id: str = Query(min_length=1, max_length=64),
    scope: ActorScope = Depends(require_web_feedback_scope),
    db: AsyncSession = Depends(get_db),
):
    """同一合法目标重复撤销成功，Agent 标记保持不变。"""
    await remove_user_feedback(db=db, scope=scope, thread_id=thread_id, turn_id=turn_id, run_id=run_id)
    return Response(status_code=204)


@router.get("/{thread_id}/feedback", summary="分页或批量恢复会话反馈", response_model=FeedbackPage)
async def get_feedback(
    thread_id: str,
    source: Literal["user", "agent"] | None = None,
    run_ids: list[str] | None = Query(default=None, max_length=100),
    after: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    scope: ActorScope = Depends(require_web_feedback_scope),
    db: AsyncSession = Depends(get_db),
):
    """按完整 ActorScope 读取当前会话，不返回原始对话或 manifest。"""
    return await list_feedback(db=db, scope=scope, thread_id=thread_id, source=source, run_ids=run_ids, after=after, limit=limit)
