from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain.messages import AIMessage, HumanMessage

from yuxi.agents import context as agent_context
from yuxi.agents.backends.sandbox import paths as workspace_paths
from yuxi.services import chat_service as svc


def _empty_agent_context(_thread_id: str, _uid: str) -> str:
    return ""


async def _fake_normalize_agent_context_config(context, **_kwargs):
    return dict(context or {})


@pytest.mark.asyncio
async def test_resolve_agent_runtime_includes_subagents_only_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeAgentRepository:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user
            assert slug == "worker"
            calls.append(kind)
            if kind == "subagent":
                return SimpleNamespace(slug="worker", backend_id="SubAgentBackend", config_json={"context": {}})
            return None

    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            return SimpleNamespace(uid="user-1", agent_id="worker", thread_id=thread_id, status="subagent")

    monkeypatch.setattr(svc, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(svc, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(
        svc.agent_manager,
        "get_agent",
        lambda backend_id: SimpleNamespace(context_schema=None) if backend_id == "SubAgentBackend" else None,
    )

    user = SimpleNamespace(uid="user-1")

    with pytest.raises(ValueError, match="智能体不存在或无权限访问"):
        await svc._resolve_agent_runtime(
            db=object(),
            user=user,
            requested_agent_slug="worker",
            thread_id="child-thread",
        )

    agent_item, backend, agent_config = await svc._resolve_agent_runtime(
        db=object(),
        user=user,
        requested_agent_slug="worker",
        thread_id="child-thread",
        agent_kind="subagent",
    )

    assert calls == ["main", "subagent"]
    assert agent_item.slug == "worker"
    assert backend.context_schema is None
    assert agent_config == {}


class _FakeConvRepo:
    def __init__(self, _db):
        self.db = _db
        self.saved_messages: list[dict] = []
        self.tool_calls: list[dict] = []
        self.conversations: dict[str, SimpleNamespace] = {}

    def _conversation(self, thread_id: str) -> SimpleNamespace:
        return self.conversations.setdefault(
            thread_id,
            SimpleNamespace(
                id=1,
                uid="user-1",
                agent_id="test-agent",
                thread_id=thread_id,
                status="active",
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
        request_id: str | None = None,
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
                "request_id": request_id,
            }
        )
        return SimpleNamespace(id=1)

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self._conversation(thread_id)

    async def get_messages_by_thread_id(self, _thread_id: str):
        return []

    async def add_tool_call(
        self,
        *,
        message_id: int,
        tool_name: str,
        tool_input: dict | None = None,
        status: str = "pending",
        langgraph_tool_call_id: str | None = None,
    ):
        self.tool_calls.append(
            {
                "message_id": message_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "status": status,
                "langgraph_tool_call_id": langgraph_tool_call_id,
            }
        )
        return SimpleNamespace(id=len(self.tool_calls))

    async def create_conversation(self, *, uid: str, agent_id: str, thread_id: str, metadata: dict | None = None):
        conversation = SimpleNamespace(
            id=1,
            uid=uid,
            agent_id=agent_id,
            thread_id=thread_id,
            status="active",
            extra_metadata=metadata or {},
        )
        self.conversations[thread_id] = conversation
        return conversation

    async def get_attachments_by_request_id(self, conversation_id: int, request_id: str):
        return []

    async def bind_attachments_to_request(self, conversation_id: int, request_id: str, file_ids: list[str]):
        return []


@pytest.mark.asyncio
async def test_save_messages_from_langgraph_state_handles_dict_tool_call_blocks() -> None:
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        {
                            "id": "ai-tool-call",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_call",
                                    "id": "call-task-1",
                                    "name": "task",
                                    "args": {"description": "write file", "subagent_slug": "worker"},
                                }
                            ],
                        }
                    ]
                }
            )

    class FakeAgent:
        async def get_graph(self, *, context):
            assert context is fake_context
            return FakeGraph()

    conv_repo = _FakeConvRepo(None)
    fake_context = object()

    await svc.save_messages_from_langgraph_state(
        agent_instance=FakeAgent(),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=fake_context,
        trace_info=None,
    )

    assert conv_repo.saved_messages[0]["content"] == ""
    assert conv_repo.saved_messages[0]["extra_metadata"]["content"][0]["id"] == "call-task-1"
    assert conv_repo.tool_calls == [
        {
            "message_id": 1,
            "tool_name": "task",
            "tool_input": {"description": "write file", "subagent_slug": "worker"},
            "status": "pending",
            "langgraph_tool_call_id": "call-task-1",
        }
    ]


