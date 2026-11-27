from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.messages import AIMessage, HumanMessage, ToolMessage

import yuxi.modules.agents.services.checkpoint_messages as message_svc
import yuxi.modules.agents.services.execution as svc
import yuxi.modules.agents.services.run_lifecycle as lifecycle
from test.unit.support.agent_context_fixtures import prepared_execution
from yuxi.modules.agents.runtime import context as agent_context


def test_approval_binding_uses_current_model_and_keeps_duplicate_call_order():
    """同名同参审批仍绑定各自真实调用，不接受缺少当前声明的动作。"""
    message = SimpleNamespace(
        extra_metadata={
            "tool_calls": [
                {"id": "first", "name": "execute", "args": {"command": "x"}},
                {"id": "second", "name": "execute", "args": {"command": "x"}},
            ]
        }
    )
    waitpoint = {
        "kind": "approval",
        "calls": [
            {"call_id": "approval-1", "name": "execute", "args": {"command": "x"}},
            {"call_id": "approval-2", "name": "execute", "args": {"command": "x"}},
        ],
    }
    bound = message_svc._bind_approval_calls(waitpoint, message)
    assert [call["tool_call_id"] for call in bound["calls"]] == ["first", "second"]
    assert "tool_call_id" not in waitpoint["calls"][0]
    with pytest.raises(ValueError, match="当前模型"):
        message_svc._bind_approval_calls(waitpoint, None)


async def _resolve_test_workdir(**_kwargs):
    """返回测试 Session 的 Project Workdir。"""

    return "projects/11111111-1111-4111-8111-111111111111"


class _EmptyModelAuditRepo:
    def __init__(self, _db):
        pass

    async def list_for_run(self, _run_id: str):
        return []


class _EmptyToolAuditRepo:
    def __init__(self, _db):
        pass

    async def list_for_run(self, _run_id: str):
        return []


@pytest.fixture(autouse=True)
def owning_turn_boundary(monkeypatch):
    """消息对账单测固定独立 Turn；事务并发由真实链路验证。"""

    class TurnRepo:
        def __init__(self, db):
            pass

        async def get_for_scope(self, **kwargs):
            return SimpleNamespace(
                id=kwargs["turn_id"],
                current_run_id="run-current" if kwargs["turn_id"] == "request-current" else "run-1",
                status="running",
                next_output_index=0,
            )

    class InputRepo:
        def __init__(self, db):
            pass

        async def get_pending_steer(self, **kwargs):
            return None

    monkeypatch.setattr(lifecycle, "SessionRepository", lambda db: db.message_repository)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", lambda db: message_svc.AgentRunRepository(db))
    monkeypatch.setattr(message_svc, "MessageRepository", lambda db: db.message_repository)
    monkeypatch.setattr(lifecycle, "AgentTurnRepository", TurnRepo)
    monkeypatch.setattr(lifecycle, "AgentInputRepository", InputRepo)

    async def settle(**kwargs):
        repo = message_svc.AgentRunRepository(kwargs["db"])
        run, changed = await repo.set_terminal_status(
            "run-1",
            status=kwargs["status"],
            worker_id=kwargs["worker_id"],
            token_usage=kwargs.get("token_usage"),
            error_type=kwargs.get("error_type"),
            error_message=kwargs.get("error_message"),
        )
        return SimpleNamespace(status=kwargs["status"], changed=changed, next_run_id=None)

    monkeypatch.setattr(message_svc, "settle_checkpoint", settle)


class _FakeDBBase:
    async def commit(self):
        """提供默认提交边界。"""

    async def rollback(self):
        """提供默认回滚边界。"""

    async def flush(self):
        """模拟仓储原语只 flush、顶层拥有提交。"""


class _FakeRunRepoBase:
    def __init__(self, _db):
        """接受事务对象，专项用例可覆盖身份校验。"""

    async def get_run(self, _run_id: str):
        """消息重建测试固定当前 Run 与 Turn 的输出归属。"""
        return SimpleNamespace(
            run_type="chat",
            id=_run_id,
            thread_id="thread-1",
            uid="user-1",
            app_id=None,
            turn_id="request-current" if _run_id == "run-current" else "turn-1",
            input_message_id=None,
        )

    async def lock_run_for_user(self, run_id, uid):
        """模拟锁后重读同一 Run。"""
        return await self.get_run(run_id)

    async def set_output_message(self, _run_id, _message_id, *, worker_id):
        """默认不记录输出绑定。"""

    async def cancel_active_execution_tree_descendants(self, _run):
        """默认没有需要取消的子 Run。"""
        return []


