"""活动代次过滤只展开例外文件，不再把每个 file_id 都写进 Milvus 表达式。"""

import pytest

from yuxi.modules.knowledge.implementations.milvus import MilvusKB


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    async def list_active_generations(self, kb_id):
        assert kb_id == "kb"
        return list(self.rows)


@pytest.fixture
def kb(monkeypatch):
    holder = {}

    def factory():
        return holder["repo"]

    monkeypatch.setattr("yuxi.modules.knowledge.implementations.milvus.KnowledgeFileRepository", factory)
    return object.__new__(MilvusKB), holder


async def test_single_generation_does_not_list_every_file(kb):
    """同一代次只保留 generation 条件。把全部 file_id 拼回去会使本测试失败。"""
    instance, holder = kb
    holder["repo"] = _Rows([(f"file-{index}", 1) for index in range(5)])
    expression = await instance._build_active_generation_expr("kb")
    assert expression == "generation == 1"
    assert "file-4" not in expression


async def test_minority_generation_is_the_only_explicit_file_list(kb):
    """多数代次用排除列表，正在重建的少数文件单独列出。"""
    instance, holder = kb
    holder["repo"] = _Rows([("b", 1), ("a", 1), ("c", 2)])
    expression = await instance._build_active_generation_expr("kb")
    assert expression == '(generation == 1 and file_id not in ["c"]) or (generation == 2 and file_id in ["c"])'


async def test_tied_generations_prefer_the_lower_generation(kb):
    """文件数相同时，较小代次作为多数，避免表达式随查询顺序变化。"""
    instance, holder = kb
    holder["repo"] = _Rows([("b", 2), ("a", 1)])
    expression = await instance._build_active_generation_expr("kb")
    assert expression == '(generation == 1 and file_id not in ["b"]) or (generation == 2 and file_id in ["b"])'


async def test_empty_knowledge_base_matches_no_file(kb):
    instance, holder = kb
    holder["repo"] = _Rows([])
    assert await instance._build_active_generation_expr("kb") == "file_id in []"
