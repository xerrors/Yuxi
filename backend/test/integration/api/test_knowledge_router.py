"""
Integration tests for knowledge router endpoints.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yuxi.knowledge.chunking.ragflow_like.presets import CHUNK_PRESET_IDS

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _assert_forbidden_response(response):
    """验证 403 禁止访问响应的格式"""
    assert response.status_code == 403
    payload = response.json()
    assert "detail" in payload
    assert isinstance(payload["detail"], str)


async def _create_test_department(test_client, admin_headers, prefix="pytest_dept"):
    suffix = uuid.uuid4().hex[:8]
    admin_uid = f"deptadmin_{suffix}"
    response = await test_client.post(
        "/api/departments",
        json={
            "name": f"{prefix}_{suffix}",
            "description": "pytest department",
            "admin_uid": admin_uid,
            "admin_password": f"Pw!{suffix}",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    payload["admin_uid"] = admin_uid
    return payload


async def _create_test_user(test_client, admin_headers, department_id):
    suffix = uuid.uuid4().hex[:8]
    password = f"Pw!{suffix}"
    response = await test_client.post(
        "/api/auth/users",
        json={
            "username": f"pytest_user_{suffix}",
            "password": password,
            "role": "user",
            "department_id": department_id,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    user = response.json()

    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": user["uid"], "password": password},
    )
    assert login_response.status_code == 200, login_response.text
    return {"user": user, "headers": {"Authorization": f"Bearer {login_response.json()['access_token']}"}}


async def _delete_user_by_id(test_client, admin_headers, user_id):
    response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
    assert response.status_code in (200, 404), response.text


async def _find_user_id_by_uid(test_client, admin_headers, uid):
    response = await test_client.get("/api/auth/users", headers=admin_headers)
    assert response.status_code == 200, response.text
    for user in response.json():
        if user["uid"] == uid:
            return user["id"]
    return None


async def _delete_department_with_admin(test_client, admin_headers, department):
    admin_user_id = await _find_user_id_by_uid(test_client, admin_headers, department["admin_uid"])
    if admin_user_id:
        await _delete_user_by_id(test_client, admin_headers, admin_user_id)
    response = await test_client.delete(f"/api/departments/{department['id']}", headers=admin_headers)
    assert response.status_code in (200, 404), response.text


async def _create_test_database(test_client, admin_headers, share_config=None):
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_acl_{uuid.uuid4().hex[:8]}",
            "description": "Knowledge permission test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
            "share_config": share_config,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_task(test_client, headers, task_id):
    for _ in range(100):
        response = await test_client.get(f"/api/tasks/{task_id}", headers=headers)
        assert response.status_code == 200, response.text
        task = response.json()["task"]
        if task["status"] in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not finish")


async def _accessible_kb_ids(test_client, headers):
    response = await test_client.get("/api/knowledge/databases/accessible", headers=headers)
    assert response.status_code == 200, response.text
    return {item["kb_id"] for item in response.json().get("databases", [])}


async def test_admin_can_manage_knowledge_databases(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    list_response = await test_client.get("/api/knowledge/databases", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    databases = list_response.json().get("databases", [])
    database = next(entry for entry in databases if entry["kb_id"] == kb_id)
    assert database["metadata"] == database["additional_params"]
    assert database["status"] == "已连接"
    assert database["row_count"] == (database["stats"]["row_count"] or database["stats"]["file_count"])
    assert database["effective_permission"] == "manage"
    assert database["can_manage"] is True

    get_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert get_response.status_code == 200, get_response.text
    detail = get_response.json()
    assert detail["kb_id"] == kb_id
    assert detail["metadata"] == detail["additional_params"]
    assert detail["stats"]["row_count"] == detail["row_count"]

    update_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={"name": knowledge_database["name"], "description": "Updated by pytest"},
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["database"]["description"] == "Updated by pytest"


async def test_document_exists_returns_false_for_missing_relative_path(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    filename = f"google_drive/shared_drives/engineering/serving-runtime/dsid_{uuid.uuid4().hex}__missing-playbook.txt"

    response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents/exists",
        params={"filename": filename},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"kb_id": kb_id, "filename": filename, "exists": False}


async def test_folder_rename_and_move_persist_tree_changes(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    async def create_folder(name, parent_id=None):
        response = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/folders",
            json={"folder_name": name, "parent_id": parent_id},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    source = await create_folder(f"source-{uuid.uuid4().hex[:6]}")
    child = await create_folder("child", source["file_id"])
    destination = await create_folder(f"destination-{uuid.uuid4().hex[:6]}")
    assert source["created_at"]
    assert source["created_by"]

    rename_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/folders/{source['file_id']}/rename",
        json={"folder_name": "renamed source"},
        headers=admin_headers,
    )
    assert rename_response.status_code == 200, rename_response.text
    assert rename_response.json()["filename"] == "renamed source"
    assert rename_response.json()["path"] == "renamed source"

    source_listing = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        params={"parent_id": source["file_id"]},
        headers=admin_headers,
    )
    assert source_listing.status_code == 200, source_listing.text
    assert [
        (item["file_id"], item["parent_id"], item["filename"], item["created_by"])
        for item in source_listing.json()["items"]
    ] == [(child["file_id"], source["file_id"], "child", child["created_by"])]

    move_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{child['file_id']}/move",
        json={"new_parent_id": destination["file_id"]},
        headers=admin_headers,
    )
    assert move_response.status_code == 200, move_response.text
    assert move_response.json()["parent_id"] == destination["file_id"]

    destination_listing = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        params={"parent_id": destination["file_id"]},
        headers=admin_headers,
    )
    assert destination_listing.status_code == 200, destination_listing.text
    assert [item["file_id"] for item in destination_listing.json()["items"]] == [child["file_id"]]

    move_to_root_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{child['file_id']}/move",
        json={"new_parent_id": None},
        headers=admin_headers,
    )
    assert move_to_root_response.status_code == 200, move_to_root_response.text
    assert move_to_root_response.json()["parent_id"] is None

    root_listing = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        headers=admin_headers,
    )
    assert root_listing.status_code == 200, root_listing.text
    assert child["file_id"] in {item["file_id"] for item in root_listing.json()["items"]}

    missing_target_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{child['file_id']}/move",
        json={},
        headers=admin_headers,
    )
    assert missing_target_response.status_code == 422, missing_target_response.text


async def test_knowledge_virtual_folder_migration_runs_without_sse_and_is_resumable(
    test_client, admin_headers, knowledge_database
):
    kb_id = knowledge_database["kb_id"]
    prefix = uuid.uuid4().hex[:6]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files "
                    "(file_id, kb_id, parent_id, filename, file_type, status, is_folder) VALUES "
                    "(:id1, :kb, NULL, :name1, 'txt', 'uploaded', FALSE), "
                    "(:id2, :kb, NULL, :name2, 'txt', 'uploaded', FALSE), "
                    "(:id3, :kb, NULL, :name3, 'txt', 'uploaded', FALSE)"
                ),
                {
                    "id1": f"file_{prefix}_1",
                    "id2": f"file_{prefix}_2",
                    "id3": f"file_{prefix}_3",
                    "kb": kb_id,
                    "name1": f"history-{prefix}/shared/a.txt",
                    "name2": f"history-{prefix}/shared/b.txt",
                    "name3": f"history-{prefix}/other/c.txt",
                },
            )

        detection = await test_client.get(
            f"/api/knowledge/databases/{kb_id}/virtual-folders/detect", headers=admin_headers
        )
        assert detection.status_code == 200, detection.text
        assert detection.json()["remaining_steps"] == 6

        start = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/virtual-folders/migrate", headers=admin_headers
        )
        assert start.status_code == 200, start.text
        task_id = start.json()["task_id"]

        task = await _wait_for_task(test_client, admin_headers, task_id)
        assert task["status"] == "success"
        assert task["result"]["processed_steps"] == 6
        assert task["result"]["remaining_files"] == 0

        events = await test_client.get(
            f"/api/knowledge/databases/{kb_id}/virtual-folders/migrations/{task_id}/events",
            headers=admin_headers,
        )
        assert events.status_code == 200, events.text
        assert '"status": "success"' in events.text

        final_detection = await test_client.get(
            f"/api/knowledge/databases/{kb_id}/virtual-folders/detect", headers=admin_headers
        )
        assert final_detection.json()["has_virtual_folders"] is False
        async with engine.connect() as connection:
            folder_creators = (
                (
                    await connection.execute(
                        text(
                            "SELECT created_by FROM knowledge_files WHERE kb_id = :kb "
                            "AND is_folder IS TRUE AND filename IN (:root, 'shared', 'other')"
                        ),
                        {"kb": kb_id, "root": f"history-{prefix}"},
                    )
                )
                .scalars()
                .all()
            )
        assert len(folder_creators) == 3
        assert all(folder_creators)
    finally:
        await engine.dispose()


async def test_virtual_folder_migration_keeps_conflicts_and_commits_other_paths(
    test_client, admin_headers, knowledge_database
):
    kb_id = knowledge_database["kb_id"]
    suffix = uuid.uuid4().hex[:6]
    blocked = f"blocked-{suffix}"
    movable = f"movable-{suffix}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files "
                    "(file_id, kb_id, parent_id, filename, file_type, status, is_folder) VALUES "
                    "(:plain, :kb, NULL, :blocked, 'txt', 'uploaded', FALSE), "
                    "(:blocked_file, :kb, NULL, :blocked_path, 'txt', 'uploaded', FALSE), "
                    "(:movable_file, :kb, NULL, :movable_path, 'txt', 'uploaded', FALSE)"
                ),
                {
                    "plain": f"file_{suffix}_plain",
                    "blocked_file": f"file_{suffix}_blocked",
                    "movable_file": f"file_{suffix}_movable",
                    "kb": kb_id,
                    "blocked": blocked,
                    "blocked_path": f"{blocked}/a.txt",
                    "movable_path": f"{movable}/b.txt",
                },
            )

        start = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/virtual-folders/migrate", headers=admin_headers
        )
        task = await _wait_for_task(test_client, admin_headers, start.json()["task_id"])
        assert task["status"] == "success"
        assert task["result"]["processed_steps"] == 1
        assert task["result"]["remaining_files"] == 1
        assert task["result"]["conflict_files"] == 1

        retry = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/virtual-folders/migrate", headers=admin_headers
        )
        retry_task = await _wait_for_task(test_client, admin_headers, retry.json()["task_id"])
        assert retry_task["result"]["processed_steps"] == 0
        assert retry_task["result"]["remaining_files"] == 1

        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT filename, parent_id FROM knowledge_files WHERE file_id IN "
                            "(:blocked_file, :movable_file) ORDER BY file_id"
                        ),
                        {
                            "blocked_file": f"file_{suffix}_blocked",
                            "movable_file": f"file_{suffix}_movable",
                        },
                    )
                )
                .mappings()
                .all()
            )
        assert {row["filename"] for row in rows} == {f"{blocked}/a.txt", "b.txt"}
        assert sum(row["parent_id"] is not None for row in rows) == 1
    finally:
        await engine.dispose()


async def test_folder_mutations_reject_invalid_name_and_directory_cycle(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    parent_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/folders",
        json={"folder_name": f"parent-{uuid.uuid4().hex[:6]}", "parent_id": None},
        headers=admin_headers,
    )
    assert parent_response.status_code == 200, parent_response.text
    parent = parent_response.json()

    child_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/folders",
        json={"folder_name": "child", "parent_id": parent["file_id"]},
        headers=admin_headers,
    )
    assert child_response.status_code == 200, child_response.text
    child = child_response.json()

    invalid_rename = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/folders/{parent['file_id']}/rename",
        json={"folder_name": "invalid/name"},
        headers=admin_headers,
    )
    assert invalid_rename.status_code == 400, invalid_rename.text
    assert "path separators" in invalid_rename.json()["detail"]

    cycle_move = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{parent['file_id']}/move",
        json={"new_parent_id": child["file_id"]},
        headers=admin_headers,
    )
    assert cycle_move.status_code == 400, cycle_move.text
    assert "own subfolder" in cycle_move.json()["detail"]


async def test_concurrent_folder_moves_cannot_create_cycle(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    async def create_folder(name):
        response = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/folders",
            json={"folder_name": name, "parent_id": None},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    folder_a = await create_folder(f"concurrent-a-{uuid.uuid4().hex[:6]}")
    folder_b = await create_folder(f"concurrent-b-{uuid.uuid4().hex[:6]}")
    responses = await asyncio.gather(
        test_client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{folder_a['file_id']}/move",
            json={"new_parent_id": folder_b["file_id"]},
            headers=admin_headers,
        ),
        test_client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{folder_b['file_id']}/move",
            json={"new_parent_id": folder_a["file_id"]},
            headers=admin_headers,
        ),
    )
    assert sorted(response.status_code for response in responses) == [200, 400]

    root_listing = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        headers=admin_headers,
    )
    assert root_listing.status_code == 200, root_listing.text
    folder_ids = {folder_a["file_id"], folder_b["file_id"]}
    root_ids = {item["file_id"] for item in root_listing.json()["items"]} & folder_ids
    assert len(root_ids) == 1

    root_id = root_ids.pop()
    child_listing = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        params={"parent_id": root_id},
        headers=admin_headers,
    )
    assert child_listing.status_code == 200, child_listing.text
    assert {item["file_id"] for item in child_listing.json()["items"]} & folder_ids == folder_ids - {root_id}


async def test_folder_move_waits_for_kb_tree_lock(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    async def create_folder(name):
        response = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/folders",
            json={"folder_name": name, "parent_id": None},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    source = await create_folder(f"locked-source-{uuid.uuid4().hex[:6]}")
    destination = await create_folder(f"locked-destination-{uuid.uuid4().hex[:6]}")
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    move_task = None
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:kb_id))"),
                {"kb_id": kb_id},
            )
            move_task = asyncio.create_task(
                test_client.put(
                    f"/api/knowledge/databases/{kb_id}/documents/{source['file_id']}/move",
                    json={"new_parent_id": destination["file_id"]},
                    headers=admin_headers,
                )
            )
            await asyncio.sleep(0.1)
            assert not move_task.done(), "移动请求未等待知识库目录树锁"

        move_response = await asyncio.wait_for(move_task, timeout=2)
        assert move_response.status_code == 200, move_response.text
        assert move_response.json()["parent_id"] == destination["file_id"]
    finally:
        if move_task is not None and not move_task.done():
            move_task.cancel()
            await asyncio.gather(move_task, return_exceptions=True)
        await engine.dispose()


async def test_create_database_with_chunk_preset(test_client, admin_headers):
    db_name = f"pytest_chunk_preset_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Chunk preset create test",
        "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        "kb_type": "milvus",
        "additional_params": {"chunk_preset_id": "book"},
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    create_payload = create_response.json()
    assert create_payload["files"] == {}
    kb_id = create_payload["kb_id"]

    info_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert info_response.status_code == 200, info_response.text
    assert info_response.json()["additional_params"]["chunk_preset_id"] == "book"

    delete_response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text


async def test_get_chunk_presets_returns_configured_options(test_client, admin_headers):
    response = await test_client.get("/api/knowledge/chunk-presets", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    options = payload["chunk_presets"]
    assert payload["message"] == "success"
    assert {option["value"] for option in options} == CHUNK_PRESET_IDS
    assert all(set(option) == {"value", "label", "description"} for option in options)
    assert all(option["label"] and option["description"] for option in options)


async def test_update_database_additional_params_merge_keeps_chunk_preset(
    test_client, admin_headers, knowledge_database
):
    kb_id = knowledge_database["kb_id"]

    first_update = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={
            "name": knowledge_database["name"],
            "description": "update with chunk preset",
            "additional_params": {"chunk_preset_id": "qa"},
        },
        headers=admin_headers,
    )
    assert first_update.status_code == 200, first_update.text

    second_update = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={
            "name": knowledge_database["name"],
            "description": "update without additional params",
        },
        headers=admin_headers,
    )
    assert second_update.status_code == 200, second_update.text

    info_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert info_response.status_code == 200, info_response.text
    assert info_response.json()["additional_params"]["chunk_preset_id"] == "qa"


async def test_knowledge_routes_enforce_permissions(test_client, standard_user, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    forbidden_create = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": "unauthorized_db",
            "description": "Should not succeed",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        },
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(forbidden_create)

    forbidden_list = await test_client.get("/api/knowledge/databases", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_list)

    forbidden_chunk_presets = await test_client.get("/api/knowledge/chunk-presets", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_chunk_presets)

    forbidden_get = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_get)

    forbidden_exists = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents/exists",
        params={"filename": "demo.txt"},
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(forbidden_exists)


async def test_kb_image_proxy_requires_auth_and_streams_private_image(test_client, admin_headers, knowledge_database):
    """知识库图片代理：未登录不可访问，鉴权后可读取私有 bucket 图片"""
    from yuxi.storage.minio.client import MinIOClient, get_minio_client

    kb_id = knowledge_database["kb_id"]
    image_name = f"proxy_{uuid.uuid4().hex[:8]}.png"
    object_name = f"{kb_id}/kb-images/{image_name}"
    image_bytes = b"\x89PNG\r\n\x1a\nfake-image-content"

    minio_client = get_minio_client()
    minio_client.upload_file(
        bucket_name=MinIOClient.KB_BUCKETS["images"],
        object_name=object_name,
        data=image_bytes,
        content_type="image/png",
    )

    proxy_path = f"/api/knowledge/databases/{kb_id}/images/kb-images/{image_name}"

    anonymous = await test_client.get(proxy_path)
    assert anonymous.status_code == 401

    invalid_headers = {"Authorization": "Bearer invalid-token"}
    forbidden = await test_client.get(proxy_path, headers=invalid_headers)
    assert forbidden.status_code == 401

    authorized = await test_client.get(proxy_path, headers=admin_headers)
    assert authorized.status_code == 200, authorized.text
    assert authorized.content == image_bytes
    assert authorized.headers["content-type"].startswith("image/png")


async def test_kb_image_proxy_rejects_invalid_or_missing_object(test_client, admin_headers, knowledge_database):
    """知识库图片代理：非法路径与不存在的图片返回 400/404"""
    kb_id = knowledge_database["kb_id"]

    invalid_path = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/images/avatar/user.png",
        headers=admin_headers,
    )
    assert invalid_path.status_code == 400

    traversal_path = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/images/kb-images/..%2Fother.png",
        headers=admin_headers,
    )
    assert traversal_path.status_code == 400

    backslash_path = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/images/kb-images/..%5Cother.png",
        headers=admin_headers,
    )
    assert backslash_path.status_code == 400

    missing_image = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/images/kb-images/missing.png",
        headers=admin_headers,
    )
    assert missing_image.status_code == 404


async def test_admin_can_create_vector_db_with_reranker(test_client, admin_headers):
    """测试创建向量库并配置 reranker 参数（通过 query_params.options）

    注意：数据库清理由 conftest.py 中的 session fixture 自动处理。
    """
    db_name = f"pytest_rerank_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Vector DB with reranker",
        "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        "kb_type": "milvus",
        "additional_params": {},
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text

    db_payload = create_response.json()
    kb_id = db_payload["kb_id"]

    # 获取查询参数配置
    params_response = await test_client.get(f"/api/knowledge/databases/{kb_id}/query-params", headers=admin_headers)
    assert params_response.status_code == 200, params_response.text

    params_payload = params_response.json()
    options = params_payload.get("params", {}).get("options", [])
    option_keys = {option.get("key") for option in options}

    # 验证新的参数名称
    assert "final_top_k" in option_keys
    assert "use_reranker" in option_keys
    assert "recall_top_k" in option_keys
    assert "reranker_model" in option_keys

    # 验证参数配置
    final_top_k_option = next((opt for opt in options if opt.get("key") == "final_top_k"), None)
    assert final_top_k_option is not None
    assert final_top_k_option.get("default") == 10

    use_reranker_option = next((opt for opt in options if opt.get("key") == "use_reranker"), None)
    assert use_reranker_option is not None
    assert use_reranker_option.get("default") is False

    # 保存查询参数（模拟前端配置）
    update_params = {
        "final_top_k": 5,
        "use_reranker": True,
        "recall_top_k": 20,
    }
    update_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/query-params", json=update_params, headers=admin_headers
    )
    assert update_response.status_code == 200, update_response.text

    # 再次获取参数，验证保存成功
    params_response2 = await test_client.get(f"/api/knowledge/databases/{kb_id}/query-params", headers=admin_headers)
    assert params_response2.status_code == 200, params_response2.text

    params_payload2 = params_response2.json()
    options2 = params_payload2.get("params", {}).get("options", [])

    # 验证保存的值
    final_top_k_option2 = next((opt for opt in options2 if opt.get("key") == "final_top_k"), None)
    assert final_top_k_option2 is not None
    assert final_top_k_option2.get("default") == 5  # 保存的值

    use_reranker_option2 = next((opt for opt in options2 if opt.get("key") == "use_reranker"), None)
    assert use_reranker_option2 is not None
    assert use_reranker_option2.get("default") is True  # 保存的值


async def test_concurrent_query_param_updates_preserve_all_options(test_client, admin_headers):
    """并发的部分更新应在数据库事务内合并，而不是后写覆盖先写。"""
    payload = {
        "database_name": f"pytest_query_params_{uuid.uuid4().hex[:6]}",
        "description": "Concurrent query params update",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }
    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    kb_id = create_response.json()["kb_id"]
    endpoint = f"/api/knowledge/databases/{kb_id}/query-params"

    first_response, second_response = await asyncio.gather(
        test_client.put(endpoint, json={"final_top_k": 7}, headers=admin_headers),
        test_client.put(endpoint, json={"similarity_threshold": 0.42}, headers=admin_headers),
    )

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text

    params_response = await test_client.get(endpoint, headers=admin_headers)
    assert params_response.status_code == 200, params_response.text
    options = params_response.json()["params"]["options"]
    saved_options = {option["key"]: option["default"] for option in options}
    assert saved_options["final_top_k"] == 7
    assert saved_options["similarity_threshold"] == 0.42


async def test_create_dify_database_success(test_client, admin_headers):
    db_name = f"pytest_dify_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Dify KB create test",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    created_payload = create_response.json()
    kb_id = created_payload["kb_id"]
    assert created_payload["embedding_model_spec"] is None
    assert "chunk_preset_id" not in created_payload["metadata"]

    info_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert info_response.status_code == 200, info_response.text
    additional_params = info_response.json()["additional_params"]
    assert additional_params["dify_api_url"] == "https://api.dify.ai/v1"
    assert additional_params["dify_token"] == "test-token"
    assert additional_params["dify_dataset_id"] == "dataset-123"


async def test_create_dify_database_missing_params_failed(test_client, admin_headers):
    payload = {
        "database_name": f"pytest_dify_missing_{uuid.uuid4().hex[:6]}",
        "description": "Dify KB missing params",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "",
            "dify_dataset_id": "",
        },
    }

    response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert response.status_code == 400, response.text
    assert "Dify 参数缺失" in response.json()["detail"]


async def test_create_dify_database_invalid_api_url_failed(test_client, admin_headers):
    payload = {
        "database_name": f"pytest_dify_bad_url_{uuid.uuid4().hex[:6]}",
        "description": "Dify KB invalid api url",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }

    response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert response.status_code == 400, response.text
    assert "/v1" in response.json()["detail"]


async def test_dify_query_params_and_documents_readonly(test_client, admin_headers):
    payload = {
        "database_name": f"pytest_dify_ro_{uuid.uuid4().hex[:6]}",
        "description": "Dify readonly routes",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    kb_id = create_response.json()["kb_id"]

    params_response = await test_client.get(f"/api/knowledge/databases/{kb_id}/query-params", headers=admin_headers)
    assert params_response.status_code == 200, params_response.text
    options = params_response.json().get("params", {}).get("options", [])
    option_keys = {item.get("key") for item in options}
    assert option_keys == {"search_mode", "final_top_k", "score_threshold_enabled", "similarity_threshold"}

    add_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents",
        json={"items": ["/tmp/demo.txt"], "params": {"content_type": "file"}},
        headers=admin_headers,
    )
    assert add_response.status_code == 400, add_response.text
    assert "只支持检索" in add_response.json()["detail"]

    parse_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents/parse",
        json=["file_id_1"],
        headers=admin_headers,
    )
    assert parse_response.status_code == 400, parse_response.text
    assert "只支持检索" in parse_response.json()["detail"]

    index_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents/index",
        json={"file_ids": ["file_id_1"], "params": {}},
        headers=admin_headers,
    )
    assert index_response.status_code == 400, index_response.text
    assert "只支持检索" in index_response.json()["detail"]

    edit_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/file_id_1/content",
        # revision 是必填项：不传会先撞 422，到不了只读连接器那道 400
        json={"content": "# x", "revision": "2026-01-01T00:00:00Z"},
        headers=admin_headers,
    )
    assert edit_response.status_code == 400, edit_response.text
    assert "只支持检索" in edit_response.json()["detail"]


# =============================================================================
# === Mindmap Tests ===
# =============================================================================


async def test_get_databases_overview(test_client, admin_headers, knowledge_database):
    """测试获取所有知识库概览"""
    response = await test_client.get("/api/knowledge/mindmap/databases", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "databases" in payload
    assert "total" in payload

    # 验证知识库在列表中
    kb_ids = [db["kb_id"] for db in payload["databases"]]
    assert knowledge_database["kb_id"] in kb_ids


async def test_get_database_files(test_client, admin_headers, knowledge_database):
    """测试获取知识库文件列表"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(f"/api/knowledge/databases/{kb_id}/mindmap/files", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert payload["kb_id"] == kb_id
    assert "files" in payload
    assert "total" in payload
    assert payload["db_name"] == knowledge_database["name"]


