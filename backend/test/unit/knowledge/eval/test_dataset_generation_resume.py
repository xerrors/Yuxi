import asyncio
from types import SimpleNamespace

import pytest

from yuxi.knowledge.eval import benchmark_generation
from yuxi.knowledge.eval.benchmark_generation import iter_generated_benchmark_items


class FakeGenerationKnowledgeBase:
    def __init__(self, query_results=None):
        self.query_results = query_results or []
        self.query_calls = []

    async def aquery(self, query_text, kb_id, **kwargs):
        self.query_calls.append({"query_text": query_text, "kb_id": kb_id, **kwargs})
        return self.query_results


class NoQueryKnowledgeBase(FakeGenerationKnowledgeBase):
    async def aquery(self, query_text, kb_id, **kwargs):
        raise AssertionError("neighbors_count=1 时不应调用 aquery")


class TrackingLlm:
    def __init__(self, content=None, delay=0):
        self.content = content or '{"query":"问题","gold_answer":"答案","gold_chunk_ids":["anchor_chunk"]}'
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls = 0

    async def call(self, prompt, stream):
        self.calls += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return SimpleNamespace(content=self.content)
        finally:
            self.active_calls -= 1


def make_chunk(chunk_id: str, *, kb_id: str = "db_1"):
    return SimpleNamespace(
        chunk_id=chunk_id,
        kb_id=kb_id,
        file_id="file_a",
        content="anchor content",
        chunk_index=0,
        graph_indexed=False,
        ent_ids=[],
        tags=None,
        extraction_result=None,
    )


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

    progress_calls = []

    async def progress_cb(progress, message):
        progress_calls.append((progress, message))

    items = [
        item
        async for item in iter_generated_benchmark_items(
            kb_instance=NoQueryKnowledgeBase(),
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


from yuxi.knowledge.eval.service import EvaluationService


def test_build_dataset_items_with_start_index():
    service = EvaluationService()
    items = service._build_dataset_items(
        "ds_1", "kb_1", [{"query": "q1"}, {"query": "q2"}], start_index=5
    )
    assert items[0]["item_index"] == 5
    assert items[1]["item_index"] == 6


from types import SimpleNamespace

from yuxi.knowledge.eval import service as eval_service_module
from yuxi.knowledge.eval.service import EvaluationService


class FakeContext:
    def __init__(self, payload):
        self.task_id = "task_1"
        self.payload = payload
        self.progress_calls = []
        self.messages = []

    async def set_progress(self, progress, message=None):
        self.progress_calls.append((progress, message))

    async def set_message(self, message):
        self.messages.append(message)

    async def raise_if_cancelled(self):
        pass


class FakeKB:
    kb_type = "milvus"


@pytest.mark.asyncio
async def test_generate_dataset_task_resumes_from_existing_items(monkeypatch):
    async def fake_iter(*args, **kwargs):
        for i in range(2):
            yield {"query": f"q{i}", "gold_answer": f"a{i}", "gold_chunk_ids": ["c1"]}

    monkeypatch.setattr(eval_service_module, "iter_generated_benchmark_items", fake_iter)
    async def mock_aget_kb(kb_id):
        return FakeKB()

    monkeypatch.setattr(
        eval_service_module, "knowledge_base", SimpleNamespace(aget_kb=mock_aget_kb)
    )
    monkeypatch.setattr(eval_service_module.config, "dataset_persist_batch_size", 1)

    added_items = []
    updated_item_counts = []

    class FakeRepo:
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
    async def mock_aget_kb(kb_id):
        return FakeKB()

    monkeypatch.setattr(
        eval_service_module, "knowledge_base", SimpleNamespace(aget_kb=mock_aget_kb)
    )
    monkeypatch.setattr(eval_service_module.config, "dataset_persist_batch_size", 2)

    flush_batches = []

    class FakeRepo:
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
async def test_resume_dataset_generation_enqueues_new_task(monkeypatch):
    async def fake_enqueue(**kwargs):
        return SimpleNamespace(id="task_2")

    monkeypatch.setattr(eval_service_module.tasker, "enqueue", fake_enqueue)
    async def fake_find_task_by_payload(**kwargs):
        return None

    monkeypatch.setattr(eval_service_module.tasker, "find_task_by_payload", fake_find_task_by_payload)

    class FakeRepo:
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

        async def update_dataset(self, dataset_id, data):
            pass

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    result = await service.resume_dataset_generation("kb_1", "ds_1", "user_1")

    assert result["task_id"] == "task_2"
    assert result["message"] == "评估数据集生成任务已恢复"


@pytest.mark.asyncio
async def test_resume_dataset_generation_returns_existing_task(monkeypatch):
    async def fake_find_task_by_payload(**kwargs):
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        eval_service_module.tasker,
        "find_task_by_payload",
        fake_find_task_by_payload,
    )

    class FakeRepo:
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

    service = EvaluationService()
    service.eval_repo = FakeRepo()

    result = await service.resume_dataset_generation("kb_1", "ds_1", "user_1")

    assert result["task_id"] == "task_1"
    assert "已有" in result["message"]


@pytest.mark.asyncio
async def test_resume_dataset_generation_when_already_complete():
    class FakeRepo:
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
