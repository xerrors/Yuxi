from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.services.agent_run_service as agent_run_service
import yuxi.services.subagent_run_service as service_module
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.subagent_run_service import SubagentRunBusy, SubagentRunService
from yuxi.utils.hash_utils import subagent_child_thread_id


def make_child_thread_id(parent_thread_id: str, agent_slug: str, tool_call_id: str) -> str:
    return subagent_child_thread_id(parent_thread_id, agent_slug, tool_call_id)


class _FakeDB:
    def __init__(self):
        self.flushes = 0
        self.added = []
        self.deleted = []
        self.committed = False
        self.created_run = None
        self.created_run_kwargs = None
        self.request_id_lookups: list[str] = []
        self.active_run_lookup = None
        self.active_run = None
        self.existing_run = None
        self._message_id = 10

    async def flush(self):
        self.flushes += 1
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = self._message_id

    async def execute(self, stmt):
        del stmt
        return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(uid="user-1", role="user"))

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass

    async def delete(self, item):
        self.deleted.append(item)

    def begin_nested(self):
        class NestedTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return NestedTransaction()


def _agent(slug: str = "worker"):
    return SimpleNamespace(slug=slug, name="Worker")


def _parent_run():
    return SimpleNamespace(id="parent-run", conversation_thread_id="parent-thread", conversation_id=10, run_type="chat")


def _relation(
    *,
    child_thread_id: str = "child-thread",
    parent_conversation_id: int = 10,
    subagent_slug: str = "worker",
):
    return SimpleNamespace(
        id=77,
        parent_conversation_id=parent_conversation_id,
        child_conversation_id=20,
        child_thread_id=child_thread_id,
        subagent_slug=subagent_slug,
    )


def _child_run(*, relation_id: int = 77, created_by_run_id: str = "parent-run"):
    return SimpleNamespace(
        id="child-run",
        run_type="subagent",
        conversation_thread_id="child-thread",
        conversation_id=20,
        created_by_run_id=created_by_run_id,
        subagent_thread_relation_id=relation_id,
    )


