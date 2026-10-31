"""个人助理 Web 接口。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.modules.agents.services.personal_assistants import create_personal_assistant, personal_assistant_view, update_personal_assistant
from yuxi.modules.identity.models import User

router = APIRouter(prefix="/agents/personal", tags=["personal-assistant"])


class PersonalCreate(BaseModel):
    """创建个人助理的可编辑字段。"""

    model_config = ConfigDict(extra="forbid")
    name: str
    avatar: str


class PersonalContextUpdate(BaseModel):
    """助理编辑表单开放的 Context 字段。"""

    model_config = ConfigDict(extra="forbid")
    system_prompt: str


class PersonalConfigUpdate(BaseModel):
    """沿用普通 Agent 的配置结构。"""

    model_config = ConfigDict(extra="forbid")
    context: PersonalContextUpdate


class PersonalUpdate(BaseModel):
    """个人助理设置补丁。"""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    icon: str | None = None
    config_json: PersonalConfigUpdate | None = None


@router.get("")
async def get_personal(user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """读取自己的助理及当前模型状态。"""
    return await personal_assistant_view(db, user.uid)


@router.post("")
async def create_personal(payload: PersonalCreate, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """创建或恢复自己的唯一助理。"""
    return await create_personal_assistant(db, user=user, name=payload.name, avatar=payload.avatar)


@router.patch("")
async def update_personal(payload: PersonalUpdate, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """修改个人助理的展示和个性化设置。"""
    return await update_personal_assistant(db, user=user, updates=payload.model_dump(exclude_unset=True))


@router.get("/sessions")
async def get_personal_sessions(user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """读取自己的委派会话摘要，完整历史继续使用普通会话接口。"""
    from yuxi.modules.agents.services.personal_assistants import personal_task_summary

    return await personal_task_summary(db, uid=user.uid)