class _FakeConvRepo:
    def __init__(self, _db):
        self.db = _db
        _db.message_repository = self
        self.saved_messages: list[dict] = []
        self.sessions: dict[str, SimpleNamespace] = {}
        self.published_message_ids: list[int] = []

    def _session(self, thread_id: str) -> SimpleNamespace:
        return self.sessions.setdefault(
            thread_id,
            SimpleNamespace(
                id=1,
                uid="user-1",
                app_id=None,
                agent_id="test-agent",
                thread_id=thread_id,
                status="active",
                queue_paused=False,
                parent_thread_id=None,
                extra_metadata={},
            ),
        )

    async def add_message_by_thread_id(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
    ):
        self.saved_messages.append(
            {
                "thread_id": thread_id,
                "role": role,
                "content": content,
                "message_type": message_type,
                "extra_metadata": extra_metadata,
                "image_content": image_content,
                "run_id": run_id,
                "turn_id": turn_id,
            }
        )
        return SimpleNamespace(
            id=1,
            session_record_id=1,
            message_type=message_type,
            extra_metadata=extra_metadata or {},
        )

    async def get_session_by_thread_id(self, thread_id: str):
        return self._session(thread_id)

    async def lock_session_by_thread_id(self, thread_id: str):
        return self._session(thread_id)

    async def get_messages_by_thread_id(self, _thread_id: str):
        return []

    async def publish_assistant_output(self, message) -> None:
        message.message_type = "text"
        self.published_message_ids.append(message.id)

    async def create_session(self, *, uid: str, agent_id: str, thread_id: str, metadata: dict | None = None):
        agent_session = SimpleNamespace(
            id=1,
            uid=uid,
            agent_id=agent_id,
            thread_id=thread_id,
            status="active",
            extra_metadata=metadata or {},
        )
        self.sessions[thread_id] = agent_session
        return agent_session

    async def get_attachments_by_turn_id(self, session_record_id: int, turn_id: str):
        return []

    async def bind_attachments_to_request(self, session_record_id: int, turn_id: str, file_ids: list[str]):
        return []


@pytest.mark.asyncio
async def test_error_output_and_failed_run_commit_together(monkeypatch):
    """异常输出必须等 Run 失败状态写入后才提交。"""
    steps = []

    class FakeDB(_FakeDBBase):
        async def commit(self):
            steps.append("commit")

        async def rollback(self):
            steps.append("rollback")

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-1", run_type="chat", input_message_id=None)

        async def set_output_message(self, *_args, **_kwargs):
            steps.append("output")

        async def cancel_active_execution_tree_descendants(self, _run):
            return []

    async def settle_checkpoint(**kwargs):
        assert kwargs["status"] == "failed"
        steps.append("failed")
        return SimpleNamespace(changed=True)

    async def publish_cancel_signals(_ids):
        steps.append("published")

    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "settle_checkpoint", settle_checkpoint)
    repo = _FakeConvRepo(FakeDB())
    message = await message_svc.save_partial_message(
        repo.db,
        "thread-1",
        run_id="run-1",
        turn_id="turn-1",
        worker_id="worker-1",
        error_type="stream_error",
    )
    assert message.id == 1
    assert steps == ["output", "failed", "commit"]


@pytest.mark.asyncio
async def test_empty_final_checkpoint_cannot_complete_prior_error_output(monkeypatch):
    """当前执行无 AI 输出时，旧错误消息不能充当完成结果。"""

    class FakeDB(_FakeDBBase):
        committed = False
        rolled_back = False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-1", run_type="chat", output_message_id=99)

    async def forbidden_settlement(**_kwargs):
        pytest.fail("缺少当前 AI 输出时不能结算完成终态")

    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", _EmptyModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)
    monkeypatch.setattr(message_svc, "settle_checkpoint", forbidden_settlement)
    db = FakeDB()
    with pytest.raises(ValueError, match="最终 State AIMessage"):
        await message_svc.save_messages_from_langgraph_state(
            state=SimpleNamespace(values={"messages": []}),
            thread_id="thread-1",
            db=_FakeConvRepo(db).db,
            run_id="run-1",
            turn_id="turn-1",
            worker_id="worker-1",
            complete_run=True,
        )
    assert db.rolled_back and not db.committed


