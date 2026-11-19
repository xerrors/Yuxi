"""属性契约以非业务专属字段证明可扩展性和拒绝边界。"""

import math

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from yuxi.modules.knowledge.metadata import (
    MetadataField,
    MetadataFieldCreate,
    MetadataFieldUpdate,
    MetadataFilesPatch,
    validate_attributes,
    validate_filter,
)
from yuxi.modules.knowledge.models import KnowledgeFile
from yuxi.modules.knowledge.repositories.metadata import metadata_predicate


def fields():
    """提供调用时定义的字段，不依赖人资概念。"""
    return [
        MetadataField(key="model", name="设备型号", type="text"),
        MetadataField(key="weight", name="重量", type="number"),
        MetadataField(key="enabled", name="启用", type="boolean"),
        MetadataField(key="date", name="生效日", type="date"),
        MetadataField(key="grade", name="等级", type="select", options=["a", "b"]),
        MetadataField(key="lines", name="产线", type="multi_select", options=["one", "two"]),
    ]


def test_optional_fields_keep_false_and_zero():
    """可选字段不必齐全，false 和零是明确值。"""
    assert validate_attributes({"weight": 0, "enabled": False}, fields()) == {"weight": 0, "enabled": False}
    assert validate_attributes({}, fields()) == {}
    assert validate_attributes({"lines": ["two", "one", "two"]}, fields()) == {"lines": ["one", "two"]}


@pytest.mark.parametrize(
    "values",
    [
        {"unknown": "x"},
        {"weight": True},
        {"weight": "3"},
        {"weight": math.inf},
        {"weight": 10**400},
        {"enabled": 1},
        {"date": "2026-02-30"},
        {"date": "20261104"},
        {"grade": "c"},
        {"lines": "one"},
        {"lines": ["missing"]},
        {"lines": []},
        {"model": None},
        {"model": ""},
        {"model": "x" * 4001},
    ],
)
def test_invalid_values_are_not_coerced(values):
    """错误属性不能靠类型猜测进入持久化。"""
    with pytest.raises(ValueError):
        validate_attributes(values, fields())


def test_grouped_condition_uses_bound_field_and_values():
    """组合规则保留分组，输入中的 SQL 字符只作为参数。"""
    expression = validate_filter(
        {
            "operator": "and",
            "conditions": [
                {"field": "weight", "operator": "gte", "value": 3},
                {
                    "operator": "or",
                    "conditions": [
                        {"field": "model", "operator": "eq", "value": "x' OR true --"},
                        {"field": "lines", "operator": "contains_all", "value": ["one", "two"]},
                    ],
                },
            ],
        },
        fields(),
    )
    statement = select(KnowledgeFile.file_id).where(metadata_predicate(expression))
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "x' OR true --" not in str(compiled)
    import json

    assert "x' OR true --" in json.dumps(compiled.params)
    assert "@>" in str(compiled) and " OR " in str(compiled)


@pytest.mark.parametrize(
    "condition",
    [
        {},
        {"operator": "and", "conditions": []},
        {"field": "unknown", "operator": "eq", "value": "x"},
        {"field": "weight", "operator": "contains_any", "value": [1]},
        {"field": "enabled", "operator": "eq", "value": "true"},
        {"field": "model", "operator": "exists", "value": "x"},
        {"field": "model", "operator": "raw_sql", "value": "true"},
        {"field": "model", "operator": "eq", "value": "x", "sql": "true"},
    ],
)
def test_invalid_filters_fail_instead_of_disabling_filter(condition):
    """无条件与非法条件不同，后者必须拒绝。"""
    assert validate_filter(None, fields()) is None
    assert validate_filter([], fields()) is None
    with pytest.raises(ValueError):
        validate_filter(condition, fields())


def test_filter_depth_and_total_nodes_are_bounded():
    """递归与总节点预算限制作用于整棵表达式。"""
    leaf = {"field": "model", "operator": "eq", "value": "a"}
    nested = leaf
    for _ in range(6):
        nested = {"operator": "and", "conditions": [nested]}
    with pytest.raises(ValueError, match="最多"):
        validate_filter(nested, fields())
    wide = {"operator": "and", "conditions": [{"operator": "or", "conditions": [leaf] * 30}] * 2}
    with pytest.raises(ValueError, match="最多"):
        validate_filter(wide, fields())
    from yuxi.modules.knowledge.schemas import MetadataSearchInputSchema

    with pytest.raises(ValueError, match="最多"):
        MetadataSearchInputSchema.model_validate({"kb_id": "sample", "query_text": "x", "filters": wide})
    assert MetadataSearchInputSchema.model_validate({"kb_id": "sample", "query_text": "x", "filters": []}).filters == []
    with pytest.raises(ValueError):
        MetadataSearchInputSchema.model_validate({"kb_id": "sample", "query_text": "x", "filters": [leaf]})
    with pytest.raises(ValueError):
        MetadataSearchInputSchema.model_validate({"kb_id": "sample", "query_text": "x", "filter": leaf})
    with pytest.raises(ValueError):
        validate_attributes({"lines": ["one"] * 129}, fields())


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "type": "select"},
        {"name": "x", "type": "text", "options": ["a"]},
        {"name": "x", "type": "multi_select", "options": ["a", "a"]},
        {"name": "x", "type": "select", "options": [" "]},
        {"name": "two\nlines", "type": "text"},
        {"name": "x", "type": "select", "options": ["two\nlines"]},
    ],
)
def test_field_definition_rejects_ambiguous_options(payload):
    """类型和选项在定义边界保持一致。"""
    with pytest.raises(ValidationError):
        MetadataFieldCreate.model_validate(payload)


def test_patch_is_explicit_and_field_type_is_immutable():
    """部分更新不允许矛盾操作或原地改类型。"""
    assert MetadataFilesPatch(file_ids=["a"], **{"set": {"enabled": False}}).unset == []
    for payload in [
        {"file_ids": ["a"], "set": {"model": "x"}, "unset": ["model"]},
        {"file_ids": ["a"], "unset": ["model", "model"]},
        {"file_ids": ["a", "a"]},
        {"file_ids": [""]},
    ]:
        with pytest.raises(ValidationError):
            MetadataFilesPatch.model_validate(payload)
    with pytest.raises(ValidationError):
        MetadataFieldUpdate.model_validate({"type": "number"})


async def test_oversized_scope_fails_before_any_milvus_operation(monkeypatch):
    """合法长度的大量文件 ID 超预算时，执行器必须明确拒绝。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yuxi.modules.knowledge.implementations import milvus
    from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig

    records = [(f"file_{index:055d}", 1) for index in range(40000)]
    repo = SimpleNamespace(list_retrieval_file_generations=AsyncMock(return_value=records))
    monkeypatch.setattr(milvus, "KnowledgeFileRepository", lambda: repo)
    kb = milvus.MilvusKB.__new__(milvus.MilvusKB)

    async def forbidden(_kb_id):
        """恢复容量缺陷时在错误执行边界使测试失败。"""
        raise AssertionError("超预算候选不得进入 Milvus")

    kb._get_existing_milvus_collection = forbidden
    with pytest.raises(ValueError, match="2 MiB"):
        await kb.aquery("example", "large", config=KnowledgeBaseConfig(kb_id="large", kb_type="milvus"))


@pytest.mark.parametrize("operator", [[], {}])
def test_invalid_non_string_operator_is_a_validation_error(operator):
    """预算检查后由 Schema 拒绝非法运算符。"""
    with pytest.raises(ValueError):
        validate_filter({"field": "label", "operator": operator, "value": "x"}, [])
