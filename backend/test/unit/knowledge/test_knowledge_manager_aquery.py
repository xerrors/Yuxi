"""验证知识库查询层与真实执行器的静默降级边界。"""

from types import SimpleNamespace

import pytest

from yuxi.knowledge.implementations.dify import DifyKB
from yuxi.knowledge.implementations.notion import NotionKB
from yuxi.knowledge.manager import KnowledgeBaseManager


async def test_dify_executor_raises_when_opted_in(monkeypatch, tmp_path):
    """开启严格模式时，Dify 请求失败必须上抛。"""
    executor = DifyKB(work_dir=str(tmp_path))

    async def failing_request(**kwargs):
        """模拟真实请求失败。"""
        raise RuntimeError("dify unavailable")

    monkeypatch.setattr(executor, "_request_dify", failing_request)

    with pytest.raises(RuntimeError, match="dify unavailable"):
        await executor.aquery("query", "kb_1", config=_kb_config(), raise_on_error=True)


async def test_dify_executor_preserves_default_silent_degradation(monkeypatch, tmp_path):
    """Dify 默认保留执行器内部的空结果降级。"""
    executor = DifyKB(work_dir=str(tmp_path))

    async def failing_request(**kwargs):
        """模拟真实请求失败。"""
        raise RuntimeError("dify unavailable")

    monkeypatch.setattr(executor, "_request_dify", failing_request)

    assert await executor.aquery("query", "kb_1", config=_kb_config()) == []


async def test_notion_executor_raises_when_opted_in(monkeypatch, tmp_path):
    """有效配置下的 Notion 检索故障在严格模式中上抛。"""
    executor = NotionKB(work_dir=str(tmp_path))

    async def failing_search(*args, **kwargs):
        """在通过连接配置校验后模拟检索失败。"""
        raise ValueError("notion unavailable")

    monkeypatch.setattr(executor, "_search_candidate_pages", failing_search)

    with pytest.raises(ValueError, match="notion unavailable"):
        await executor.aquery("query", "kb_1", config=_notion_config(), raise_on_error=True)


async def test_notion_executor_preserves_default_silent_degradation(monkeypatch, tmp_path):
    """Notion 默认保留执行器内部的空结果降级。"""
    executor = NotionKB(work_dir=str(tmp_path))

    async def failing_search(*args, **kwargs):
        """在通过连接配置校验后模拟检索失败。"""
        raise ValueError("notion unavailable")

    monkeypatch.setattr(executor, "_search_candidate_pages", failing_search)

    assert await executor.aquery("query", "kb_1", config=_notion_config()) == []


@pytest.mark.parametrize("query_options", [{}, {"raise_on_error": True}])
async def test_aquery_propagates_executor_error(monkeypatch, query_options):
    """共享入口在默认和严格模式中都保留执行器的异常。"""
    manager = KnowledgeBaseManager.__new__(KnowledgeBaseManager)

    async def failing_executor(*args, **kwargs):
        """模拟主检索失败。"""
        raise RuntimeError("query failed")

    async def get_config(kb_id):
        """返回有效的知识库类型。"""
        return SimpleNamespace(kb_type="milvus")

    async def get_instance(kb_type):
        """返回会抛出主检索异常的执行器。"""
        return SimpleNamespace(aquery=failing_executor)

    monkeypatch.setattr(manager, "get_kb_config", get_config)
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", get_instance)

    with pytest.raises(RuntimeError, match="query failed"):
        await manager.aquery("query", "kb_1", **query_options)


async def test_aquery_propagates_config_error_by_default(monkeypatch):
    """共享入口不把配置读取故障降级成成功空结果。"""
    manager = KnowledgeBaseManager.__new__(KnowledgeBaseManager)

    async def get_config(kb_id):
        """模拟配置读取失败。"""
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(manager, "get_kb_config", get_config)

    with pytest.raises(RuntimeError, match="config unavailable"):
        await manager.aquery("query", "kb_1")


@pytest.mark.parametrize("raise_on_error", [False, True])
async def test_aquery_forwards_error_contract_to_executor(monkeypatch, raise_on_error):
    """共享入口透传错误策略和普通查询选项。"""
    manager = KnowledgeBaseManager.__new__(KnowledgeBaseManager)
    received_options = {}

    async def executor_query(*args, **kwargs):
        """记录查询选项并返回空结果。"""
        received_options.update(kwargs)
        return []

    async def get_config(kb_id):
        """返回有效的知识库类型。"""
        return SimpleNamespace(kb_type="dify")

    async def get_instance(kb_type):
        """返回记录查询参数的执行器。"""
        return SimpleNamespace(aquery=executor_query)

    monkeypatch.setattr(manager, "get_kb_config", get_config)
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", get_instance)

    assert await manager.aquery("query", "kb_1", raise_on_error=raise_on_error, final_top_k=3) == []
    assert received_options["raise_on_error"] is raise_on_error
    assert received_options["final_top_k"] == 3


def _kb_config():
    """构造 Dify 执行器测试所需的最小配置。"""
    return SimpleNamespace(
        kb_type="dify",
        query_options={},
        additional_params={"dify_api_url": "https://dify.example/v1", "dify_token": "token", "dify_dataset_id": "ds"},
    )


def _notion_config():
    """构造 Notion 执行器测试所需的最小连接配置。"""
    return SimpleNamespace(
        query_options={},
        additional_params={"notion_token": "token", "notion_data_source_id": "data_source"},
    )
