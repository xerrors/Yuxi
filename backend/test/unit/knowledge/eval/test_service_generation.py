from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import timedelta

import pytest

from yuxi.knowledge.eval import service as eval_service_module
from yuxi.knowledge.eval.service import EvaluationService, build_evaluation_run_name


class FakeEvaluationRepository:
    def __init__(self):
        self.created_dataset = None
        self.updated_dataset = None
        self.dataset = None
        self.created_run = None
        self.deleted_dataset = False

    async def create_dataset_in_session(self, session, payload):
        self.created_dataset = payload

    async def update_dataset(self, dataset_id, payload):
        self.updated_dataset = (dataset_id, payload)

    async def get_dataset(self, dataset_id):
        return self.dataset

    async def delete_dataset(self, dataset_id):
        """记录数据集删除副作用。"""
        self.deleted_dataset = True

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


@pytest.mark.asyncio
async def test_list_runs_projects_cancelled_durable_task_to_cancelled_run():
    started_at = eval_service_module.utc_now_naive()
    run = SimpleNamespace(
        run_id="run_12345678",
        name="评估",
        dataset_id="dataset_1",
        status="running",
        started_at=started_at,
        completed_at=None,
        total_items=1,
        completed_items=7,
        overall_score=None,
        retrieval_config={},
        metrics={},
    )

    class RunRepository(FakeEvaluationRepository):
        async def list_runs(self, kb_id):
            return [run]

        async def update_run(self, run_id, data):
            self.updated_run = (run_id, data)

    class CancelledTaskRepository:
        async def list_by_payload_values(self, **kwargs):
            return [
                SimpleNamespace(
                    payload={"run_id": "run_12345678"},
                    status="cancelled",
                    error="任务已取消",
                    message="任务已取消",
                    completed_at=started_at,
                )
            ]

    service = EvaluationService()
    service.eval_repo = RunRepository()
    service.task_repo = CancelledTaskRepository()

    result = await service.list_runs("kb_1")

    assert result[0]["status"] == "cancelled"
    assert result[0]["completed_items"] == 7
    assert service.eval_repo.updated_run[1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_list_runs_projects_orphan_running_run_to_failed():
    started_at = eval_service_module.utc_now_naive() - timedelta(minutes=2)
    run = SimpleNamespace(
        run_id="run_12345678",
        name="评估",
        dataset_id="dataset_1",
        status="running",
        started_at=started_at,
        completed_at=None,
        total_items=1,
        completed_items=7,
        overall_score=None,
        retrieval_config={},
        metrics={},
    )

    class RunRepository(FakeEvaluationRepository):
        async def list_runs(self, kb_id):
            return [run]

        async def update_run(self, run_id, data):
            self.updated_run = (run_id, data)

    class MissingTaskRepository:
        async def list_by_payload_values(self, **kwargs):
            return []

    service = EvaluationService()
    service.eval_repo = RunRepository()
    service.task_repo = MissingTaskRepository()

    result = await service.list_runs("kb_1")

    assert result[0]["status"] == "failed"
    assert result[0]["completed_items"] == 7
    assert service.eval_repo.updated_run[1]["status"] == "failed"
    assert service.eval_repo.updated_run[1]["metrics"] == {"error": "评估任务提交中断"}


@pytest.mark.asyncio
async def test_cancel_run_requests_active_durable_task(monkeypatch):
    run = SimpleNamespace(run_id="run_12345678", kb_id="kb_1", status="running")

    class RunRepository:
        async def get_run(self, run_id):
            return run

    class TaskRepository:
        async def find_latest_by_payload(self, **kwargs):
            assert kwargs["task_type"] == "rag_evaluation"
            assert kwargs["payload_match"] == {"run_id": "run_12345678"}
            assert kwargs["statuses"] == {"pending", "running"}
            return SimpleNamespace(id="task_1")

    class Tasker:
        async def cancel_task(self, task_id):
            assert task_id == "task_1"
            return SimpleNamespace(status="cancelled", cancel_requested=True)

    service = EvaluationService()
    service.eval_repo = RunRepository()
    service.task_repo = TaskRepository()
    monkeypatch.setattr(eval_service_module, "tasker", Tasker())

    result = await service.cancel_run("kb_1", "run_12345678")

    assert result == {
        "run_id": "run_12345678",
        "task_id": "task_1",
        "status": "cancelled",
        "cancel_requested": True,
    }


@pytest.mark.asyncio
async def test_cancel_run_rejects_terminal_run():
    class RunRepository:
        async def get_run(self, run_id):
            return SimpleNamespace(run_id="run_12345678", kb_id="kb_1", status="completed")

    service = EvaluationService()
    service.eval_repo = RunRepository()

    with pytest.raises(ValueError, match="已结束"):
        await service.cancel_run("kb_1", "run_12345678")


@pytest.mark.asyncio
async def test_delete_run_rejects_running_run():
    class RunRepository:
        def __init__(self):
            self.deleted = False

        async def get_run(self, run_id):
            return SimpleNamespace(run_id="run_12345678", kb_id="kb_1", status="running")

        async def delete_run(self, run_id):
            self.deleted = True

    repo = RunRepository()
    service = EvaluationService()
    service.eval_repo = repo

    with pytest.raises(ValueError, match="先取消"):
        await service.delete_run("kb_1", "run_12345678")

    assert repo.deleted is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
async def test_delete_dataset_rejects_active_generation(status):
    """等待中和运行中的生成任务都阻止数据集删除。"""

    class ActiveTaskRepository:
        """提供尚未结束的生成任务。"""

        async def get_by_id(self, task_id):
            """返回数据集关联的活跃任务。"""
            assert task_id == "task_1"
            return SimpleNamespace(status=status, progress=30, message="生成中")

    repo = FakeEvaluationRepository()
    repo.dataset = SimpleNamespace(
        dataset_id="dataset_1",
        build_metadata={"source": "generated", "status": status, "task_id": "task_1"},
    )
    service = EvaluationService()
    service.eval_repo = repo
    service.task_repo = ActiveTaskRepository()

    with pytest.raises(ValueError, match="先取消生成任务"):
        await service.delete_dataset("dataset_1")

    assert repo.deleted_dataset is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", None])
async def test_delete_dataset_allows_inactive_dataset(status):
    """终态及没有生成元数据的数据集保持可删除。"""
    repo = FakeEvaluationRepository()
    repo.dataset = SimpleNamespace(
        dataset_id="dataset_1",
        build_metadata={"source": "generated", "status": status} if status else None,
    )
    service = EvaluationService()
    service.eval_repo = repo

    await service.delete_dataset("dataset_1")

    assert repo.deleted_dataset is True


@pytest.mark.asyncio
@pytest.mark.parametrize("task_status", ["success", "failed", "cancelled"])
async def test_delete_dataset_projects_terminal_task_before_delete(task_status):
    """先同步任务终态，避免陈旧 running 元数据误拦删除。"""

    class TerminalTaskRepository:
        """提供已经结束的生成任务。"""

        async def get_by_id(self, task_id):
            """返回数据集关联的终态任务。"""
            assert task_id == "task_1"
            return SimpleNamespace(status=task_status, error=None, message="任务结束")

    repo = FakeEvaluationRepository()
    repo.dataset = SimpleNamespace(
        dataset_id="dataset_1",
        build_metadata={"source": "generated", "status": "running", "task_id": "task_1"},
    )
    service = EvaluationService()
    service.eval_repo = repo
    service.task_repo = TerminalTaskRepository()

    await service.delete_dataset("dataset_1")

    expected_status = "completed" if task_status == "success" else "failed"
    assert repo.dataset.build_metadata["status"] == expected_status
    assert repo.updated_dataset == ("dataset_1", {"build_metadata": repo.dataset.build_metadata})
    assert repo.deleted_dataset is True


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


@pytest.mark.asyncio
async def test_run_evaluation_forces_query_error_fail_fast(task_submission):
    repo = FakeEvaluationRepository()
    repo.dataset = SimpleNamespace(
        dataset_id="dataset_1",
        kb_id="db_1",
        name="dataset",
        item_count=1,
        build_metadata={"status": "completed"},
    )
    service = EvaluationService()
    service.eval_repo = repo
    service.kb_repo = FakeKnowledgeBaseRepository()

    await service.run_evaluation(
        kb_id="db_1",
        dataset_id="dataset_1",
        model_config={"raise_on_error": False},
    )

    assert repo.created_run["retrieval_config"]["raise_on_error"] is True
