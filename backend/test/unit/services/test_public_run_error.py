"""公开 Run 错误只保留有意文本，解释器转储不能进入聊天。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from yuxi.modules.agents.services.public_items import serialize_public_run
from yuxi.modules.agents.services import public_resources

pytestmark = pytest.mark.unit

PUBLIC_RUN_FAILURE = "执行未能完成，请稍后重试。"

INTERNAL = "Token var=<ContextVar name='input_messages' at 0x7f8b1c2a3e40> was created in a different Context"
TRACE = "Traceback (most recent call last):\n  File \"runner.py\", line 1, in <module>\n    raise RuntimeError('secret')"


@pytest.mark.parametrize(
    ("stored", "public"),
    [
        (INTERNAL, PUBLIC_RUN_FAILURE),
        (TRACE, PUBLIC_RUN_FAILURE),
        ("DETERMINISTIC_RATE_LIMIT exhausted", "DETERMINISTIC_RATE_LIMIT exhausted"),
        ("权限已撤销", "权限已撤销"),
        (None, None),
    ],
)
def test_public_run_error_hides_interpreter_dumps_only(stored, public):
    """对象地址和堆栈换成稳定说明，业务与提供者文本保持原样。"""
    assert public_resources.public_run_error_message(stored) == public


def test_failed_turn_and_run_projection_hide_historical_interpreter_dump():
    """历史失败行仍经公开投影发出，不能把内存地址交给主聊天。"""
    timestamp = datetime(2026, 11, 3, tzinfo=UTC)
    run = SimpleNamespace(
        id="run",
        execution_seq=1,
        turn_id="turn",
        thread_id="session",
        agent_slug="agent",
        run_type="chat",
        status="failed",
        created_by_run_id=None,
        resume_from_run_id=None,
        token_usage={},
        langfuse_trace_id=None,
        error_type="worker_error",
        error_message=INTERNAL,
        started_at=timestamp,
        prepared_at=None,
        first_model_request_at=None,
        first_output_at=None,
        finished_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )
    turn = SimpleNamespace(
        id="turn",
        thread_id="session",
        status="failed",
        created_at=timestamp,
        finished_at=timestamp,
        result_run_id="run",
        current_run_id="run",
    )
    assert serialize_public_run(run, [])["error_message"] == PUBLIC_RUN_FAILURE
    assert INTERNAL not in str(serialize_public_run(run, []))
    assert public_resources.turn_core(turn, run, started_at=timestamp)["error"]["message"] == PUBLIC_RUN_FAILURE
    run.error_message = "请联系管理员配置模型"
    assert serialize_public_run(run, [])["error_message"] == "请联系管理员配置模型"
