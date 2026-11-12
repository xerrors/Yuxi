from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.modules.knowledge.evaluation import service as eval_service_module
from yuxi.modules.knowledge.evaluation.service import EvaluationService, build_evaluation_run_name
from yuxi.shared.errors import InvalidInputError


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
def job_submission(monkeypatch):
    """捕获 owning transaction 内创建并在提交后发布的 Job。"""
    captured = {}

    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace()

    async def fake_create_in_session(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="job_1"), True

    async def fake_publish(job_id):
        captured["published"] = job_id

    monkeypatch.setattr(eval_service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(eval_service_module, "register_job_in_session", fake_create_in_session)
    monkeypatch.setattr(eval_service_module, "dispatch_job", fake_publish)
    return captured


@pytest.mark.asyncio
async def test_generate_dataset_saves_generation_params(job_submission):
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

    assert result["job_id"] == "job_1"
    assert job_submission["payload_match"] == {"dataset_id": job_submission["payload"]["dataset_id"]}
    assert job_submission["published"] == "job_1"
    params = service.eval_repo.created_dataset["build_metadata"]["params"]
    assert params["generation_mode"] == "graph_enhanced"
    assert params["graph_expand_top_k"] == 2
    assert service.eval_repo.created_dataset["build_metadata"]["job_id"] == "job_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_id", [None, "missing-job"])
async def test_dataset_read_preserves_persisted_state_when_job_is_missing(job_id):
    """读取不猜测断链资源的终态，也不回填新的作业关联。"""

    class MissingBackgroundJobRepository:
        async def get_by_id(self, job_id):
            return None

    service = EvaluationService()
    service.eval_repo = FakeEvaluationRepository()
    service.job_repo = MissingBackgroundJobRepository()
    row = SimpleNamespace(build_metadata={"source": "generated", "status": "pending", "job_id": job_id})
    result = await service._dataset_build_metadata(row)
    assert result == row.build_metadata
    assert service.eval_repo.updated_dataset is None


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
async def test_list_runs_preserves_persisted_business_state_without_read_side_repair():
    started_at = eval_service_module.utc_now()
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

    class FailedBackgroundJobRepository:
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
    service.job_repo = FailedBackgroundJobRepository()

    result = await service.list_runs("kb_1")

    assert result[0]["status"] == "running"
    assert result[0]["metrics"] == {}
    assert not hasattr(service.eval_repo, "updated_run")


def test_build_evaluation_run_name_uses_eval_date_hash_format():
    name = build_evaluation_run_name(hash_value="abcdef12")

    assert name.startswith("eval-")
    assert name.endswith("-abcdef")
    assert len(name.split("-")[1]) == 8


@pytest.mark.asyncio
async def test_run_evaluation_saves_custom_name(job_submission):
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
    assert job_submission["published"] == "job_1"
    assert repo.created_run["name"] == "回归评估"
    assert repo.created_run["retrieval_config"]["top_k"] == 3
    assert repo.created_run["retrieval_config"]["answer_llm"] == "test:model"


@pytest.mark.asyncio
async def test_run_evaluation_rejects_retrieval_override_outside_schema(job_submission, monkeypatch):
    """评估启动不能把超限 top_k 写进后续会原样展开的检索配置。"""

    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.milvus.model_cache.get_all_specs",
        lambda _kind=None: [],
    )
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

    with pytest.raises(InvalidInputError, match="超出允许范围"):
        await service.run_evaluation(
            kb_id="db_1",
            dataset_id="dataset_1",
            model_config={"final_top_k": 1000, "answer_llm": "test:model"},
            created_by="user_1",
        )

    assert "published" not in job_submission
    assert repo.created_run is None
