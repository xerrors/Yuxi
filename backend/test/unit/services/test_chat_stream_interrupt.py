"""测试 chat_service 中的 interrupt 相关函数"""

import json
import sys
import os
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.getcwd())

from yuxi.services.chat_service import (
    _normalize_interrupt_questions,
    _build_ask_user_question_payload,
    _coerce_interrupt_payload,
    stream_agent_resume,
)
from yuxi.services import chat_service as svc
from yuxi.utils.question_utils import normalize_options


class _FakeSession:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


class TestNormalizeInterruptOptions:
    """测试 _normalize_interrupt_options 函数"""

    def test_empty_input(self):
        assert normalize_options(None) == []
        assert normalize_options([]) == []

    def test_dict_options(self):
        raw = [
            {"label": "选项1", "value": "option1"},
            {"label": "选项2", "value": "option2"},
        ]
        result = normalize_options(raw)
        assert len(result) == 2
        assert result[0] == {"label": "选项1", "value": "option1"}
        assert result[1] == {"label": "选项2", "value": "option2"}

    def test_string_options(self):
        raw = ["选项1", "选项2", "选项3"]
        result = normalize_options(raw)
        assert len(result) == 3
        assert result[0] == {"label": "选项1", "value": "选项1"}

    def test_mixed_options(self):
        raw = [{"label": "选项1", "value": "option1"}, "选项2"]
        result = normalize_options(raw)
        assert len(result) == 2
        assert result[0] == {"label": "选项1", "value": "option1"}
        assert result[1] == {"label": "选项2", "value": "选项2"}

    def test_invalid_options(self):
        raw = [{"label": "只有label"}, {}, "  "]
        result = normalize_options(raw)
        assert len(result) == 1  # 只有有效的选项
        assert result[0] == {"label": "只有label", "value": "只有label"}

    def test_value_only(self):
        raw = [{"value": "only_value"}]
        result = normalize_options(raw)
        assert len(result) == 1
        assert result[0] == {"label": "only_value", "value": "only_value"}


class TestBuildAskUserQuestionPayload:
    """测试 _build_ask_user_question_payload 函数"""

    def test_basic_questions(self):
        info = {
            "questions": [
                {
                    "question": "请确认是否继续？",
                    "options": [
                        {"label": "确认", "value": "yes"},
                        {"label": "取消", "value": "no"},
                    ],
                }
            ],
        }
        result = _build_ask_user_question_payload(info, "thread-123")

        assert len(result["questions"]) == 1
        assert result["questions"][0]["question"] == "请确认是否继续？"
        assert len(result["questions"][0]["options"]) == 2
        assert result["questions"][0]["options"][0] == {"label": "确认", "value": "yes"}
        assert result["questions"][0]["options"][1] == {"label": "取消", "value": "no"}
        assert result["source"] == "interrupt"
        assert result["thread_id"] == "thread-123"

    def test_questions_with_source(self):
        info = {
            "questions": [{"question": "选择一个选项", "options": ["A", "B", "C"]}],
            "source": "ask_user_question",
        }
        result = _build_ask_user_question_payload(info, "thread-456")

        assert result["source"] == "ask_user_question"
        assert len(result["questions"][0]["options"]) == 3

    def test_multi_select(self):
        info = {
            "questions": [
                {
                    "question": "选择多个",
                    "options": ["A", "B", "C"],
                    "multi_select": True,
                }
            ],
        }
        result = _build_ask_user_question_payload(info, "thread-789")

        assert result["questions"][0]["multi_select"] is True

    def test_disable_allow_other(self):
        info = {
            "questions": [{"question": "只能选择", "options": ["A", "B"], "allow_other": False}],
        }
        result = _build_ask_user_question_payload(info, "thread-000")

        assert result["questions"][0]["allow_other"] is False

    def test_with_operation(self):
        info = {
            "questions": [
                {
                    "question": "是否执行操作？",
                    "operation": "删除文件",
                    "options": [
                        {"label": "批准", "value": "approve"},
                        {"label": "拒绝", "value": "reject"},
                    ],
                }
            ],
        }
        result = _build_ask_user_question_payload(info, "thread-op")

        assert result["questions"][0]["operation"] == "删除文件"

    def test_default_question_when_questions_missing(self):
        info = {}
        result = _build_ask_user_question_payload(info, "thread-no-opt")

        assert len(result["questions"]) == 1
        assert result["questions"][0]["question"] == "请选择一个选项"
        assert result["questions"][0]["options"] == []
        assert result["source"] == "interrupt"

    def test_question_id_generation(self):
        """测试 question_id 自动生成"""
        info = {"questions": [{"question": "测试？"}]}
        result = _build_ask_user_question_payload(info, "thread-id")

        assert result["questions"][0]["question_id"] != ""
        assert len(result["questions"][0]["question_id"]) > 0


