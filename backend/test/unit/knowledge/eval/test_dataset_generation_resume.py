import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from conftest import NoQueryKnowledgeBase, TrackingLlm, make_chunk

from yuxi.knowledge.eval import benchmark_generation
from yuxi.knowledge.eval import service as eval_service_module
from yuxi.knowledge.eval.benchmark_generation import iter_generated_benchmark_items
from yuxi.knowledge.eval.service import EvaluationService


@pytest.fixture(autouse=True)
def fake_chunk_repository(monkeypatch):
    class FakeChunkRepository:
        chunks = [make_chunk("anchor_chunk")]

        async def list_by_kb_id(self, kb_id):
            return [chunk for chunk in self.chunks if chunk.kb_id == kb_id]

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository",
        FakeChunkRepository,
    )
    return FakeChunkRepository


@pytest.mark.asyncio
async def test_iter_generated_benchmark_items_uses_progress_base_and_total_progress(monkeypatch):
    fake_llm = TrackingLlm()
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)
    monkeypatch.setattr(benchmark_generation, "kb_manager", NoQueryKnowledgeBase())

    progress_calls = []

    async def progress_cb(progress, message):
        progress_calls.append((progress, message))

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_id="db_1",
            count=2,
            neighbors_count=1,
            llm_model_spec="test:model",
            progress_base=3,
            total_progress=5,
            progress_cb=progress_cb,
        )
    ]

    assert len(items) == 2
    generation_calls = [(p, m) for p, m in progress_calls if m and "已生成" in m]
    assert generation_calls[-1][0] == int(99 * 5 / 5)
    assert "5/5" in generation_calls[-1][1]


def test_build_dataset_items_with_start_index():
    service = EvaluationService()
    items = service._build_dataset_items("ds_1", "kb_1", [{"query": "q1"}, {"query": "q2"}], start_index=5)
    assert items[0]["item_index"] == 5
    assert items[1]["item_index"] == 6


class FakeContext:
    def __init__(self, payload):
        self.task_id = "task_1"
        self.payload = payload
        self.progress_calls = []
        self.messages = []
        self.cancellation_reason = None

    async def set_progress(self, progress, message=None):
        self.progress_calls.append((progress, message))

    async def set_message(self, message):
        self.messages.append(message)

    def is_cancel_requested(self):
        return False

    async def raise_if_cancelled(self):
        pass

    async def run_owned_transaction(self, operation):
        await operation(None, None)


class OwnedFakeRepo:
    async def update_dataset_in_session(self, session, dataset_id, data):
        await self.update_dataset(dataset_id, data)
        return SimpleNamespace(dataset_id=dataset_id)

    async def add_dataset_items_in_session(self, session, items):
        return await self.add_dataset_items(items)


class FakeKB:
    kb_type = "milvus"