async def test_get_database_files_not_found(test_client, admin_headers):
    """测试获取不存在的知识库文件列表"""
    response = await test_client.get("/api/knowledge/databases/nonexistent_kb_id/mindmap/files", headers=admin_headers)
    assert response.status_code == 404


async def test_generate_mindmap_empty_files(test_client, admin_headers, knowledge_database):
    """测试空文件列表生成思维导图"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/mindmap/generate",
        json={"file_ids": [], "user_prompt": ""},
        headers=admin_headers,
    )
    # 空文件应该返回400错误
    assert response.status_code == 400
    assert "中没有文件" in response.json()["detail"]


async def test_get_database_mindmap_not_exists(test_client, admin_headers, knowledge_database):
    """测试获取不存在的思维导图"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(f"/api/knowledge/databases/{kb_id}/mindmap", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kb_id"] == kb_id
    assert payload["mindmap"] is None  # 尚未生成思维导图


# =============================================================================
# === Knowledge Router Additional Tests ===
# =============================================================================


async def test_get_accessible_databases(test_client, admin_headers, knowledge_database):
    """测试获取可访问的知识库列表"""
    response = await test_client.get("/api/knowledge/databases/accessible", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "databases" in payload

    # 验证知识库在列表中
    kb_ids = [db["kb_id"] for db in payload["databases"]]
    assert knowledge_database["kb_id"] in kb_ids


async def test_create_database_defaults_to_global_share_config(test_client, admin_headers):
    database = await _create_test_database(test_client, admin_headers)
    kb_id = database["kb_id"]
    try:
        assert database["share_config"] == {
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        }
    finally:
        await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)