def _patch_repos(
    monkeypatch: pytest.MonkeyPatch,
    *,
    captured: dict[str, object] | None = None,
    parent_run=None,
    child_run=None,
    child_conversation=None,
    existing_child_conversation=None,
    existing_relation=None,
    created_relation=None,
    relation_by_id=None,
):
    captured = captured if captured is not None else {}
    parent_run = parent_run or _parent_run()
    child_conversation = child_conversation or SimpleNamespace(id=20, uid="user-1", agent_id="worker", status="active")

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_run_for_user(self, run_id: str, uid: str):
            assert uid == "user-1"
            return {"parent-run": parent_run, "child-run": child_run}.get(run_id)

        async def get_subagent_run_for_creator(self, *, uid: str, created_by_run_id: str, run_id: str):
            assert uid == "user-1"
            captured["get_subagent_run_for_creator"] = {
                "uid": uid,
                "created_by_run_id": created_by_run_id,
                "run_id": run_id,
            }
            creator_run = await self.get_run_for_user(created_by_run_id, uid)
            run = await self.get_run_for_user(run_id, uid)
            if not creator_run or not run or run.run_type != "subagent":
                return None
            if run.created_by_run_id != creator_run.id:
                return None
            relation_id = run.subagent_thread_relation_id
            if not relation_id or not relation_by_id or relation_by_id.id != relation_id:
                return None
            if relation_by_id.parent_conversation_id != creator_run.conversation_id:
                return None
            if relation_by_id.child_thread_id != run.conversation_thread_id:
                return None
            return run

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            captured["lookup_thread_id"] = thread_id
            if existing_child_conversation and getattr(existing_child_conversation, "thread_id", None) == thread_id:
                return existing_child_conversation
            return None

        async def add_conversation(
            self,
            *,
            uid: str,
            agent_id: str,
            title: str,
            thread_id: str,
            metadata: dict,
        ):
            captured["conversation"] = {
                "uid": uid,
                "agent_id": agent_id,
                "title": title,
                "thread_id": thread_id,
                "metadata": metadata,
            }
            child_conversation.thread_id = thread_id
            return child_conversation

    class ThreadRepo:
        def __init__(self, _db):
            pass

        async def get_by_child_thread_for_user(self, child_thread_id: str, uid: str):
            assert uid == "user-1"
            captured["relation_lookup_thread_id"] = child_thread_id
            if existing_relation and existing_relation.child_thread_id != child_thread_id:
                return None
            return existing_relation

        async def create(self, **kwargs):
            captured["relation"] = kwargs
            relation = created_relation or _relation(child_thread_id=kwargs["child_thread_id"])
            relation.child_thread_id = kwargs["child_thread_id"]
            return relation

        async def get_for_user(self, relation_id: int, uid: str):
            assert relation_id == 77
            assert uid == "user-1"
            return relation_by_id

    monkeypatch.setattr(service_module, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(service_module, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(service_module, "SubagentThreadRepository", ThreadRepo)


def _patch_run_record_creation(
    monkeypatch: pytest.MonkeyPatch,
    db: _FakeDB,
    *,
    missing_subagent: bool = False,
    active_run=None,
):
    db.active_run = active_run

    class _FakeContext:
        def __init__(self):
            self.model = "agent-default-model"

        def update_from_dict(self, data: dict):
            for key, value in data.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    class _FakeBackend:
        context_schema = _FakeContext

    class ConvRepo:
        def __init__(self, db_session):
            del db_session

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=20, uid="user-1", status="subagent", agent_id="worker")

    class AgentRepo:
        def __init__(self, db_session):
            del db_session

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user
            assert kind == "subagent"
            if missing_subagent:
                return None
            return SimpleNamespace(
                slug=slug,
                name="Worker",
                backend_id="SubAgentBackend",
                config_json={"context": {}},
                is_subagent=True,
            )

    class RunRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_run_by_request_id(self, request_id: str):
            self.db.request_id_lookups.append(request_id)
            return self.db.existing_run

        async def get_active_run_by_thread_for_user(self, *, agent_slug: str, conversation_thread_id: str, uid: str):
            self.db.active_run_lookup = {
                "agent_slug": agent_slug,
                "conversation_thread_id": conversation_thread_id,
                "uid": uid,
            }
            return self.db.active_run

        async def create_run(self, **kwargs):
            self.db.created_run_kwargs = kwargs
            self.db.created_run = SimpleNamespace(
                id=kwargs["run_id"],
                conversation_thread_id=kwargs["conversation_thread_id"],
                agent_slug=kwargs["agent_slug"],
                status="pending",
                request_id=kwargs["request_id"],
                uid=kwargs["uid"],
                run_type=kwargs["run_type"],
                created_by_run_id=kwargs.get("created_by_run_id"),
                subagent_thread_relation_id=kwargs.get("subagent_thread_relation_id"),
            )
            return self.db.created_run

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)


def _fake_create_run_record(captured: dict[str, object], *, run_id: str = "child-run"):
    async def fake_create_run_record(_self, **kwargs):
        captured["create_run_record"] = kwargs
        return (
            SimpleNamespace(
                id=run_id,
                conversation_thread_id="child-thread",
                agent_slug="worker",
                status="pending",
                created_by_run_id=kwargs["creator_run"].id,
                subagent_thread_relation_id=kwargs["relation"].id,
                input_payload={
                    "runtime": {
                        "tool_call_id": kwargs["tool_call_id"],
                    },
                },
            ),
            True,
        )

    return fake_create_run_record


@pytest.mark.asyncio
async def test_subagent_run_service_creates_child_relation_run_and_enqueue(monkeypatch: pytest.MonkeyPatch):
    db = _FakeDB()
    captured: dict[str, object] = {}
    enqueued: list[str] = []
    child_conversation = SimpleNamespace(id=20, uid="user-1", agent_id="worker", status="active")
    relation = _relation(child_thread_id="")

    _patch_repos(
        monkeypatch,
        captured=captured,
        child_conversation=child_conversation,
        created_relation=relation,
    )

    async def fake_enqueue(run_id: str):
        enqueued.append(run_id)

    monkeypatch.setattr(SubagentRunService, "_create_run_record", _fake_create_run_record(captured))
    monkeypatch.setattr(service_module.agent_run_service, "enqueue_agent_run", fake_enqueue)

    result = await SubagentRunService(db).start(
        uid="user-1",
        created_by_run_id="parent-run",
        agent_item=_agent(),
        input_message=build_chat_input_message("run in background"),
        tool_call_id="tool-1",
        model_spec="provider:model",
    )

    child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-1")
    assert result.created is True
    assert result.continuing is False
    assert result.relation.child_thread_id == child_thread_id
    assert result.relation is relation
    assert child_conversation.status == "subagent"
    assert captured["conversation"]["metadata"]["parent_conversation_id"] == 10
    assert captured["relation"] == {
        "uid": "user-1",
        "parent_conversation_id": 10,
        "child_conversation_id": 20,
        "child_thread_id": child_thread_id,
        "subagent_slug": "worker",
        "created_by_run_id": "parent-run",
    }
    assert captured["create_run_record"]["relation"].id == 77
    assert captured["create_run_record"]["model_spec"] == "provider:model"
    assert captured["create_run_record"]["creator_run"].id == "parent-run"
    assert captured["create_run_record"]["input_message"].content == "run in background"
    assert captured["create_run_record"]["input_message"].raw_message()["type"] == "human"
    assert captured["create_run_record"]["input_message"].raw_message()["content"] == "run in background"
    assert db.committed is True
    assert enqueued == ["child-run"]


