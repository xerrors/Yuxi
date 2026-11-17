"""插件提及目录、文件与对话标题搜索路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.mentions import (
    InvalidMentionThreadError,
    MentionThreadNotFoundError,
    list_plugin_mentions,
    search_mentions,
    search_session_mentions,
)
from yuxi.modules.identity.models import User

mention_router = APIRouter(prefix="/mention", tags=["mention"])


class MentionPluginItem(BaseModel):
    """会话中可引用插件的稳定标识与显示信息。"""

    id: str
    name: str
    description: str


@mention_router.get("/plugins", response_model=list[MentionPluginItem])
async def get_mention_plugins(
    request: Request,
    thread_id: str = Query(..., description="当前聊天会话 ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """返回已有 Web 会话启用且当前用户可读取的插件。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="插件引用仅支持 Web 登录用户")
    return await list_plugin_mentions(db=db, scope=ActorScope(uid=current_user.uid, app_id=None), thread_id=thread_id, user=current_user)


class MentionSessionItem(BaseModel):
    """对话引用的身份、标题与活动时间。"""

    thread_id: str
    title: str | None
    updated_at: str | None


@mention_router.get("/sessions", response_model=list[MentionSessionItem])
async def search_mention_sessions(
    request: Request,
    query: str = Query("", max_length=256, description="仅匹配对话标题；为空时返回最近对话"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """搜索当前 Web 用户的对话标题，不返回消息正文。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="对话引用仅支持 Web 登录用户")
    return await search_session_mentions(db=db, scope=ActorScope(uid=current_user.uid, app_id=None), query=query)


class MentionFileItem(BaseModel):
    """提及文件搜索结果条目"""

    name: str
    path: str
    is_dir: bool
    source: str


@mention_router.get("/search", response_model=list[MentionFileItem])
async def search_mention_files(
    thread_id: str | None = Query(None, description="当前聊天会话 ID；为空时仅搜索用户工作区"),
    query: str = Query("", description="模糊搜索关键字"),
    sources: str | None = Query(None, description="搜索来源：workspace,thread；为空时自动选择"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """提及文件模糊搜索：未创建 thread 时只搜索用户 workspace，已有 thread 时可搜索当前对话文件。"""
    try:
        return await search_mentions(
            thread_id=thread_id,
            query=query,
            sources=sources,
            current_user=current_user,
            db=db,
        )
    except MentionThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidMentionThreadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