@pytest.mark.parametrize(
    ("access_level", "scope_key"),
    [("department", "department_ids"), ("user", "user_uids")],
)
async def test_share_config_filters_accessible_databases(test_client, admin_headers, access_level, scope_key):
    """按 share_config 的访问范围过滤可访问知识库，department 与 user 两级同构。"""
    department_a = await _create_test_department(test_client, admin_headers, "pytest_dept_a")
    department_b = await _create_test_department(test_client, admin_headers, "pytest_dept_b")
    user_a = user_b = None
    database = None

    try:
        user_a = await _create_test_user(test_client, admin_headers, department_a["id"])
        user_b = await _create_test_user(test_client, admin_headers, department_b["id"])
        if access_level == "department":
            scope = {"access_level": "department", "department_ids": [department_a["id"]], "user_uids": []}
            scope_target = department_a["id"]
        else:
            scope = {"access_level": "user", "department_ids": [], "user_uids": [user_a["user"]["uid"]]}
            scope_target = user_a["user"]["uid"]
        database = await _create_test_database(
            test_client,
            admin_headers,
            {"version": 2, "read_scope": scope, "manage_scope": scope},
        )

        saved_config = database["share_config"]
        assert saved_config["manage_scope"]["access_level"] == access_level
        assert scope_target in saved_config["manage_scope"][scope_key]

        assert database["kb_id"] in await _accessible_kb_ids(test_client, user_a["headers"])
        assert database["kb_id"] not in await _accessible_kb_ids(test_client, user_b["headers"])
    finally:
        if database:
            await test_client.delete(f"/api/knowledge/databases/{database['kb_id']}", headers=admin_headers)
        if user_a:
            await _delete_user_by_id(test_client, admin_headers, user_a["user"]["id"])
        if user_b:
            await _delete_user_by_id(test_client, admin_headers, user_b["user"]["id"])
        await _delete_department_with_admin(test_client, admin_headers, department_a)
        await _delete_department_with_admin(test_client, admin_headers, department_b)


