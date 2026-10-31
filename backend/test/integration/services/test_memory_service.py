"""真实 PostgreSQL 上的用户 Memory 并发与执行授权测试。"""

from __future__ import annotations

import asyncio
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

import yuxi.modules.agents.services.memory as memory_service
from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.migrations.schema import create_business_tables
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.services.memory_history import MemoryHistoryService
from yuxi.modules.identity.models import User, UserConfig
from yuxi.modules.workspace import paths as workspace_paths
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Project
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture()
async def memory_database(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """提供独立连接与临时 UserWorkspace，并在用例后清理数据库事实。"""
    schema, admin, engine, manager = await create_isolated_manager("memory_service")
    await create_business_tables(manager)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def local_session_context():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    monkeypatch.setattr(pg_manager, "get_async_session_context", local_session_context)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))

    uid = f"pytest-memory-{uuid.uuid4().hex}"
    thread_id = f"thread-{uuid.uuid4().hex}"
    turn_id = f"turn-{uuid.uuid4().hex}"
    run_id = f"run-{uuid.uuid4().hex}"
    project_id = str(uuid.uuid4())
    worker_id = f"worker-{uuid.uuid4().hex}"
    async with session_factory() as db:
        db.add(User(username=uid, uid=uid, password_hash="test", role="user"))
        await db.flush()
        db.add(UserConfig(uid=uid, enable_memory=True))
        db.add(
            Project(
                id=project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=f"projects/{project_id}",
                directory_mode="managed",
            )
        )
        await db.flush()
        agent_session = Session(
            thread_id=thread_id,
            uid=uid,
            project_id=project_id,
            agent_id="main",
            status="active",
        )
        db.add(agent_session)
        await db.flush()
        db.add(AgentTurn(id=turn_id, thread_id=thread_id, uid=uid, status="running"))
        await db.flush()
        db.add(
            AgentRun(
                id=run_id,
                thread_id=thread_id,
                runtime_scope_id=thread_id,
                agent_slug="main",
                uid=uid,
                status="running",
                turn_id=turn_id,
                session_record_id=agent_session.id,
                run_type="chat",
                input_payload={},
                worker_id=worker_id,
                heartbeat_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(minutes=5),
            )
        )
        await db.commit()
    workspace_paths.ensure_user_workspace(uid)

    identity = {
        "uid": uid,
        "thread_id": thread_id,
        "run_id": run_id,
        "worker_id": worker_id,
    }
    try:
        yield session_factory, identity
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_concurrent_writes_are_serialized_without_lost_updates(memory_database):
    """同一 uid 的跨连接写入必须串行读改写并保留双方内容。"""
    _session_factory, identity = memory_database
    first, second = await asyncio.gather(
        memory_service.remember_memory(**identity, content="并发记忆 A"),
        memory_service.remember_memory(**identity, content="并发记忆 B"),
    )

    stored = Workspace(identity["uid"]).read_authorized_file(memory_service.MEMORY_PATH, 4096).decode()
    assert {first["status"], second["status"]} == {"updated"}
    assert stored.count("并发记忆 A") == 1
    assert stored.count("并发记忆 B") == 1


@pytest.mark.self_managed_resources
async def test_cancelled_memory_write_keeps_lock_until_file_replace_finishes(memory_database, monkeypatch):
    """取消后保留事务锁直到文件替换完成，下一写入不得丢失前一次结果。"""
    session_factory, identity = memory_database
    entered = threading.Event()
    release = threading.Event()
    replace = memory_service._replace_memory_file

    def blocked_replace(uid, updated):
        """暂停实际替换，观察取消期间的数据库锁。"""
        entered.set()
        if not release.wait(10):
            raise TimeoutError("测试没有释放文件写入")
        return replace(uid, updated)

    monkeypatch.setattr(memory_service, "_replace_memory_file", blocked_replace)
    first = asyncio.create_task(memory_service.remember_memory(**identity, content="取消中的记忆"))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        first.cancel()
        async with session_factory() as db:
            acquired = await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"user-memory:{identity['uid']}"},
            )
            assert acquired is False
        assert not first.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    monkeypatch.setattr(memory_service, "_replace_memory_file", replace)
    await memory_service.remember_memory(**identity, content="后续记忆")
    stored = Workspace(identity["uid"]).read_authorized_file(memory_service.MEMORY_PATH, 4096).decode()
    assert stored.count("取消中的记忆") == 1
    assert stored.count("后续记忆") == 1


async def test_write_rechecks_switch_and_current_lease_owner(memory_database):
    """执行处必须从数据库重读开关，并拒绝伪造或过期 owner。"""
    session_factory, identity = memory_database
    original = Workspace(identity["uid"]).read_authorized_file(memory_service.MEMORY_PATH, 4096)

    with pytest.raises(ValueError, match="lease owner"):
        await memory_service.remember_memory(**(identity | {"worker_id": "forged-owner"}), content="不能写入")

    async with session_factory() as db:
        await db.execute(update(UserConfig).where(UserConfig.uid == identity["uid"]).values(enable_memory=False))
        await db.commit()

    with pytest.raises(ValueError, match="Memory 已关闭"):
        await memory_service.remember_memory(**identity, content="仍不能写入")
    assert Workspace(identity["uid"]).read_authorized_file(memory_service.MEMORY_PATH, 4096) == original


