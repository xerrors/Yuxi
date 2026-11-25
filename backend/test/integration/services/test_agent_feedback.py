"""真实 PostgreSQL 上验证反馈持久约束、执行身份与事务。"""

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.base import BusinessBase
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.feedback import AgentFeedbackRecord
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.runtime.middlewares.authorization import AgentExecutionRevoked
from yuxi.modules.agents.services.feedback import (
    list_feedback,
    list_feedback_agent_options,
    mark_run_for_improvement,
    remove_user_feedback,
    save_user_feedback,
)
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import Project
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, InvalidOperationError, PermissionDeniedError, ResourceNotFoundError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件仅操作自行创建的 PostgreSQL Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离测试没有 HTTP 资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离测试没有沙盒资源。"""
    yield


@pytest_asyncio.fixture
async def feedback_db():
    """创建真实作用域与完成结果，测试结束仅删除专属 Schema。"""
    schema = f"pytest_feedback_{uuid.uuid4().hex[:16]}"
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    load_models()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(BusinessBase.metadata.create_all)
        async with sessions() as db:
            db.add(User(username="feedback-user", uid="feedback-user", password_hash="unused", role="user"))
            await db.flush()
            db.add(
                Project(
                    id="project",
                    uid="feedback-user",
                    selection_status="implicit",
                    workdir_path="projects/project",
                    directory_mode="managed",
                )
            )
            db.add(
                Agent(
                    slug="agent", name="Agent", backend_id="ChatbotAgent", visibility="private", created_by="feedback-user", share_config={}
                )
            )
            await db.flush()
            thread = Session(
                thread_id="thread",
                tree_root_thread_id="thread",
                uid="feedback-user",
                agent_id="agent",
                project_id="project",
                config_snapshot={},
            )
            db.add(thread)
            await db.flush()
            db.add(AgentTurn(id="turn", thread_id="thread", uid="feedback-user", status="completed"))
            await db.flush()
            run = AgentRun(
                id="run",
                thread_id="thread",
                runtime_scope_id="thread",
                turn_id="turn",
                uid="feedback-user",
                agent_slug="agent",
                session_record_id=thread.id,
                status="completed",
            )
            db.add(run)
            await db.flush()
            output = Message(session_record_id=thread.id, role="assistant", content="FINAL", run_id="run", turn_id="turn")
            db.add(output)
            await db.flush()
            run.output_message_id = output.id
            turn = await db.get(AgentTurn, "turn")
            turn.current_run_id = turn.result_run_id = "run"
            await db.commit()
        yield sessions
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def save_rating(sessions, *, scope=ActorScope(uid="feedback-user", app_id=None), rating="like", reason="说明", run_id="run"):
    """通过真实用例保存最终结果评价。"""
    async with sessions() as db:
        return await save_user_feedback(db=db, scope=scope, thread_id="thread", turn_id="turn", run_id=run_id, rating=rating, reason=reason)


async def mark(sessions, *, reason="纠正了流程，保存可复用方法", uid="feedback-user", worker_id="worker:attempt", tool_call_id="call"):
    """使用本测试真实 worker token 调用持久执行边界。"""
    async with sessions() as db:
        return await mark_run_for_improvement(
            db=db, run_id="run", thread_id="thread", uid=uid, worker_id=worker_id, tool_call_id=tool_call_id, reason=reason
        )


async def activate_run(sessions):
    """仅为工具事务测试设定真实有效 lease。"""
    async with sessions() as db:
        await db.execute(
            update(AgentRun).values(status="running", worker_id="worker:attempt", lease_expires_at=utc_now() + timedelta(minutes=5))
        )
        await db.commit()


async def test_user_concurrency_replace_and_revoke_read_database(feedback_db):
    """并发首次评价收敛一行，修改与撤销以独立事务回读验证。"""
    first, second = await asyncio.gather(save_rating(feedback_db), save_rating(feedback_db))
    assert first["id"] == second["id"] and first["updated_at"] == second["updated_at"]
    updated = await save_rating(feedback_db, rating="dislike", reason="  需要改进  ")
    assert updated["id"] == first["id"] and updated["reason"] == "需要改进"
    async with feedback_db() as db:
        records = (await db.scalars(select(AgentFeedbackRecord))).all()
        assert len(records) == 1 and records[0].rating == "dislike"
        await remove_user_feedback(
            db=db, scope=ActorScope(uid="feedback-user", app_id=None), thread_id="thread", turn_id="turn", run_id="run"
        )
    async with feedback_db() as db:
        assert await db.scalar(select(func.count()).select_from(AgentFeedbackRecord)) == 0
        await remove_user_feedback(
            db=db, scope=ActorScope(uid="feedback-user", app_id=None), thread_id="thread", turn_id="turn", run_id="run"
        )


@pytest.mark.parametrize(
    "scope",
    [
        ActorScope(uid="other", app_id=None),
        ActorScope(uid="feedback-user", app_id="other-app"),
        ActorScope(uid="other", app_id=None, is_superadmin=True),
    ],
)
async def test_user_feedback_never_uses_admin_or_partial_scope(feedback_db, scope):
    """其他用户、不同 APP 和超级管理员均不能冒用作者。"""
    with pytest.raises(DomainError) as exc:
        await save_rating(feedback_db, scope=scope)
    assert isinstance(exc.value, ResourceNotFoundError)
    async with feedback_db() as db:
        assert await db.scalar(select(func.count()).select_from(AgentFeedbackRecord)) == 0


@pytest.mark.parametrize("defect", ["waiting", "wrong-result", "no-output", "empty-output"])
async def test_feedback_requires_explicit_final_output(feedback_db, defect):
    """等待、旧结果和无输出不接受最终回答评价。"""
    async with feedback_db() as db:
        if defect in {"waiting", "wrong-result"}:
            await db.execute(update(AgentTurn).values(status="waiting" if defect == "waiting" else "completed", result_run_id=None))
        elif defect == "no-output":
            await db.execute(update(AgentRun).values(output_message_id=None))
        else:
            await db.execute(update(Message).values(content=" "))
        await db.commit()
    with pytest.raises(DomainError) as exc:
        await save_rating(feedback_db)
    assert isinstance(exc.value, StateConflictError)


async def test_mark_replay_conflict_and_terminal_retention(feedback_db):
    """同一 call 去重，冲突拒绝；失败 Run 保留先前提交的标记。"""
    await activate_run(feedback_db)
    first, second = await asyncio.gather(mark(feedback_db), mark(feedback_db))
    assert first["id"] == second["id"]
    with pytest.raises(DomainError) as exc:
        await mark(feedback_db, reason="改变了内容")
    assert isinstance(exc.value, StateConflictError)
    await mark(feedback_db, tool_call_id="another-call")
    async with feedback_db() as db:
        await db.execute(update(AgentRun).values(status="failed", lease_expires_at=None))
        await db.commit()
    assert (await mark(feedback_db))["id"] == first["id"]
    async with feedback_db() as db:
        page = await list_feedback(db=db, scope=ActorScope(uid="feedback-user", app_id=None), thread_id="thread")
        assert len(page["items"]) == 2 and {item["run_status"] for item in page["items"]} == {"failed"}


async def test_tool_database_failure_rolls_back_without_success(feedback_db, monkeypatch):
    """真实数据库拒绝 insert 时工具返回错误，记录不存在且可正常重试。"""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.extensions.tools.builtin.mark_for_improvement import mark_for_improvement

    await activate_run(feedback_db)
    async with feedback_db() as db:
        await db.execute(
            text(
                "CREATE FUNCTION reject_feedback() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'feedback write rejected'; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER reject_feedback_insert BEFORE INSERT ON agent_feedback_records "
                "FOR EACH ROW EXECUTE FUNCTION reject_feedback()"
            )
        )
        await db.commit()

    @asynccontextmanager
    async def isolated_session():
        """工具仍执行真实 PG 事务，只把数据库切到测试专属 Schema。"""
        async with feedback_db() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", isolated_session)
    runtime = SimpleNamespace(
        context=SimpleNamespace(run_id="run", thread_id="thread", uid="feedback-user", worker_id="worker:attempt"),
        tool_call_id="database-call",
    )
    result = await mark_for_improvement.ainvoke({"reason": "记录可复用方法", "runtime": runtime})
    assert "未确认保存" in result and "saved" not in result
    async with feedback_db() as db:
        assert await db.scalar(select(func.count()).select_from(AgentFeedbackRecord)) == 0
        await db.execute(text("DROP TRIGGER reject_feedback_insert ON agent_feedback_records"))
        await db.commit()
    retry = await mark_for_improvement.ainvoke({"reason": "记录可复用方法", "runtime": runtime})
    assert retry["saved"] is True
    async with feedback_db() as db:
        assert await db.scalar(select(AgentFeedbackRecord.id)) == retry["id"]


@pytest.mark.parametrize("defect", ["expired", "stale-worker", "identity", "revoked-agent", "deleted-user"])
async def test_mark_rejects_invalid_execution_owner(feedback_db, defect):
    """executor 的独立入口不能绕过 lease、身份或权限检查。"""
    await activate_run(feedback_db)
    async with feedback_db() as db:
        if defect == "expired":
            await db.execute(update(AgentRun).values(lease_expires_at=utc_now() - timedelta(seconds=1)))
        elif defect == "revoked-agent":
            await db.execute(update(Agent).values(created_by="other"))
        elif defect == "deleted-user":
            await db.execute(update(User).values(is_deleted=1))
        await db.commit()
    with pytest.raises(AgentExecutionRevoked):
        await mark(
            feedback_db,
            uid="other" if defect == "identity" else "feedback-user",
            worker_id="old-attempt" if defect == "stale-worker" else "worker:attempt",
        )
    async with feedback_db() as db:
        assert await db.scalar(select(func.count()).select_from(AgentFeedbackRecord)) == 0


@pytest.mark.parametrize(
    "values",
    [
        {"source": "agent", "reason": " ", "tool_call_id": "call"},
        {"source": "user", "rating": None},
        {"source": "user", "rating": "like", "tool_call_id": "call"},
        {"source": "agent", "reason": "reason", "rating": "like", "tool_call_id": "call"},
    ],
)
async def test_database_rejects_malformed_feedback(feedback_db, values):
    """直接 SQL 写路径也受到 source 组合约束保护。"""
    with pytest.raises(IntegrityError, match="ck_agent_feedback_source_shape"):
        async with feedback_db() as db:
            db.add(AgentFeedbackRecord(run_id="run", **values))
            await db.commit()


async def test_platform_query_guards_cursor_and_deleted_session(feedback_db):
    """集中查询不泄漏清单，游标受过滤约束，删除后所有查询隐藏记录。"""
    record = await save_rating(feedback_db)
    platform = ActorScope(uid="platform", app_id=None, is_superadmin=True)
    async with feedback_db() as db:
        page = await list_feedback(db=db, scope=platform, platform=True)
        assert page["items"][0]["id"] == record["id"]
        assert set(page["items"][0]) == {
            "id",
            "run_id",
            "source",
            "rating",
            "reason",
            "created_at",
            "updated_at",
            "agent_id",
            "agent_name",
            "session_id",
            "turn_id",
            "run_status",
        }
        with pytest.raises(DomainError) as exc:
            await list_feedback(db=db, scope=platform, platform=True, source="agent", after=record["id"])
        assert isinstance(exc.value, InvalidOperationError)
        with pytest.raises(DomainError) as exc:
            await list_feedback(db=db, scope=ActorScope(uid="platform", app_id="app", api_key_id=1, is_superadmin=True), platform=True)
        assert isinstance(exc.value, PermissionDeniedError)
        await db.execute(update(Session).values(status="deleted"))
        await db.commit()
    async with feedback_db() as db:
        assert (await list_feedback(db=db, scope=platform, platform=True))["items"] == []
        assert await db.scalar(select(func.count()).select_from(AgentFeedbackRecord)) == 1
        # 硬删除 Run 时级联删除反馈；先解除数据库中的结果关联。
        await db.execute(update(AgentTurn).values(current_run_id=None, result_run_id=None))
        await db.execute(update(AgentRun).values(output_message_id=None))
        await db.execute(delete(Message))
        await db.execute(delete(AgentRun))
        await db.commit()
    async with feedback_db() as db:
        assert await db.scalar(select(func.count()).select_from(AgentFeedbackRecord)) == 0


async def test_deleted_agent_stays_named_in_feedback_list_and_filter(feedback_db):
    """删除智能体行后，反馈仍可按同一展示名筛选，记录条数不变。"""
    record = await save_rating(feedback_db)
    platform = ActorScope(uid="platform", app_id=None, is_superadmin=True)
    async with feedback_db() as db:
        page = await list_feedback(db=db, scope=platform, platform=True)
        assert page["items"][0]["agent_name"] == "Agent"
        await db.execute(delete(Agent))
        await db.commit()
    async with feedback_db() as db:
        page = await list_feedback(db=db, scope=platform, platform=True)
        assert [item["id"] for item in page["items"]] == [record["id"]]
        assert page["items"][0]["agent_name"] == "未登记智能体（agent）"
        options = await list_feedback_agent_options(db=db, scope=platform, platform=True)
        assert options["agents"] == [{"agent_id": "agent", "name": "未登记智能体（agent）"}]