@pytest.mark.asyncio
async def test_save_messages_from_langgraph_state_backfills_run_output_message(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDB:
        def __init__(self):
            self.commit_count = 0

        async def commit(self):
            self.commit_count += 1

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="question"), AIMessage(content="answer")]})

    class FakeAgent:
        async def get_graph(self, *, context):
            assert context is fake_context
            return FakeGraph()

    fake_db = FakeDB()
    conv_repo = _FakeConvRepo(fake_db)
    fake_context = object()
    captured: dict[str, object] = {}

    class FakeRunRepo:
        def __init__(self, db):
            assert db is fake_db

        async def set_output_message(self, run_id: str, message_id: int):
            captured["run_id"] = run_id
            captured["message_id"] = message_id

    monkeypatch.setattr(svc, "AgentRunRepository", FakeRunRepo)

    await svc.save_messages_from_langgraph_state(
        agent_instance=FakeAgent(),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=fake_context,
        trace_info={"langfuse_trace_id": "trace-1"},
        run_id="run-1",
        request_id="req-1",
    )

    assert conv_repo.saved_messages[0]["content"] == "answer"
    assert conv_repo.saved_messages[0]["run_id"] == "run-1"
    assert conv_repo.saved_messages[0]["request_id"] == "req-1"
    assert conv_repo.saved_messages[0]["extra_metadata"]["langfuse_trace_id"] == "trace-1"
    assert captured == {"run_id": "run-1", "message_id": 1}
    assert fake_db.commit_count == 1


@pytest.mark.asyncio
async def test_build_agent_input_context_loads_all_workspace_agent_context_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    workspace_paths.ensure_thread_dirs("thread-1", "user-1")
    agents_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    (agents_dir / "AGENTS.md").write_text("行为约束", encoding="utf-8")
    (agents_dir / "USER.md").write_text("用户信息", encoding="utf-8")
    (agents_dir / "MEMORY.md").write_text("长期记忆", encoding="utf-8")

    context = await agent_context.build_agent_input_context({}, thread_id="thread-1", uid="user-1")

    assert context["system_prompt"] == (
        "用户工作区 agents/AGENTS.md 内容：\n行为约束\n\n"
        "用户工作区 agents/USER.md 内容：\n用户信息\n\n"
        "用户工作区 agents/MEMORY.md 内容：\n长期记忆"
    )


@pytest.mark.asyncio
async def test_build_agent_input_context_merges_workspace_agent_context(monkeypatch: pytest.MonkeyPatch):
    def fake_agent_context(_thread_id: str, _uid: str) -> str:
        return (
            "用户工作区 agents/AGENTS.md 内容：\n回答前先读取 AGENTS.md\n\n"
            "用户工作区 agents/USER.md 内容：\n用户偏好中文"
        )

    monkeypatch.setattr(agent_context, "_load_workspace_agent_context", fake_agent_context)

    context = await agent_context.build_agent_input_context(
        {"system_prompt": "原始系统提示词", "temperature": 0.1},
        thread_id="thread-1",
        uid="user-1",
    )

    assert context["system_prompt"] == (
        "原始系统提示词\n\n"
        "用户工作区 agents/AGENTS.md 内容：\n回答前先读取 AGENTS.md\n\n"
        "用户工作区 agents/USER.md 内容：\n用户偏好中文"
    )
    assert context["temperature"] == 0.1
    assert context["thread_id"] == "thread-1"
    assert context["uid"] == "user-1"


