"""
Integration tests for dataset generation resume endpoint.
"""

from __future__ import annotations

import uuid

import pytest

from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(autouse=True)
async def reinit_pg_manager():
    """重新初始化 pg_manager 异步引擎，使其绑定到当前测试的事件循环。"""
    if pg_manager.async_engine:
        await pg_manager.async_engine.dispose()
    pg_manager._initialized = False
    pg_manager.initialize()
    yield


async def test_resume_dataset_generation_enqueues_task(
    test_client, admin_headers, knowledge_database
):
    repo = EvaluationRepository()
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    await repo.create_dataset(
        {
            "dataset_id": dataset_id,
            "kb_id": knowledge_database["kb_id"],
            "name": "Pytest Resume Dataset",
            "description": "for resume integration test",
            "item_count": 0,
            "has_gold_chunks": True,
            "has_gold_answers": True,
            "build_metadata": {
                "source": "generated",
                "status": "failed",
                "params": {
                    "count": 5,
                    "neighbors_count": 1,
                    "concurrency_count": 1,
                    "llm_model_spec": "test:model",
                    "generation_mode": "vector",
                    "graph_expand_top_k": 1,
                },
            },
            "created_by": "admin",
        }
    )

    try:
        response = await test_client.post(
            f"/api/evaluation/databases/{knowledge_database['kb_id']}/datasets/{dataset_id}/resume",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload.get("message") == "success"
        assert payload.get("data", {}).get("task_id")
        assert payload.get("data", {}).get("message") == "评估数据集生成任务已恢复"
    finally:
        from yuxi.services.task_service import tasker

        # Best-effort cleanup of any task enqueued during the test
        tasks = (await tasker.list_tasks()).get("tasks", [])
        for task in tasks:
            if (task.get("payload") or {}).get("dataset_id") == dataset_id:
                await tasker.delete_task(task["id"])
                break
