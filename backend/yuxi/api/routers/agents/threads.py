"""产品 Web 的对话创建与工作目录可用性。"""

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, require_product_user
from yuxi.api.routers.public_v1.agents.schemas import SessionResponse
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.inputs import create_thread, thread_id_for_creation
from yuxi.modules.agents.services.threads import get_session_resource
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.services.bindings import resolve_authorized_workdir

router = APIRouter(prefix="/agent/threads", tags=["agent"], include_in_schema=False)


class WebThreadCreate(BaseModel):
    """创建空对话时冻结可选的工作目录命名文本。"""

    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    project_id: str | None = None
    workdir_naming_text: str | None = Field(default=None, max_length=32768)
    tool_approval_mode: str | None = None


@router.post("", status_code=201, response_model=SessionResponse)
async def create_web_thread(
    payload: WebThreadCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(require_product_user),
    db: AsyncSession = Depends(get_db),
):
    """幂等创建 Web 空对话，后续消息由原输入入口接收。"""
    scope = ActorScope(uid=str(user.uid), app_id=None)
    result = await create_thread(
        db=db,
        scope=scope,
        agent_slug=payload.agent_id,
        thread_id=thread_id_for_creation(scope, idempotency_key),
        idempotency_key=idempotency_key,
        project_id=payload.project_id,
        title=payload.title,
        naming_text=payload.workdir_naming_text,
        tool_approval_mode=payload.tool_approval_mode,
        source="public_api",
        channel="web",
    )
    return await get_session_resource(db=db, scope=scope, thread_id=result["thread_id"], receipt=result)


@router.get("/{thread_id}/workdir", status_code=204)
async def check_thread_workdir(thread_id: str, user: User = Depends(require_product_user), db: AsyncSession = Depends(get_db)):
    """为会话输入区检查当前用户的绑定目录。"""
    await resolve_authorized_workdir(thread_id=thread_id, uid=str(user.uid), db=db)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