@pytest.mark.asyncio
async def test_subagent_run_service_continues_existing_relation(monkeypatch: pytest.MonkeyPatch):
    db = _FakeDB()
    captured: dict[str, object] = {}
    relation = _relation()
    _patch_repos(monkeypatch, captured=captured, existing_relation=relation)

    async def fake_enqueue(run_id: str):
        captured["enqueued"] = run_id

    monkeypatch.setattr(
        SubagentRunService,
        "_create_run_record",
        _fake_create_run_record(captured, run_id="child-run-2"),
    )
    monkeypatch.setattr(service_module.agent_run_service, "enqueue_agent_run", fake_enqueue)

    result = await SubagentRunService(db).start(
        uid="user-1",
        created_by_run_id="parent-run",
        agent_item=_agent(),
        input_message=build_chat_input_message("continue"),
        tool_call_id="tool-2",
        requested_thread_id="child-thread",
    )

    assert result.continuing is True
    assert result.relation is relation
    assert captured["relation_lookup_thread_id"] == "child-thread"
    assert captured["create_run_record"]["creator_run"].id == "parent-run"
    assert captured["create_run_record"]["input_message"].content == "continue"
    assert captured["create_run_record"]["input_message"].raw_message()["type"] == "human"
    assert captured["create_run_record"]["input_message"].raw_message()["content"] == "continue"
    assert captured["enqueued"] == "child-run-2"


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_thread_from_another_parent(monkeypatch: pytest.MonkeyPatch):
    _patch_repos(monkeypatch, existing_relation=_relation(parent_conversation_id=99))

    with pytest.raises(ValueError, match="线程不属于当前对话"):
        await SubagentRunService(_FakeDB()).start(
            uid="user-1",
            created_by_run_id="parent-run",
            agent_item=_agent(),
            input_message=build_chat_input_message("continue"),
            tool_call_id="tool-2",
            requested_thread_id="child-thread",
        )


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_thread_from_another_subagent(monkeypatch: pytest.MonkeyPatch):
    _patch_repos(monkeypatch, existing_relation=_relation(subagent_slug="other"))

    with pytest.raises(ValueError, match="属于子智能体 other"):
        await SubagentRunService(_FakeDB()).start(
            uid="user-1",
            created_by_run_id="parent-run",
            agent_item=_agent(),
            input_message=build_chat_input_message("continue"),
            tool_call_id="tool-2",
            requested_thread_id="child-thread",
        )


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_parent_run_without_conversation_before_child_creation(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}
    _patch_repos(
        monkeypatch,
        captured=captured,
        parent_run=SimpleNamespace(id="parent-run", conversation_thread_id="parent-thread", conversation_id=None),
    )

    with pytest.raises(ValueError, match="缺少 conversation_id"):
        await SubagentRunService(_FakeDB()).start(
            uid="user-1",
            created_by_run_id="parent-run",
            agent_item=_agent(),
            input_message=build_chat_input_message("new child"),
            tool_call_id="tool-2",
        )

    assert "conversation" not in captured
    assert "relation" not in captured


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_subagent_as_creator(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    _patch_repos(
        monkeypatch,
        captured=captured,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="parent-thread",
            conversation_id=10,
            run_type="subagent",
        ),
    )

    with pytest.raises(ValueError, match="子智能体不能创建子智能体"):
        await SubagentRunService(_FakeDB()).start(
            uid="user-1",
            created_by_run_id="parent-run",
            agent_item=_agent(),
            input_message=build_chat_input_message("nested child"),
            tool_call_id="tool-2",
        )

    assert "relation_lookup_thread_id" not in captured
    assert "conversation" not in captured
    assert "relation" not in captured


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_child_thread_owned_by_normal_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}
    child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-2")
    _patch_repos(
        monkeypatch,
        captured=captured,
        existing_child_conversation=SimpleNamespace(
            id=20,
            uid="user-1",
            agent_id="worker",
            thread_id=child_thread_id,
            status="active",
        ),
    )

    with pytest.raises(ValueError, match="普通对话占用"):
        await SubagentRunService(_FakeDB()).start(
            uid="user-1",
            created_by_run_id="parent-run",
            agent_item=_agent(),
            input_message=build_chat_input_message("new child"),
            tool_call_id="tool-2",
        )

    assert captured["lookup_thread_id"] == child_thread_id
    assert "relation" not in captured


