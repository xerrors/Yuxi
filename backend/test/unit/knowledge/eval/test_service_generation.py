from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.knowledge.eval import service as eval_service_module
from yuxi.knowledge.eval.service import EvaluationService, build_evaluation_run_name


class FakeEvaluationRepository:
    def __init__(self):
        self.created_dataset = None
        self.updated_dataset = None
        self.dataset = None
        self.created_run = None

    async def create_dataset_in_session(self, session, payload):
        self.created_dataset = payload

    async def update_dataset(self, dataset_id, payload):
        self.updated_dataset = (dataset_id, payload)

    async def get_dataset(self, dataset_id):
        return self.dataset

    async def create_run_in_session(self, session, payload):
        self.created_run = payload


class FakeChunkRepository:
    def __init__(self, indexed_count):
        self.indexed_count = indexed_count

    async def count_graph_indexed_by_kb_id(self, kb_id):
        return self.indexed_count


class FakeKnowledgeBaseRepository:
    async def get_by_kb_id(self, kb_id):
        return SimpleNamespace(query_params={"options": {"top_k": 3}})


@pytest.fixture
def task_submission(monkeypatch):
    """捕获 owning transaction 内创建并在提交后发布的 Task。"""
    captured = {}

    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace()

    async def fake_create_in_session(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="task_1")

    async def fake_publish(task):
        captured["published"] = task.id

    monkeypatch.setattr(eval_service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(eval_service_module.tasker, "create_in_session", fake_create_in_session)
    monkeypatch.setattr(eval_service_module.tasker, "publish", fake_publish)
    return captured


@pytest.mark.asyncio
async def test_generate_dataset_saves_generation_params(task_submission):
    service = EvaluationService()
    service.eval_repo = FakeEvaluationRepository()
    service.chunk_repo = FakeChunkRepository(indexed_count=1)

    result = await service.generate_dataset(
        kb_id="db_1",
        name="dataset",
        description="desc",
        count=2,
        neighbors_count=3,
        concurrency_count=4,
        llm_model_spec="test:model",
        generation_mode="graph_enhanced",
        graph_expand_top_k=2,
        created_by="user_1",
    )

    assert result["task_id"] == "task_1"
    assert task_submission["payload_match"] == {"dataset_id": task_submission["payload"]["dataset_id"]}
    assert task_submission["published"] == "task_1"
    params = service.eval_repo.created_dataset["build_metadata"]["params"]
    assert params["generation_mode"] == "graph_enhanced"
    assert params["graph_expand_top_k"] == 2
    assert service.eval_repo.created_dataset["build_metadata"]["task_id"] == "task_1"


@pytest.mark.asyncio
async def test_dataset_submission_gap_remains_pending_instead_of_false_failure(monkeypatch):
    async def no_active_task(**_kwargs):
        return None

    class MissingTaskRepository:
        async def get_by_id(self, task_id):
            return None

    monkeypatch.setattr(eval_service_module.tasker, "find_task_by_payload", no_active_task)
    service = EvaluationService()
    service.eval_repo = FakeEvaluationRepository()
    service.task_repo = MissingTaskRepository()
    row = SimpleNamespace(
        dataset_id="dataset_1",
        build_metadata={"source": "generated", "status": "pending", "params": {"count": 5}},
    )

    await service._sync_dataset_build_metadata(row)

    assert row.build_metadata["status"] == "pending"
    assert service.eval_repo.updated_dataset is None


@pytest.mark.asyncio
async def test_pending_dataset_recovers_active_task_association(monkeypatch):
    active_task = SimpleNamespace(id="task_1", status="pending", progress=0, message="等待 worker")

    async def find_active_task(**kwargs):
        assert kwargs == {
            "task_type": "dataset_generation",
            "payload_match": {"dataset_id": "dataset_1"},
            "statuses": {"pending", "running"},
        }
        return active_task

    class MissingTaskRepository:
        async def get_by_id(self, task_id):
            return None

    monkeypatch.setattr(eval_service_module.tasker, "find_task_by_payload", find_active_task)
    service = EvaluationService()
    service.eval_repo = FakeEvaluationRepository()
    service.task_repo = MissingTaskRepository()
    row = SimpleNamespace(
        dataset_id="dataset_1",
        build_metadata={"source": "generated", "status": "pending", "params": {"count": 5}},
    )

    await service._sync_dataset_build_metadata(row)

    assert row.build_metadata["task_id"] == "task_1"
    assert row.build_metadata["status"] == "pending"
    assert service.eval_repo.updated_dataset == ("dataset_1", {"build_metadata": row.build_metadata})


@pytest.mark.asyncio
async def test_generate_dataset_rejects_graph_mode_without_indexed_chunks():
    service = EvaluationService()
    service.eval_repo = FakeEvaluationRepository()
    service.chunk_repo = FakeChunkRepository(indexed_count=0)

    with pytest.raises(ValueError, match="尚未完成图索引"):
        await service.generate_dataset(
            kb_id="db_1",
            name="dataset",
            description="desc",
            count=2,
            neighbors_count=3,
            concurrency_count=4,
            llm_model_spec="test:model",
            generation_mode="graph_enhanced",
            graph_expand_top_k=1,
            created_by="user_1",
        )

    assert service.eval_repo.created_dataset is None


@pytest.mark.asyncio
async def test_list_runs_projects_failed_durable_task_to_evaluation_run():
    started_at = eval_service_module.utc_now_naive()
    run = SimpleNamespace(
        run_id="run_12345678",
        name="评估",
        dataset_id="dataset_1",
        status="running",
        started_at=started_at,
        completed_at=None,
        total_items=1,
        completed_items=0,
        overall_score=None,
        retrieval_config={},
        metrics={},
    )

    class RunRepository(FakeEvaluationRepository):
        async def list_runs(self, kb_id):
            return [run]

        async def update_run(self, run_id, data):
            self.updated_run = (run_id, data)

    class FailedTaskRepository:
        async def list_by_payload_values(self, **kwargs):
            assert kwargs["payload_values"] == {"run_12345678"}
            return [
                SimpleNamespace(
                    payload={"run_id": "run_12345678"},
                    status="failed",
                    error="worker_lease_expired",
                    message="执行中断",
                    completed_at=started_at,
                )
            ]

    service = EvaluationService()
    service.eval_repo = RunRepository()
    service.task_repo = FailedTaskRepository()

    result = await service.list_runs("kb_1")

    assert result[0]["status"] == "failed"
    assert result[0]["metrics"] == {"error": "worker_lease_expired"}
    assert service.eval_repo.updated_run[0] == "run_12345678"


def test_build_evaluation_run_name_uses_eval_date_hash_format():
    name = build_evaluation_run_name(hash_value="abcdef12")

    assert name.startswith("eval-")
    assert name.endswith("-abcdef")
    assert len(name.split("-")[1]) == 8


@pytest.mark.asyncio
async def test_run_evaluation_saves_custom_name(task_submission):
    repo = FakeEvaluationRepository()
    repo.dataset = SimpleNamespace(
        dataset_id="dataset_1",
        kb_id="db_1",
        name="dataset",
        item_count=2,
        build_metadata={"status": "completed"},
    )
    service = EvaluationService()
    service.eval_repo = repo
    service.kb_repo = FakeKnowledgeBaseRepository()

    run_id = await service.run_evaluation(
        kb_id="db_1",
        dataset_id="dataset_1",
        name="  回归评估  ",
        model_config={"answer_llm": "test:model"},
        created_by="user_1",
    )

    assert run_id.startswith("run_")
    assert task_submission["published"] == "task_1"
    assert repo.created_run["name"] == "回归评估"
    assert repo.created_run["retrieval_config"]["top_k"] == 3
    assert repo.created_run["retrieval_config"]["answer_llm"] == "test:model"
