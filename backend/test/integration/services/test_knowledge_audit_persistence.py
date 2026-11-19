"""模块审计涉及的检索来源、失败统计和用户事务事实。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test.integration.support.module_audit import isolated_audit_store
from test.unit.support.milvus import FakeCollection, make_kb
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.identity.models import User
from yuxi.modules.identity.repositories.users import UserRepository
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.mcp.references import knowledge_reference_sources
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeChunk, KnowledgeFile
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.services.background_jobs import fail_knowledge_file_job
from yuxi.workers.background_jobs import process_background_job

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("mode", ["vector", "keyword", "hybrid", "graph"])
async def test_retrieval_to_agent_reference_uses_postgres_filename(monkeypatch, mode):
    """完整检索返回的引用标题由当前 PostgreSQL 文件名决定。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            db.add(KnowledgeBase(kb_id="kb-source", name="source", kb_type="milvus"))
            await db.flush()
            db.add(KnowledgeFile(file_id="file-1", kb_id="kb-source", filename="当前文档.md", status="indexed"))
            await db.flush()
            db.add(KnowledgeChunk(chunk_id="chunk-1", file_id="file-1", kb_id="kb-source", chunk_index=0, content="权威正文"))
        monkeypatch.setattr(
            "yuxi.modules.knowledge.implementations.milvus.get_embedding_function",
            lambda spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts],
        )
        kb = make_kb(FakeCollection())
        kb._hydrate_chunk_sources = MilvusKB._hydrate_chunk_sources.__get__(kb)
        kb._build_retrieval_scope = MilvusKB._build_retrieval_scope.__get__(kb)
        if mode == "graph":
            from unittest.mock import AsyncMock

            kb._retrieve_graph_chunks = AsyncMock(
                return_value=[
                    {"content": "旧投影", "metadata": {"file_id": "file-1", "chunk_id": "chunk-1", "source": "未知来源"}, "score": 1}
                ]
            )
            kb._get_existing_milvus_collection.return_value.query = lambda **kwargs: [{"chunk_id": "chunk-1"}]
        chunks = await kb.aquery(
            "query",
            "kb-source",
            config=KnowledgeBaseConfig(kb_id="kb-source", kb_type="milvus", embedding_model_spec="test:embedding"),
            search_mode="keyword" if mode == "graph" else mode,
            use_graph_retrieval=mode == "graph",
        )
        sources = knowledge_reference_sources(chunks, "query_kb", "kb-source")
        assert len(sources) == 1
        assert sources[0]["title"] == "当前文档.md"
        assert sources[0]["content"] == "权威正文"