class TestNormalizeInterruptQuestions:
    """测试 _normalize_interrupt_questions 函数"""

    def test_empty_input(self):
        assert _normalize_interrupt_questions(None) == []
        assert _normalize_interrupt_questions([]) == []

    def test_normalize_basic_question(self):
        raw = [{"question": "Q1", "options": ["A", "B"]}]
        result = _normalize_interrupt_questions(raw)

        assert len(result) == 1
        assert result[0]["question"] == "Q1"
        assert result[0]["options"][0] == {"label": "A", "value": "A"}
        assert result[0]["multi_select"] is False
        assert result[0]["allow_other"] is True

    def test_invalid_question_filtered(self):
        raw = [{"question": "  "}, "Q2", {"question": "有效问题"}]
        result = _normalize_interrupt_questions(raw)

        assert len(result) == 1
        assert result[0]["question"] == "有效问题"


@pytest.mark.asyncio
async def test_stream_agent_resume_init_does_not_render_resume_input():
    stream = stream_agent_resume(
        thread_id="thread-1",
        resume_input={"language": "python"},
        meta={"request_id": "req-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    first_chunk = json.loads((await stream.__anext__()).decode("utf-8"))
    await stream.aclose()

    assert first_chunk["status"] == "init"
    assert "msg" not in first_chunk
    assert "Resume with input" not in json.dumps(first_chunk, ensure_ascii=False)


@pytest.mark.asyncio
async def test_stream_agent_resume_commits_before_stream_and_routes_subagent_chunks(monkeypatch):
    db = _FakeSession()

    class FakeContext:
        def __init__(self):
            self.thread_id = None
            self.uid = None

        def update(self, values):
            for key, value in values.items():
                setattr(self, key, value)

        def model_dump(self):
            return {"thread_id": self.thread_id, "uid": self.uid}

    class FakeAgent:
        context_schema = FakeContext

        async def stream_resume_with_state(self, resume_command, input_context=None, **kwargs):
            assert db.commit_count == 1
            yield (
                "messages",
                (
                    {"content": "child token", "id": "msg-child"},
                    {"namespace": ["task:1"], "thread_id": "child-thread"},
                ),
            )

        async def get_graph(self, context=None):
            class FakeGraph:
                async def aget_state(self, _config):
                    return SimpleNamespace(values={})

            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="main-agent", backend_id="ChatbotAgent"), FakeAgent(), {}

    async def fake_save_messages_from_langgraph_state(**_kwargs):
        return None

    async def fake_check_and_handle_interrupts(*_args, **_kwargs):
        if False:
            yield None

    async def fake_build_agent_input_context(*_args, **_kwargs):
        return {"thread_id": "parent-thread", "uid": "user-1"}

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "build_agent_input_context", fake_build_agent_input_context)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **_kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[]),
    )
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_check_and_handle_interrupts)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc, "ConversationRepository", lambda _db: object())
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)

    stream = stream_agent_resume(
        thread_id="parent-thread",
        resume_input={"ok": True},
        meta={"request_id": "req-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=db,
    )

    chunks = []
    loading = None
    async for raw in stream:
        chunk = json.loads(raw.decode("utf-8"))
        chunks.append(chunk)
        if chunk.get("status") == "loading":
            loading = chunk
        if chunk.get("status") == "finished":
            break
    await stream.aclose()

    assert loading is not None
    assert loading["thread_id"] == "child-thread"
    assert loading["response"] == "child token"
    assert loading["stream_event"]["thread_id"] == "child-thread"
    finished = chunks[-1]
    assert finished["status"] == "finished"
    assert finished["meta"]["agent_slug"] == "main-agent"
    assert "agent_id" not in finished["meta"]


class TestCoerceInterruptPayload:
    """测试 _coerce_interrupt_payload 函数"""

    def test_dict_input(self):
        info = {"question": "test?", "options": ["a", "b"]}
        result = _coerce_interrupt_payload(info)
        assert result == info

    def test_string_input(self):
        info = "just a string"
        result = _coerce_interrupt_payload(info)
        assert isinstance(result, dict)

    def test_none_input(self):
        result = _coerce_interrupt_payload(None)
        assert isinstance(result, dict)
