"""真实 HTTP、PostgreSQL 与 worker 的目录和导入回归。"""

import asyncio
import hashlib
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from yuxi.infrastructure.minio.client import get_minio_client
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile, KnowledgeImport, KnowledgeProjectionOutbox

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def directory_kb(test_client, admin_headers, postgres_sessions):
    """显式建立不依赖外部模型的知识库，回收本例数据库与对象。"""
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200
    uid, kb_id = me.json()["uid"], "directory_" + uuid4().hex
    async with postgres_sessions() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name=kb_id, kb_type="milvus", created_by=uid, additional_params={}))
        await session.commit()
    yield kb_id
    async with postgres_sessions() as session:
        jobs = (
            await session.scalars(
                select(BackgroundJobRecord).where(
                    BackgroundJobRecord.payload["kb_id"].as_string() == kb_id,
                )
            )
        ).all()
        assert all(job.status not in {"pending", "running"} for job in jobs), "测试仍有活动 worker"
        for job in jobs:
            await session.delete(job)
        await session.execute(delete(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.kb_id == kb_id))
        await session.execute(delete(KnowledgeImport).where(KnowledgeImport.kb_id == kb_id))
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
        await session.commit()
    storage = get_minio_client()
    for bucket in set(storage.KB_BUCKETS.values()):
        await storage.adelete_objects_by_prefix(bucket, kb_id + "/")


async def staged(client, headers, kb_id, name="Report.txt", content=b"original bytes\n"):
    """通过实际上传取得原件，不伪造内容指纹。"""
    response = await client.post("/api/knowledge/files/upload", params={"kb_id": kb_id}, files={"file": (name, content)}, headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["content_hash"] == hashlib.sha256(content).hexdigest()
    return payload


def manifest(payloads, paths=None, **params):
    """用显式输入清单建立请求，预期目录树在断言中独立定义。"""
    items = [p["file_path"] for p in payloads]
    params["content_hashes"] = {p["file_path"]: p["content_hash"] for p in payloads}
    if paths is not None:
        params["source_paths"] = dict(zip(items, paths, strict=True))
    return {"items": items, "params": params}


async def test_http_concurrent_import_materializes_and_suffixes(test_client, admin_headers, directory_kb, postgres_sessions):
    """并发导入同目录同名叶子，数据库只有一条目录链且原件字节完整。"""
    kb_id = directory_kb
    contents = [b"original bytes\n", b"second bytes\n"]
    payloads = [await staged(test_client, admin_headers, kb_id, content=content) for content in contents]
    responses = await asyncio.gather(
        *[
            test_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/documents/add", json=manifest([p], ["资料/Report.txt"]), headers=admin_headers
            )
            for p in payloads
        ]
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json()["added"] == 1 for r in responses), [r.json() for r in responses]
    async with postgres_sessions() as observer:
        rows = (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))).all()
        folders = [r for r in rows if r.is_folder]
        files = [r for r in rows if not r.is_folder]
        assert [(r.filename, r.parent_id) for r in folders] == [("资料", None)]
        assert {r.filename for r in files} == {"Report.txt", "Report (1).txt"}
        assert {r.parent_id for r in files} == {folders[0].file_id}
        assert len({r.file_id for r in files}) == 2
    for payload, content in zip(payloads, contents, strict=True):
        assert await get_minio_client().adownload_file(payload["bucket_name"], payload["object_name"]) == content