@pytest.mark.asyncio
async def test_steer_before_first_model_yields_without_ai_output(monkeypatch):
    """首次模型调用前让位允许空输出，提交后才投递接续段。"""
    run = SimpleNamespace(id="run-1", status="running", output_message_id=None)
    events = []

    class DB(_FakeDBBase):
        async def commit(self):
            events.append("commit")

        async def rollback(self):
            pytest.fail("合法的 steer 让位不应回滚")

    class RunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *args, **kwargs):
            return run

    class InputRepo:
        def __init__(self, db):
            pass

        async def get_pending_steer(self, **kwargs):
            return SimpleNamespace(id="S3", kind="steer")

        async def ready_batch(self, **kwargs):
            return [await self.get_pending_steer(**kwargs)]

    async def readable_ready_prefix(*, db, batch):
        """固定本用例的已就绪批次，真实文件由集成用例覆盖。"""
        return batch

    async def workdir_available(**_kwargs):
        """真实目录准入由 PostgreSQL 和文件系统集成用例覆盖。"""
        return True

    async def settle(**kwargs):
        """模拟已由 PG 专项验证的同事务接管结果。"""
        run.status = "yielded"
        return SimpleNamespace(status="yielded", changed=True, next_run_id="next-run")

    async def dispatch(run_id):
        assert run_id == "next-run" and events == ["commit", "cleanup"]
        events.append("dispatch")

    monkeypatch.setattr(message_svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(lifecycle, "session_workdir_is_available", workdir_available)
    monkeypatch.setattr(lifecycle, "readable_ready_prefix", readable_ready_prefix)
    monkeypatch.setattr(lifecycle, "AgentInputRepository", InputRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", _EmptyModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)
    monkeypatch.setattr(message_svc, "settle_checkpoint", settle)

    async def cleanup(target):
        """旧 Run 的资源清理必须发生在提交后、接续投递前。"""
        assert target.id == run.id and events == ["commit"]
        events.append("cleanup")

    monkeypatch.setattr(message_svc, "complete_run_runtime_cleanup", cleanup)
    monkeypatch.setattr(message_svc, "deliver", dispatch)
    repo = _FakeConvRepo(DB())
    status = await message_svc.save_messages_from_langgraph_state(
        state=SimpleNamespace(values={"messages": []}),
        thread_id="thread-1",
        db=repo.db,
        run_id="run-1",
        turn_id="turn-1",
        worker_id="owner",
        complete_run=True,
    )
    assert status == run.status == "yielded" and run.output_message_id is None
    assert repo.saved_messages == repo.published_message_ids == []
    assert events == ["commit", "cleanup", "dispatch"]


@pytest.mark.asyncio
async def test_checkpoint_without_lifecycle_does_not_create_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDB(_FakeDBBase):
        def __init__(self):
            self.commit_count = 0

        async def commit(self):
            self.commit_count += 1

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="question"), AIMessage(content="answer")]})

    fake_db = FakeDB()
    session_repo = _FakeConvRepo(fake_db)
    captured: dict[str, object] = {}

    class FakeRunRepo(_FakeRunRepoBase):
        def __init__(self, db):
            assert db is fake_db

        async def lock_output_persistence(
            self,
            run_id: str,
            *,
            worker_id: str,
            thread_id: str,
        ):
            captured["locked"] = (run_id, worker_id, thread_id)
            return object()

        async def set_output_message(self, run_id: str, message_id: int, *, worker_id: str):
            captured["run_id"] = run_id
            captured["message_id"] = message_id
            captured["worker_id"] = worker_id
            return object()

    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", _EmptyModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=session_repo.db,
        trace_info={"langfuse_trace_id": "trace-1"},
        run_id="run-1",
        turn_id="turn-1",
        worker_id="worker-1",
    )

    assert session_repo.saved_messages == []
    assert captured == {"locked": ("run-1", "worker-1", "thread-1")}
    assert fake_db.commit_count == 1


