"""用户数据访问层 - Repository"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC
from datetime import datetime as dt
from typing import Annotated, Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import APIKey, ScheduledAgentJob, User
from yuxi.utils.hash_utils import hash_id


def _utc_now() -> dt:
    # 使用 naive datetime 以匹配 PostgreSQL TIMESTAMP WITHOUT TIME ZONE 列
    return dt.now(UTC).replace(tzinfo=None)


class UserRepository:
    """用户数据访问层"""

    def __init__(self, db_session: AsyncSession | None = None):
        self.db_session = db_session

    async def get_or_create_public_end_user(self, *, owner: User, app_id: str, end_user_id: str) -> User:
        """按 Key 用户、APP 和外部 ID 并发安全地解析终端用户。"""
        if self.db_session is None:
            raise RuntimeError("终端用户身份解析需要请求事务")

        query = select(User).where(
            User.owner_user_id == owner.id,
            User.app_id == app_id,
            User.end_user_id == end_user_id,
        )
        existing = await self.db_session.scalar(query)
        if existing is not None:
            return existing

        identity = json.dumps([owner.id, app_id, end_user_id], ensure_ascii=False, separators=(",", ":"))
        uid = hash_id("endusr_", identity, length=64)
        await self.db_session.execute(
            pg_insert(User)
            .values(
                username=uid,
                uid=uid,
                password_hash="!disabled",
                role="user",
                user_kind="end_user",
                owner_user_id=owner.id,
                app_id=app_id,
                end_user_id=end_user_id,
            )
            .on_conflict_do_nothing()
        )
        result = await self.db_session.scalar(query)
        if result is None:
            raise RuntimeError("终端用户 UID 与既有用户冲突")
        return result

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """复用请求会话，未注入时创建独立事务会话。"""
        if self.db_session is not None:
            yield self.db_session
            return
        async with pg_manager.get_async_session_context() as session:
            yield session

    async def get_by_id(self, id: int) -> User | None:
        """根据 ID 获取用户"""
        async with self._session() as session:
            return await self.get_by_id_with_db(session, id)

    async def is_first_run(self) -> bool:
        """检查系统是否尚未创建用户。"""
        async with self._session() as session:
            result = await session.execute(select(func.count(User.id)))
            return (result.scalar() or 0) == 0

    async def get_active_by_id(self, id: int, *, for_update: bool = False) -> User | None:
        """根据 ID 获取未删除用户。"""
        async with self._session() as session:
            query = select(User).where(User.id == id, User.is_deleted == 0)
            if for_update:
                query = query.with_for_update()
            result = await session.execute(query)
            return result.scalar_one_or_none()

    @staticmethod
    async def _revoke_api_keys(session: AsyncSession, user_id: int, revoked_at: dt) -> None:
        """撤销用户的全部 API Key，并保留已有撤销时间。"""

        api_key_result = await session.execute(select(APIKey).where(APIKey.user_id == user_id))
        for api_key in api_key_result.scalars().all():
            api_key.is_enabled = False
            if api_key.revoked_at is None:
                api_key.revoked_at = revoked_at

    @staticmethod
    async def _delete_scheduled_jobs(session: AsyncSession, uid: str) -> None:
        """账号删除时移除任务定义，数据库级联清理调度历史。"""

        await session.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.uid == str(uid)))

    async def get_by_id_with_db(self, db: AsyncSession, id: int) -> User | None:
        """使用指定的 db 根据 ID 获取用户"""
        result = await db.execute(select(User).where(User.id == id))
        return result.scalar_one_or_none()

    async def get_by_uid(self, uid: str) -> User | None:
        """根据 uid 获取用户"""
        async with self._session() as session:
            return await self.get_by_uid_with_db(session, uid)

    async def get_by_uid_with_db(self, db: AsyncSession, uid: str) -> User | None:
        """使用指定的 db 获取用户"""
        result = await db.execute(select(User).where(User.uid == uid))
        return result.scalar_one_or_none()

    async def list_by_uids(self, uids: list[str]) -> list[User]:
        """批量获取指定 uid 的用户。"""
        normalized_uids = sorted({str(uid).strip() for uid in uids if str(uid).strip()})
        if not normalized_uids:
            return []

        async with self._session() as session:
            result = await session.execute(select(User).where(User.uid.in_(normalized_uids)))
            return list(result.scalars().all())

    async def get_by_phone(self, phone: str) -> User | None:
        """根据手机号获取用户"""
        async with self._session() as session:
            result = await session.execute(select(User).where(User.phone_number == phone))
            return result.scalar_one_or_none()

    async def get_by_login_identifier(self, identifier: str) -> User | None:
        """按 uid 优先、手机号兜底查找登录用户。"""
        async with self._session() as session:
            result = await session.execute(select(User).where(User.uid == identifier))
            user = result.scalar_one_or_none()
            if user is not None:
                return user
            result = await session.execute(select(User).where(User.phone_number == identifier))
            return result.scalar_one_or_none()

    async def get_by_username(self, username: str, exclude_user_id: int | None = None) -> User | None:
        """按用户名查找用户，可排除指定用户。"""
        async with self._session() as session:
            query = select(User).where(User.username == username)
            if exclude_user_id is not None:
                query = query.where(User.id != exclude_user_id)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_by_phone_excluding(self, phone: str, exclude_user_id: int) -> User | None:
        """按手机号查找除指定用户外的用户。"""
        async with self._session() as session:
            result = await session.execute(select(User).where(User.phone_number == phone, User.id != exclude_user_id))
            return result.scalar_one_or_none()

    async def list_users(
        self, skip: int = 0, limit: int = 100, department_id: int | None = None, role: str | None = None
    ) -> list[User]:
        """获取用户列表"""
        async with self._session() as session:
            query = select(User).where(User.is_deleted == 0)
            if department_id is not None:
                query = query.where(User.department_id == department_id)
            if role is not None:
                query = query.where(User.role == role)
            query = query.order_by(User.id.asc()).offset(skip).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def list_with_department(
        self, skip: int = 0, limit: int = 100, department_id: int | None = None, role: str | None = None
    ) -> Annotated[list[tuple[User, str | None]], "用户列表，包含部门名称"]:
        """获取用户列表，包含部门名称"""
        async with self._session() as session:
            from yuxi.storage.postgres.models_business import Department

            query = (
                select(User, Department.name.label("department_name"))
                .outerjoin(Department, User.department_id == Department.id)
                .where(User.is_deleted == 0)
            )
            if department_id is not None:
                query = query.where(User.department_id == department_id)
            if role is not None:
                query = query.where(User.role == role)
            query = query.order_by(User.id.asc()).offset(skip).limit(limit)
            result = await session.execute(query)
            return list(result.all())

    async def list_page_with_department(
        self,
        *,
        offset: int,
        limit: int,
        department_id: int | None = None,
        role: str | None = None,
        search: str | None = None,
    ) -> tuple[list[tuple[User, str | None]], int]:
        """分页查询有效用户，并返回过滤后的总数。"""
        async with self._session() as session:
            from yuxi.storage.postgres.models_business import Department

            filters = [User.is_deleted == 0]
            if department_id is not None:
                filters.append(User.department_id == department_id)
            if role is not None:
                filters.append(User.role == role)
            if search:
                filters.append(
                    or_(
                        User.username.icontains(search, autoescape=True),
                        User.uid.icontains(search, autoescape=True),
                        User.phone_number.icontains(search, autoescape=True),
                    )
                )

            total_result = await session.execute(select(func.count(User.id)).where(*filters))
            page_result = await session.execute(
                select(User, Department.name.label("department_name"))
                .outerjoin(Department, User.department_id == Department.id)
                .where(*filters)
                .order_by(User.id.asc())
                .offset(offset)
                .limit(limit)
            )
            return list(page_result.all()), total_result.scalar() or 0

    async def create(self, data: dict[str, Any]) -> User:
        """创建用户"""
        async with self._session() as session:
            user = User(**data)
            session.add(user)
            await session.flush()
            await session.refresh(user)
        return user

    async def save(self, user: User, *, refresh: bool = False) -> User:
        """flush 用户实体的当前变更，事务提交由用例 owner 负责。"""
        async with self._session() as session:
            await session.flush()
            if refresh:
                await session.refresh(user)
            return user

    async def update(self, id: int, data: dict[str, Any]) -> User | None:
        """更新用户"""
        async with self._session() as session:
            result = await session.execute(select(User).where(User.id == id, User.is_deleted == 0))
            user = result.scalar_one_or_none()
            if user is None:
                return None
            for key, value in data.items():
                if key != "id":
                    setattr(user, key, value)
            await session.flush()
        return user

    async def soft_delete(self, id: int, username: str | None = None, phone_number: str | None = None) -> bool:
        """软删除用户"""
        async with self._session() as session:
            result = await session.execute(select(User).where(User.id == id, User.is_deleted == 0).with_for_update())
            user = result.scalar_one_or_none()
            if user is None:
                return False
            user.is_deleted = 1

            user.deleted_at = _utc_now()
            if username:
                import hashlib

                hash_suffix = hashlib.sha256(user.uid.encode()).hexdigest()[:4]
                user.username = f"已注销用户-{hash_suffix}"
            if phone_number:
                user.phone_number = None
            await self._revoke_api_keys(session, user.id, user.deleted_at)
            await self._delete_scheduled_jobs(session, user.uid)
            await session.flush()
        return True

    async def delete_for_admin(self, user: User) -> None:
        """软删除用户并在同一事务中不可恢复地撤销其 API Key。"""
        async with self._session() as session:
            user.is_deleted = 1
            user.deleted_at = _utc_now()
            user.username = f"已注销用户-{user.id}"
            user.phone_number = None
            user.password_hash = "DELETED"
            user.avatar = None
            await self._revoke_api_keys(session, user.id, user.deleted_at)
            await self._delete_scheduled_jobs(session, user.uid)
            await session.flush()

    async def exists_by_uid(self, uid: str) -> bool:
        """检查 uid 是否存在"""
        async with self._session() as session:
            result = await session.execute(select(User.id).where(User.uid == uid))
            return result.scalar_one_or_none() is not None

    async def exists_by_phone(self, phone: str) -> bool:
        """检查手机号是否存在"""
        async with self._session() as session:
            result = await session.execute(select(User.id).where(User.phone_number == phone))
            return result.scalar_one_or_none() is not None

    async def count(self, department_id: int | None = None) -> int:
        """统计用户数量"""
        async with self._session() as session:
            query = select(func.count(User.id)).where(User.is_deleted == 0)
            if department_id is not None:
                query = query.where(User.department_id == department_id)
            result = await session.execute(query)
            return result.scalar() or 0

    async def get_all_uids(self) -> list[str]:
        """获取所有 uid"""
        async with self._session() as session:
            result = await session.execute(select(User.uid))
            return [uid for (uid,) in result.all()]

    async def get_admin_count_in_department(self, department_id: int, exclude_user_id: int | None = None) -> int:
        """统计部门中管理员数量"""
        async with self._session() as session:
            query = select(func.count(User.id)).where(
                User.department_id == department_id, User.role == "admin", User.is_deleted == 0
            )
            if exclude_user_id is not None:
                query = query.where(User.id != exclude_user_id)
            result = await session.execute(query)
            return result.scalar() or 0