@pytest.mark.parametrize("rollback", [False, True])
async def test_job_failure_atomically_refreshes_only_owned_file_stats(monkeypatch, rollback):
    """失败 hook 的文件终态和统计同事务提交或回滚，其他 owner 不受影响。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            db.add(
                KnowledgeBase(
                    kb_id="kb-failure", name="failure", kb_type="milvus", additional_params={"keep": True, "stats": {"processing_count": 3}}
                )
            )
            await db.flush()
            db.add_all(
                [
                    KnowledgeFile(
                        file_id="parse", kb_id="kb-failure", filename="parse.md", status="parsing", processing_job_id="failed-job"
                    ),
                    KnowledgeFile(
                        file_id="index", kb_id="kb-failure", filename="index.md", status="indexing", processing_job_id="failed-job"
                    ),
                    KnowledgeFile(
                        file_id="other", kb_id="kb-failure", filename="other.md", status="indexing", processing_job_id="other-job"
                    ),
                ]
            )
        async with sessions() as db:
            await fail_knowledge_file_job(db, SimpleNamespace(id="failed-job"), "worker crashed")
            if rollback:
                await db.rollback()
            else:
                await db.commit()
        async with sessions() as db:
            files = {row.file_id: row for row in (await db.scalars(select(KnowledgeFile))).all()}
            kb = await db.scalar(select(KnowledgeBase))
            assert files["other"].status == "indexing"
            assert files["other"].processing_job_id == "other-job"
            assert kb.additional_params["keep"] is True
            if rollback:
                assert files["parse"].status == "parsing"
                assert files["index"].status == "indexing"
                assert kb.additional_params["stats"]["processing_count"] == 3
            else:
                assert files["parse"].status == "error_parsing"
                assert files["index"].status == "error_indexing"
                assert files["parse"].processing_job_id is None
                assert files["index"].processing_job_id is None
                stats = await KnowledgeFileRepository().query_kb_file_stats("kb-failure", session=db)
                assert stats["processing_count"] == 1
                assert stats["pending_index_count"] == 1
                assert kb.additional_params["stats"] == stats


async def test_user_repository_injected_session_preserves_commit_and_rollback_owner(monkeypatch):
    """用户写入只 flush，提交或回滚由注入会话的用例拥有。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            user = await UserRepository(db).create({"username": "owner", "uid": "owner", "password_hash": "disabled", "role": "user"})
            await UserRepository(db).update(user.id, {"username": "committed"})
        async with sessions() as db:
            stored = await db.scalar(select(User).where(User.uid == "owner"))
            assert stored.username == "committed"
            stored.username = "rolled-back"
            await UserRepository(db).save(stored)
            await db.rollback()
        async with sessions() as db:
            assert await db.scalar(select(User.username).where(User.uid == "owner")) == "committed"


@pytest.mark.parametrize("operation", ["create", "save", "update", "delete_for_admin", "lock_identity_changes"])
async def test_user_repository_requires_caller_transaction_for_writes(monkeypatch, operation):
    """无参写入明确拒绝；不让 detached 实体变更被静默忽略。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            user = User(username="owner", uid="owner", password_hash="disabled", role="user")
            db.add(user)
        user.username = "detached-change"
        repo = UserRepository()
        with pytest.raises(RuntimeError, match="用户写入需要调用方事务"):
            if operation == "create":
                await repo.create({"username": "new", "uid": "new", "password_hash": "disabled", "role": "user"})
            elif operation == "save":
                await repo.save(user)
            elif operation == "update":
                await repo.update(user.id, {"username": "changed"})
            elif operation == "delete_for_admin":
                await repo.delete_for_admin(user)
            else:
                await repo.lock_identity_changes()
        async with sessions() as db:
            stored = await db.scalar(select(User).where(User.uid == "owner"))
            assert stored.username == "owner" and not stored.is_deleted
            assert await db.scalar(select(User).where(User.uid == "new")) is None
        assert (await repo.get_by_uid("owner")).username == "owner"


async def test_shipping_job_failure_commits_job_files_and_stats_together(monkeypatch):
    """真实 worker 入口的失败收尾同时提交 Job、文件与统计事实。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            db.add(KnowledgeBase(kb_id="kb-worker", name="worker", kb_type="dify", additional_params={"stats": {"processing_count": 1}}))
            await db.flush()
            db.add(
                KnowledgeFile(
                    file_id="worker-file", kb_id="kb-worker", filename="worker.md", status="parsing", processing_job_id="worker-job"
                )
            )
            db.add(
                BackgroundJobRecord(
                    id="worker-job",
                    name="worker-job",
                    type="knowledge_parse",
                    payload={"kb_id": "kb-worker", "file_ids": ["worker-file"], "operator_id": "fixture"},
                )
            )
        await process_background_job({"worker_id": "fixture-worker"}, "worker-job")
        async with sessions() as db:
            job = await db.get(BackgroundJobRecord, "worker-job")
            file = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "worker-file"))
            kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == "kb-worker"))
            assert job.status == "failed"
            assert job.attempt_count == 1
            assert job.worker_id is None
            assert file.status == "error_parsing"
            assert file.processing_job_id is None
            assert kb.additional_params["stats"]["processing_count"] == 0
            assert kb.additional_params["stats"] == await KnowledgeFileRepository().query_kb_file_stats("kb-worker", session=db)