@pytest.mark.asyncio
async def test_checkpoint_cannot_create_missing_current_or_previous_run_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDB = _FakeDBBase

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        AIMessage(content="old intermediate", id="old-model-audit"),
                        AIMessage(content="current answer", id="current-final"),
                    ]
                }
            )

    captured: dict[str, int] = {}

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return object()

        async def set_output_message(self, _run_id, message_id, *, worker_id):
            captured[worker_id] = message_id

    fake_db = FakeDB()
    session_repo = _FakeConvRepo(fake_db)
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", _EmptyModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=session_repo.db,
        run_id="run-current",
        turn_id="request-current",
        worker_id="worker-current",
    )

    assert session_repo.saved_messages == []
    assert captured == {}


def test_tool_state_only_enriches_running_error_awaiting_terminal() -> None:
    running = SimpleNamespace(execution_status="running", extra_metadata={})
    awaiting_error = SimpleNamespace(
        execution_status="running",
        extra_metadata={"awaiting_run_terminal": True},
    )
    completed = SimpleNamespace(execution_status="completed", extra_metadata={})

    assert not message_svc._should_reconcile_tool_state(running, {"status": "success"})
    assert not message_svc._should_reconcile_tool_state(awaiting_error, {"status": "success"})
    assert message_svc._should_reconcile_tool_state(awaiting_error, {"status": "error"})
    assert not message_svc._should_reconcile_tool_state(completed, {"status": "success"})
    assert not message_svc._should_reconcile_tool_state(
        completed,
        {"status": "success", "content": "Tool result too large, saved in the filesystem"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("interrupt_run", "expected"), [(False, ["current error"]), (True, [])])
async def test_state_reconcile_uses_latest_error_unless_run_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
    interrupt_run: bool,
    expected: list[str],
) -> None:
    """只用最后一条错误补全审计，中断保留声明与独立执行事实。"""

    FakeDB = _FakeDBBase

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        ToolMessage(content="old error", tool_call_id="shared-call", status="error"),
                        ToolMessage(content="current error", tool_call_id="shared-call", status="error"),
                    ]
                }
            )

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-current", run_type="chat", session_record_id=1)

        async def set_terminal_status(self, *_args, **_kwargs):
            return SimpleNamespace(status="interrupted"), True

        async def cancel_active_execution_tree_descendants(self, _run):
            return []

    class FakeToolAuditRepo:
        def __init__(self, _db):
            pass

        async def list_for_run(self, _run_id):
            return [
                SimpleNamespace(
                    operation_id="shared-call",
                    execution_status="running",
                    extra_metadata={"awaiting_run_terminal": True},
                )
            ]

    reconciled: list[str] = []

    async def reconcile_tool(_session_repo, **kwargs):
        reconciled.append(kwargs["msg_dict"]["content"])

    fake_db = FakeDB()
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", _EmptyModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", FakeToolAuditRepo)
    monkeypatch.setattr(message_svc, "_reconcile_tool_error_from_state", reconcile_tool)

    await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=_FakeConvRepo(fake_db).db,
        run_id="run-current",
        turn_id="request-current",
        worker_id="worker-current",
        interrupt_run=interrupt_run,
    )

    assert reconciled == expected


