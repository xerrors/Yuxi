"""真实 PostgreSQL 验证短目录标识的持久绑定与并发分配。"""

import asyncio
import os
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.modules.workspace.paths as paths
import yuxi.modules.workspace.services.projects as projects
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("first_status", ["active", "deleted", "concurrent"])
async def test_short_prefix_collision_keeps_unmaterialized_project_bindings_distinct(monkeypatch, tmp_path, first_status: str):
    """同前缀项目即使未落盘、软删除或并发创建也分配不同目录。"""
    uid = f"pytest-project-allocation-{uuid4()}"
    project_ids = iter([UUID("a1b2c3d4-e5f6-4789-8123-456789abcdef"), UUID("a1b2c3ff-e5f6-4789-8123-456789abcdef")])
    monkeypatch.setattr(projects, "uuid", SimpleNamespace(uuid4=lambda: next(project_ids)))
    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "shanghai_now", lambda: datetime.fromisoformat("2026-10-15T20:57:41+08:00"))
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            db.add(User(uid=uid, username=uid, password_hash="test"))
            await db.commit()

        async def create_project(status: str = "active"):
            """在独立事务中创建并提交测试项目。"""
            async with factory() as db:
                project = await projects.create_implicit_project(uid=uid, db=db)
                project.status = status
                await db.commit()

        if first_status == "concurrent":
            async with factory() as db:
                await projects.create_implicit_project(uid=uid, db=db)
                second = asyncio.create_task(create_project())
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(second), timeout=0.5)
                    await db.commit()
                    await asyncio.wait_for(second, timeout=5)
                finally:
                    if not second.done():
                        second.cancel()
                    await asyncio.gather(second, return_exceptions=True)
        else:
            await create_project(first_status)
            await create_project()

        async with factory() as db:
            bindings = set((await db.scalars(select(Project.workdir_path).where(Project.uid == uid))).all())
        assert bindings == {"agents/tasks/2026-10-15/new-chat-a1b2c3", "agents/tasks/2026-10-15/new-chat-a1b2c3-1"}
        assert not paths.user_workspace_dir(uid).exists()
        for binding in bindings:
            paths.create_managed_workdir(uid, binding)
            target = paths.user_workdir_host_dir(uid, binding)
            (target / "owner.txt").write_text(binding, encoding="utf-8")
        for binding in bindings:
            assert (paths.user_workdir_host_dir(uid, binding) / "owner.txt").read_text(encoding="utf-8") == binding
    finally:
        async with factory() as db:
            await db.execute(delete(Project).where(Project.uid == uid))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()
