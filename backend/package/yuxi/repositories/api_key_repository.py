"""API Key 数据访问层。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import APIKey, User
from yuxi.utils.datetime_utils import utc_now_naive


@dataclass(frozen=True)
class APIKeyAccessResult:
    """带存在性信息的 API Key 可见性查询结果。"""

    api_key: APIKey | None
    exists: bool


class APIKeyIdempotencyConflict(Exception):
    """同一幂等请求 ID 被用于不同的 API Key 创建意图。"""


class APIKeySubjectUnavailable(Exception):
    """API Key 关联用户不存在或已删除。"""


class APIKeyDepartmentConflict(Exception):
    """API Key 部门与关联用户当前部门不一致。"""


class APIKeyRepository:
    """封装 API Key 的可见性查询与写事务。"""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    @staticmethod
    def _intent_hash(
        *,
        name: str,
        user_id: int,
        department_id: int | None,
        expires_at: datetime | None,
        created_by: str,
        access_level: str = "full",
        app_id: str | None = None,
    ) -> str:
        """稳定标识原始创建意图，不受资源后续可变字段影响。"""

        payload = {
            "name": name,
            "user_id": user_id,
            "department_id": department_id,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "created_by": created_by,
        }
        if access_level != "full" or app_id is not None:
            payload["access_level"] = access_level
            payload["app_id"] = app_id
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def list_visible(
        self, *, requester_user_id: int, is_superadmin: bool, skip: int, limit: int
    ) -> tuple[list[APIKey], int]:
        """列出请求者可见的 API Key 并返回总数。"""
        query = (
            select(APIKey)
            .where(APIKey.revoked_at.is_(None))
            .order_by(APIKey.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        count_query = select(func.count(APIKey.id)).where(APIKey.revoked_at.is_(None))
        if not is_superadmin:
            query = query.where(APIKey.user_id == requester_user_id)
            count_query = count_query.where(APIKey.user_id == requester_user_id)

        result = await self.db_session.execute(query)
        count_result = await self.db_session.execute(count_query)
        return list(result.scalars().all()), count_result.scalar() or 0

    async def get_accessible(
        self, *, api_key_id: int, requester_user_id: int, is_superadmin: bool
    ) -> APIKeyAccessResult:
        """按请求者可见性读取 API Key，并区分不存在与无权访问。"""
        query = select(APIKey).where(APIKey.id == api_key_id, APIKey.revoked_at.is_(None))
        if not is_superadmin:
            query = query.where(APIKey.user_id == requester_user_id)
        result = await self.db_session.execute(query)
        api_key = result.scalar_one_or_none()
        if api_key is not None:
            return APIKeyAccessResult(api_key=api_key, exists=True)

        exists_result = await self.db_session.execute(
            select(APIKey.id).where(APIKey.id == api_key_id, APIKey.revoked_at.is_(None))
        )
        return APIKeyAccessResult(api_key=None, exists=exists_result.scalar_one_or_none() is not None)

    async def create(
        self,
        *,
        key_hash: str,
        key_prefix: str,
        request_id: str,
        name: str,
        user_id: int,
        department_id: int | None,
        expires_at: datetime | None,
        created_by: str,
        access_level: str = "full",
        app_id: str | None = None,
    ) -> APIKey:
        """在幂等锁内创建或重放同一 API Key 事实。"""

        bind = self.db_session.get_bind()
        if bind.dialect.name == "postgresql":
            await self.db_session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                {"scope": f"api-key:{request_id}"},
            )

        subject = await self.db_session.scalar(
            select(User).where(User.id == user_id, User.is_deleted == 0).with_for_update()
        )
        if subject is None or subject.user_kind == "end_user":
            raise APIKeySubjectUnavailable("关联的用户不存在")
        if department_id is not None and department_id != subject.department_id:
            raise APIKeyDepartmentConflict("API Key 部门必须与关联用户部门一致")

        intent_hash = self._intent_hash(
            name=name,
            user_id=user_id,
            department_id=department_id,
            expires_at=expires_at,
            created_by=created_by,
            access_level=access_level,
            app_id=app_id,
        )
        existing = await self.db_session.scalar(select(APIKey).where(APIKey.request_id == request_id))
        if existing is not None:
            expected = existing.intent_hash == intent_hash
            if existing.intent_hash is None:
                expected = (
                    existing.key_hash == key_hash
                    and existing.name == name
                    and existing.user_id == user_id
                    and existing.department_id == department_id
                    and existing.expires_at == expires_at
                    and existing.created_by == created_by
                    and existing.access_level == access_level
                    and existing.app_id == app_id
                )
                if expected:
                    existing.intent_hash = intent_hash
                    await self.db_session.flush()
            if not expected:
                raise APIKeyIdempotencyConflict("幂等请求 ID 已绑定另一项 API Key 创建意图")
            if existing.revoked_at is not None:
                raise APIKeyIdempotencyConflict("该幂等创建请求对应的 API Key 已撤销，不能重放")
            if existing.key_hash != key_hash:
                raise APIKeyIdempotencyConflict("历史 API Key 无法使用当前主密钥安全重放")
            return existing

        api_key = APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            request_id=request_id,
            intent_hash=intent_hash,
            name=name,
            user_id=user_id,
            department_id=department_id,
            expires_at=expires_at,
            created_by=created_by,
            access_level=access_level,
            app_id=app_id,
        )
        self.db_session.add(api_key)
        await self.db_session.flush()
        await self.db_session.refresh(api_key)
        return api_key

    async def update(self, api_key: APIKey, data: dict[str, Any]) -> APIKey:
        """更新并提交 API Key。"""
        for key, value in data.items():
            setattr(api_key, key, value)
        await self.db_session.commit()
        await self.db_session.refresh(api_key)
        return api_key

    async def delete(self, api_key: APIKey) -> None:
        """撤销 API Key 并保留幂等 tombstone，阻止同一请求复活凭据。"""

        api_key.is_enabled = False
        api_key.revoked_at = utc_now_naive()
        await self.db_session.commit()