@pytest.mark.asyncio
async def test_model_state_reconcile_uses_latest_message_when_operation_id_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨 Run 复用 Model operation_id 时不得补建同线程历史工具事实。"""

    audit = SimpleNamespace(
        id=11,
        operation_id="shared-model",
        content="",
        extra_metadata={},
        execution_status="running",
        message_type="model_audit",
    )

    FakeDB = _FakeDBBase

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        AIMessage(
                            content="historical",
                            id="shared-model",
                            tool_calls=[{"id": "old-call", "name": "search", "args": {}}],
                        ),
                        AIMessage(content="current", id="shared-model"),
                    ]
                }
            )

    class FakeModelAuditRepo:
        def __init__(self, _db):
            pass

        async def list_for_run(self, _run_id):
            return [audit]

        async def get(self, *, run_id, operation_id):
            assert (run_id, operation_id) == ("run-current", "shared-model")
            return audit

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-1", run_type="chat", input_message_id=None)

    session_repo = _FakeConvRepo(FakeDB())
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", FakeModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=session_repo.db,
        run_id="run-current",
        turn_id="request-current",
        worker_id="worker-current",
    )

    assert audit.content == "current"
    assert audit.extra_metadata["tool_calls"] == []
    assert session_repo.saved_messages == []


@pytest.mark.asyncio
async def test_completed_run_rejects_unmatched_final_state_message(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_message = SimpleNamespace(
        id=9,
        operation_id="known-intermediate",
        content="",
        extra_metadata={},
        execution_status="completed",
        message_type="model_audit",
        session_record_id=1,
    )

    FakeDB = _FakeDBBase

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        AIMessage(content="known", id="known-intermediate"),
                        AIMessage(content="unmatched final", id="missing-final"),
                    ]
                }
            )

    class FakeAuditRepo:
        def __init__(self, _db):
            pass

        async def list_for_run(self, _run_id):
            return [audit_message]

        async def get(self, *, run_id, operation_id):
            assert run_id == "run-1"
            return audit_message if operation_id == "known-intermediate" else None

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-1", run_type="chat", input_message_id=None)

    fake_db = FakeDB()
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", FakeAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    with pytest.raises(ValueError, match="最终 State AIMessage"):
        await message_svc.save_messages_from_langgraph_state(
            state=await FakeGraph().aget_state({}),
            thread_id="thread-1",
            db=_FakeConvRepo(fake_db).db,
            run_id="run-1",
            turn_id="turn-1",
            worker_id="worker-1",
            complete_run=True,
        )


@pytest.mark.asyncio
async def test_interrupted_run_does_not_bind_older_reconciled_model_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State 最后一条 AIMessage 未证明时不得绑定更早审计行。"""
    older_audit = SimpleNamespace(
        id=9,
        operation_id="known-older",
        content="",
        extra_metadata={},
        execution_status="completed",
        message_type="model_audit",
        session_record_id=1,
    )

    FakeDB = _FakeDBBase

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        AIMessage(content="older", id="known-older"),
                        AIMessage(content="unmatched interrupt", id="missing-current"),
                    ]
                }
            )

    class FakeAuditRepo:
        def __init__(self, _db):
            pass

        async def list_for_run(self, _run_id):
            return [older_audit]

        async def get(self, *, run_id, operation_id):
            assert run_id == "run-1"
            return older_audit if operation_id == "known-older" else None

    output_ids: list[int] = []

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-1", run_type="chat", input_message_id=None)

        async def set_output_message(self, _run_id, message_id, *, worker_id):
            output_ids.append(message_id)

        async def set_terminal_status(self, *_args, **_kwargs):
            return SimpleNamespace(status="interrupted"), True

        async def cancel_active_execution_tree_descendants(self, _run):
            return []

    fake_db = FakeDB()
    session_repo = _FakeConvRepo(fake_db)
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", FakeAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    committed = await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=session_repo.db,
        run_id="run-1",
        turn_id="turn-1",
        worker_id="worker-1",
        interrupt_run=True,
    )

    assert committed == "interrupted"
    assert output_ids == []
    assert session_repo.published_message_ids == []


