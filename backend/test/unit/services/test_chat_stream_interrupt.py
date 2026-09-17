"""测试 chat_service 中的 interrupt 相关函数"""

import json
from types import SimpleNamespace

import pytest

from yuxi.services.chat_service import (
    _build_ask_user_question_payload,
    _build_tool_approval_payload,
    _normalize_interrupt_questions,
    stream_agent_resume,
)
from test.unit.agent_context_fixtures import prepared_execution
from yuxi.services import chat_service as svc
from yuxi.utils.question_utils import normalize_options


class _FakeSession:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


async def _resolve_test_workdir(**_kwargs):
    """返回测试 Conversation 的 Project Workdir。"""

    return "projects/11111111-1111-4111-8111-111111111111"


def test_build_tool_approval_payload_rejects_mismatched_lists():
    assert _build_tool_approval_payload({"action_requests": [{}], "review_configs": []}, "thread-1") is None


class TestNormalizeInterruptOptions:
    """测试 _normalize_interrupt_options 函数"""

    def test_empty_input(self):
        assert normalize_options(None) == []
        assert normalize_options([]) == []

    def test_deeply_nested_json_is_rejected(self):
        raw = "[" * 10_000 + "0" + "]" * 10_000
        assert normalize_options(raw) == []

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                [{"label": "选项1", "value": "option1"}, {"label": "选项2", "value": "option2"}],
                [{"label": "选项1", "value": "option1"}, {"label": "选项2", "value": "option2"}],
            ),
            (
                ["选项1", "选项2", "选项3"],
                [
                    {"label": "选项1", "value": "选项1"},
                    {"label": "选项2", "value": "选项2"},
                    {"label": "选项3", "value": "选项3"},
                ],
            ),
            (
                [{"label": "选项1", "value": "option1"}, "选项2"],
                [{"label": "选项1", "value": "option1"}, {"label": "选项2", "value": "选项2"}],
            ),
        ],
    )
    def test_options_normalized(self, raw, expected):
        assert normalize_options(raw) == expected

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

    def test_wrapper_item_dict(self):
        raw = {
            "item": [
                {"label": "选项1 (Recommended)", "value": "v1", "description": "描述1"},
                {"label": "选项2", "value": "v2", "description": "描述2"},
            ]
        }
        result = normalize_options(raw)
        assert len(result) == 2
        assert result[0] == {"label": "选项1 (Recommended)", "value": "v1", "description": "描述1"}
        assert result[1] == {"label": "选项2", "value": "v2", "description": "描述2"}

    def test_string_bool_questions_normalization(self):
        info = {
            "questions": [
                {
                    "question": "本次调研分析的最终落点是什么？",
                    "options": {
                        "item": [
                            {"label": "建议", "value": "strategy", "description": "战略描述"},
                        ]
                    },
                    "multi_select": "false",
                    "allow_other": "false",
                    "question_id": "final_deliverable",
                }
            ]
        }
        result = _build_ask_user_question_payload(info, "thread-123")
        assert len(result["questions"]) == 1
        q = result["questions"][0]
        assert q["question_id"] == "final_deliverable"
        assert q["multi_select"] is False
        assert q["allow_other"] is False
        assert q["options"] == [{"label": "建议", "value": "strategy", "description": "战略描述"}]


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

    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            ({"multi_select": True}, {"multi_select": True}),
            ({"allow_other": False}, {"allow_other": False}),
            ({"operation": "删除文件"}, {"operation": "删除文件"}),
        ],
    )
    def test_single_field_pass_through(self, extra, expected):
        info = {
            "questions": [
                {
                    "question": "请确认？",
                    "options": ["A", "B"],
                    **extra,
                }
            ]
        }
        result = _build_ask_user_question_payload(info, "thread-param")

        for key, value in expected.items():
            assert result["questions"][0][key] == value

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
        prepared_execution=prepared_execution(),
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
    lifecycle: list[str] = []

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
            await kwargs.pop("on_prepared")()
            assert db.commit_count == 1
            assert lifecycle[-1] == "prepared"
            lifecycle.append("streaming")
            yield (
                "messages",
                (
                    {"content": "child token", "id": "msg-child"},
                    {"namespace": ["task:1"], "thread_id": "child-thread"},
                ),
            )
            yield "checkpoint", SimpleNamespace(values={})

        async def get_graph(self, context=None):
            class FakeGraph:
                async def aget_state(self, _config):
                    return SimpleNamespace(values={})

            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return (
            SimpleNamespace(slug="main-agent", backend_id="ChatbotAgent"),
            FakeAgent(),
            prepared_execution().context,
            SimpleNamespace(
                id=1,
                uid="user-1",
                status="active",
                project_id="11111111-1111-4111-8111-111111111111",
                extra_metadata={"attachments": []},
            ),
        )

    async def fake_save_messages_from_langgraph_state(**_kwargs):
        return None

    async def fake_check_and_handle_interrupts(*_args, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_path", _resolve_test_workdir)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **_kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[], trace_id=None),
    )
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_check_and_handle_interrupts)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)

    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return SimpleNamespace(
                id=1,
                uid="user-1",
                status="active",
                project_id="11111111-1111-4111-8111-111111111111",
                extra_metadata={"attachments": []},
            )

        async def get_attachments(self, _conversation_id):
            return []

    monkeypatch.setattr(svc, "ConversationRepository", FakeConversationRepository)

    class UnexpectedSandboxBackend:
        def __init__(self, **_kwargs):
            raise AssertionError("Resume 流不应在执行前构造 Sandbox Backend")

        def ensure_available(self):
            raise AssertionError("Resume 流不应预创建 Sandbox")

    monkeypatch.setattr(svc, "ProvisionerSandboxBackend", UnexpectedSandboxBackend, raising=False)
    monkeypatch.setattr(
        svc,
        "get_user_skills_root_dir",
        lambda _uid: (_ for _ in ()).throw(AssertionError("Resume 流不应物化 Skill 投影根")),
        raising=False,
    )
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)

    async def on_prepared() -> None:
        assert db.commit_count == 1
        lifecycle.append("prepared")

    stream = stream_agent_resume(
        prepared_execution=prepared_execution(),
        thread_id="parent-thread",
        resume_input={"ok": True},
        meta={"request_id": "req-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=db,
        on_prepared=on_prepared,
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
    assert lifecycle == ["prepared", "streaming"]

    async def fail_output_persistence(**_kwargs):
        raise ValueError("output binding rejected")

    db.commit_count = 0
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fail_output_persistence)
    failing_chunks = []
    async for raw in stream_agent_resume(
        prepared_execution=prepared_execution(),
        thread_id="parent-thread",
        resume_input={"ok": True},
        meta={
            "run_id": "resume-output-error",
            "request_id": "resume-request-error",
            "worker_id": "resume-worker:attempt-1",
        },
        current_user=SimpleNamespace(uid="user-1"),
        db=db,
        on_prepared=on_prepared,
    ):
        failing_chunks.append(json.loads(raw.decode("utf-8")))

    assert failing_chunks[-1]["status"] == "error"
    assert failing_chunks[-1]["error_type"] == "output_persistence_error"
    assert all(chunk.get("status") not in {"finished", "warning"} for chunk in failing_chunks)