@pytest.mark.parametrize("identity", ["", "x" * 129])
async def test_http_import_rejects_invalid_identity(test_client, admin_headers, directory_kb, postgres_sessions, identity):
    """显式空标识和过长标识拒绝登记，不能静默改用派生身份。"""
    p = await staged(test_client, admin_headers, directory_kb)
    result = await test_client.post(
        f"/api/knowledge/knowledge-bases/{directory_kb}/documents/add",
        json={**manifest([p]), "import_id": identity},
        headers=admin_headers,
    )
    assert result.status_code == 422, result.text
    async with postgres_sessions() as observer:
        assert not (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
        assert not (await observer.scalars(select(KnowledgeImport).where(KnowledgeImport.kb_id == directory_kb))).all()


async def test_http_import_replay_conflict_and_deleted_tombstone(test_client, admin_headers, directory_kb, postgres_sessions):
    """丢失响应后重试复用回执；不同请求拒绝，已删除条目不会复活。"""
    kb_id = directory_kb
    p = await staged(test_client, admin_headers, kb_id)
    body = {**manifest([p]), "import_id": uuid4().hex}
    url = f"/api/knowledge/knowledge-bases/{kb_id}/documents/add"
    first = await test_client.post(url, json=body, headers=admin_headers)
    assert first.status_code == 200 and first.json()["added"] == 1, first.text
    replay = await test_client.post(url, json=body, headers=admin_headers)
    assert replay.json() == first.json()
    conflict = await test_client.post(url, json={**body, "params": {**body["params"], "auto_index": True}}, headers=admin_headers)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "import_conflict"
    file_id = first.json()["items"][0]["file_id"]
    async with postgres_sessions() as session:
        await session.execute(text("UPDATE knowledge_files SET deleted_at = now() WHERE file_id = :id"), {"id": file_id})
        await session.commit()
    replay = await test_client.post(url, json=body, headers=admin_headers)
    assert replay.json() == first.json()
    async with postgres_sessions() as observer:
        rows = (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))).all()
        assert len(rows) == 1 and rows[0].deleted_at is not None


async def test_http_folder_constraints_and_move(test_client, admin_headers, directory_kb, postgres_sessions):
    """大小写、NFC、根目录、类型占用和移动共同遵守名称约束。"""
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"

    async def folder(name, parent=None):
        return await test_client.post(root + "/folders", json={"folder_name": name, "parent_id": parent}, headers=admin_headers)

    a = await folder("Café")
    assert a.status_code == 200, a.text
    duplicate = await folder(" CAFE\u0301 ")
    assert duplicate.status_code == 409, duplicate.text
    parent = await folder("Parent")
    child = await folder("Café", parent.json()["file_id"])
    moved = await test_client.put(root + f"/documents/{child.json()['file_id']}/move", json={"new_parent_id": None}, headers=admin_headers)
    assert moved.status_code == 409, moved.text
    renamed = await test_client.put(root + f"/folders/{a.json()['file_id']}/rename", json={"folder_name": "CAFÉ"}, headers=admin_headers)
    assert renamed.status_code == 200, renamed.text
    invalid = await folder("../bad")
    assert invalid.status_code == 422
    async with postgres_sessions() as observer:
        node = await observer.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == child.json()["file_id"]))
        assert node.parent_id == parent.json()["file_id"] and node.filename == "Café"


async def test_pg_rejects_direct_duplicate_without_service(directory_kb, postgres_sessions):
    """绕过 service 的原始 SQL 也必须被指定唯一索引拒绝。"""
    statement = text("INSERT INTO knowledge_files(file_id,kb_id,filename,is_folder) VALUES (:id,:kb,:name,:folder)")
    async with postgres_sessions() as session:
        await session.execute(statement, {"id": uuid4().hex, "kb": directory_kb, "name": "Occupied", "folder": False})
        await session.commit()
    async with postgres_sessions() as session:
        with pytest.raises(IntegrityError) as error:
            await session.execute(statement, {"id": uuid4().hex, "kb": directory_kb, "name": "occupied", "folder": True})
        assert getattr(error.value.orig.__cause__, "constraint_name", None) == "uq_knowledge_files_active_name"
        await session.rollback()


