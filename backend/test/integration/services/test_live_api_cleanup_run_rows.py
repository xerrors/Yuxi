"""真实 PostgreSQL 上测试资源清理的归属和物理删除语义。"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.modules.workspace.services.projects as project_service
from test import live_api_cleanup as cleanup_module
from test.live_api_cleanup import (
    delete_test_session_resources,
    delete_test_session_rows,
    list_test_session_resources,
    make_test_session_metadata,
    make_test_session_title,
    validate_test_runs_terminal,
    validate_test_workdirs_exclusive,
)
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun, AgentRunAttempt
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.input_messages import build_chat_input_message, serialize_input_message
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.paths import user_workdir_host_dir, user_workspace_dir
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import ResourceNotFoundError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def _seed_thread(session_factory, *, thread_prefix: str) -> dict:
    """构造有完整 Input、Turn、Run 与审计消息的测试线程。"""
    thread_id = f"{thread_prefix}-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    input_id = f"cleanup-input-{uuid.uuid4()}"
    receipt_id = f"cleanup-receipt-{uuid.uuid4()}"
    turn_id = f"cleanup-turn-{uuid.uuid4()}"
    run_id = str(uuid.uuid4())
    workdir_path = f"projects/YUXI_TEST_cleanup-{uuid.uuid4()}"
    async with session_factory() as db:
        db.add(User(username=uid, uid=uid, password_hash="test"))
        await db.flush()
        db.add(Project(id=project_id, uid=uid, selection_status="implicit", workdir_path=workdir_path, directory_mode="managed"))
        agent_session = Session(
            thread_id=thread_id,
            uid=uid,
            project_id=project_id,
            agent_id="main",
            title=make_test_session_title(thread_prefix),
            status="active",
            extra_metadata=make_test_session_metadata(thread_prefix),
        )
        db.add(agent_session)
        await db.flush()
        input_repo = AgentInputRepository(db)
        receipt_repo = AgentInputReceiptRepository(db)
        turn_repo = AgentTurnRepository(db)
        input_item = await input_repo.create(
            input_id=input_id,
            thread_id=thread_id,
            uid=uid,
            app_id=None,
            agent_slug="main",
            kind="follow_up",
            messages=[serialize_input_message(build_chat_input_message("input"))],
            input_payload={},
        )
        await receipt_repo.create(
            receipt_id=receipt_id,
            idempotency_key=f"YUXI_TEST_cleanup-{uuid.uuid4()}",
            uid=uid,
            app_id=None,
            thread_id=thread_id,
            event_type="message",
            intent_hash="test",
            input_id=input_id,
        )
        turn = await turn_repo.create(turn_id=turn_id, thread_id=thread_id, uid=uid, app_id=None)
        run = await AgentRunRepository(db).create_run(
            run_id=run_id,
            thread_id=thread_id,
            agent_slug="main",
            uid=uid,
            turn_id=turn_id,
            input_payload={},
            session_record_id=agent_session.id,
        )
        await turn_repo.set_current(turn, run_id=run.id)
        [input_message] = await input_repo.consume(inputs=[input_item], turn_id=turn_id, run_id=run.id)
        run.input_message_id = input_message.id
        run.status = "completed"
        await db.flush()
        await turn_repo.set_terminal(turn, status="completed", result_run_id=run_id)
        output_message = Message(
            session_record_id=agent_session.id,
            run_id=run_id,
            turn_id=turn_id,
            role="assistant",
            content="output",
            delivery_status="complete",
        )
        db.add(output_message)
        await db.flush()
        db.add(
            Message(
                session_record_id=output_message.session_record_id,
                turn_id=output_message.turn_id,
                run_id=output_message.run_id,
                role="tool",
                message_type="tool_audit",
                operation_id="fs",
                content="",
                execution_status="running",
                started_at=output_message.created_at,
                extra_metadata={"source_model_message_id": output_message.id, "tool_name": "fs", "input": {}},
            )
        )
        attempt = AgentRunAttempt(
            run_id=run_id,
            attempt_no=1,
            worker_id="test-owner",
            started_at=utc_now(),
            finished_at=utc_now(),
            outcome="completed",
        )
        db.add(attempt)
        await db.commit()
        return {
            "thread_id": thread_id,
            "uid": uid,
            "project_id": project_id,
            "workdir_path": workdir_path,
            "session_record_id": agent_session.id,
            "input_id": input_id,
            "receipt_id": receipt_id,
            "turn_id": turn_id,
            "run_id": run_id,
            "attempt_id": attempt.id,
            "input_message_id": input_message.id,
            "output_message_id": output_message.id,
        }


async def _cleanup_seed(session_factory, seeds: list[dict]) -> None:
    """仅清理本测试创建的线程及用户。"""
    await delete_test_session_rows({seed["thread_id"] for seed in seeds})
    async with session_factory() as db:
        await db.execute(delete(Project).where(Project.id.in_([seed["project_id"] for seed in seeds])))
        await db.execute(delete(User).where(User.uid.in_([seed["uid"] for seed in seeds])))
        await db.commit()


async def test_delete_test_session_rows_removes_history_and_preserves_neighbor(postgres_sessions):
    """物理清理删除目标整条生命周期历史，同时保留相邻线程。"""
    target = await _seed_thread(postgres_sessions, thread_prefix="pytest-conv-target")
    neighbor = await _seed_thread(postgres_sessions, thread_prefix="pytest-conv-neighbor")
    try:
        await delete_test_session_rows({target["thread_id"]})
        async with postgres_sessions() as db:
            for model, key in (
                (Session, "session_record_id"),
                (AgentInput, "input_id"),
                (AgentInputReceipt, "receipt_id"),
                (AgentTurn, "turn_id"),
                (AgentRun, "run_id"),
                (AgentRunAttempt, "attempt_id"),
                (Message, "input_message_id"),
                (Message, "output_message_id"),
            ):
                assert await db.get(model, target[key]) is None
                assert await db.get(model, neighbor[key]) is not None
            assert await db.scalar(select(Message.id).where(Message.source_input_id == target["input_id"])) is None
            assert await db.scalar(select(Message.id).where(Message.source_input_id == neighbor["input_id"])) is not None
    finally:
        await _cleanup_seed(postgres_sessions, [target, neighbor])


async def test_delete_test_session_rows_is_idempotent(postgres_sessions):
    """重复清理同一测试线程仍保持物理删除。"""
    target = await _seed_thread(postgres_sessions, thread_prefix="pytest-conv-idem")
    try:
        await delete_test_session_rows({target["thread_id"]})
        await delete_test_session_rows({target["thread_id"]})
        async with postgres_sessions() as db:
            assert await db.get(Session, target["session_record_id"]) is None
    finally:
        await _cleanup_seed(postgres_sessions, [target])


async def test_delete_test_session_rows_preserves_selectable_project(postgres_sessions):
    """删除最后一个 Session 时保留可选择的 Project。"""
    target = await _seed_thread(postgres_sessions, thread_prefix="pytest-selectable-project")
    try:
        async with postgres_sessions() as db:
            project = await db.get(Project, target["project_id"])
            project.selection_status = "selectable"
            project.name = "Test selectable project"
            await db.commit()
        await delete_test_session_rows({target["thread_id"]})
        async with postgres_sessions() as db:
            assert await db.get(Project, target["project_id"]) is not None
    finally:
        await _cleanup_seed(postgres_sessions, [target])


async def test_receipt_prefix_matching_treats_underscores_literally(postgres_sessions):
    """Receipt 幂等键前缀按字面匹配，不把下划线当 SQL 通配符。"""
    uid = f"pytest-prefix-user-{uuid.uuid4()}"
    valid_thread_id = f"pytest-prefix-valid-{uuid.uuid4()}"
    ordinary_thread_id = f"pytest-prefix-ordinary-{uuid.uuid4()}"
    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    receipt_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    try:
        async with postgres_sessions() as db:
            db.add(User(username=uid, uid=uid, password_hash="test"))
            await db.flush()
            for project_id, thread_id in zip(project_ids, (valid_thread_id, ordinary_thread_id), strict=True):
                db.add(
                    Project(
                        id=project_id,
                        uid=uid,
                        selection_status="implicit",
                        workdir_path=f"projects/{thread_id}",
                        directory_mode="managed",
                    )
                )
            db.add_all(
                [
                    Session(
                        thread_id=valid_thread_id,
                        uid=uid,
                        project_id=project_ids[0],
                        agent_id="main",
                        title="ordinary valid",
                    ),
                    Session(
                        thread_id=ordinary_thread_id,
                        uid=uid,
                        project_id=project_ids[1],
                        agent_id="main",
                        title="ordinary neighbor",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    AgentInputReceipt(
                        id=receipt_ids[0],
                        idempotency_key=f"YUXI_TEST_valid_{uuid.uuid4()}",
                        uid=uid,
                        app_id=None,
                        thread_id=valid_thread_id,
                        event_type="control",
                        intent_hash="test",
                    ),
                    AgentInputReceipt(
                        id=receipt_ids[1],
                        idempotency_key=f"YUXI-TEST-ordinary-{uuid.uuid4()}",
                        uid=uid,
                        app_id=None,
                        thread_id=ordinary_thread_id,
                        event_type="control",
                        intent_hash="test",
                    ),
                ]
            )
            await db.commit()
        resources = await list_test_session_resources(uid)
        assert valid_thread_id in resources
        assert ordinary_thread_id not in resources
    finally:
        await delete_test_session_rows({valid_thread_id, ordinary_thread_id})
        async with postgres_sessions() as db:
            await db.execute(delete(Project).where(Project.id.in_(project_ids)))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()


async def test_workdir_guard_rejects_ancestor_or_descendant_owner(postgres_sessions):
    """目标 Project Workdir 与非目标 Session 路径嵌套时必须拒绝。"""

    session_factory = postgres_sessions
    uid = f"pytest-workdir-user-{uuid.uuid4()}"
    target_thread_id = f"pytest-workdir-target-{uuid.uuid4()}"
    neighbor_thread_id = f"pytest-workdir-neighbor-{uuid.uuid4()}"
    target_project_id = str(uuid.uuid4())
    neighbor_project_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(username=uid, uid=uid, password_hash="test"))
        await db.flush()
        db.add_all(
            [
                Project(
                    id=target_project_id,
                    uid=uid,
                    selection_status="implicit",
                    workdir_path="projects/shared",
                    directory_mode="managed",
                ),
                Project(
                    id=neighbor_project_id,
                    uid=uid,
                    selection_status="implicit",
                    workdir_path="projects/shared/nested",
                    directory_mode="managed",
                ),
                Session(
                    thread_id=target_thread_id,
                    uid=uid,
                    project_id=target_project_id,
                    agent_id="main",
                    title="target",
                ),
                Session(
                    thread_id=neighbor_thread_id,
                    uid=uid,
                    project_id=neighbor_project_id,
                    agent_id="main",
                    title="neighbor",
                ),
            ]
        )
        await db.commit()
    try:
        with pytest.raises(RuntimeError, match="overlapping Workdir"):
            await validate_test_workdirs_exclusive(
                {(uid, "projects/shared"): {target_project_id}},
                {target_project_id},
            )
    finally:
        async with session_factory() as db:
            await db.execute(delete(Session).where(Session.thread_id.in_([target_thread_id, neighbor_thread_id])))
            await db.execute(delete(Project).where(Project.id.in_([target_project_id, neighbor_project_id])))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()


async def test_run_guard_rejects_nonterminal_run(postgres_sessions):
    """非终态 Run 存在时，清理 guard 必须拒绝后续破坏性动作。"""

    session_factory = postgres_sessions
    target = await _seed_thread(session_factory, thread_prefix="pytest-running-guard")
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, target["run_id"])
            assert run is not None
            run.status = "running"
            await db.commit()

        with pytest.raises(RuntimeError, match="not terminal"):
            await validate_test_runs_terminal({target["thread_id"]})
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_run_guard_waits_for_interrupted_history_runtime_cleanup(postgres_sessions):
    """恢复后的旧 interrupted 段只需等待 runtime owner 释放。"""
    session_factory = postgres_sessions
    target = await _seed_thread(session_factory, thread_prefix="pytest-resumed-guard")
    resume_id = str(uuid.uuid4())
    try:
        async with session_factory() as db:
            old_run = await db.get(AgentRun, target["run_id"])
            turn = await db.get(AgentTurn, target["turn_id"])
            old_run.status = "interrupted"
            old_run.runtime_cleanup_pending = True
            db.add(
                AgentRun(
                    id=resume_id,
                    thread_id=target["thread_id"],
                    runtime_scope_id=target["thread_id"],
                    agent_slug="main",
                    uid=target["uid"],
                    status="completed",
                    turn_id=target["turn_id"],
                    resume_from_run_id=target["run_id"],
                    input_payload={},
                )
            )
            turn.current_run_id = resume_id
            turn.result_run_id = resume_id
            await db.commit()

        async def release_runtime() -> None:
            """模拟中断段 owner 在 Turn 完成后释放运行时。"""
            await asyncio.sleep(0.3)
            async with session_factory() as db:
                old_run = await db.get(AgentRun, target["run_id"])
                old_run.runtime_cleanup_pending = False
                await db.commit()

        release = asyncio.create_task(release_runtime())
        try:
            await validate_test_runs_terminal({target["thread_id"]})
            async with session_factory() as db:
                old_run = await db.get(AgentRun, target["run_id"])
                assert old_run.runtime_cleanup_pending is False, "Turn 已完成仍须等待旧 Run 清理提交"
        finally:
            await release
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_run_guard_rejects_waiting_turn_with_interrupted_run(postgres_sessions):
    """尚在等待输入的 Turn 不能因为 Run 已 interrupted 而被清理。"""
    session_factory = postgres_sessions
    target = await _seed_thread(session_factory, thread_prefix="pytest-waiting-guard")
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, target["run_id"])
            turn = await db.get(AgentTurn, target["turn_id"])
            run.status = "interrupted"
            turn.status = "waiting"
            turn.result_run_id = None
            await db.commit()

        with pytest.raises(RuntimeError, match="Turn is not terminal"):
            await validate_test_runs_terminal({target["thread_id"]})
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_run_guard_rejects_unreleased_runtime_after_deadline(postgres_sessions, monkeypatch):
    """运行时长期未释放时，清理仍保持失败关闭。"""
    session_factory = postgres_sessions
    target = await _seed_thread(session_factory, thread_prefix="pytest-runtime-guard")
    monkeypatch.setattr(cleanup_module, "RUN_CLEANUP_WAIT_SECONDS", 0.1)
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, target["run_id"])
            run.runtime_cleanup_pending = True
            await db.commit()

        with pytest.raises(RuntimeError, match="runtime cleanup did not finish"):
            await validate_test_runs_terminal({target["thread_id"]})
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_run_guard_deadline_covers_blocked_database_query(postgres_sessions, monkeypatch):
    """真实表锁阻塞查询时，总期限仍需生效，锁由独立连接释放。"""
    session_factory = postgres_sessions
    target = await _seed_thread(session_factory, thread_prefix="pytest-query-guard")
    monkeypatch.setattr(cleanup_module, "RUN_CLEANUP_WAIT_SECONDS", 0.1)
    try:
        async with session_factory() as locker:
            await locker.execute(text("LOCK TABLE agent_runs IN ACCESS EXCLUSIVE MODE"))

            async def release_lock():
                """使缺少总期限的实现也能结束并暴露未抛错，而非挂死测试。"""
                await asyncio.sleep(0.4)
                await locker.rollback()

            release = asyncio.create_task(release_lock())
            try:
                with pytest.raises(RuntimeError, match="runtime cleanup did not finish"):
                    await validate_test_runs_terminal({target["thread_id"]})
            finally:
                await release
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_resource_cleanup_lock_makes_overlapping_linked_project_revalidate(postgres_sessions, monkeypatch):
    """绑定待清理子目录的 linked Project 必须在删除后重新校验。"""

    session_factory = postgres_sessions
    uid = f"pytest-lock-user-{uuid.uuid4()}"
    target_thread_id = f"pytest-lock-target-{uuid.uuid4()}"
    neighbor_thread_id = f"pytest-lock-neighbor-{uuid.uuid4()}"
    target_project_id = str(uuid.uuid4())
    neighbor_project_id = str(uuid.uuid4())
    workdir_path = f"projects/2026-01-01_00-00-00_{target_project_id[:8]}"
    linked_workdir_path = f"{workdir_path}/child"
    (user_workspace_dir(uid) / workdir_path).mkdir(parents=True)
    user_workdir_host_dir(uid, workdir_path).joinpath("child").mkdir()
    async with session_factory() as db:
        db.add(User(username=uid, uid=uid, password_hash="test"))
        await db.flush()
        db.add(
            Project(
                id=target_project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=workdir_path,
                directory_mode="managed",
            )
        )
        agent_session = Session(
            thread_id=target_thread_id,
            uid=uid,
            project_id=target_project_id,
            agent_id="main",
            title=make_test_session_title("workdir-lock"),
            status="deleted",
            extra_metadata=make_test_session_metadata("workdir-lock"),
        )
        db.add(agent_session)
        await db.commit()
        session_record_id = agent_session.id

    creation_result: dict[str, object] = {}
    lock_attempted = threading.Event()
    worker_holder: dict[str, threading.Thread] = {}
    original_lock = project_service._lock_project_workdir_changes
    original_remove = cleanup_module.remove_test_workdir

    async def observed_lock(**kwargs) -> None:
        lock_attempted.set()
        await original_lock(**kwargs)

    monkeypatch.setattr(project_service, "_lock_project_workdir_changes", observed_lock)

    def remove_while_project_waits(_uid: str, _workdir_path: str) -> None:
        """启动真实 Project 创建，并在其等待路径锁时删除目录。"""

        async def create_linked_project() -> None:
            engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
            creator_sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with creator_sessions() as db:
                    try:
                        await project_service.create_project_view(
                            uid=uid,
                            request_id=neighbor_project_id,
                            name="neighbor",
                            directory_mode="linked",
                            workdir_path=linked_workdir_path,
                            db=db,
                        )
                    except ResourceNotFoundError:
                        creation_result["outcome"] = "not_found"
                    else:
                        creation_result["outcome"] = "created"
            finally:
                await engine.dispose()

        worker = threading.Thread(target=lambda: asyncio.run(create_linked_project()))
        worker_holder["worker"] = worker
        worker.start()
        assert lock_attempted.wait(timeout=3)
        original_remove(_uid, _workdir_path)

    monkeypatch.setattr("test.live_api_cleanup.remove_test_workdir", remove_while_project_waits)

    try:
        await delete_test_session_resources(
            {(uid, workdir_path): {target_project_id}},
            {target_thread_id},
            {target_project_id},
        )
        worker_holder["worker"].join(timeout=5)
        assert not worker_holder["worker"].is_alive()
        assert creation_result == {"outcome": "not_found"}

        async with session_factory() as db:
            assert await db.get(Session, session_record_id) is None
            assert await db.get(Project, neighbor_project_id) is None
    finally:
        async with session_factory() as db:
            await db.execute(delete(Session).where(Session.thread_id.in_([target_thread_id, neighbor_thread_id])))
            await db.execute(delete(Project).where(Project.id.in_([target_project_id, neighbor_project_id])))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()


async def test_resource_cleanup_reports_file_failure_after_database_commit(postgres_sessions, monkeypatch):
    """文件清理失败必须显式报告，且不能恢复已提交的 Session 行。"""

    session_factory = postgres_sessions
    target = await _seed_thread(session_factory, thread_prefix="pytest-cleanup-file-failure")
    workdir_path = f"projects/YUXI_TEST_failure-{uuid.uuid4()}"
    async with session_factory() as db:
        agent_session = await db.get(Session, target["session_record_id"])
        project = await db.get(Project, target["project_id"])
        assert agent_session is not None and project is not None
        agent_session.status = "deleted"
        project.workdir_path = workdir_path
        await db.commit()

    def fail_file_cleanup(_uid: str, _workdir_path: str) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr("test.live_api_cleanup.remove_test_workdir", fail_file_cleanup)

    try:
        with pytest.raises(RuntimeError, match="rows were deleted, but filesystem cleanup failed"):
            await delete_test_session_resources(
                {(target["uid"], workdir_path): {target["project_id"]}},
                {target["thread_id"]},
                {target["project_id"]},
            )

        async with session_factory() as db:
            assert await db.get(Session, target["session_record_id"]) is None
    finally:
        await _cleanup_seed(session_factory, [target])
