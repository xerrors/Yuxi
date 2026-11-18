"""评估删除和作业取消通过路由、worker 与隔离 PostgreSQL 验证。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.dependencies.knowledge import require_knowledge_base_manage, require_knowledge_base_read
from yuxi.api.routers.knowledge import evaluation as router
from yuxi.modules.background_jobs import repository as job_repository
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.background_jobs.repository import BackgroundJobRepository
from yuxi.modules.knowledge.evaluation import evaluator, service
from yuxi.modules.knowledge.models import (
    EvaluationDataset,
    EvaluationDatasetItem,
    EvaluationRun,
    EvaluationRunItem,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeFile,
)
from yuxi.modules.knowledge.repositories import bases, evaluation, files
from yuxi.workers import background_jobs

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def evaluation_environment(monkeypatch):
    """独占测试 schema，替换外部模型/检索与队列投递，保留领域和事务装配。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_eval_cancel")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    models = (
        BackgroundJobRecord,
        KnowledgeBase,
        KnowledgeFile,
        KnowledgeChunk,
        EvaluationDataset,
        EvaluationDatasetItem,
        EvaluationRun,
        EvaluationRunItem,
    )
    try:
        async with engine.begin() as connection:
            for model in models:
                await connection.run_sync(model.__table__.create)
        for module in (evaluation, bases, files, job_repository, service):
            monkeypatch.setattr(module, "pg_manager", manager)
        monkeypatch.setattr(service, "dispatch_job", AsyncMock())
        monkeypatch.setattr(background_jobs, "publish_job", AsyncMock())
        async with manager.get_async_session_context() as session:
            session.add(KnowledgeBase(kb_id="kb", name="合成评测", kb_type="milvus", query_params={"options": {"final_top_k": 3}}))
            await session.flush()
            session.add(
                KnowledgeFile(
                    kb_id="kb", file_id="file-1", filename="合成守则", content_hash="frozen-source", generation=1, active_generation=1
                )
            )
        app = FastAPI()
        app.include_router(router.evaluation, prefix="/api")
        actor = SimpleNamespace(uid="evaluation-fixture")
        app.dependency_overrides[require_knowledge_base_manage] = lambda: actor
        app.dependency_overrides[require_knowledge_base_read] = lambda: actor
        app.dependency_overrides[router.require_evaluation_dataset_read] = lambda: actor
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield manager, client
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("stage", ["pending", "retrieval", "generation", "judging", "item_write", "progress_write"])
async def test_delete_evaluation_stops_active_worker_and_preserves_other_run(evaluation_environment, monkeypatch, stage):
    """删除后持久取消且不影响同数据集的另一轮评估。"""
    manager, client = evaluation_environment
    entered, release = asyncio.Event(), asyncio.Event()
    at_persistence = stage in {"item_write", "progress_write"}

    async def hold_stage(name):
        """将 worker 停在本用例指定的边界。"""
        if stage == name:
            entered.set()
            await release.wait()

    async def generate(*args, **kwargs):
        await hold_stage("generation")
        return SimpleNamespace(content="18:00")

    async def judge_answer(**kwargs):
        await hold_stage("judging")
        return {"score": 1.0}

    answer = AsyncMock(side_effect=generate)
    judge = AsyncMock(side_effect=judge_answer)

    async def retrieve(*args, **kwargs):
        await hold_stage("retrieval")
        return [{"content": "18 点关门"}]

    monkeypatch.setattr(evaluator.kb_manager, "aquery", retrieve)
    monkeypatch.setattr(service, "select_model", lambda **_kwargs: SimpleNamespace(call=answer))
    monkeypatch.setattr(evaluator.EvaluationMetricsCalculator, "calculate_answer_metrics", judge)

    if at_persistence:
        method = "_persist_run_item" if stage == "item_write" else "_persist_completed_items"
        persist = getattr(service.EvaluationService, method)

        async def hold_before_persist(self, *args):
            """在作业已通过取消检查、持久化事务尚未加锁时暂停。"""
            await hold_stage(stage)
            return await persist(self, *args)

        monkeypatch.setattr(service.EvaluationService, method, hold_before_persist)

    runs_path = "/api/evaluation/knowledge-bases/kb/runs"
    response = await client.post(
        "/api/evaluation/knowledge-bases/kb/datasets/upload",
        data={"name": "合成评测"},
        files={"file": ("synthetic.jsonl", b'{"query":"When?","gold_answer":"18:00"}', "application/x-ndjson")},
    )
    assert response.status_code == 200, response.text
    dataset_id = response.json()["data"]["dataset_id"]
    run_ids = []
    for _ in range(2):
        response = await client.post(runs_path, json={"dataset_id": dataset_id, "model_config": {"answer_llm": "answer"}})
        assert response.status_code == 200, response.text
        run_ids.append(response.json()["data"]["run_id"])
    run_id, other_run_id = run_ids
    jobs = BackgroundJobRepository()
    job = await jobs.find_latest_by_payload(job_type="rag_evaluation", payload_match={"run_id": run_id})
    other_job = await jobs.find_latest_by_payload(job_type="rag_evaluation", payload_match={"run_id": other_run_id})
    execution = None
    try:
        if stage != "pending":
            execution = asyncio.create_task(background_jobs.process_background_job({}, job.id))
            await asyncio.wait_for(entered.wait(), timeout=5)
        if stage == "progress_write":
            async with manager.get_async_session_context() as session:
                assert await session.scalar(select(func.count(EvaluationRunItem.id)).where(EvaluationRunItem.run_id == run_id)) == 1
        response = await client.delete(f"{runs_path}/{run_id}")
        assert response.status_code == 200, response.text
        assert (await jobs.get_by_id(job.id)).cancel_requested == 1
    finally:
        release.set()
        if execution is not None:
            await asyncio.wait_for(execution, timeout=5)
    if stage == "pending":
        await background_jobs.process_background_job({}, job.id)

    cancelled = await jobs.get_by_id(job.id)
    assert (cancelled.status, cancelled.worker_id) == ("cancelled", None)
    if stage != "pending":
        assert cancelled.error == "任务已取消"
    unaffected = await jobs.get_by_id(other_job.id)
    assert (unaffected.status, unaffected.cancel_requested) == ("pending", 0)
    assert answer.await_count == int(at_persistence or stage in {"generation", "judging"})
    assert judge.await_count == int(at_persistence or stage == "judging")
    if at_persistence:
        await background_jobs.process_background_job({}, other_job.id)
        assert (await jobs.get_by_id(other_job.id)).status == "success"
    async with manager.get_async_session_context() as session:
        assert await session.scalar(select(EvaluationRun.id).where(EvaluationRun.run_id == run_id)) is None
        assert await session.scalar(select(func.count(EvaluationRunItem.id)).where(EvaluationRunItem.run_id == run_id)) == 0
        assert await session.scalar(select(EvaluationRun.id).where(EvaluationRun.run_id == other_run_id)) is not None
        if at_persistence:
            assert await session.scalar(select(func.count(EvaluationRunItem.id)).where(EvaluationRunItem.run_id == other_run_id)) == 1