@pytest.mark.asyncio
async def test_subagent_run_service_translates_busy_run(monkeypatch: pytest.MonkeyPatch):
    _patch_repos(monkeypatch, existing_relation=_relation())

    async def fake_create_run_record(_self, **kwargs):
        del kwargs
        raise HTTPException(status_code=409, detail={"code": "run_busy", "active_run_id": "active-run"})

    async def fake_enqueue(run_id: str):
        del run_id
        raise AssertionError("busy run should not enqueue")

    monkeypatch.setattr(SubagentRunService, "_create_run_record", fake_create_run_record)
    monkeypatch.setattr(service_module.agent_run_service, "enqueue_agent_run", fake_enqueue)

    with pytest.raises(SubagentRunBusy) as exc:
        await SubagentRunService(_FakeDB()).start(
            uid="user-1",
            created_by_run_id="parent-run",
            agent_item=_agent(),
            input_message=build_chat_input_message("continue"),
            tool_call_id="tool-2",
            requested_thread_id="child-thread",
        )

    assert exc.value.thread_id == "child-thread"
    assert exc.value.active_run_id == "active-run"
    assert exc.value.to_payload() == {
        "status": "busy",
        "thread_id": "child-thread",
        "active_run_id": "active-run",
        "active_run_status": None,
        "message": None,
    }


@pytest.mark.asyncio
async def test_subagent_run_service_create_run_record_persists_subagent_context(monkeypatch: pytest.MonkeyPatch):
    db = _FakeDB()
    _patch_run_record_creation(monkeypatch, db)
    creator_run = SimpleNamespace(id="parent-run", conversation_id=10, conversation_thread_id="parent-thread")
    relation = _relation(child_thread_id="child-thread", parent_conversation_id=10, subagent_slug="worker")

    run, created = await SubagentRunService(db)._create_run_record(
        input_message=build_chat_input_message("delegate this"),
        request_id="subagent-req",
        current_uid="user-1",
        model_spec=None,
        creator_run=creator_run,
        relation=relation,
        tool_call_id="tool-1",
        file_thread_id="parent-file-thread",
    )

    assert created is True
    assert run is db.created_run
    assert db.added[0].content == "delegate this"
    assert db.added[0].extra_metadata["source"] == "subagent"
    assert db.added[0].extra_metadata["raw_message"]["type"] == "human"
    assert db.added[0].extra_metadata["raw_message"]["content"] == "delegate this"
    assert db.created_run_kwargs["run_type"] == "subagent"
    assert db.created_run_kwargs["created_by_run_id"] == "parent-run"
    assert db.created_run_kwargs["subagent_thread_relation_id"] == 77
    assert db.created_run_kwargs["conversation_thread_id"] == "child-thread"
    assert db.created_run_kwargs["input_message_id"] == 10
    assert db.created_run_kwargs["input_payload"] == {
        "model_spec": "agent-default-model",
        "runtime": {
            "tool_call_id": "tool-1",
            "subagent_name": "Worker",
            "parent_thread_id": "parent-thread",
            "file_thread_id": "parent-file-thread",
            "skills_thread_id": "child-thread",
        },
    }
    assert db.committed is False