async def test_manifest_rejection_and_per_file_rollback(test_client, admin_headers, directory_kb, postgres_sessions):
    """非法清单零写入；中间目录被文件占用时失败条目不留下半条目录链。"""
    p = await staged(test_client, admin_headers, directory_kb, name="a.txt", content=b"file-a\n")
    q = await staged(test_client, admin_headers, directory_kb, name="b.txt", content=b"file-b\n")
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    bad = await test_client.post(root + "/documents/add", json=manifest([p, q], ["a/../x.txt", "ok/b.txt"]), headers=admin_headers)
    assert bad.status_code == 422
    async with postgres_sessions() as observer:
        assert not (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
    initial = await test_client.post(root + "/documents/add", json=manifest([p]), headers=admin_headers)
    assert initial.json()["added"] == 1
    result = await test_client.post(root + "/documents/add", json=manifest([q], ["a.txt/sub/b.txt"]), headers=admin_headers)
    assert result.status_code == 200 and result.json()["failed"] == 1, result.text
    async with postgres_sessions() as observer:
        rows = (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
        assert len(rows) == 1 and not rows[0].is_folder


async def test_real_worker_uses_registered_file(test_client, admin_headers, directory_kb, postgres_sessions):
    """实际 worker 解析已登记身份；重试不新增作业或文件，产物可回读。"""
    p = await staged(test_client, admin_headers, directory_kb, name="source.txt", content=b"Hello real worker.\n")
    body = manifest([p], ["docs/source.txt"])
    url = f"/api/knowledge/knowledge-bases/{directory_kb}/documents"
    response = await test_client.post(url, json=body, headers=admin_headers)
    assert response.status_code == 200 and response.json()["added"] == 1, response.text
    result = response.json()
    replay = await test_client.post(url, json=body, headers=admin_headers)
    assert replay.json() == result
    async with asyncio.timeout(60):
        while True:
            async with postgres_sessions() as observer:
                job = await observer.get(BackgroundJobRecord, result["job_id"])
                if job.status in {"success", "failed", "cancelled"}:
                    break
            await asyncio.sleep(0.2)
    assert job.status == "success", job.error
    async with postgres_sessions() as observer:
        files = (
            await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb, KnowledgeFile.is_folder.is_(False)))
        ).all()
        assert len(files) == 1 and files[0].file_id == result["items"][0]["file_id"] and files[0].status == "parsed"
    from yuxi.infrastructure.minio.object_urls import parse_minio_url

    bucket, name = parse_minio_url(files[0].markdown_file)
    assert b"Hello real worker." in await get_minio_client().adownload_file(bucket, name)


@pytest.mark.parametrize("name", ["a#b.txt", "a%20b.txt", "a?b.txt", "资" * 508 + ".txt"])
async def test_reserved_url_characters_keep_registered_original(test_client, admin_headers, directory_kb, name):
    """合法逻辑名称通过 URL 编码回读同一对象，提交清理不能删除原件。"""
    from yuxi.infrastructure.minio.object_urls import parse_minio_url

    p = await staged(test_client, admin_headers, directory_kb, name=name)
    result = await test_client.post(
        f"/api/knowledge/knowledge-bases/{directory_kb}/documents/add", json=manifest([p], ["dir/" + name]), headers=admin_headers
    )
    assert result.status_code == 200 and result.json()["added"] == 1, result.text
    meta = result.json()["items"][0]["file_meta"]
    assert meta["filename"] == name
    bucket, key = parse_minio_url(meta["path"])
    assert key.endswith("/source.txt")
    assert await get_minio_client().adownload_file(bucket, key) == b"original bytes\n"


async def test_concurrent_same_receipt_and_explicit_new_import(test_client, admin_headers, directory_kb, postgres_sessions):
    """并发重放只登记一次；相同内容的新导入返回已有文件。"""
    p = await staged(test_client, admin_headers, directory_kb)
    body = {**manifest([p]), "import_id": uuid4().hex}
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    responses = await asyncio.gather(*[test_client.post(root + "/documents/add", json=body, headers=admin_headers) for _ in range(2)])
    assert responses[0].status_code == responses[1].status_code == 200
    assert responses[0].json() == responses[1].json()
    first = responses[0].json()["items"][0]
    new_import_id = uuid4().hex
    second_response = await test_client.post(root + "/documents/add", json={**body, "import_id": new_import_id}, headers=admin_headers)
    assert second_response.status_code == 200 and second_response.json()["added"] == 1, second_response.text
    second = second_response.json()
    assert second["import_id"] == new_import_id
    assert second["items"][0]["file_id"] == first["file_id"]
    assert second["items"][0]["file_meta"]["path"] == first["file_meta"]["path"]
    async with postgres_sessions() as observer:
        receipts = (await observer.scalars(select(KnowledgeImport).where(KnowledgeImport.kb_id == directory_kb))).all()
        files = (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
        assert {receipt.import_id for receipt in receipts} == {body["import_id"], new_import_id}
        assert len(files) == 1 and files[0].deleted_at is None and files[0].file_id == first["file_id"]


async def test_partial_import_worker_processes_successful_original_index(test_client, admin_headers, directory_kb, postgres_sessions):
    """第一项失败、第二项成功仍由真实 worker 正确解析成功文件。"""
    blocker = await staged(test_client, admin_headers, directory_kb, name="blocked.txt", content=b"blocked\n")
    conflict = await staged(test_client, admin_headers, directory_kb, name="child.txt", content=b"conflict\n")
    good = await staged(test_client, admin_headers, directory_kb, name="good.txt", content=b"Hello good worker.\n")
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    initial = await test_client.post(root + "/documents/add", json=manifest([blocker]), headers=admin_headers)
    assert initial.status_code == 200 and initial.json()["added"] == 1
    body = manifest([conflict, good], ["blocked.txt/child.txt", "docs/good.txt"])
    response = await test_client.post(root + "/documents", json=body, headers=admin_headers)
    assert response.status_code == 200 and (response.json()["added"], response.json()["failed"]) == (1, 1), response.text
    result = response.json()
    assert result["items"][0]["index"] == 1
    async with asyncio.timeout(60):
        while True:
            async with postgres_sessions() as observer:
                job = await observer.get(BackgroundJobRecord, result["job_id"])
                if job.status in {"success", "failed", "cancelled"}:
                    break
            await asyncio.sleep(0.2)
    assert job.status == "success", job.error
    async with postgres_sessions() as observer:
        node = await observer.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == result["items"][0]["file_id"]))
        assert node.status == "parsed" and node.filename == "good.txt"