async def test_history_query_uses_postgres_visibility_and_field_allowlists(memory_database):
    """真实 PostgreSQL 按用户可见性与消息类型筛选，只按显式开关返回工具执行。"""
    session_factory, identity = memory_database
    uid = identity["uid"]
    project_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(
            Project(
                id=project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=f"projects/{project_id}",
                directory_mode="managed",
            )
        )
        await db.flush()
        visible = Session(
            thread_id=f"visible-{uuid.uuid4().hex}",
            uid=uid,
            project_id=project_id,
            agent_id="main",
            status="active",
        )
        parent = Session(
            thread_id=f"parent-{uuid.uuid4().hex}",
            uid=uid,
            project_id=project_id,
            agent_id="main",
            status="active",
        )
        child = Session(
            thread_id=f"child-{uuid.uuid4().hex}",
            uid=uid,
            project_id=project_id,
            agent_id="worker",
            status="active",
        )
        db.add_all([visible, parent])
        await db.flush()
        child.tree_root_thread_id = parent.thread_id
        child.parent_thread_id = parent.thread_id
        child.cooperation_name = "worker"
        child.cooperation_path = "/root/worker"
        db.add(child)
        await db.flush()
        visible_message = Message(
            session_record_id=visible.id,
            role="assistant",
            content="needle visible",
            message_type="text",
            extra_metadata={"secret": "METADATA-SECRET"},
            image_content="IMAGE-SECRET",
        )
        db.add_all(
            [
                visible_message,
                Message(session_record_id=visible.id, role="tool", content="needle TOOL-ROLE", message_type="text"),
                Message(
                    session_record_id=visible.id,
                    role="assistant",
                    content="needle TOOL-TYPE",
                    message_type="tool_result",
                ),
                Message(session_record_id=child.id, role="assistant", content="needle CHILD", message_type="text"),
            ]
        )
        await db.flush()
        db.add(
            Message(
                session_record_id=visible_message.session_record_id,
                turn_id=visible_message.turn_id,
                run_id=visible_message.run_id,
                role="tool",
                message_type="tool_audit",
                operation_id="memory-history-call",
                content="",
                execution_status="completed",
                started_at=visible_message.created_at,
                extra_metadata={
                    "output": "TOOLCALL-OUTPUT",
                    "source_model_message_id": visible_message.id,
                    "tool_name": "history_fixture",
                    "input": {"secret": "TOOLCALL-INPUT"},
                },
            )
        )
        await db.commit()

    async with session_factory() as db:
        search = await MemoryHistoryService(db).search_memory_messages(uid=uid, query="needle", limit=10)
        default_read = await MemoryHistoryService(db).read_memory_messages(uid=uid, thread_id=visible.thread_id)
        tool_read = await MemoryHistoryService(db).read_memory_messages(
            uid=uid,
            thread_id=visible.thread_id,
            include_tools=True,
        )

    assert {item["thread_id"] for item in search["items"]} == {visible.thread_id, child.thread_id}
    assert [item["content"] for item in default_read["messages"]] == ["needle visible"]
    assert default_read["tool_calls"] == []
    serialized_default = str(default_read)
    assert "METADATA-SECRET" not in serialized_default
    assert "IMAGE-SECRET" not in serialized_default
    assert tool_read["tool_calls"][0]["input"] == '{"secret":"TOOLCALL-INPUT"}'
    assert tool_read["tool_calls"][0]["output"] == "TOOLCALL-OUTPUT"


async def test_memory_tools_reject_forged_source_from_other_session_or_turn(memory_database):
    """伪造合法来源 ID 仍不能越过会话和 Turn 的执行事实边界。"""
    session_factory, identity = memory_database
    foreign_uid = f"pytest-memory-foreign-{uuid.uuid4().hex}"
    foreign_project_id = str(uuid.uuid4())
    message_ids = []
    try:
        async with session_factory() as db:
            own_session = await db.scalar(select(Session).where(Session.thread_id == identity["thread_id"]))
            own_run = await db.get(AgentRun, identity["run_id"])
            db.add(User(username=foreign_uid, uid=foreign_uid, password_hash="test", role="user"))
            await db.flush()
            db.add(
                Project(
                    id=foreign_project_id,
                    uid=foreign_uid,
                    selection_status="implicit",
                    workdir_path=f"projects/{foreign_project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            foreign_session = Session(
                thread_id=f"foreign-{uuid.uuid4().hex}",
                uid=foreign_uid,
                project_id=foreign_project_id,
                agent_id="main",
                status="active",
            )
            source = Message(
                session_record_id=own_session.id,
                role="assistant",
                message_type="text",
                content="authorized historical source",
            )
            db.add_all([foreign_session, source])
            await db.flush()
            facts = [
                Message(
                    session_record_id=session_id,
                    run_id=run_id,
                    turn_id=turn_id,
                    role="tool",
                    message_type="tool_audit",
                    execution_status="completed",
                    operation_id=output,
                    content="",
                    extra_metadata={"output": output, "source_model_message_id": source.id, "tool_name": "fixture", "input": {}},
                )
                for session_id, run_id, turn_id, output in [
                    (own_session.id, None, None, "AUTHORIZED-OUTPUT"),
                    (foreign_session.id, None, None, "FOREIGN-USER-SECRET"),
                    (own_session.id, own_run.id, own_run.turn_id, "OTHER-TURN-SECRET"),
                ]
            ]
            db.add_all(facts)
            await db.flush()
            message_ids = [source.id, *(fact.id for fact in facts)]
            await db.commit()

        async with session_factory() as db:
            result = await MemoryHistoryService(db).read_memory_messages(
                uid=identity["uid"], thread_id=identity["thread_id"], include_tools=True
            )
        assert [tool["output"] for tool in result["tool_calls"]] == ["AUTHORIZED-OUTPUT"]
    finally:
        async with session_factory() as db:
            await db.execute(delete(Message).where(Message.id.in_(message_ids)))
            await db.execute(delete(Session).where(Session.uid == foreign_uid))
            await db.execute(delete(Project).where(Project.uid == foreign_uid))
            await db.execute(delete(User).where(User.uid == foreign_uid))
            await db.commit()