@pytest.mark.asyncio
async def test_generate_dataset_task_resumes_from_existing_items(monkeypatch):
    async def fake_iter(*args, **kwargs):
        for i in range(2):
            yield {"query": f"q{i}", "gold_answer": f"a{i}", "gold_chunk_ids": ["c1"]}

    monkeypatch.setattr(eval_service_module, "iter_generated_benchmark_items", fake_iter)

    async def mock_get_kb_config(kb_id):
        return SimpleNamespace(kb_type="milvus")

    monkeypatch.setattr(eval_service_module, "kb_manager", SimpleNamespace(get_kb_config=mock_get_kb_config))
    monkeypatch.setattr(eval_service_module, "DATASET_PERSIST_BATCH_SIZE", 1)

    added_items = []
    updated_item_counts = []

    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 3

        async def add_dataset_items(self, items):
            added_items.extend(items)

        async def update_dataset(self, dataset_id, data):
            if data.get("item_count") is not None:
                updated_item_counts.append(data["item_count"])

        async def get_dataset(self, dataset_id):
            return None

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    context = FakeContext(
        {
            "dataset_id": "ds_1",
            "kb_id": "kb_1",
            "count": 5,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "test:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        }
    )
    await service._generate_dataset_task(context)

    assert len(added_items) == 2
    assert added_items[0]["item_index"] == 3
    assert added_items[1]["item_index"] == 4
    assert updated_item_counts == [4, 5]


@pytest.mark.asyncio
async def test_generate_dataset_task_persists_in_batches(monkeypatch):
    async def fake_iter(*args, **kwargs):
        for i in range(5):
            yield {"query": f"q{i}", "gold_answer": f"a{i}", "gold_chunk_ids": ["c1"]}

    monkeypatch.setattr(eval_service_module, "iter_generated_benchmark_items", fake_iter)

    async def mock_get_kb_config(kb_id):
        return SimpleNamespace(kb_type="milvus")

    monkeypatch.setattr(eval_service_module, "kb_manager", SimpleNamespace(get_kb_config=mock_get_kb_config))
    monkeypatch.setattr(eval_service_module, "DATASET_PERSIST_BATCH_SIZE", 2)

    flush_batches = []

    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 0

        async def add_dataset_items(self, items):
            flush_batches.append(items[:])

        async def update_dataset(self, dataset_id, data):
            pass

        async def get_dataset(self, dataset_id):
            return None

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    context = FakeContext(
        {
            "dataset_id": "ds_1",
            "kb_id": "kb_1",
            "count": 5,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "test:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        }
    )
    await service._generate_dataset_task(context)

    assert len(flush_batches) == 3
    assert len(flush_batches[0]) == 2
    assert len(flush_batches[1]) == 2
    assert len(flush_batches[2]) == 1


@pytest.mark.asyncio
async def test_generate_dataset_task_fails_when_generated_count_is_below_target(monkeypatch):
    async def fake_iter(*args, **kwargs):
        yield {"query": "q1", "gold_answer": "a1", "gold_chunk_ids": ["c1"]}

    async def mock_get_kb_config(kb_id):
        return SimpleNamespace(kb_type="milvus")

    monkeypatch.setattr(eval_service_module, "iter_generated_benchmark_items", fake_iter)
    monkeypatch.setattr(eval_service_module, "kb_manager", SimpleNamespace(get_kb_config=mock_get_kb_config))

    added_items = []
    metadata_updates = []

    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 0

        async def add_dataset_items(self, items):
            added_items.extend(items)

        async def update_dataset(self, dataset_id, data):
            if "build_metadata" in data:
                metadata_updates.append(dict(data["build_metadata"]))

    service = EvaluationService()
    service.eval_repo = FakeRepo()
    context = FakeContext(
        {
            "dataset_id": "ds_1",
            "kb_id": "kb_1",
            "count": 5,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "test:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        }
    )

    with pytest.raises(ValueError, match="仅生成 1/5 道有效评估题目"):
        await service._generate_dataset_task(context)

    assert len(added_items) == 1
    assert metadata_updates[-1]["status"] == "running"


@pytest.mark.asyncio
async def test_resume_dataset_generation_enqueues_new_task(monkeypatch):
    """无进行中任务时按数据库 dedupe key 原子创建恢复任务。"""
    captured = {}

    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace()

    async def fake_create_unique(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="task_2"), True

    async def fake_publish(task):
        captured["published"] = task.id

    monkeypatch.setattr(eval_service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(eval_service_module.tasker, "create_unique_in_session", fake_create_unique)
    monkeypatch.setattr(eval_service_module.tasker, "publish", fake_publish)

    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 1

        async def get_dataset(self, dataset_id):
            return SimpleNamespace(
                dataset_id="ds_1",
                kb_id="kb_1",
                name="Test",
                description="desc",
                build_metadata={
                    "source": "generated",
                    "params": {
                        "count": 5,
                        "neighbors_count": 1,
                        "concurrency_count": 1,
                        "llm_model_spec": "test:model",
                        "generation_mode": "vector",
                        "graph_expand_top_k": 1,
                    },
                },
            )

        async def attach_dataset_generation_task_in_session(self, session, dataset_id, task_id):
            self.attached = (dataset_id, task_id)
            return SimpleNamespace(build_metadata={"status": "pending", "task_id": task_id})

        async def update_dataset(self, dataset_id, data):
            pass

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    result = await service.resume_dataset_generation("kb_1", "ds_1", "user_1")

    assert result["task_id"] == "task_2"
    assert result["message"] == "评估数据集生成任务已恢复"
    assert captured["payload_match"] == {"dataset_id": "ds_1"}
    assert "statuses" not in captured
    assert captured["payload"]["dataset_id"] == "ds_1"


@pytest.mark.asyncio
async def test_resume_dataset_generation_returns_existing_task(monkeypatch):
    """已有进行中任务时直接返回该任务（created=False），不重复创建。"""

    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace()

    async def fake_create_unique(session, **kwargs):
        return SimpleNamespace(id="task_1"), False

    monkeypatch.setattr(eval_service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(eval_service_module.tasker, "create_unique_in_session", fake_create_unique)

    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 1

        async def get_dataset(self, dataset_id):
            return SimpleNamespace(
                dataset_id="ds_1",
                kb_id="kb_1",
                name="Test",
                description="desc",
                build_metadata={"source": "generated", "params": {"count": 5}},
            )

        async def attach_dataset_generation_task_in_session(self, session, dataset_id, task_id):
            self.attached = (dataset_id, task_id)
            return SimpleNamespace(build_metadata={"status": "pending", "task_id": task_id})

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    result = await service.resume_dataset_generation("kb_1", "ds_1", "user_1")

    assert result["task_id"] == "task_1"
    assert "已有" in result["message"]


@pytest.mark.asyncio
async def test_resume_dataset_generation_when_already_complete():
    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 5

        async def get_dataset(self, dataset_id):
            return SimpleNamespace(
                dataset_id="ds_1",
                kb_id="kb_1",
                name="Test",
                description="desc",
                build_metadata={"source": "generated", "params": {"count": 5}},
            )

        async def update_dataset(self, dataset_id, data):
            self.updated = (dataset_id, data)

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    result = await service.resume_dataset_generation("kb_1", "ds_1", "user_1")

    assert result["message"] == "数据集已完成生成"


class FlakyQueryKB:
    kb_type = "milvus"

    def __init__(self, fail_after):
        self.fail_after = fail_after
        self.calls = 0

    async def aquery(self, query_text, kb_id, **kwargs):
        self.calls += 1
        if self.calls > self.fail_after:
            raise RuntimeError("kb query failed")
        return []


def make_real_generator_service(monkeypatch, kb, batch_size, added_items, metadata_updates, add_items_error=None):
    fake_llm = TrackingLlm()
    monkeypatch.setattr(benchmark_generation, "select_model", lambda model_spec: fake_llm)

    async def mock_get_kb_config(kb_id):
        return SimpleNamespace(kb_type="milvus")

    kb.get_kb_config = mock_get_kb_config
    monkeypatch.setattr(eval_service_module, "kb_manager", kb)
    monkeypatch.setattr(benchmark_generation, "kb_manager", kb)
    monkeypatch.setattr(eval_service_module, "DATASET_PERSIST_BATCH_SIZE", batch_size)

    class FakeRepo(OwnedFakeRepo):
        async def count_dataset_items(self, dataset_id):
            return 0

        async def add_dataset_items(self, items):
            if add_items_error is not None:
                raise add_items_error
            added_items.append(items[:])

        async def update_dataset(self, dataset_id, data):
            if "build_metadata" in data:
                metadata_updates.append(data["build_metadata"])

        async def get_dataset(self, dataset_id):
            return None

    service = EvaluationService()
    service.eval_repo = FakeRepo()
    return service


def make_generation_context(neighbors_count=2):
    return FakeContext(
        {
            "dataset_id": "ds_1",
            # 与本文件 autouse fixture fake_chunk_repository 中 chunk 的 kb_id 对齐
            "kb_id": "db_1",
            "count": 5,
            "neighbors_count": neighbors_count,
            "concurrency_count": 1,
            "llm_model_spec": "test:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_after", "batch_size", "expected_batches"),
    [
        (2, 1, [1, 1]),  # 已按批落库后失败
        (3, 2, [2, 1]),  # 失败时未满一批的残余 buffer 一并落库
    ],
)
async def test_generate_dataset_task_persists_partial_batches_on_failure(
    monkeypatch, fail_after, batch_size, expected_batches
):
    """生成中途失败时保留已提交 checkpoint，终态由 Task failure hook 原子投影。"""
    added_items = []
    metadata_updates = []
    service = make_real_generator_service(
        monkeypatch,
        FlakyQueryKB(fail_after=fail_after),
        batch_size,
        added_items,
        metadata_updates,
    )

    with pytest.raises(RuntimeError, match="kb query failed"):
        await service._generate_dataset_task(make_generation_context())

    assert [len(batch) for batch in added_items] == expected_batches
    assert metadata_updates[-1]["status"] == "running"


@pytest.mark.asyncio
async def test_generate_dataset_task_flushes_remaining_buffer_on_cancellation(monkeypatch):
    """取消时未满一批的残余 buffer 经 except CancelledError 分支落库（批次 2+1）。"""
    added_items = []
    metadata_updates = []
    service = make_real_generator_service(monkeypatch, FakeKB(), 2, added_items, metadata_updates)
    context = make_generation_context(neighbors_count=1)
    cancel_calls = 0

    async def raise_if_cancelled():
        nonlocal cancel_calls
        cancel_calls += 1
        if cancel_calls >= 4:
            raise asyncio.CancelledError("cancelled by test")

    context.raise_if_cancelled = raise_if_cancelled

    with pytest.raises(asyncio.CancelledError):
        await service._generate_dataset_task(context)

    assert [len(batch) for batch in added_items] == [2, 1]


@pytest.mark.asyncio
async def test_lost_lease_attempt_cannot_flush_or_overwrite_dataset_state(monkeypatch):
    """旧 attempt 已知失权后不能再提交 buffer 或 failed metadata。"""
    added_items = []
    metadata_updates = []
    service = make_real_generator_service(monkeypatch, FakeKB(), 10, added_items, metadata_updates)
    context = make_generation_context(neighbors_count=1)
    cancel_calls = 0

    async def lose_lease():
        nonlocal cancel_calls
        cancel_calls += 1
        if cancel_calls >= 3:
            context.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("lease lost")

    context.raise_if_cancelled = lose_lease

    with pytest.raises(asyncio.CancelledError):
        await service._generate_dataset_task(context)

    assert added_items == []
    assert metadata_updates[-1]["status"] == "running"


@pytest.mark.asyncio
async def test_generate_dataset_task_preserves_original_error_when_flush_fails(monkeypatch):
    """残余落库失败不掩盖原始异常，Handler 不提前提交领域失败终态。"""
    added_items = []
    metadata_updates = []
    service = make_real_generator_service(
        monkeypatch,
        FlakyQueryKB(fail_after=1),
        2,
        added_items,
        metadata_updates,
        add_items_error=RuntimeError("db down"),
    )

    with pytest.raises(RuntimeError, match="kb query failed"):
        await service._generate_dataset_task(make_generation_context())

    assert metadata_updates[-1]["status"] == "running"