async def test_delete_and_new_child_share_tree_lock(test_client, admin_headers, directory_kb, postgres_sessions):
    """真实并发删除与新建都等待同一树锁，最终没有有效节点指向删除父目录。"""
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    folder = await test_client.post(root + "/folders", json={"folder_name": "parent"}, headers=admin_headers)
    assert folder.status_code == 200
    parent = folder.json()["file_id"]
    async with postgres_sessions() as guard:
        await guard.execute(text("SELECT pg_advisory_xact_lock(hashtext(:kb))"), {"kb": directory_kb})
        deleting = asyncio.create_task(test_client.delete(root + "/documents/" + parent, headers=admin_headers))
        creating = asyncio.create_task(
            test_client.post(root + "/folders", json={"folder_name": "child", "parent_id": parent}, headers=admin_headers)
        )
        await asyncio.sleep(0.1)
        assert not deleting.done() and not creating.done()
        await guard.commit()
    deleted, created = await asyncio.gather(deleting, creating)
    assert deleted.status_code == 200, deleted.text
    assert created.status_code in {200, 404}, created.text
    async with postgres_sessions() as observer:
        nodes = (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
        assert nodes and all(node.deleted_at is not None for node in nodes)


async def test_initial_queue_failure_keeps_committed_import_and_real_worker_recovers(
    test_client, admin_headers, directory_kb, postgres_sessions, monkeypatch
):
    """首投失败不撤销文件，重放后重复投递仍只处理已登记身份。"""
    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.background_jobs import dispatch
    from yuxi.modules.knowledge.services.imports import register_import

    p = await staged(test_client, admin_headers, directory_kb, name="delivery.txt")
    body = manifest([p], ["docs/delivery.txt"])
    uid = (await test_client.get("/api/auth/me", headers=admin_headers)).json()["uid"]
    publish = dispatch.publish_job

    async def unavailable(_job_id):
        raise ConnectionError("injected initial queue failure")

    try:
        monkeypatch.setattr(dispatch, "publish_job", unavailable)
        result = await register_import(kb_id=directory_kb, items=body["items"], params=body["params"], operator_id=uid, process=True)
        async with postgres_sessions() as observer:
            node = await observer.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == result["items"][0]["file_id"]))
            job = await observer.get(BackgroundJobRecord, result["job_id"])
            assert node is not None and job is not None and job.handler_version == 2
        monkeypatch.setattr(dispatch, "publish_job", publish)
        replay = await test_client.post(f"/api/knowledge/knowledge-bases/{directory_kb}/documents", json=body, headers=admin_headers)
        assert replay.json() == result
        await publish(result["job_id"])
        await publish(result["job_id"])
        async with asyncio.timeout(60):
            while True:
                async with postgres_sessions() as observer:
                    job = await observer.get(BackgroundJobRecord, result["job_id"])
                    if job.status in {"success", "failed", "cancelled"}:
                        break
                await asyncio.sleep(0.2)
        assert job.status == "success", job.error
        async with postgres_sessions() as observer:
            files = (
                await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb, KnowledgeFile.is_folder.is_(False)))
            ).all()
            assert len(files) == 1 and files[0].file_id == result["items"][0]["file_id"] and files[0].status == "parsed"
    finally:
        await pg_manager.close()