async def test_get_knowledge_base_types(test_client, admin_headers):
    """测试获取支持的知识库类型"""
    response = await test_client.get("/api/knowledge/types", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "kb_types" in payload
    assert "default_config" not in payload["kb_types"]["dify"]
    assert payload["kb_types"]["dify"]["name"] == "Dify"
    assert payload["kb_types"]["dify"]["description"] == "连接 Dify Dataset 的只读检索知识库"
    assert payload["kb_types"]["dify"]["requires_embedding_model"] is False
    assert payload["kb_types"]["dify"]["supports_documents"] is False
    assert [option["key"] for option in payload["kb_types"]["dify"]["create_params"]["options"]] == [
        "dify_api_url",
        "dify_token",
        "dify_dataset_id",
    ]
    assert "default_config" not in payload["kb_types"]["notion"]
    assert payload["kb_types"]["notion"]["name"] == "Notion"
    assert (
        payload["kb_types"]["notion"]["description"]
        == "连接 Notion Data Source 的只读知识库，支持检索、打开页面和页内查找"
    )
    assert payload["kb_types"]["notion"]["requires_embedding_model"] is False
    assert payload["kb_types"]["notion"]["supports_documents"] is False
    assert [option["key"] for option in payload["kb_types"]["notion"]["create_params"]["options"]] == [
        "notion_token",
        "notion_data_source_id",
        "notion_version",
    ]


async def test_get_knowledge_base_statistics(test_client, admin_headers):
    """测试获取知识库统计信息"""
    response = await test_client.get("/api/knowledge/stats", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "stats" in payload


async def test_get_supported_file_types(test_client, admin_headers):
    """测试获取支持的文件类型"""
    response = await test_client.get("/api/knowledge/files/supported-types", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "file_types" in payload
    assert isinstance(payload["file_types"], list)


async def test_markdown_endpoint_parses_uploaded_text_file(test_client, admin_headers):
    """测试 /files/markdown 能解析上传文件并返回 markdown。"""
    data_dir = Path(__file__).resolve().parents[2] / "data"
    test_file = data_dir / "A_Dream_of_Red_Mansions_10hui.txt"

    assert test_file.exists(), f"测试文件不存在: {test_file}"

    with test_file.open("rb") as f:
        response = await test_client.post(
            "/api/knowledge/files/markdown",
            headers=admin_headers,
            files={"file": (test_file.name, f, "text/plain")},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert isinstance(payload.get("markdown_content"), str)
    assert payload["markdown_content"].strip()


async def test_duplicate_database_name(test_client, admin_headers, knowledge_database):
    """测试重复创建同名知识库"""
    db_name = knowledge_database["name"]
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": db_name,
            "description": "Duplicate name test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "已存在" in response.json()["detail"]


async def test_create_lightrag_knowledge_base_is_unsupported(test_client, admin_headers):
    db_name = f"pytest_lightrag_{uuid.uuid4().hex[:6]}"
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": db_name,
            "description": "Unsupported LightRAG knowledge base",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "lightrag",
            "additional_params": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "Unsupported knowledge base type: lightrag" in response.json()["detail"]


async def test_sample_questions_endpoints(test_client, admin_headers, knowledge_database):
    """测试示例问题接口（空文件时预期返回400）"""
    kb_id = knowledge_database["kb_id"]

    # 获取示例问题（空知识库应该返回空列表）
    get_response = await test_client.get(f"/api/knowledge/databases/{kb_id}/sample-questions", headers=admin_headers)
    assert get_response.status_code == 200, get_response.text
    get_payload = get_response.json()
    assert get_payload["kb_id"] == kb_id
    assert "questions" in get_payload
    assert get_payload["count"] == 0  # 空知识库没有问题

    # 生成示例问题（空知识库应该返回400）
    generate_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/sample-questions",
        json={"count": 5},
        headers=admin_headers,
    )
    assert generate_response.status_code == 400
    assert "中没有文件" in generate_response.json()["detail"]


async def test_mindmap_permissions(test_client, standard_user, knowledge_database):
    """测试思维导图接口的权限控制"""
    kb_id = knowledge_database["kb_id"]

    # 普通用户应该无法访问
    forbidden_list = await test_client.get("/api/knowledge/mindmap/databases", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_list)

    forbidden_files = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/mindmap/files", headers=standard_user["headers"]
    )
    _assert_forbidden_response(forbidden_files)

    forbidden_generate = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/mindmap/generate",
        json={"file_ids": []},
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(forbidden_generate)


@pytest.mark.parametrize(
    "search_params",
    [
        {},
        {"query": "nonexistent-needle-xyz", "offset": 0, "limit": 50},
    ],
)
async def test_document_search_returns_empty_results(test_client, admin_headers, knowledge_database, search_params):
    """空关键词或不存在关键词都返回空结果，且不命中 /documents/{doc_id} 路由。"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents/search",
        params=search_params,
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["files"] == []
    assert payload["total"] == 0
    assert payload["has_more"] is False
    assert payload["offset"] == search_params.get("offset", 0)
    if "limit" in search_params:
        assert payload["limit"] == search_params["limit"]


async def test_document_search_requires_admin(test_client, standard_user, knowledge_database):
    """普通用户不能访问管理端搜索接口。"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents/search",
        params={"query": "x"},
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(response)


# =============================================================================
# === 解析产物编辑（入库前复核；只对待入库文件开放） ===
# =============================================================================


async def _seed_document_with_chunks(kb_id, prefix, *, status, chunk_count=3, markdown_file=True):
    """直接落库文档与分块，绕开耗时的真实解析/嵌入链路，聚焦编辑后的清理语义。

    `markdown_file=True` 时同时把产物对象写进 MinIO——真实的 parsed 文件必然有产物，
    只落库会让「产物内容」在编辑前无从比对（读引用会 NoSuchKey）。
    """
    file_id = f"file_{prefix}"
    if markdown_file:
        from yuxi.storage.minio import get_minio_client

        await get_minio_client().aupload_file(
            "knowledgebases",
            f"{kb_id}/parsed/{file_id}.md",
            f"# {prefix}\n\n种子产物内容。".encode(),
            content_type="text/markdown",
        )
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files "
                    "(file_id, kb_id, parent_id, filename, file_type, status, is_folder, "
                    " markdown_file, chunk_count, token_count, created_at, updated_at) "
                    "VALUES (:fid, :kb, NULL, :name, 'txt', :status, FALSE, :md, :cc, :tc,"
                    " now(), now())"
                ),
                {
                    "fid": file_id,
                    "kb": kb_id,
                    "name": f"{prefix}.txt",
                    "status": status,
                    "md": (f"http://minio/knowledgebases/{kb_id}/parsed/{file_id}.md" if markdown_file else None),
                    "cc": chunk_count,
                    "tc": chunk_count * 10,
                },
            )
            for index in range(chunk_count):
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_chunks (chunk_id, file_id, kb_id, chunk_index, content) "
                        "VALUES (:cid, :fid, :kb, :idx, :content)"
                    ),
                    {
                        "cid": f"{file_id}_chunk_{index}",
                        "fid": file_id,
                        "kb": kb_id,
                        "idx": index,
                        "content": f"旧内容分块 {index}",
                    },
                )
    finally:
        await engine.dispose()
    return file_id


