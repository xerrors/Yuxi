"""沙盒路由

提供沙盒预创建、释放、销毁的 API 端点。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.backends.sandbox_provider import get_sandbox_provider
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.conversation_service import require_user_conversation
from yuxi.storage.postgres.models_business import User
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.utils.logging_config import logger


class SandboxPrepareRequest(BaseModel):
    thread_id: str


class SandboxPrepareResponse(BaseModel):
    sandbox_key: str
    sandbox_url: str | None = None
    status: str


class SandboxReleaseRequest(BaseModel):
    thread_id: str


class SandboxDestroyRequest(BaseModel):
    thread_id: str


class SandboxStatusResponse(BaseModel):
    thread_id: str
    sandbox_key: str
    is_active: bool
    sandbox_url: str | None = None


sandbox_router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@sandbox_router.post("/prepare", response_model=SandboxPrepareResponse)
async def prepare_sandbox(
    request: SandboxPrepareRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """预创建沙盒并复制附件

    触发时机：用户打开对话时（前端主动调用或 GET /chat/agent/{agent_id}/state）
    """
    user_id = str(current_user.id)
    thread_id = request.thread_id

    try:
        conv_repo = ConversationRepository(db)
        await require_user_conversation(conv_repo, thread_id, user_id)

        provider = get_sandbox_provider()

        # 获取或创建沙盒
        sandbox_backend = await asyncio.to_thread(provider.acquire, thread_id)

        return SandboxPrepareResponse(
            sandbox_key=sandbox_backend.id,
            sandbox_url=None,  # 内部使用，暂不暴露
            status="ready",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare sandbox for user {user_id}, thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to prepare sandbox: {str(e)}")


@sandbox_router.post("/release", response_model=dict)
async def release_sandbox(
    request: SandboxReleaseRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """释放沙盒（标记为空闲，可被热池复用）"""
    user_id = str(current_user.id)
    thread_id = request.thread_id

    try:
        conv_repo = ConversationRepository(db)
        await require_user_conversation(conv_repo, thread_id, user_id)

        provider = get_sandbox_provider()
        await asyncio.to_thread(provider.release, thread_id)
        return {"status": "released"}

    except Exception as e:
        logger.error(f"Failed to release sandbox for user {user_id}, thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to release sandbox: {str(e)}")


@sandbox_router.post("/destroy", response_model=dict)
async def destroy_sandbox(
    request: SandboxDestroyRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """销毁沙盒（停止容器）"""
    user_id = str(current_user.id)
    thread_id = request.thread_id

    try:
        conv_repo = ConversationRepository(db)
        await require_user_conversation(conv_repo, thread_id, user_id)

        provider = get_sandbox_provider()
        await asyncio.to_thread(provider.destroy, thread_id)
        return {"status": "destroyed"}

    except Exception as e:
        logger.error(f"Failed to destroy sandbox for user {user_id}, thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to destroy sandbox: {str(e)}")


@sandbox_router.get("/status", response_model=SandboxStatusResponse)
async def get_sandbox_status(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取沙盒状态"""
    user_id = str(current_user.id)

    try:
        conv_repo = ConversationRepository(db)
        await require_user_conversation(conv_repo, thread_id, user_id)

        provider = get_sandbox_provider()
        sandbox_key = provider._deterministic_sandbox_id(thread_id)

        with provider._lock:
            is_active = sandbox_key in provider._sandboxes
            info = provider._sandbox_infos.get(sandbox_key)
            if info is None:
                warm_info, _ = provider._warm_pool.get(sandbox_key, (None, None))
                info = warm_info

        return SandboxStatusResponse(
            thread_id=thread_id,
            sandbox_key=sandbox_key,
            is_active=is_active,
            sandbox_url=info.sandbox_url if info else None,
        )

    except Exception as e:
        logger.error(f"Failed to get sandbox status for user {user_id}, thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get sandbox status: {str(e)}")