async def test_import_permissions_and_foreign_parent_are_enforced(
    test_client, admin_headers, standard_user, directory_kb, postgres_sessions
):
    """实际权限依赖与同库父查询拒绝旁路，拒绝请求不发布文件或回执。"""
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    p = await staged(test_client, admin_headers, directory_kb)
    denied = await test_client.post(root + "/documents/add", json=manifest([p]), headers=standard_user["headers"])
    assert denied.status_code == 403, denied.text
    denied_folder = await test_client.post(root + "/folders", json={"folder_name": "private"}, headers=standard_user["headers"])
    assert denied_folder.status_code == 403, denied_folder.text
    other = "foreign_" + uuid4().hex
    async with postgres_sessions() as session:
        session.add(KnowledgeBase(kb_id=other, name=other, kb_type="milvus"))
        await session.flush()
        session.add(KnowledgeFile(file_id=other, kb_id=other, filename="parent", is_folder=True))
        await session.commit()
    try:
        rejected = await test_client.post(root + "/documents/add", json=manifest([p], parent_id=other), headers=admin_headers)
        assert rejected.status_code == 404, rejected.text
        async with postgres_sessions() as observer:
            assert not (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
            assert not (await observer.scalars(select(KnowledgeImport).where(KnowledgeImport.kb_id == directory_kb))).all()
    finally:
        async with postgres_sessions() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == other))
            await session.commit()


async def test_manifest_equivalence_and_real_path_existence(test_client, admin_headers, directory_kb):
    """清单等价重复在写入前拒绝；现有路径查询沿真实父关系读取。"""
    p, q = [await staged(test_client, admin_headers, directory_kb, name=name) for name in ["first.txt", "second.txt"]]
    root = f"/api/knowledge/knowledge-bases/{directory_kb}"
    invalid = await test_client.post(
        root + "/documents/add", json=manifest([p, q], ["Café/a.txt", "Cafe\u0301/A.txt"]), headers=admin_headers
    )
    assert invalid.status_code == 422, invalid.text
    valid = await test_client.post(root + "/documents/add", json=manifest([p], ["Café/a.txt"]), headers=admin_headers)
    assert valid.status_code == 200 and valid.json()["added"] == 1, valid.text
    found = await test_client.get(root + "/documents/exists", params={"filename": "CAFÉ/A.TXT"}, headers=admin_headers)
    assert found.status_code == 200 and found.json()["exists"] is True, found.text
    missing = await test_client.get(root + "/documents/exists", params={"filename": "a.txt"}, headers=admin_headers)
    assert missing.status_code == 200 and missing.json()["exists"] is False


async def test_long_extension_exhaustion_is_per_file_failure(test_client, admin_headers, directory_kb, postgres_sessions):
    """两位序号耗尽主名预算时明确拒绝该条，其他成功项仍原子登记。"""
    extension = "." + "x" * 506
    requested = "a" + extension
    async with postgres_sessions() as session:
        for number in range(10):
            name = requested if number == 0 else f"a ({number})" + extension
            session.add(KnowledgeFile(file_id=uuid4().hex, kb_id=directory_kb, filename=name, is_folder=False))
        await session.commit()
    p, q = [await staged(test_client, admin_headers, directory_kb, name=name) for name in ["a.txt", "good.txt"]]
    result = await test_client.post(
        f"/api/knowledge/knowledge-bases/{directory_kb}/documents/add",
        json=manifest([p, q], [requested, "good.txt"]),
        headers=admin_headers,
    )
    assert result.status_code == 200 and (result.json()["added"], result.json()["failed"]) == (1, 1), result.text
    assert "扩展名过长" in result.json()["failed_items"][0]["error"]
    async with postgres_sessions() as observer:
        nodes = (await observer.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == directory_kb))).all()
        assert len(nodes) == 11 and max(len(node.filename) for node in nodes) <= 512
