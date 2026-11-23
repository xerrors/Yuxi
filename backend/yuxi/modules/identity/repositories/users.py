"""用户数据访问层 - Repository"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime as dt
from typing import Annotated, Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.identity.models import IDENTITY_CHANGE_LOCK_KEY, APIKey, User
from yuxi.shared.datetime import utc_now
from yuxi.shared.hashing import hash_id


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
                query = query.with_for_update().execution_options(populate_existing=True)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def lock_active_human(self, uid: str) -> User | None:
        """在资源写入事务内刷新并共享锁定当前操作人身份。"""
        if self.db_session is None:
            raise RuntimeError("身份锁需要资源写入事务")
        return await self.db_session.scalar(
            select(User)
            .where(User.uid == uid, User.is_deleted == 0, User.user_kind == "human")
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

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
        result = await db.execute(select(User).where(User.uid == uid).execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def list_by_uids(self, uids: list[str]) -> list[User]:
        """批量获取指定 uid 的用户。"""
        normalized_uids = sorted({str(uid).strip() for uid in uids if str(uid).strip()})
        if not normalized_uids:
            return []

        async with self._session() as session:
            result = await session.execute(select(User).where(User.uid.in_(normalized_uids)))
            return list(result.scalars().all())

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

    async def list_with_department(
        self, skip: int = 0, limit: int = 100, department_id: int | None = None, role: str | None = None
    ) -> Annotated[list[tuple[User, str | None]], "用户列表，包含部门名称"]:
        """获取有效系统用户及部门名称。"""
        async with self._session() as session:
            from yuxi.modules.identity.models import Department

            query = (
                select(User, Department.name.label("department_name"))
                .outerjoin(Department, User.department_id == Department.id)
                .where(User.is_deleted == 0, User.user_kind == "human")
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
        department_ids: list[int] | None = None,
        role: str | None = None,
        roles: list[str] | None = None,
        search: str | None = None,
        user_kind: str | None = "human",
    ) -> tuple[list[tuple[User, str | None]], int]:
        """分页查询有效用户，并返回过滤后的总数。"""
        async with self._session() as session:
            from yuxi.modules.identity.models import Department

            filters = [User.is_deleted == 0]
            if user_kind is not None:
                filters.append(User.user_kind == user_kind)
            if department_ids:
                filters.append(User.department_id.in_(department_ids))
            elif department_id is not None:
                filters.append(User.department_id == department_id)
            if roles:
                filters.append(User.role.in_(roles))
            elif role is not None:
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

    async def search_access_options(
        self,
        *,
        search: str | None = None,
        include_uids: list[str] | None = None,
        roles: list[str] | None = None,
        limit: int = 1000,
    ) -> Annotated[list[tuple[User, str | None]], "可见用户选项，含部门名"]:
        """授权选择器搜索：``search`` 命中项 ∪ 已选 ``include_uids``，沿用有效真人用户过滤。

        ``roles`` 非空时先按角色收窄再取前 limit 条（「可管理」选择器只认管理员，
        不能取全体前 50 条再在浏览器里筛）。无 search、无 include_uids 且无 roles 时
        退回“返回前 limit 条”，与既有 access-options 行为一致。
        """
        async with self._session() as session:
            from yuxi.modules.identity.models import Department

            filters = [User.is_deleted == 0, User.user_kind == "human"]
            if roles:
                filters.append(User.role.in_(roles))
            scope = []
            keyword = (search or "").strip()
            if keyword:
                scope.append(
                    or_(
                        User.username.icontains(keyword, autoescape=True),
                        User.uid.icontains(keyword, autoescape=True),
                        User.phone_number.icontains(keyword, autoescape=True),
                    )
                )
            uids = [str(uid).strip() for uid in (include_uids or []) if str(uid).strip()]
            if uids:
                scope.append(User.uid.in_(uids))
            if scope:
                filters.append(or_(*scope))

            result = await session.execute(
                select(User, Department.name.label("department_name"))
                .outerjoin(Department, User.department_id == Department.id)
                .where(*filters)
                .order_by(User.id.asc())
                .limit(limit)
            )
            return list(result.all())

    async def create(self, data: dict[str, Any]) -> User:
        """创建用户"""
        async with self._session(write=True) as session:
            user = User(**data)
            session.add(user)
            await session.flush()
            await session.refresh(user)
        return user

    async def save(self, user: User, *, refresh: bool = False) -> User:
        """flush 用户实体的当前变更，事务提交由用例 owner 负责。"""
        async with self._session(write=True) as session:
            await session.flush()
            if refresh:
                await session.refresh(user)
            return user

    async def update(self, id: int, data: dict[str, Any]) -> User | None:
        """更新用户"""
        async with self._session(write=True) as session:
            result = await session.execute(select(User).where(User.id == id, User.is_deleted == 0))
            user = result.scalar_one_or_none()
            if user is None:
                return None
            for key, value in data.items():
                if key != "id":
                    setattr(user, key, value)
            await session.flush()
        return user

    async def delete_for_admin(self, user: User) -> None:
        """软删除用户并在同一事务中不可恢复地撤销其 API Key。"""
        async with self._session(write=True) as session:
            await self.ensure_deletable_superadmin(session, user)
            user.is_deleted = 1
            user.deleted_at = utc_now()
            user.username = f"已注销用户-{user.id}"
            user.phone_number = None
            user.password_hash = "DELETED"
            user.avatar = None
            await self._revoke_api_keys(session, user.id, user.deleted_at)
            await session.flush()

    async def lock_identity_changes(self) -> None:
        """删除身份前按共同顺序取得事务锁。"""
        async with self._session(write=True) as session:
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": IDENTITY_CHANGE_LOCK_KEY})

    async def ensure_deletable_superadmin(self, session, user: User) -> None:
        """保留至少一个未删除的系统管理员。"""
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": IDENTITY_CHANGE_LOCK_KEY})
        if user.role == "superadmin" and not user.is_deleted:
            survivor = await session.scalar(
                select(User.id)
                .where(
                    User.role == "superadmin",
                    User.is_deleted == 0,
                    User.id != user.id,
                )
                .limit(1)
            )
            if survivor is None:
                raise ValueError("必须至少保留一个有效系统管理员")

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
        """统计有效系统用户数量。"""
        async with self._session() as session:
            query = select(func.count(User.id)).where(User.is_deleted == 0, User.user_kind == "human")
            if department_id is not None:
                query = query.where(User.department_id == department_id)
            result = await session.execute(query)
            return result.scalar() or 0

    async def get_all_uids(self) -> list[str]:
        """读取完整身份空间的 UID，供账号创建检查唯一性。"""
        async with self._session() as session:
            result = await session.execute(select(User.uid))
            return [uid for (uid,) in result.all()]

    @asynccontextmanager
    async def _session(self, *, write: bool = False) -> AsyncIterator[AsyncSession]:
        """写入要求调用方事务，独立会话只用于读取。"""
        if write and self.db_session is None:
            raise RuntimeError("用户写入需要调用方事务")
        if self.db_session is not None:
            yield self.db_session
            return
        async with pg_manager.get_async_session_context() as session:
            yield session

    @staticmethod
    async def _revoke_api_keys(session: AsyncSession, user_id: int, revoked_at: dt) -> None:
        """撤销用户的全部 API Key，并保留已有撤销时间。"""

        api_key_result = await session.execute(select(APIKey).where(APIKey.user_id == user_id))
        for api_key in api_key_result.scalars().all():
            api_key.is_enabled = False
            if api_key.revoked_at is None:
                api_key.revoked_at = revoked_at
