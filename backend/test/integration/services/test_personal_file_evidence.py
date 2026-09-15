"""真实PG证明个人文件删除后历史与摘要拒发，而非文本关键词猜测。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from yuxi.storage.postgres.models_business import Conversation
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry
from yuxi.services.personal_file_evidence_service import (
    PersonalFileEvidenceUnavailable,
    validate_personal_file_evidence,
)
from yuxi.agents.middlewares.personal_file_evidence import PersonalFileEvidenceMiddleware
from yuxi.utils.datetime_utils import utc_now_naive
from test.integration.services.personal_history_fixture import personal_history_app as personal_fixture  # noqa: F401


@pytest.fixture
async def personal_app(personal_fixture):  # noqa: F811
    """复用独立schema与真实HTTP应用fixture。"""
    return personal_fixture


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("state", ["pending_delete", "trashed", "purging", "purged"])
async def test_deleted_file_refuses_old_model_and_summary_history(personal_app, state):
    env = personal_app
    context = SimpleNamespace(uid="member", thread_id="child")
    async with env.sessions() as db:
        parent = await db.scalar(select(Conversation).where(Conversation.thread_id == "main"))
        child = await db.scalar(select(Conversation).where(Conversation.thread_id == "child"))
        stamp = parent.created_at + timedelta(seconds=1)
        child.created_at = stamp + timedelta(seconds=1)
        db.add(
            PersonalTrashEntry(
                id="deleted",
                uid="member",
                name="secret.txt",
                kind="workspace",
                paths=[{"path": "/secret.txt"}],
                state=state,
                deleted_at=stamp,
                purge_after=stamp + timedelta(days=30),
            )
        )
        await db.commit()
    called = []

    async def handler(request):
        called.append(request)

    # 没有任何原文件名/来源标识的摘要仍受父会话删除屏障约束。
    request = SimpleNamespace(state={"messages": []}, runtime=SimpleNamespace(context=context))
    with pytest.raises(PersonalFileEvidenceUnavailable):
        await PersonalFileEvidenceMiddleware().awrap_model_call(request, handler)
    from yuxi.agents.middlewares.summary import YuxiSummarizationMiddleware

    class RecordingSummaryModel:
        """按摘要器模型协议记录真实发送，禁止通过替换只读model属性伪造。"""

        _llm_type = "test-chat"
        profile = {"max_input_tokens": 128000}

        def _get_ls_params(self):
            return {"ls_provider": "openai"}

        def with_retry(self, **kwargs):
            return self

        async def ainvoke(self, prompt, config=None):
            called.append(prompt)
            return SimpleNamespace(text="summary")

    from deepagents.backends import CompositeBackend

    summary = YuxiSummarizationMiddleware(
        model=RecordingSummaryModel(),
        backend=CompositeBackend(
            default=SimpleNamespace(), routes={}, artifacts_root="/home/gem/user-data/projects/fixture/outputs"
        ),
        trigger=("tokens", 90000),
        keep=("tokens", 45000),
    )
    summary.personal_file_context = context
    from langchain_core.messages import HumanMessage

    with pytest.raises(PersonalFileEvidenceUnavailable):
        await summary._acreate_summary([HumanMessage(content="private historical summary")])
    assert called == []
    async with env.sessions() as db:
        entry = await db.get(PersonalTrashEntry, "deleted")
        entry.state = "restored"
        await db.commit()
    await validate_personal_file_evidence(context)
    assert await summary._acreate_summary([HumanMessage(content="private historical summary")]) == "summary"
    assert len(called) == 1


async def test_new_thread_and_other_user_not_blocked_by_old_tombstone(personal_app):
    env = personal_app
    now = utc_now_naive()
    async with env.sessions() as db:
        db.add(
            PersonalTrashEntry(
                id="gone",
                uid="member",
                name="old",
                kind="workspace",
                paths=[],
                state="purged",
                deleted_at=now - timedelta(days=1),
                purge_after=now,
            )
        )
        await db.commit()
    await validate_personal_file_evidence(SimpleNamespace(uid="member", thread_id="main"))
    await validate_personal_file_evidence(SimpleNamespace(uid="outsider", thread_id="outsider-thread"))
    with pytest.raises(PersonalFileEvidenceUnavailable):
        await validate_personal_file_evidence(SimpleNamespace(uid="outsider", thread_id="main"))


async def test_personal_history_database_failure_refuses_send(monkeypatch):
    from yuxi.services.personal_file_evidence_service import pg_manager

    def fail():
        raise RuntimeError("fixture DB unavailable")

    monkeypatch.setattr(pg_manager, "get_async_session_context", fail)
    with pytest.raises(PersonalFileEvidenceUnavailable):
        await validate_personal_file_evidence(SimpleNamespace(uid="member", thread_id="main"))


async def test_registered_worker_cron_physically_purges_due_entry(personal_app, tmp_path, monkeypatch):
    """实际worker注册的协程使用真实PG到期记录并删除真实隔离字节。"""
    from uuid import uuid4
    from yuxi.services import run_worker
    from yuxi.workspace import trash as filesystem

    env = personal_app
    monkeypatch.setattr(filesystem, "get_user_data_dir", lambda: tmp_path)
    identity = uuid4().hex
    folder = tmp_path / "trash" / "member" / identity
    folder.mkdir(parents=True)
    quarantined = folder / "0"
    quarantined.write_bytes(b"expired-private-byte-oracle")
    inode = quarantined.stat()
    now = utc_now_naive()
    async with env.sessions() as db:
        db.add(
            PersonalTrashEntry(
                id=identity,
                uid="member",
                name="expired.txt",
                kind="workspace",
                paths=[{"path": "/expired.txt", "dev": inode.st_dev, "ino": inode.st_ino, "is_dir": False}],
                state="trashed",
                deleted_at=now - timedelta(days=31),
                purge_after=now - timedelta(days=1),
            )
        )
        await db.commit()
    job = next(job for job in run_worker.WorkerSettings.cron_jobs if job.coroutine is run_worker.purge_personal_files)
    assert job.minute == {10, 30, 50}
    assert await job.coroutine({}) == {"completed": 1, "failed": 0}
    assert not quarantined.exists()
    async with env.sessions() as db:
        assert (await db.get(PersonalTrashEntry, identity)).state == "purged"


@pytest.mark.parametrize("kind", ["search", "read"])
async def test_history_consumption_propagates_personal_tombstone_boundary(personal_app, kind):
    """新会话消费旧历史后，不因其创建时间较新而绕过删除屏障。"""
    from yuxi.services.memory_service import search_thread_messages, read_thread_messages
    from yuxi.storage.postgres.models_business import Message

    env = personal_app
    now = utc_now_naive()
    async with env.sessions() as db:
        source = await db.scalar(select(Conversation).where(Conversation.thread_id == "main"))
        source.created_at = now - timedelta(days=2)
        db.add(Message(conversation_id=source.id, role="assistant", content="personal-history-oracle"))
        db.add(Conversation(thread_id="fresh", uid="member", agent_id="chatbot", project_id="member", status="active"))
        await db.commit()
    kwargs = {"uid": "member", "consumer_thread_id": "fresh"}
    if kind == "search":
        result = await search_thread_messages(query="personal-history-oracle", **kwargs)
    else:
        result = await read_thread_messages(thread_id="main", **kwargs)
    assert "personal-history-oracle" in str(result)
    async with env.sessions() as db:
        consumer = await db.scalar(select(Conversation).where(Conversation.thread_id == "fresh"))
        assert consumer.extra_metadata["memory_history_started_at"]
        db.add(
            PersonalTrashEntry(
                id="history-deleted",
                uid="member",
                name="old",
                kind="workspace",
                paths=[],
                state="trashed",
                deleted_at=now - timedelta(days=1),
                purge_after=now + timedelta(days=29),
            )
        )
        await db.commit()
    with pytest.raises(PersonalFileEvidenceUnavailable):
        await validate_personal_file_evidence(SimpleNamespace(uid="member", thread_id="fresh"))
    with pytest.raises(ValueError):
        if kind == "search":
            await search_thread_messages(query="personal-history-oracle", **kwargs)
        else:
            await read_thread_messages(thread_id="main", **kwargs)


async def test_personal_history_timestamp_is_server_owned(personal_app):
    """普通会话更新不得清除来源戳，直接注入时间也拒绝。"""
    from yuxi.repositories.conversation_repository import ConversationRepository

    env = personal_app
    async with env.sessions() as db:
        row = await db.scalar(select(Conversation).where(Conversation.thread_id == "main"))
        expected = (utc_now_naive() - timedelta(days=2)).isoformat()
        row.extra_metadata = {"memory_history_started_at": expected}
        await db.commit()
        repo = ConversationRepository(db)
        await repo.update_conversation("main", metadata={"ordinary": "kept"})
        assert row.extra_metadata["memory_history_started_at"] == expected
        with pytest.raises(ValueError):
            await repo.update_conversation("main", metadata={"memory_history_started_at": utc_now_naive().isoformat()})
        await repo._save_metadata(row, {"ordinary": "new", "memory_history_started_at": None})
        assert row.extra_metadata["memory_history_started_at"] == expected


async def test_compression_guard_reuses_locked_transaction_without_self_deadlock(personal_app):
    """主动压缩已持会话锁，复核使用原事务且清理后不泄漏上下文。"""
    import asyncio
    from yuxi.services.personal_file_evidence_service import personal_file_evidence_db
    from yuxi.repositories.personal_trash_repository import PersonalTrashRepository

    async with personal_app.sessions() as db:
        repository = PersonalTrashRepository(db)
        await repository.lock_user("member")
        await repository.lock_conversations("member", "main")
        token = personal_file_evidence_db.set(db)
        try:
            await asyncio.wait_for(
                validate_personal_file_evidence(SimpleNamespace(uid="member", thread_id="main")), timeout=2
            )
        finally:
            personal_file_evidence_db.reset(token)
    assert personal_file_evidence_db.get() is None
