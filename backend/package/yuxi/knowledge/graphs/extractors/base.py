from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from yuxi.knowledge.graphs.graph_utils import normalize_entity_name


class GraphExtractor(ABC):
    extractor_type: str

    def __init__(self, options: dict[str, Any] | None = None):
        self.options = options or {}

    @abstractmethod
    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        pass

    def validate_options(self) -> None:
        return None


# 与 knowledge_graph_entities / knowledge_graph_triples 的列长度对齐（见 storage/postgres/models_knowledge.py，
# test/unit/graphs 里有一条用例断言它们一致，避免将来漂移）。
#
# 这几个值都由模型生成、本身没有长度上限。实测出现过把整段正文当成实体名（1672 字符），
# 而 upsert_chunk_graph 是把一个分块的所有实体放进**一条多行 INSERT**：一个值超长，整批
# 被 PostgreSQL 拒绝，该分块的图谱全部丢失，且 graph_indexed 不置位——分块永远停在 pending，
# 每次重试都要再烧一次模型调用，还照样失败。
#
# 所以在落库前丢弃越界的单位（而不是抛异常：抛异常会让整个分块的抽取都失败，比丢弃更糟），
# 让损失止于它自己。
MAX_ENTITY_NAME_LENGTH = 512
MAX_ENTITY_LABEL_LENGTH = 128
MAX_RELATION_TYPE_LENGTH = 256


def _is_persistable_entity(entity: dict[str, Any]) -> bool:
    """实体名、归一化名与标签是否都能落进 knowledge_graph_entities 的列。"""
    return (
        len(entity["text"]) <= MAX_ENTITY_NAME_LENGTH
        and len(normalize_entity_name(entity["text"])) <= MAX_ENTITY_NAME_LENGTH
        and len(entity["label"]) <= MAX_ENTITY_LABEL_LENGTH
    )


def normalize_extraction_result(result: dict[str, Any], extractor_type: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("extraction_result 必须是对象")

    entities = result.get("entities") or []
    relations = result.get("relations") or []
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise ValueError("extraction_result.entities 和 relations 必须是数组")

    normalized_entities_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entity_refs: dict[str, dict[str, Any]] = {}
    dropped_refs: set[str] = set()
    # 按实体去重计数：同一个越界实体可能在多条关系里被反复引用，按出现次数计会虚高
    dropped_entity_keys: set[tuple[str, str]] = set()

    def add_entity(entity: Any, path: str) -> dict[str, Any] | None:
        normalized_entity = _normalize_entity(entity, path)
        if not _is_persistable_entity(normalized_entity):
            # 记下它的引用标识，引用它的关系要一并丢弃，否则会变成悬空关系
            dropped_refs.update(_entity_refs(entity, normalized_entity))
            dropped_entity_keys.add(_entity_key(normalized_entity))
            return None

        key = _entity_key(normalized_entity)
        existing = normalized_entities_by_key.get(key)
        if existing is None:
            normalized_entities_by_key[key] = normalized_entity
            existing = normalized_entity
        else:
            _merge_attributes(existing, normalized_entity)

        for ref in _entity_refs(entity, existing):
            entity_refs[ref] = existing
        return existing

    for index, entity in enumerate(entities):
        add_entity(entity, f"entities[{index}]")

    normalized_relations = []
    dropped_relations = 0
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError("relations 元素必须是对象")
        source = _normalize_relation_endpoint(
            relation.get("source"),
            entity_refs,
            add_entity,
            dropped_refs,
            result,
            f"relations[{index}].source",
        )
        target = _normalize_relation_endpoint(
            relation.get("target"),
            entity_refs,
            add_entity,
            dropped_refs,
            result,
            f"relations[{index}].target",
        )
        text = str(relation.get("text") or "").strip()
        if not text:
            raise ValueError("relations[].text 不能为空")
        label = str(relation.get("label") or "RELATED_TO").strip() or "RELATED_TO"
        if source is None or target is None or len(label) > MAX_RELATION_TYPE_LENGTH:
            dropped_relations += 1
            continue
        normalized_relations.append(
            {
                "source": source,
                "target": target,
                "text": text,
                "label": label,
            }
        )

    metadata = dict(result.get("metadata") or {})
    metadata.setdefault("extractor_type", extractor_type)
    metadata.setdefault("schema_version", 1)
    if dropped_entity_keys or dropped_relations:
        # 丢弃必须可观察：否则只表现为图谱里少了几条，无法判断是模型没抽到还是被丢掉
        metadata["dropped_entities"] = len(dropped_entity_keys)
        metadata["dropped_relations"] = dropped_relations
    return {
        "entities": list(normalized_entities_by_key.values()),
        "relations": normalized_relations,
        "metadata": metadata,
    }


def _normalize_relation_endpoint(
    endpoint: Any,
    entity_refs: dict[str, dict[str, Any]],
    add_entity: Callable[[Any, str], dict[str, Any] | None],
    dropped_refs: set[str],
    result: dict[str, Any],
    path: str,
) -> dict[str, Any] | None:
    if isinstance(endpoint, dict):
        return add_entity(endpoint, path)

    endpoint_ref = str(endpoint or "").strip()
    if len(endpoint_ref) > MAX_ENTITY_NAME_LENGTH:
        # 引用名本身就超过实体名的列长度：不可能对应任何可落库的实体，丢弃这条关系
        return None
    if endpoint_ref in dropped_refs:
        # 引用的是一个因越界被丢弃的实体：连这条关系一起丢弃，而不是报错
        return None
    entity = entity_refs.get(endpoint_ref)
    if entity is None:
        raise ValueError(
            f"relations[].source/target 必须是实体对象，或引用 entities[].text/id，"
            f"未找到: {path}={endpoint_ref}, Result: {result}"
        )
    return entity


def _normalize_entity(entity: Any, path: str) -> dict[str, Any]:
    if not isinstance(entity, dict):
        raise ValueError(f"{path} 必须是对象")

    text = str(entity.get("text") or "").strip()
    if not text:
        raise ValueError(f"{path}.text 不能为空")

    attributes = entity.get("attributes") or []
    if not isinstance(attributes, list):
        raise ValueError(f"{path}.attributes 必须是数组")

    normalized_attributes = []
    for attribute in attributes:
        if not isinstance(attribute, dict):
            raise ValueError(f"{path}.attributes 元素必须是对象")
        attr_text = str(attribute.get("text") or "").strip()
        if not attr_text:
            continue
        normalized_attributes.append(
            {
                "text": attr_text,
                "label": str(attribute.get("label") or "Attribute").strip() or "Attribute",
            }
        )

    return {
        "text": text,
        "label": str(entity.get("label") or "Entity").strip() or "Entity",
        "attributes": normalized_attributes,
    }


def _entity_key(entity: dict[str, Any]) -> tuple[str, str]:
    return (normalize_entity_name(entity["text"]), entity["label"])


def _entity_refs(raw_entity: Any, entity: dict[str, Any]) -> list[str]:
    refs = [entity["text"]]
    if isinstance(raw_entity, dict):
        entity_id = str(raw_entity.get("id") or "").strip()
        if entity_id:
            refs.append(entity_id)
    return refs


def _merge_attributes(target: dict[str, Any], source: dict[str, Any]) -> None:
    known_attributes = {(attr["text"], attr["label"]) for attr in target.get("attributes") or []}
    for attribute in source.get("attributes") or []:
        attribute_key = (attribute["text"], attribute["label"])
        if attribute_key not in known_attributes:
            target.setdefault("attributes", []).append(attribute)
            known_attributes.add(attribute_key)
