from types import SimpleNamespace

from yuxi.modules.knowledge.evaluation import evaluator as evaluator_module
from yuxi.modules.knowledge.evaluation.service import EvaluationService


class FakeContext:
    def __init__(self, payload: dict):
        self.payload = payload

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_result(self, result: dict) -> None:
        return None

    async def raise_if_cancelled(self) -> None:
        return None

    async def run_owned_transaction(self, operation) -> None:
        await operation(None, None)


class FakeEvaluationRepository:
    def __init__(self, items):
        self.items = items
        self.run_items: dict[int, dict] = {}

    async def get_dataset(self, dataset_id: str):
        return SimpleNamespace(kb_id="kb-1", has_gold_chunks=True, has_gold_answers=False)

    async def list_all_dataset_items(self, dataset_id: str):
        return self.items

    async def upsert_run_item_in_session(self, session, run_id: str, item_index: int, data: dict):
        self.run_items[item_index] = data

    async def update_run_in_session(self, session, run_id: str, data: dict):
        return object()


class FakeChunkRepository:
    def __init__(self, chunk_ids):
        self.chunk_ids = chunk_ids

    async def list_chunk_ids_by_kb_id(self, kb_id: str) -> list[str]:
        return self.chunk_ids


def make_item(item_id: str, query: str, gold_chunk_ids: list[str]):
    return SimpleNamespace(item_id=item_id, query_text=query, gold_chunk_ids=gold_chunk_ids, gold_answer=None)


async def test_evaluation_excludes_stale_gold_chunks_and_reports_warning(monkeypatch):
    # 文件同参数重新入库：gold 的旧代次后缀仍能对上；换了分块参数的参考块已不在知识库，需标出而非计 0 分
    items = [
        make_item("item-1", "q1", ["file_a_chunk_0-g1"]),
        make_item("item-2", "q2", ["file_b_chunk_5-g1", "file_b_chunk_6-g1"]),
    ]
    repo = FakeEvaluationRepository(items)
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    service.chunk_repo = FakeChunkRepository(["file_a_chunk_0-g2", "file_b_chunk_0-g3"])

    async def fake_aquery(query_text, kb_id, **kwargs):
        return [{"chunk_id": "file_a_chunk_0-g2"}, {"chunk_id": "file_b_chunk_0-g3"}]

    monkeypatch.setattr(evaluator_module.kb_manager, "aquery", fake_aquery)

    result = await service._run_evaluation_job(
        FakeContext({"run_id": "run-1", "kb_id": "kb-1", "dataset_id": "dataset-1", "retrieval_config": {}})
    )

    warning = "2 个参考块已失效，对应文件可能重新入库过或分块参数已变化，相关题目未计入检索指标"
    assert result["metrics"]["stale_gold_chunk_count"] == 2
    assert result["metrics"]["warning"] == warning
    assert result["metrics"]["recall@10"] == 1.0
    assert result["overall_score"] == 1.0

    assert repo.run_items[0]["metrics"]["recall@1"] == 1.0
    assert "warning" not in repo.run_items[0]["metrics"]
    stale_metrics = repo.run_items[1]["metrics"]
    assert stale_metrics["stale_gold_chunk_count"] == 2
    assert stale_metrics["warning"] == warning
    assert "recall@10" not in stale_metrics


async def test_evaluation_without_stale_gold_chunks_has_no_warning(monkeypatch):
    repo = FakeEvaluationRepository([make_item("item-1", "q1", ["file_a_chunk_0"])])
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    service.chunk_repo = FakeChunkRepository(["file_a_chunk_0-g2"])

    async def fake_aquery(query_text, kb_id, **kwargs):
        return [{"chunk_id": "file_a_chunk_1-g2"}]

    monkeypatch.setattr(evaluator_module.kb_manager, "aquery", fake_aquery)

    result = await service._run_evaluation_job(
        FakeContext({"run_id": "run-1", "kb_id": "kb-1", "dataset_id": "dataset-1", "retrieval_config": {}})
    )

    # 参考块仍存在但没检索到，是真实的 0 分，必须计入平均值
    assert "warning" not in result["metrics"]
    assert result["metrics"]["recall@10"] == 0.0
    assert repo.run_items[0]["metrics"]["recall@10"] == 0.0
