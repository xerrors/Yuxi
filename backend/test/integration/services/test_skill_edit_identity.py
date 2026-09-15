"""真实 PostgreSQL 验证技能编辑在锁内刷新持久事实。"""

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills import service as svc
from yuxi.storage.postgres.models_business import Skill, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def shared_skill(tmp_path, monkeypatch):
    """创建独立技能与真实 Session，退出后仅删除本测试行。"""
    if os.environ.get("TEST_ALLOW_SKILL_EDIT_DB") != "1":
        pytest.skip("Set TEST_ALLOW_SKILL_EDIT_DB=1 for a dedicated PostgreSQL database")
    url = os.environ.get("TEST_POSTGRES_URL") or os.environ["POSTGRES_URL"]
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Skill.__table__.create(sync, checkfirst=True))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    slug = f"pytest-edit-{uuid.uuid4().hex}"
    monkeypatch.setattr(svc, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(svc, "get_skill_projection_dir", lambda: tmp_path / "projections")
    directory = tmp_path / "shared" / slug
    directory.mkdir(parents=True)
    target = directory / "SKILL.md"
    target.write_text(f"---\nname: {slug}\ndisplay_name: Old\ndescription: fixture\n---\nbody\n", encoding="utf-8")
    operator = User(username="editor", uid="editor", role="user", password_hash="test")
    try:
        async with sessions() as db:
            db.add(
                Skill(
                    slug=slug,
                    name="Old",
                    description="fixture",
                    dir_path=f"shared/{slug}",
                    source_type="upload",
                    created_by="owner",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "global"},
                        "manage_scope": {"access_level": "user", "user_uids": ["editor"]},
                    },
                )
            )
            await db.commit()
        yield sessions, slug, target, operator
    finally:
        async with sessions() as db:
            await db.execute(delete(Skill).where(Skill.slug == slug))
            await db.commit()
        await engine.dispose()


async def test_edit_refreshes_stale_identity_before_restoring_old_name(shared_skill):
    """两个 Session 预读 Old 后依次写 New/Old，数据库和文件必须均恢复 Old。"""
    sessions, slug, target, operator = shared_skill
    async with sessions() as first, sessions() as second:
        first_item = await svc.get_manageable_skill_or_raise(first, operator, slug)
        stale_item = await svc.get_manageable_skill_or_raise(second, operator, slug)
        assert first_item.name == stale_item.name == "Old"
        await svc.update_skill_display_name(first, slug=slug, display_name="New", operator=operator)
        assert stale_item.name == "Old"
        assert svc._parse_skill_markdown(target.read_text(encoding="utf-8"))[1] == "New"
        await svc.update_skill_display_name(second, slug=slug, display_name="Old", operator=operator)
    async with sessions() as observer:
        persisted = await observer.scalar(select(Skill).where(Skill.slug == slug))
        assert persisted.name == "Old"
        assert svc._parse_skill_markdown(target.read_text(encoding="utf-8"))[:3] == (
            persisted.slug,
            persisted.name,
            persisted.description,
        )


@pytest.mark.parametrize(
    "change,error", [("permission", "无权管理"), ("builtin", "无权管理"), ("directory", "目录已变化")]
)
async def test_edit_rechecks_changed_skill_facts_before_touching_file(shared_skill, change, error):
    """旧 identity map 中的管理权、来源和目录均不得用于后续写入。"""
    sessions, slug, target, operator = shared_skill
    original = target.read_bytes()
    async with sessions() as first, sessions() as second:
        stale_item = await svc.get_manageable_skill_or_raise(second, operator, slug)
        current = await svc.get_manageable_skill_or_raise(first, operator, slug)
        if change == "permission":
            current.share_config = {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": {"access_level": "user", "user_uids": ["owner"]},
            }
        elif change == "builtin":
            current.source_type = "builtin"
        else:
            current.dir_path = f"shared/{slug}-moved"
        await first.commit()
        assert stale_item.name == "Old"
        with pytest.raises(ValueError, match=error):
            await svc.update_skill_display_name(second, slug=slug, display_name="Forbidden", operator=operator)
        await second.rollback()
    assert target.read_bytes() == original
    async with sessions() as observer:
        persisted = await observer.scalar(select(Skill).where(Skill.slug == slug))
        assert persisted.name == "Old"