@pytest.mark.asyncio
async def test_tool_call_interrupt_ignores_historical_same_id_tool_message(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_message = SimpleNamespace(
        id=12,
        operation_id="tool-model",
        content="",
        extra_metadata={},
        execution_status="completed",
        message_type="model_audit",
        session_record_id=1,
    )

    FakeDB = _FakeDBBase

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        ToolMessage(content="historical output", tool_call_id="call-1"),
                        AIMessage(
                            content="",
                            id="tool-model",
                            tool_calls=[{"id": "call-1", "name": "search", "args": {}}],
                        ),
                    ]
                }
            )

    class FakeAuditRepo:
        def __init__(self, _db):
            pass

        async def list_for_run(self, _run_id):
            return [audit_message]

        async def get(self, *, run_id, operation_id):
            return audit_message

    output_ids: list[int] = []

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            return SimpleNamespace(id="run-1", run_type="chat", input_message_id=None)

        async def set_output_message(self, _run_id, message_id, *, worker_id):
            output_ids.append(message_id)

        async def set_terminal_status(self, *_args, **_kwargs):
            return SimpleNamespace(status="interrupted"), True

        async def cancel_active_execution_tree_descendants(self, _run):
            return []

    fake_db = FakeDB()
    session_repo = _FakeConvRepo(fake_db)
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", FakeAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    committed = await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=session_repo.db,
        run_id="run-1",
        turn_id="turn-1",
        worker_id="worker-1",
        interrupt_run=True,
    )

    assert committed == "interrupted"
    assert output_ids == [audit_message.id]
    assert audit_message.message_type == "model_audit"
    assert audit_message.extra_metadata["state_reconciled"] is True
    assert session_repo.published_message_ids == []