async def _chunk_rows_for_file(file_id: str) -> int:
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT count(*) FROM knowledge_chunks WHERE file_id = :fid"), {"fid": file_id}
            )
            return result.scalar_one()
    finally:
        await engine.dispose()


async def _read_markdown_object(test_client, kb_id: str, file_id: str, headers) -> str:
    """读取该文件**当前权威**的产物对象。

    编辑产物用内容寻址名（`{file_id}.{hash}.md`），所以必须先取行上的 `markdown_file`
    引用再下载——拼固定路径只能读到解析产出那一份，编辑后就会读到旧内容。
    引用从 HTTP 元数据接口取：测试进程与 app 不共享连接池，直接用 pg_manager 会撞上
    「Future attached to a different loop」。
    """
    from yuxi.knowledge.utils.kb_utils import parse_minio_url
    from yuxi.storage.minio import get_minio_client

    meta = await test_client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/basic", headers=headers)
    assert meta.status_code == 200, meta.text
    url = meta.json()["meta"]["markdown_file"]
    assert url, "文件行没有 markdown_file，产物引用缺失"

    bucket_name, object_name = parse_minio_url(url)
    raw = await get_minio_client().adownload_file(bucket_name, object_name)
    return raw.decode("utf-8")


async def _fetch_content(test_client, kb_id: str, file_id: str, headers) -> dict:
    response = await test_client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/content", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _content_url(kb_id: str, file_id: str) -> str:
    return f"/api/knowledge/databases/{kb_id}/documents/{file_id}/content"