@pytest.mark.asyncio
async def test_subagent_run_service_create_run_record_uses_creator_thread_when_file_thread_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _FakeDB()
    _patch_run_record_creation(monkeypatch, db)
    creator_run = SimpleNamespace(id="parent-run", conversation_id=10, conversation_thread_id="current-parent-thread")
    relation = _relation(child_thread_id="child-thread", parent_conversation_id=10, subagent_slug="worker")

    await SubagentRunService(db)._create_run_record(
        input_message=build_chat_input_message("continue this"),
        request_id="subagent-req-2",
        current_uid="user-1",
        model_spec=None,
        creator_run=creator_run,
        relation=relation,
        tool_call_id="tool-2",
        file_thread_id=None,
    )

    assert db.created_run_kwargs["created_by_run_id"] == "parent-run"
    assert db.created_run_kwargs["input_payload"]["runtime"]["parent_thread_id"] == "current-parent-thread"
    assert db.created_run_kwargs["input_payload"]["runtime"]["file_thread_id"] == "current-parent-thread"


@pytest.mark.asyncio
async def test_subagent_run_service_create_run_record_rejects_non_subagent_definition(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _FakeDB()
    _patch_run_record_creation(monkeypatch, db, missing_subagent=True)
    creator_run = SimpleNamespace(id="parent-run", conversation_id=10, conversation_thread_id="parent-thread")
    relation = _relation(child_thread_id="child-thread", parent_conversation_id=10, subagent_slug="worker")

    with pytest.raises(HTTPException) as exc:
        await SubagentRunService(db)._create_run_record(
            input_message=build_chat_input_message("delegate this"),
            request_id="subagent-req",
            current_uid="user-1",
            model_spec=None,
            creator_run=creator_run,
            relation=relation,
            tool_call_id="tool-1",
            file_thread_id="parent-thread",
        )

    assert exc.value.status_code == 404
    assert "智能体不存在" in exc.value.detail
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_subagent_run_service_create_run_record_rejects_relation_parent_mismatch(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _FakeDB()
    _patch_run_record_creation(monkeypatch, db)
    creator_run = SimpleNamespace(id="parent-run", conversation_id=99, conversation_thread_id="parent-thread")
    relation = _relation(child_thread_id="child-thread", parent_conversation_id=10, subagent_slug="worker")

    with pytest.raises(HTTPException) as exc:
        await SubagentRunService(db)._create_run_record(
            input_message=build_chat_input_message("delegate this"),
            request_id="subagent-req",
            current_uid="user-1",
            model_spec=None,
            creator_run=creator_run,
            relation=relation,
            tool_call_id="tool-1",
            file_thread_id="parent-thread",
        )

    assert exc.value.status_code == 409
    assert "subagent thread relation" in exc.value.detail
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_subagent_run_service_loads_run_only_for_current_parent_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    parent_run = SimpleNamespace(id="parent-run", conversation_id=10)
    child_run = _child_run()
    _patch_repos(
        monkeypatch,
        parent_run=parent_run,
        child_run=child_run,
        relation_by_id=_relation(),
    )

    run = await SubagentRunService(_FakeDB()).get_run_for_creator(
        uid="user-1",
        created_by_run_id="parent-run",
        run_id="child-run",
    )

    assert run is child_run


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_run_from_another_parent_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    parent_run = SimpleNamespace(id="parent-run", conversation_id=10)
    _patch_repos(
        monkeypatch,
        parent_run=parent_run,
        child_run=_child_run(),
        relation_by_id=_relation(parent_conversation_id=99),
    )

    with pytest.raises(ValueError, match="不存在或不属于当前父运行"):
        await SubagentRunService(_FakeDB()).get_run_for_creator(
            uid="user-1",
            created_by_run_id="parent-run",
            run_id="child-run",
        )


@pytest.mark.asyncio
async def test_subagent_run_service_rejects_run_from_another_parent_run(
    monkeypatch: pytest.MonkeyPatch,
):
    parent_run = SimpleNamespace(id="parent-run", conversation_id=10)
    _patch_repos(
        monkeypatch,
        parent_run=parent_run,
        child_run=_child_run(created_by_run_id="previous-parent-run"),
        relation_by_id=_relation(parent_conversation_id=10),
    )

    with pytest.raises(ValueError, match="不存在或不属于当前父运行"):
        await SubagentRunService(_FakeDB()).get_run_for_creator(
            uid="user-1",
            created_by_run_id="parent-run",
            run_id="child-run",
        )