@pytest.mark.asyncio
async def test_get_agent_state_view_rejects_async_subagent_without_child_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    child_thread_id = "missing-child-conversation"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return None

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="child-run",
                conversation_thread_id=child_thread_id,
                agent_slug="worker",
                status="running",
                created_by_run_id="parent-run",
                subagent_thread_relation_id=77,
                input_payload={"runtime": {"tool_call_id": "tool-1"}},
            )

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            raise AssertionError("async subagent state must be loaded through child conversation relation")

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)

    with pytest.raises(HTTPException) as exc:
        await svc.get_agent_state_view(
            thread_id=child_thread_id,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
            include_messages=True,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_agent_state_view_returns_interrupted_checkpoint_payload(monkeypatch: pytest.MonkeyPatch):
    thread_id = "thread-1"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, requested_thread_id: str):
            assert requested_thread_id == thread_id
            return SimpleNamespace(id=20, uid="user-1", agent_id="main", status="active")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "main"
            return SimpleNamespace(backend_id="ChatBot", config_json={"context": {}})

    class ThreadRepo:
        def __init__(self, _db):
            pass

        async def get_by_child_conversation_for_user(self, conversation_id: int, uid: str):
            assert conversation_id == 20
            assert uid == "user-1"
            return None

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_run_by_thread_for_user(self, requested_thread_id: str, uid: str):
            assert requested_thread_id == thread_id
            assert uid == "user-1"
            return SimpleNamespace(id="run-1", status="interrupted", input_payload={})

    class Context:
        def __init__(self, *, thread_id="", uid=""):
            self.thread_id = thread_id
            self.uid = uid

        def update(self, data: dict):
            for key, value in data.items():
                setattr(self, key, value)

    class Agent:
        context_schema = Context

        async def get_graph(self, *, context):
            assert context.thread_id == thread_id
            return SimpleNamespace(
                aget_state=lambda _config: None,
            )

    checkpoint_state = SimpleNamespace(
        values={},
        tasks=[
            SimpleNamespace(
                interrupts=[
                    SimpleNamespace(
                        value={
                            "action_requests": [{"name": "execute", "args": {"command": "pytest -q"}}],
                            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
                        }
                    )
                ]
            )
        ],
    )

    async def read_checkpoint_state(*_args, **_kwargs):
        return checkpoint_state

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "SubagentThreadRepository", ThreadRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "_read_checkpoint_state", read_checkpoint_state)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: Agent())

    result = await svc.get_agent_state_view(
        thread_id=thread_id,
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result["interrupt"]["status"] == "human_approval_required"
    assert result["interrupt"]["run_id"] == "run-1"
    assert result["interrupt"]["approval"]["action_requests"][0]["name"] == "execute"


@pytest.mark.asyncio
async def test_get_agent_state_view_includes_subagent_thread_relation(monkeypatch: pytest.MonkeyPatch):
    child_thread_id = "child-thread"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            if thread_id == child_thread_id:
                return SimpleNamespace(id=20, uid="user-1", agent_id="worker", status="subagent")
            return None

        async def get_conversation_by_id(self, conversation_id: int):
            assert conversation_id == 11
            return SimpleNamespace(id=11, thread_id="parent-thread", uid="user-1", status="active")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "worker"
            return SimpleNamespace(
                backend_id="SubAgentBackend",
                config_json={"context": {}},
            )

    class ThreadRepo:
        def __init__(self, _db):
            pass

        async def get_by_child_conversation_for_user(self, child_conversation_id: int, uid: str):
            assert child_conversation_id == 20
            assert uid == "user-1"
            return SimpleNamespace(
                id=77,
                parent_conversation_id=11,
                child_conversation_id=20,
                child_thread_id=child_thread_id,
                subagent_slug="worker",
                to_dict=lambda: {
                    "id": 77,
                    "parent_conversation_id": 11,
                    "child_conversation_id": 20,
                    "child_thread_id": child_thread_id,
                    "subagent_slug": "worker",
                },
            )

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(status="running", input_payload={"model_spec": "provider:run-model"})

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="child-run",
                conversation_thread_id=child_thread_id,
                agent_slug="worker",
                uid="user-1",
                status="running",
                created_by_run_id="parent-run",
                subagent_thread_relation_id=77,
                input_payload={
                    "runtime": {
                        "tool_call_id": "tool-1",
                        "subagent_name": "Worker",
                        "description": "do work",
                    },
                },
                error_message=None,
                created_at=None,
                finished_at=None,
                to_dict=lambda: {"created_at": "2026-06-21T01:00:00Z", "finished_at": None},
            )

    class Graph:
        async def aget_state(self, config):
            assert config["configurable"]["thread_id"] == child_thread_id
            return SimpleNamespace(
                values={
                    "messages": [HumanMessage(content="do work"), AIMessage(content="working")],
                    "artifacts": ["out.txt"],
                }
            )

    class Context:
        def __init__(self, *, thread_id="", uid=""):
            self.thread_id = thread_id
            self.uid = uid
            self.model = ""

        def update(self, data: dict):
            for key, value in data.items():
                setattr(self, key, value)

    class Agent:
        context_schema = Context

        async def get_graph(self, *, context):
            assert context.thread_id == child_thread_id
            assert context.uid == "user-1"
            assert context.model == "provider:run-model"
            return Graph()

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "SubagentThreadRepository", ThreadRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: Agent())

    result = await svc.get_agent_state_view(
        thread_id=child_thread_id,
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
        include_messages=True,
    )

    assert result["parent_thread_id"] == "parent-thread"
    assert result["subagent_thread"]["id"] == 77
    assert result["subagent_run"]["run_id"] == "child-run"
    assert result["agent_state"]["artifacts"] == ["out.txt"]
    assert [message["type"] for message in result["messages"]] == ["human", "ai"]


