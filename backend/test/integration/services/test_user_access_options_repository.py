"""真实 PostgreSQL 上验证 access-options 选择器搜索：search ∪ include_uids 与真人账号过滤。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.repositories.users import UserRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _seed(session):
    """种两部门各若干真人账号与一个可被关键词命中的公共终端影子账号。"""
    token = uuid.uuid4().hex[:8]
    dept_a = Department(name=f"dept_a_{token}")
    dept_b = Department(name=f"dept_b_{token}")
    session.add_all([dept_a, dept_b])
    await session.flush()
    alpha = User(
        username=f"opt_{token}_alpha",
        uid=f"u_{token}_alpha",
        password_hash="x",
        role="user",
        department_id=dept_a.id,
    )
    beta = User(
        username=f"opt_{token}_beta",
        uid=f"u_{token}_beta",
        password_hash="x",
        role="user",
        department_id=dept_a.id,
    )
    gamma = User(
        username=f"opt_{token}_gamma",
        uid=f"u_{token}_gamma",
        password_hash="x",
        role="user",
        department_id=dept_b.id,
    )
    admin = User(
        username=f"opt_{token}_admin",
        uid=f"u_{token}_admin",
        password_hash="x",
        role="admin",
        department_id=dept_a.id,
    )
    session.add_all([alpha, beta, gamma, admin])
    await session.flush()
    shadow = User(
        username=f"opt_{token}_shadow",
        uid=f"u_{token}_shadow",
        password_hash="x",
        role="user",
        user_kind="end_user",
        owner_user_id=alpha.id,
        app_id="opt_app",
        end_user_id=f"end_{token}",
        department_id=dept_a.id,
    )
    session.add(shadow)
    await session.flush()
    users = {alpha.uid: alpha, beta.uid: beta, gamma.uid: gamma, admin.uid: admin, shadow.uid: shadow}
    return token, users


@pytest.mark.parametrize(
    ("search", "include_uids", "roles", "expected_uids", "forbidden_uids"),
    [
        ("", [], None, {"alpha", "beta", "gamma", "admin"}, set()),  # 无 search/include/roles → 返回前 limit 条
        ("alpha", [], None, {"alpha"}, {"beta", "gamma", "admin"}),  # 服务端搜索收窄
        ("", ["beta"], None, {"beta"}, {"alpha", "gamma", "admin"}),  # include 回填已选
        ("zzz_nomatch", ["beta"], None, {"beta"}, {"alpha", "gamma", "admin"}),  # 不匹配搜索但已选仍返回
        # roles 白名单：可管理选择器首屏只取管理员，全体前 N 条筛法会漏后位管理员
        ("", [], ["admin", "superadmin"], {"admin"}, {"alpha", "beta", "gamma"}),
    ],
)
async def test_search_access_options(postgres_sessions, search, include_uids, roles, expected_uids, forbidden_uids):
    async with postgres_sessions() as session:
        token, users = await _seed(session)
        by_suffix = {uid.rsplit("_", 1)[-1]: uid for uid in users}
        resolved_include = [by_suffix[suffix] for suffix in include_uids]
        # 空 search 的"全量"用例按 id 升序取前 limit 条；取当前表行数，避免在共享大库下
        # 新 seed 的高 id 行落在固定窗口之外导致脆弱失败。
        total_users = (await session.scalar(select(func.count()).select_from(User))) or 0
        rows = await UserRepository(session).search_access_options(
            search=search or None,
            include_uids=resolved_include,
            roles=roles,
            limit=max(500, total_users),
        )
        # 限定 token，避免与库中其它历史行冲突；end_user 影子账号不得出现在任何结果中
        uids = {user.uid for user, _ in rows if user.username.startswith(f"opt_{token}")}
        suffixes = {uid.rsplit("_", 1)[-1] for uid in uids}
        assert expected_uids <= suffixes
        assert not (forbidden_uids & suffixes)
        assert "shadow" not in suffixes

        await session.rollback()
