import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.knowledge.eval import evaluator
from yuxi.knowledge.eval.evaluator import (
    aggregate_metrics,
    build_answer_prompt,
    evaluate_question,
    normalize_query_result,
)


def test_normalize_query_result_supports_dict_and_list():
    answer, chunks = normalize_query_result({"answer": "A", "retrieved_chunks": [{"content": "C"}]})
    assert answer == "A"
    assert chunks == [{"content": "C"}]

    answer, chunks = normalize_query_result([{"content": "C"}])
    assert answer == ""
    assert chunks == [{"content": "C"}]


def test_build_answer_prompt_uses_first_five_non_empty_chunks():
    chunks = [{"content": f"内容{i}"} for i in range(6)] + [{"content": ""}]

    prompt = build_answer_prompt("问题", chunks)

    assert "用户问题：问题" in prompt
    assert "内容0" in prompt
    assert "内容4" in prompt
    assert "内容5" not in prompt


async def test_evaluate_question_uses_runtime_kb_manager(monkeypatch):
    aquery = AsyncMock(return_value=[{"content": "检索结果"}])
    monkeypatch.setattr(evaluator, "kb_manager", SimpleNamespace(aquery=aquery))

    result = await evaluate_question(
        kb_id="kb-1",
        question_data={"query": "问题"},
        retrieval_config={"final_top_k": 3},
        has_gold_chunks=False,
        has_gold_answers=False,
        judge_llm=None,
        select_model_fn=lambda **_: None,
    )

    aquery.assert_awaited_once_with("问题", "kb-1", final_top_k=3)
    assert result["detail"]["retrieved_chunks"] == [{"content": "检索结果"}]


def test_aggregate_metrics_matches_service_output_shape():
    metrics, _ = aggregate_metrics(
        [{"recall@1": 1.0, "f1@1": 0.0}, {"recall@1": 0.0, "f1@1": 1.0}],
        [{"score": 1.0}, {"score": 0.0}],
        include_overall_score=True,
    )

    assert metrics["recall@1"] == 0.5
    assert metrics["f1@1"] == 0.5
    assert metrics["answer_correctness"] == 0.5
    assert metrics["overall_score"] == 0.5