@pytest.mark.asyncio
async def test_interrupt_without_model_does_not_invent_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple] = []

    class FakeDB(_FakeDBBase):
        async def commit(self):
            events.append(("commit",))

        async def rollback(self):
            events.append(("rollback",))

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [AIMessage(content="waiting")]})

    class FakeRunRepo(_FakeRunRepoBase):
        async def lock_output_persistence(self, *_args, **_kwargs):
            events.append(("lock",))
            return SimpleNamespace(id="run-1", run_type="chat", input_message_id=None)

        async def set_output_message(self, run_id, message_id, *, worker_id):
            events.append(("message", run_id, message_id, worker_id))

        async def set_terminal_status(self, run_id, **kwargs):
            events.append(("terminal", run_id, kwargs))
            return SimpleNamespace(status="interrupted"), True

        async def cancel_active_execution_tree_descendants(self, _run):
            events.append(("descendants",))
            return []

    fake_db = FakeDB()
    monkeypatch.setattr(message_svc, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(lifecycle, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", _EmptyModelAuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)

    terminal_committed = await message_svc.save_messages_from_langgraph_state(
        state=await FakeGraph().aget_state({}),
        thread_id="thread-1",
        db=_FakeConvRepo(fake_db).db,
        run_id="run-1",
        turn_id="turn-1",
        worker_id="worker-1",
        interrupt_run=True,
        interrupt_error_type="ask_user_question_required",
        interrupt_error_message="请选择",
    )

    assert terminal_committed == "interrupted"
    assert [event[0] for event in events] == ["lock", "terminal", "commit"]
    assert events[-2][2] == {
        "status": "interrupted",
        "error_type": "ask_user_question_required",
        "error_message": "请选择",
        "token_usage": {"available": False},
        "worker_id": "worker-1",
    }


@pytest.mark.parametrize(
    ("agent_session", "requested_agent", "error"),
    [
        (None, "worker", "对话线程不存在"),
        (SimpleNamespace(uid="user-1", agent_id="worker", status="deleted"), "worker", "对话线程不存在"),
        (SimpleNamespace(uid="user-1", agent_id="worker", status="archived"), "worker", "对话线程不存在"),
        (SimpleNamespace(uid="other-user", agent_id="worker", status="active"), "worker", "对话线程不存在"),
        (SimpleNamespace(uid="user-1", agent_id="worker", status="active"), "other-agent", "不能切换"),
    ],
)
async def test_execution_requires_existing_authorized_session(monkeypatch, agent_session, requested_agent, error):
    """缺失或越权线程不能在执行层补建，也不能借已有快照绕过绑定校验。"""
    from unittest.mock import AsyncMock

    repository = SimpleNamespace(get_session_by_thread_id=AsyncMock(return_value=agent_session))
    monkeypatch.setattr(svc, "SessionRepository", lambda _db: repository)
    with pytest.raises(ValueError, match=error):
        await svc._resolve_agent_runtime(
            db=object(),
            user=SimpleNamespace(uid="user-1"),
            requested_agent_slug=requested_agent,
            thread_id="thread-1",
            prepared_execution=prepared_execution(),
        )


async def test_execution_rejects_archived_cooperation_session(monkeypatch):
    """Project 归档后的子线程不能在 worker 中继续执行。"""
    from unittest.mock import AsyncMock

    repository = SimpleNamespace(
        get_session_by_thread_id=AsyncMock(return_value=SimpleNamespace(uid="user-1", agent_id="worker", status="archived"))
    )
    monkeypatch.setattr(svc, "SessionRepository", lambda _db: repository)
    with pytest.raises(ValueError, match="对话线程不存在"):
        await svc._resolve_agent_runtime(
            db=object(),
            user=SimpleNamespace(uid="user-1"),
            requested_agent_slug="worker",
            thread_id="child-thread",
            prepared_execution=prepared_execution(backend_id="ChatbotAgent"),
        )


@pytest.mark.parametrize(
    ("snapshot", "error", "message"),
    [
        ({}, AttributeError, "context|backend_id"),
        (prepared_execution(backend_id="changed"), ValueError, "后端在执行准备后发生变化"),
    ],
)
async def test_execution_does_not_rebuild_missing_snapshot_context(monkeypatch, snapshot, error, message):
    """错误快照显式失败，不回退到数据库配置。"""
    from unittest.mock import AsyncMock

    agent_session = SimpleNamespace(uid="user-1", agent_id="worker", status="active")
    agent = SimpleNamespace(slug="worker", backend_id="ChatbotAgent", config_json={"context": {"system_prompt": "new"}})
    monkeypatch.setattr(
        svc,
        "SessionRepository",
        lambda _db: SimpleNamespace(get_session_by_thread_id=AsyncMock(return_value=agent_session)),
    )
    monkeypatch.setattr(svc, "AgentRepository", lambda _db: SimpleNamespace(get_visible_by_slug=AsyncMock(return_value=agent)))
    monkeypatch.setattr(svc, "get_agent_backend", lambda _backend: object())
    monkeypatch.setattr(svc, "resolve_session_workdir_path", _resolve_test_workdir)
    monkeypatch.setattr(agent_context, "normalize_agent_context_config", AsyncMock(side_effect=AssertionError("不得重新解析配置")))
    with pytest.raises(error, match=message):
        await svc._resolve_agent_runtime(
            db=object(),
            user=SimpleNamespace(uid="user-1"),
            requested_agent_slug="worker",
            thread_id="thread-1",
            prepared_execution=snapshot,
        )


@pytest.mark.asyncio
async def test_malformed_final_call_cannot_complete(monkeypatch):
    """最终畸形调用没有执行，结算必须回滚且不能发布成功。"""
    invalid = AIMessage(
        id="bad-final", content="", invalid_tool_calls=[{"id": "bad", "name": "write_file", "args": '{"path":', "error": "invalid JSON"}]
    )
    db = _FakeDBBase()
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    repo = _FakeConvRepo(db)
    audit = SimpleNamespace(
        id=42, operation_id="bad-final", content="", extra_metadata={}, execution_status="completed", message_type="model_audit"
    )

    class AuditRepo(_EmptyModelAuditRepo):
        """提供当前模型生命周期事实。"""

        async def list_for_run(self, _run_id):
            """返回当前 Run 审计。"""
            return [audit]

        async def get(self, **_kwargs):
            """返回同一模型消息。"""
            return audit

    class RunRepo(_FakeRunRepoBase):
        """允许旧结算逻辑完成，隔离目标缺陷。"""

        async def lock_output_persistence(self, *_args, **_kwargs):
            """保留有效执行 Owner。"""
            return SimpleNamespace(id="run-1")

        async def set_terminal_status(self, *_args, **_kwargs):
            """模拟合法的完成事务。"""
            return SimpleNamespace(status="completed"), True

    monkeypatch.setattr(message_svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(message_svc, "ModelMessageAuditRepository", AuditRepo)
    monkeypatch.setattr(message_svc, "ToolMessageAuditRepository", _EmptyToolAuditRepo)
    with pytest.raises(ValueError, match="工具调用参数无效.*write_file"):
        await message_svc.save_messages_from_langgraph_state(
            state=SimpleNamespace(values={"messages": [invalid]}),
            thread_id="thread-1",
            db=db,
            run_id="run-1",
            turn_id="turn-1",
            worker_id="worker-1",
            complete_run=True,
        )
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    assert repo.published_message_ids == []
