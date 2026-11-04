"""生命周期服务的纯逻辑边界；持久因果另由真实 PG/worker 测试证明。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.agents.services.subscriptions import _Cursor, _parse_cursor
from yuxi.modules.agents.services.scheduler import Dispatch, deliver
from yuxi.modules.agents.services.turns import _summarize_turn_usage, _validate_resume_response
from yuxi.shared.errors import DomainError, InvalidInputError


def test_thread_cursor_round_trip_across_receipt_run_and_redis_positions():
    """接收序号、Run 序号和 Redis 位置能独立恢复。"""
    cursor = _Cursor(receipt_seq=31, run_seq=12, event_seq="172-4", run_phase=1)
    assert _parse_cursor(cursor.encode()) == cursor
    with pytest.raises(DomainError) as error:
        _parse_cursor("12-0")
    assert isinstance(error.value, InvalidInputError)


def test_turn_usage_counts_only_completed_audits_with_real_token_numbers():
    """未知/失败的模型审计不能推算或计入 Turn 用量。"""
    audits = [
        SimpleNamespace(
            operation_id="model-1",
            execution_status="completed",
            usage={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
        ),
        SimpleNamespace(
            operation_id="model-2",
            execution_status="failed",
            usage={"input_tokens": 99, "output_tokens": 99, "total_tokens": 198},
        ),
        SimpleNamespace(operation_id=None, execution_status="completed", usage={"total_tokens": 50}),
    ]
    assert _summarize_turn_usage(audits) == {
        "available": True,
        "complete": False,
        "operations": 1,
        "missing_operations": 2,
        "input_tokens": 8,
        "output_tokens": 4,
        "total_tokens": 12,
    }


def test_waitpoint_rejects_partial_or_reordered_answers():
    """恢复命令必须与等待点问题顺序和数量完全一致。"""
    waitpoint = {"kind": "answer", "questions": [{"question_id": "q-1"}, {"question_id": "q-2"}]}
    for answers in (
        [{"question_id": "q-1", "answer": "yes"}],
        [{"question_id": "q-2", "answer": "later"}, {"question_id": "q-1", "answer": "yes"}],
    ):
        with pytest.raises(DomainError) as error:
            _validate_resume_response(waitpoint, {"type": "answer", "answers": answers})
        assert isinstance(error.value, InvalidInputError)


def test_waitpoint_rejects_unlisted_approval_decision():
    """审批只能回答等待点中同序的全部 call_id 和允许的决定。"""
    waitpoint = {
        "kind": "approval",
        "calls": [
            {"call_id": "call-1", "allowed_decisions": ["approve", "reject"]},
            {"call_id": "call-2", "allowed_decisions": ["approve", "reject"]},
        ],
    }
    with pytest.raises(DomainError) as error:
        _validate_resume_response(
            waitpoint,
            {
                "type": "approval",
                "decisions": [
                    {"call_id": "call-1", "decision": "approve"},
                    {"call_id": "call-2", "decision": "skip"},
                ],
            },
        )
    assert isinstance(error.value, InvalidInputError)


@pytest.mark.asyncio
async def test_dispatch_materializes_bound_workdir_before_publishing(monkeypatch):
    """Run 已由事务创建后，目录物化先于同一个 Run 的队列投递。"""
    calls = []
    monkeypatch.setattr(
        "yuxi.modules.agents.services.scheduler.ensure_bound_user_workdir",
        lambda uid, path: calls.append(("workdir", uid, path)),
    )

    async def enqueue(run_id):
        calls.append(("publish", run_id))

    monkeypatch.setattr("yuxi.modules.agents.services.transport.enqueue_agent_run", enqueue)
    binding = SimpleNamespace(materialize_managed=True, uid="user-1", workdir_path="projects/p-1")
    await deliver(Dispatch(run_id="run-1", binding=binding))
    assert calls == [("workdir", "user-1", "projects/p-1"), ("publish", "run-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_mode", ["default", "always_trust"])
async def test_waitpoint_cleanup_preserves_run_approval_mode(monkeypatch, approval_mode):
    """取消清理从 Run 快照恢复审批模式，不能回退到智能体默认值。"""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.context import BaseContext
    from yuxi.modules.agents.services import run_lifecycle

    @asynccontextmanager
    async def session_context():
        """隔离数据库，只检查清理图的装配输入。"""
        yield object()

    seen = []
    graph = SimpleNamespace(cleared=False)

    async def get_graph(*, context, checkpoint_only):
        """记录真正传给清理图的运行上下文。"""
        assert checkpoint_only
        seen.append(context)
        return graph

    async def cancel_checkpoint(actual_graph, config):
        """清理结果仅来自当前 Run 对应的图与线程。"""
        assert actual_graph is graph
        assert config == {"configurable": {"uid": "user-1", "thread_id": "thread-1"}}
        actual_graph.cleared = True

    monkeypatch.setattr(run_lifecycle, "pg_manager", SimpleNamespace(get_async_session_context=session_context))
    monkeypatch.setattr(
        "yuxi.modules.agents.repositories.definitions.AgentRepository",
        lambda db: SimpleNamespace(get_by_slug=AsyncMock(return_value=SimpleNamespace(backend_id="ChatbotAgent"))),
    )
    monkeypatch.setattr(
        "yuxi.modules.agents.runtime.agent_backends.get_agent_backend",
        lambda backend_id: SimpleNamespace(context_schema=BaseContext, get_graph=get_graph),
    )
    monkeypatch.setattr(run_lifecycle, "require_thread", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        "yuxi.modules.workspace.services.bindings.resolve_session_workdir_binding",
        AsyncMock(return_value=SimpleNamespace(workdir_path="projects/p-1")),
    )
    monkeypatch.setattr("yuxi.modules.agents.runtime.checkpoint_cleanup.cancel_waitpoint_checkpoint", cancel_checkpoint)
    run = SimpleNamespace(
        id="run-1",
        uid="user-1",
        thread_id="thread-1",
        agent_slug="agent-1",
        app_id=None,
        runtime_scope_id="thread-1",
        input_payload={"context_snapshot": {"model": "test:model", "tool_approval_mode": approval_mode}},
    )

    await run_lifecycle._clear_waitpoint_checkpoint(run)

    assert graph.cleared
    assert seen[0].tool_approval_mode == approval_mode
    assert seen[0].run_id == "run-1"