async def test_edit_parsed_document_writes_content_and_keeps_status(test_client, admin_headers, knowledge_database):
    """第一阶段的核心闭环：待入库文件可改产物，状态不变，用户继续走既有「入库」。"""
    kb_id = knowledge_database["kb_id"]
    file_id = await _seed_document_with_chunks(kb_id, uuid.uuid4().hex[:8], status="parsed", chunk_count=0)

    before = await _fetch_content(test_client, kb_id, file_id, admin_headers)
    revision = before["content_revision"]
    assert revision

    response = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "# 修订后的标题\n\n这是人工修正后的内容。", "revision": revision},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["status"] == "parsed"
    # 期望版本必须随保存推进，否则用户紧接着再保存一次会拿旧版本去比对
    assert payload["content_revision"] != revision

    edited = "# 修订后的标题\n\n这是人工修正后的内容。"
    assert await _read_markdown_object(test_client, kb_id, file_id, admin_headers) == edited
    after = await _fetch_content(test_client, kb_id, file_id, admin_headers)
    # 内容与修订必须配成**同一次读取**的一对：分别取两次可能拿到（旧内容 + 新版本），
    # 那正是编辑保存要防的组合
    assert after["content"] == edited
    assert after["content_revision"] == payload["content_revision"]

    basic = await test_client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/basic", headers=admin_headers)
    assert basic.status_code == 200, basic.text
    assert basic.json()["meta"]["status"] == "parsed"


