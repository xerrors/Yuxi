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
    _build_human_approval_payload,
    _is_human_approval_payload,
    _coerce_interrupt_payload,
    check_and_handle_interrupts,
    stream_agent_resume,
)
from yuxi.services import chat_service as svc
from yuxi.services import run_worker
from yuxi.utils.question_utils import normalize_options


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
async def test_stream_agent_resume_routes_subagent_chunks(monkeypatch):
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
        db=object(),
    )

    chunks = []
    async for raw in stream:
        chunk = json.loads(raw.decode("utf-8"))
        chunks.append(chunk)
        if chunk.get("status") == "loading":
            break
    await stream.aclose()

    loading = chunks[-1]
    assert loading["thread_id"] == "child-thread"
    assert loading["response"] == "child token"
    assert loading["stream_event"]["thread_id"] == "child-thread"


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


class TestHumanApprovalPayloadDetection:
    """测试 HIL 工具审批 interrupt 的判别与载荷构建。"""

    def test_is_human_approval_payload_true_for_action_requests(self):
        payload = {"action_requests": [{"name": "delete_file", "args": {"path": "/x"}}], "review_configs": []}
        assert _is_human_approval_payload(payload) is True

    def test_is_human_approval_payload_false_for_questions(self):
        assert _is_human_approval_payload({"questions": [{"question": "Q"}]}) is False

    def test_is_human_approval_payload_false_for_empty(self):
        assert _is_human_approval_payload({}) is False
        assert _is_human_approval_payload({"action_requests": []}) is False

    def test_build_human_approval_payload_preserves_action_requests(self):
        info = {
            "action_requests": [{"name": "send_email", "args": {"to": "a@b.com"}, "description": "需要确认"}],
            "review_configs": [{"action_name": "send_email", "allowed_decisions": ["approve", "reject"]}],
        }
        result = _build_human_approval_payload(info, "thread-hil-1")

        assert result["source"] == "human_approval"
        assert result["thread_id"] == "thread-hil-1"
        assert len(result["action_requests"]) == 1
        assert result["action_requests"][0]["name"] == "send_email"
        assert result["action_requests"][0]["description"] == "需要确认"
        assert result["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]

    def test_build_human_approval_payload_fills_missing_description(self):
        """HIL payload 缺 description 时补默认提示,便于前端展示。"""
        info = {"action_requests": [{"name": "delete_file", "args": {"path": "/tmp/x"}}], "review_configs": []}
        result = _build_human_approval_payload(info, "thread-hil-2")

        desc = result["action_requests"][0]["description"]
        assert "delete_file" in desc
        assert "/tmp/x" in desc


class TestCheckAndHandleInterruptsRouting:
    """测试 check_and_handle_interrupts 按载荷类型分流到 ask_user_question / human_approval。"""

    @pytest.mark.asyncio
    async def test_routes_human_approval_interrupt_to_human_approval_required(self):
        """HIL interrupt(含 action_requests)应发 human_approval_required,不再塞默认空问题。"""
        hil_value = {
            "action_requests": [{"name": "delete_file", "args": {"path": "/x"}}],
            "review_configs": [{"action_name": "delete_file", "allowed_decisions": ["approve", "reject"]}],
        }

        class FakeGraph:
            async def aget_state(self, _config):
                return SimpleNamespace(
                    values={"__interrupt__": [SimpleNamespace(value=hil_value)]},
                    tasks=[],
                )

        class FakeAgent:
            async def get_graph(self):
                return FakeGraph()

        chunks: list[dict] = []

        def make_chunk(status, meta, **kwargs):
            return json.dumps({"status": status, "meta": meta, **kwargs}, ensure_ascii=False).encode("utf-8")

        stream = check_and_handle_interrupts(FakeAgent(), {"configurable": {"thread_id": "t"}}, make_chunk, {}, "t")
        async for raw in stream:
            chunks.append(json.loads(raw.decode("utf-8")))

        assert len(chunks) == 1
        assert chunks[0]["status"] == "human_approval_required"
        assert "action_requests" in chunks[0]
        assert chunks[0]["action_requests"][0]["name"] == "delete_file"
        assert "review_configs" in chunks[0]
        # 不应再出现被误塞的默认空问题
        assert "questions" not in chunks[0]

    @pytest.mark.asyncio
    async def test_routes_ask_user_question_interrupt_unchanged(self):
        """ask_user_question interrupt(含 questions)仍走原有 ask_user_question_required 通道。"""
        info_value = {"questions": [{"question": "选择一个", "options": ["A", "B"]}]}

        class FakeGraph:
            async def aget_state(self, _config):
                return SimpleNamespace(
                    values={"__interrupt__": [SimpleNamespace(value=info_value)]},
                    tasks=[],
                )

        class FakeAgent:
            async def get_graph(self):
                return FakeGraph()

        chunks: list[dict] = []

        def make_chunk(status, meta, **kwargs):
            return json.dumps({"status": status, "meta": meta, **kwargs}, ensure_ascii=False).encode("utf-8")

        stream = check_and_handle_interrupts(FakeAgent(), {"configurable": {"thread_id": "t"}}, make_chunk, {}, "t")
        async for raw in stream:
            chunks.append(json.loads(raw.decode("utf-8")))

        assert len(chunks) == 1
        assert chunks[0]["status"] == "ask_user_question_required"
        assert chunks[0]["questions"][0]["question"] == "选择一个"


class TestInterruptSummary:
    """测试 run_worker._interrupt_summary 兼容两类 interrupt 载荷。"""

    def test_summary_from_questions(self):
        chunk = {"questions": [{"question": "是否继续？"}]}
        assert run_worker._interrupt_summary(chunk) == "是否继续？"

    def test_summary_from_action_requests_with_args(self):
        chunk = {"action_requests": [{"name": "delete_file", "args": {"path": "/x"}}]}
        summary = run_worker._interrupt_summary(chunk)
        assert "delete_file" in summary
        assert "/x" in summary

    def test_summary_from_action_requests_without_args(self):
        chunk = {"action_requests": [{"name": "send_email", "args": {}}]}
        assert run_worker._interrupt_summary(chunk) == "操作需要确认: send_email"

    def test_summary_empty_when_no_payload(self):
        assert run_worker._interrupt_summary({}) == ""
        assert run_worker._interrupt_summary(None) == ""
