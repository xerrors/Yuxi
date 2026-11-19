"""真实 HTTP 与 PostgreSQL 证明文件属性管理、导入和谓词语义。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from test.integration.api.test_knowledge_directory_imports import directory_kb as directory_kb
from test.integration.api.test_knowledge_directory_imports import manifest, staged
from yuxi.modules.knowledge.metadata import MetadataField, validate_filter
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile
from yuxi.modules.knowledge.repositories.metadata import metadata_predicate

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def create_field(client, headers, kb_id, name, kind="text", options=None):
    """通过真实管理入口创建调用时确定的字段。"""
    response = await client.post(
        f"/api/knowledge/knowledge-bases/{kb_id}/metadata-fields",
        headers=headers,
        json={"name": name, "type": kind, "options": options or []},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "kb_type, connection_params",
    [
        ("feishu", {"feishu_user_token": "fixture-token", "feishu_space_id": "123"}),
        ("dify", {"dify_api_url": "https://example.invalid/v1", "dify_token": "fixture-token", "dify_dataset_id": "fixture"}),
        ("notion", {"notion_token": "fixture-token", "notion_data_source_id": "9c944837-6727-4108-b5a9-35d8b69e61ab"}),
    ],
)
async def test_external_connectors_reject_attributes_instead_of_ignoring_filters(
    test_client, admin_headers, directory_kb, postgres_sessions, kb_type, connection_params
):
    """外接库公开不支持，并在管理和查询入口明确拒绝而不请求外部服务。"""
    async with postgres_sessions() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == directory_kb))
        kb.kb_type = kb_type
        kb.additional_params = connection_params
        await session.commit()
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    try:
        schema = await test_client.get(root + "/metadata-fields", headers=admin_headers)
        assert schema.status_code == 200, schema.text
        assert schema.json()["supported"] is False
        created = await test_client.post(root + "/metadata-fields", headers=admin_headers, json={"name": "型号", "type": "text"})
        assert created.status_code == 400 and "不支持本地文件属性管理" in created.text
        queried = await test_client.post(
            root + "/query-test",
            headers=admin_headers,
            json={"query": "device", "meta": {"filters": {"field": "model", "operator": "eq", "value": "A"}}},
        )
        assert queried.status_code == 400 and "不支持文件属性筛选" in queried.text
        async with postgres_sessions() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == directory_kb))
            assert kb.metadata_fields == []
    finally:
        async with postgres_sessions() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == directory_kb))
            kb.kb_type = "milvus"
            kb.additional_params = {}
            await session.commit()


async def test_metadata_dynamic_fields_patch_and_definition_evolution(test_client, admin_headers, directory_kb, postgres_sessions):
    """新旧文件、并发部分更新、稳定字段身份及删除约束都回读持久结果。"""
    kb_id = directory_kb
    model = await create_field(test_client, admin_headers, kb_id, "设备型号")
    lines = await create_field(test_client, admin_headers, kb_id, "产线", "multi_select", ["A", "B"])
    ids = [uuid4().hex, uuid4().hex]
    async with postgres_sessions() as session:
        session.add_all([KnowledgeFile(file_id=file_id, kb_id=kb_id, filename=file_id + ".md") for file_id in ids])
        await session.commit()
    root = f"/api/knowledge/knowledge-bases/{kb_id}"
    replies = await asyncio.gather(
        *[
            test_client.patch(root + "/documents/attributes", headers=admin_headers, json={"file_ids": ids, "set": changes})
            for changes in [{model["key"]: "X"}, {lines["key"]: ["A", "B"]}]
        ]
    )
    assert all(response.status_code == 200 for response in replies), [response.text for response in replies]
    expected = {model["key"]: "X", lines["key"]: ["A", "B"]}
    async with postgres_sessions() as session:
        rows = list((await session.scalars(select(KnowledgeFile).where(KnowledgeFile.file_id.in_(ids)))).all())
        assert all(row.attributes == expected and row.generation == 1 for row in rows)
    changed = await test_client.patch(root + f"/metadata-fields/{model['key']}", headers=admin_headers, json={"name": "新型号名称"})
    assert changed.status_code == 200 and changed.json()["key"] == model["key"]
    blocked = await test_client.patch(root + f"/metadata-fields/{lines['key']}", headers=admin_headers, json={"options": ["A"]})
    assert blocked.status_code == 409
    blocked = await test_client.delete(root + f"/metadata-fields/{model['key']}", headers=admin_headers)
    assert blocked.status_code == 409
    cleared = await test_client.patch(
        root + "/documents/attributes", headers=admin_headers, json={"file_ids": ids, "unset": [model["key"]]}
    )
    assert cleared.status_code == 200
    async with postgres_sessions() as session:
        rows = list((await session.scalars(select(KnowledgeFile).where(KnowledgeFile.file_id.in_(ids)))).all())
        assert all(row.attributes == {lines["key"]: ["A", "B"]} for row in rows)
    deleted = await test_client.delete(root + f"/metadata-fields/{model['key']}", headers=admin_headers)
    assert deleted.status_code == 200
    schema = await test_client.get(root + "/metadata-fields", headers=admin_headers)
    assert [field["key"] for field in schema.json()["fields"]] == [lines["key"]]


async def test_metadata_patch_is_atomic_and_cannot_cross_knowledge_bases(
    test_client, admin_headers, standard_user, directory_kb, postgres_sessions
):
    """未知字段、越权、跨库 ID 与错误值不留下部分更新。"""
    kb_id = directory_kb
    field = await create_field(test_client, admin_headers, kb_id, "开关", "boolean")
    file_id, foreign_id, foreign_kb = uuid4().hex, uuid4().hex, "metadata_other_" + uuid4().hex
    async with postgres_sessions() as session:
        session.add(KnowledgeBase(kb_id=foreign_kb, name=foreign_kb, kb_type="milvus"))
        await session.flush()
        session.add_all(
            [
                KnowledgeFile(kb_id=kb_id, file_id=file_id, filename="original.md", attributes={field["key"]: False}),
                KnowledgeFile(kb_id=foreign_kb, file_id=foreign_id, filename="foreign.md"),
            ]
        )
        await session.commit()
    try:
        endpoint = f"/api/knowledge/knowledge-bases/{kb_id}/documents/attributes"
        for headers, body, status in [
            (admin_headers, {"file_ids": [file_id, foreign_id], "set": {field["key"]: True}}, 404),
            (admin_headers, {"file_ids": [file_id], "set": {field["key"]: "false"}}, 422),
            (admin_headers, {"file_ids": [file_id], "set": {"unknown": True}}, 422),
            (standard_user["headers"], {"file_ids": [file_id], "set": {field["key"]: True}}, 403),
        ]:
            response = await test_client.patch(endpoint, headers=headers, json=body)
            assert response.status_code == status, response.text
            async with postgres_sessions() as session:
                assert (await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))).attributes == {
                    field["key"]: False
                }
                assert (await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == foreign_id))).attributes == {}
    finally:
        async with postgres_sessions() as session:
            await session.delete(await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == foreign_kb)))
            await session.commit()


async def test_import_attributes_are_independent_of_parser_and_part_of_receipt(test_client, admin_headers, directory_kb, postgres_sessions):
    """从真实暂存导入属性，同一幂等键改变属性必须拒绝。"""
    field = await create_field(test_client, admin_headers, directory_kb, "设备编号")
    upload = await staged(test_client, admin_headers, directory_kb, "equipment.md", b"Equipment documentation\n")
    payload = manifest([upload], attributes={field["key"]: "XY-1"})
    payload["import_id"] = uuid4().hex
    endpoint = f"/api/knowledge/knowledge-bases/{directory_kb}/documents/add"
    response = await test_client.post(endpoint, headers=admin_headers, json=payload)
    assert response.status_code == 200, response.text
    file_id = response.json()["items"][0]["file_id"]
    async with postgres_sessions() as session:
        row = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        assert row.attributes == {field["key"]: "XY-1"}
        assert "attributes" not in (row.processing_params or {})
        assert row.status == "uploaded" and row.chunk_count == 0
    payload["params"]["attributes"][field["key"]] = "XY-2"
    repeated = await test_client.post(endpoint, headers=admin_headers, json=payload)
    assert repeated.status_code == 409, repeated.text

    payload["import_id"] = uuid4().hex
    duplicate = await test_client.post(endpoint, headers=admin_headers, json=payload)
    assert duplicate.status_code == 200, duplicate.text
    result = duplicate.json()
    assert result["added"] == 0 and result["failed"] == 1
    assert result["failed_items"][0]["code"] == "import_attribute_conflict"
    async with postgres_sessions() as session:
        current = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        assert current.attributes == {field["key"]: "XY-1"}


async def test_postgres_filters_use_grouping_typed_values_and_missing_semantics(directory_kb, postgres_sessions):
    """独立预期集合证明 JSONB 条件，而不是只验证 SQL 文本。"""
    fields = [
        MetadataField(key="number", name="数字", type="number"),
        MetadataField(key="flag", name="布尔", type="boolean"),
        MetadataField(key="tags", name="选项", type="multi_select", options=["A", "B"]),
        MetadataField(key="day", name="日期", type="date"),
    ]
    records = {
        "alpha": {"number": 0, "flag": False, "tags": ["A"], "day": "2026-01-01"},
        "beta": {"number": 9, "flag": True, "tags": ["A", "B"], "day": "2026-11-04"},
        "empty": {},
    }
    ids = {name: uuid4().hex for name in records}
    async with postgres_sessions() as session:
        session.add_all(
            [KnowledgeFile(kb_id=directory_kb, file_id=ids[name], filename=name, attributes=values) for name, values in records.items()]
        )
        await session.commit()
    cases = [
        ({"field": "number", "operator": "eq", "value": 0}, {"alpha"}),
        ({"field": "flag", "operator": "eq", "value": False}, {"alpha"}),
        ({"field": "number", "operator": "ne", "value": 0}, {"beta"}),
        ({"field": "tags", "operator": "contains_any", "value": ["A", "B"]}, {"alpha", "beta"}),
        ({"field": "tags", "operator": "contains_all", "value": ["A", "B"]}, {"beta"}),
        ({"field": "number", "operator": "not_exists"}, {"empty"}),
        (
            {
                "operator": "and",
                "conditions": [
                    {"field": "day", "operator": "lt", "value": "2026-06-01"},
                    {
                        "operator": "or",
                        "conditions": [
                            {"field": "number", "operator": "eq", "value": 9},
                            {"field": "flag", "operator": "eq", "value": False},
                        ],
                    },
                ],
            },
            {"alpha"},
        ),
    ]
    async with postgres_sessions() as session:
        for raw_filter, expected in cases:
            found = set(
                (
                    await session.scalars(
                        select(KnowledgeFile.filename).where(
                            KnowledgeFile.kb_id == directory_kb, metadata_predicate(validate_filter(raw_filter, fields))
                        )
                    )
                ).all()
            )
            assert found == expected


async def test_field_capacity_rejects_the_65th_definition(test_client, admin_headers, directory_kb, postgres_sessions):
    """第 64 个字段可发布，第 65 个字段拒绝且不改变持久定义。"""
    async with postgres_sessions() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == directory_kb))
        kb.metadata_fields = [MetadataField(key=f"f_{index}", name=f"字段{index}", type="text").model_dump() for index in range(63)]
        await session.commit()
    await create_field(test_client, admin_headers, directory_kb, "最后允许的字段")
    response = await test_client.post(
        f"/api/knowledge/knowledge-bases/{directory_kb}/metadata-fields",
        headers=admin_headers,
        json={"name": "不应发布", "type": "text"},
    )
    assert response.status_code == 422 and "64" in response.text
    async with postgres_sessions() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == directory_kb))
        assert len(kb.metadata_fields) == 64
        assert all(field["name"] != "不应发布" for field in kb.metadata_fields)


async def test_merged_attribute_size_failure_rolls_back_earlier_batch_file(test_client, admin_headers, directory_kb, postgres_sessions):
    """前一文件更新合法，后一文件合并后超限，整批仍不发布任何修改。"""
    definitions = [MetadataField(key=f"f_{index}", name=f"文本{index}", type="text") for index in range(17)]
    first, second = "a_" + uuid4().hex, "z_" + uuid4().hex
    original = {field.key: "x" * 4000 for field in definitions[:-1]}
    async with postgres_sessions() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == directory_kb))
        kb.metadata_fields = [field.model_dump() for field in definitions]
        session.add_all(
            [
                KnowledgeFile(kb_id=directory_kb, file_id=first, filename="empty.md", attributes={}),
                KnowledgeFile(kb_id=directory_kb, file_id=second, filename="full.md", attributes=original),
            ]
        )
        await session.commit()
    response = await test_client.patch(
        f"/api/knowledge/knowledge-bases/{directory_kb}/documents/attributes",
        headers=admin_headers,
        json={"file_ids": [first, second], "set": {definitions[-1].key: "y" * 4000}},
    )
    assert response.status_code == 422 and "64 KiB" in response.text
    async with postgres_sessions() as session:
        rows = {
            row.file_id: row
            for row in (await session.scalars(select(KnowledgeFile).where(KnowledgeFile.file_id.in_([first, second])))).all()
        }
        assert rows[first].attributes == {} and rows[first].updated_by is None
        assert rows[second].attributes == original and rows[second].updated_by is None