async def test_edit_rejects_stale_revision_without_overwriting(test_client, admin_headers, knowledge_database):
    """用过期的期望版本保存：409，且不覆盖先写入的内容。"""
    kb_id = knowledge_database["kb_id"]
    file_id = await _seed_document_with_chunks(kb_id, uuid.uuid4().hex[:8], status="parsed", chunk_count=0)
    stale = (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"]

    first = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "# 先到的修改", "revision": stale},
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text

    second = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "# 迟到的修改", "revision": stale},
        headers=admin_headers,
    )
    assert second.status_code == 409, second.text
    assert "已被其他人修改" in second.json()["detail"]
    assert await _read_markdown_object(test_client, kb_id, file_id, admin_headers) == "# 先到的修改"


async def test_concurrent_edits_leave_exactly_one_winner(test_client, admin_headers, knowledge_database):
    """两个**同时**提交的保存：期望版本是同一条条件更新的等值条件，只能有一个命中。

    这条是本功能并发语义的直接证据：状态 CAS 单独用挡不住两个 parsed 编辑者
    （两边状态都成立），所以期望版本必须参与落库条件。
    """
    kb_id = knowledge_database["kb_id"]
    file_id = await _seed_document_with_chunks(kb_id, uuid.uuid4().hex[:8], status="parsed", chunk_count=0)
    revision = (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"]

    responses = await asyncio.gather(
        test_client.put(
            _content_url(kb_id, file_id),
            json={"content": "# 编辑A", "revision": revision},
            headers=admin_headers,
        ),
        test_client.put(
            _content_url(kb_id, file_id),
            json={"content": "# 编辑B", "revision": revision},
            headers=admin_headers,
        ),
    )
    codes = sorted(response.status_code for response in responses)
    assert codes == [200, 409], [response.text for response in responses]

    # 落库内容是赢家的，且版本已推进（输家不能静默覆盖）
    winner = next(response for response in responses if response.status_code == 200).json()
    assert await _read_markdown_object(test_client, kb_id, file_id, admin_headers) in {"# 编辑A", "# 编辑B"}
    after = await _fetch_content(test_client, kb_id, file_id, admin_headers)
    assert after["content_revision"] == winner["content_revision"]


async def test_edit_rejects_indexed_document_and_keeps_chunks(test_client, admin_headers, knowledge_database):
    """已入库文件不能从这个接口改：它的修改要走「重新入库」链路，不能借编辑绕过清理。"""
    kb_id = knowledge_database["kb_id"]
    file_id = await _seed_document_with_chunks(kb_id, uuid.uuid4().hex[:8], status="indexed")
    assert await _chunk_rows_for_file(file_id) == 3
    revision = (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"]

    response = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "# 想直接改已入库内容", "revision": revision},
        headers=admin_headers,
    )
    assert response.status_code == 409, response.text
    assert "不支持编辑解析产物" in response.json()["detail"]
    # 被拒绝时既不碰分块、也不推进版本（版本未动即产物未写）
    assert await _chunk_rows_for_file(file_id) == 3
    assert (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"] == revision


async def test_edit_document_rejects_invalid_requests(test_client, admin_headers, knowledge_database):
    """空内容 400；缺 revision 422；revision 非法 400；文档不存在 400；知识库不存在 404。"""
    kb_id = knowledge_database["kb_id"]
    file_id = await _seed_document_with_chunks(kb_id, uuid.uuid4().hex[:8], status="parsed", chunk_count=0)
    revision = (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"]

    empty = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "   \n  ", "revision": revision},
        headers=admin_headers,
    )
    assert empty.status_code == 400, empty.text
    assert "不能为空" in empty.json()["detail"]

    missing_revision = await test_client.put(
        _content_url(kb_id, file_id), json={"content": "# x"}, headers=admin_headers
    )
    assert missing_revision.status_code == 422, missing_revision.text

    bad_revision = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "# x", "revision": "not-a-time"},
        headers=admin_headers,
    )
    assert bad_revision.status_code == 400, bad_revision.text
    assert "修订标识" in bad_revision.json()["detail"]

    # 文档不存在时 _load_file_meta 抛 ValueError("File ... not found")，与 move_document
    # 等既有同级端点一样映射为 400；只有 KBNotFoundError（知识库不存在）才是 404。
    missing_doc = await test_client.put(
        _content_url(kb_id, "file_not_exist"),
        json={"content": "# x", "revision": revision},
        headers=admin_headers,
    )
    assert missing_doc.status_code == 400, missing_doc.text
    assert "not found" in missing_doc.json()["detail"]

    missing_kb = await test_client.put(
        _content_url("kb_not_exist", "file_not_exist"),
        json={"content": "# x", "revision": revision},
        headers=admin_headers,
    )
    assert missing_kb.status_code == 404, missing_kb.text