@pytest.mark.asyncio
async def test_get_agent_state_view_reports_malformed_subagent_run_as_server_error(
    monkeypatch: pytest.MonkeyPatch,
):
    child_thread_id = "child-thread"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            assert thread_id == child_thread_id
            return SimpleNamespace(id=20, uid="user-1", agent_id="worker", status="subagent")

        async def get_conversation_by_id(self, conversation_id: int):
            assert conversation_id == 11
            return SimpleNamespace(id=11, thread_id="parent-thread", uid="user-1", status="active")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "worker"
            return SimpleNamespace(backend_id="SubAgentBackend", config_json={"context": {}})

    class ThreadRepo:
        def __init__(self, _db):
            pass

        async def get_by_child_conversation_for_user(self, child_conversation_id: int, uid: str):
            assert child_conversation_id == 20
            assert uid == "user-1"
            return SimpleNamespace(
                id=77,
                parent_conversation_id=11,
                to_dict=lambda: {"id": 77},
            )

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return None

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="child-run",
                conversation_thread_id=child_thread_id,
                agent_slug="worker",
                status="running",
                input_payload={"runtime": {}},
            )

    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={})

    class Context:
        def __init__(self, *, thread_id="", uid=""):
            self.thread_id = thread_id
            self.uid = uid

        def update(self, data: dict):
            for key, value in data.items():
                setattr(self, key, value)

    class Agent:
        context_schema = Context

        async def get_graph(self, *, context):
            assert context.thread_id == child_thread_id
            assert context.uid == "user-1"
            return Graph()

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "SubagentThreadRepository", ThreadRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: Agent())

    with pytest.raises(HTTPException) as exc:
        await svc.get_agent_state_view(
            thread_id=child_thread_id,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "子智能体运行记录格式异常"


@pytest.mark.asyncio
async def test_build_agent_input_context_keeps_prompt_when_workspace_agent_context_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_context, "_load_workspace_agent_context", _empty_agent_context)

    context = await agent_context.build_agent_input_context(
        {"system_prompt": "原始系统提示词"},
        thread_id="thread-1",
        uid="user-1",
    )

    assert context["system_prompt"] == "原始系统提示词"