async def test_edit_document_requires_manage_permission(test_client, admin_headers, standard_user, knowledge_database):
    """非管理员不得编辑产物；被拒绝的请求不得改动产物。

    知识库文档路由两侧都由 `get_admin_user` 收口（读是 `require_knowledge_base_read`，
    写是 `require_knowledge_base_manage`），因此 HTTP 层构造不出「能读不能管」的用户，
    这里断言的是「非管理员整体被拒」这一真实边界；产物的前置与回读用管理员凭据完成，
    否则读接口本身就会 403，断言的不是写入被拦。
    """
    kb_id = knowledge_database["kb_id"]
    file_id = await _seed_document_with_chunks(kb_id, uuid.uuid4().hex[:8], status="parsed", chunk_count=0)

    before_revision = (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"]
    before_object = await _read_markdown_object(test_client, kb_id, file_id, admin_headers)

    response = await test_client.put(
        _content_url(kb_id, file_id),
        json={"content": "# 未授权写入", "revision": before_revision},
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(response)

    # 被拒绝的请求不得推进版本，也不得换掉权威产物（版本未动即引用未切换）
    after_revision = (await _fetch_content(test_client, kb_id, file_id, admin_headers))["content_revision"]
    assert after_revision == before_revision
    assert await _read_markdown_object(test_client, kb_id, file_id, admin_headers) == before_object
