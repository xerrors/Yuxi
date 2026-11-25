from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile

import yuxi.api.routers.knowledge.management as knowledge_router
import yuxi.modules.knowledge.services.background_jobs as knowledge_job_service
import yuxi.modules.knowledge.services.document_actions as document_actions
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.knowledge.services import bases as knowledge_bases
from yuxi.modules.knowledge.services import documents as knowledge_documents
from yuxi.modules.knowledge.services import graph_build, uploads

pytestmark = pytest.mark.asyncio


def _patch_submit_job(monkeypatch, fake):
    """路由与服务共享作业提交入口，测试同时替换，避免按调用路径分叉。"""
    monkeypatch.setattr(document_actions, "submit_knowledge_job", fake)


def _database_detail(**stats) -> KnowledgeBaseDetail:
    return KnowledgeBaseDetail(
        kb_id="kb_1",
        name="测试知识库",
        description=None,
        kb_type="milvus",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        created_by=None,
        created_at=None,
        **stats,
    )


class FakeBackgroundJobContext:
    def __init__(self, payload: dict | None = None):
        self.job_id = "job_1"
        self.worker_id = "worker_1"
        self.payload = payload or {}
        self.result = None

    async def set_message(self, message: str) -> None:
        return None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_result(self, result: dict) -> None:
        self.result = result

    async def raise_if_cancelled(self) -> None:
        return None


async def test_document_file_exists_returns_boolean_for_relative_path(monkeypatch):
    captured = {}

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_document_file_exists(kb_id: str, filename: str) -> bool:
        captured["exists"] = (kb_id, filename)
        return True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_documents, "document_file_exists", fake_document_file_exists)

    result = await knowledge_router.document_file_exists(
        "kb_1",
        filename=" google_drive/shared_drives/engineering/playbook.txt ",
        current_user=SimpleNamespace(uid="user_1"),
    )

    assert result == {
        "kb_id": "kb_1",
        "filename": "google_drive/shared_drives/engineering/playbook.txt",
        "exists": True,
    }
    assert captured == {
        "ensure": ("kb_1", "文档存在性检查"),
        "exists": ("kb_1", "google_drive/shared_drives/engineering/playbook.txt"),
    }


async def test_document_file_exists_rejects_blank_filename(monkeypatch):
    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.document_file_exists(
            "kb_1",
            filename="   ",
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "filename is required"


async def test_upload_file_rejects_jsonl_uploads():
    upload = UploadFile(filename="dataset.jsonl", file=BytesIO(b'{"query":"hello"}\n'))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id=None, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported file type: .jsonl"


@pytest.mark.parametrize(
    "call_upload",
    [
        lambda upload: knowledge_router.upload_file(upload, kb_id="kb_1", current_user=SimpleNamespace(uid="user_1")),
    ],
    ids=["upload_file"],
)
async def test_rejects_oversized_file(monkeypatch, call_upload):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await call_upload(upload)

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


@pytest.mark.parametrize(
    ("kb_id", "status_code", "error_detail"),
    [
        ("missing", 404, "知识库 missing 不存在"),
        ("readonly", 400, "只支持检索，不支持文档上传"),
    ],
)
async def test_upload_file_fails_before_read_or_minio(monkeypatch, kb_id, status_code, error_detail):
    calls = {"read": 0, "upload": 0}

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> None:
        raise HTTPException(status_code=status_code, detail=error_detail)

    async def fake_read_upload_with_limit(*_args, **_kwargs) -> bytes:
        calls["read"] += 1
        return b"demo"

    async def fake_upload_to_minio(*_args, **_kwargs) -> str:
        calls["upload"] += 1
        return "minio://knowledgebases/kb_1/upload/demo.txt"

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", fake_read_upload_with_limit)
    monkeypatch.setattr(uploads, "get_minio_client", lambda: SimpleNamespace(aupload_file=fake_upload_to_minio))

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id=kb_id, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == status_code
    assert calls == {"read": 0, "upload": 0}


async def test_index_documents_uses_uid_for_operator(monkeypatch):
    captured = {}

    async def fake_get_knowledge_base_info(kb_id: str) -> KnowledgeBaseDetail:
        return _database_detail()

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> dict:
        return await fake_get_knowledge_base_info(kb_id)

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None, **_kwargs):
        captured["operator_id"] = operator_id
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, job_type: str, payload: dict):
        await knowledge_job_service.run_knowledge_index(FakeBackgroundJobContext(payload))
        return SimpleNamespace(id="job_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_bases, "get_knowledge_base_info", fake_get_knowledge_base_info)
    monkeypatch.setattr(document_actions, "index_file", fake_index_file)
    _patch_submit_job(monkeypatch, fake_enqueue)

    result = await knowledge_router.index_documents(
        "kb_1",
        ["file_1"],
        params={},
        current_user=SimpleNamespace(id="numeric-id", uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["operator_id"] == "uid-user"


async def test_parse_documents_rejects_oversized_direct_batch():
    file_ids = [f"file_{index}" for index in range(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS + 1)]

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.parse_documents(
            "kb_1",
            file_ids,
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert str(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS) in exc_info.value.detail


async def test_parse_pending_documents_enqueues_status_scoped_job(monkeypatch):
    captured = {"list_calls": [], "parsed": []}

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> dict:
        captured["ensure"] = (kb_id, operation)
        return await fake_get_knowledge_base_info(kb_id)

    async def fake_get_knowledge_base_info(kb_id: str) -> KnowledgeBaseDetail:
        return _database_detail(pending_parse_count=2)

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        captured["list_calls"].append({"kb_id": kb_id, "statuses": statuses, "after_file_id": after_file_id, "limit": limit})
        return ["file_1", "file_2"] if after_file_id is None else []

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None, **_kwargs):
        captured["parsed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id})
        return {"file_id": file_id, "status": "parsed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["payload_match"] = kwargs["payload_match"]
        await knowledge_job_service.run_knowledge_parse(FakeBackgroundJobContext(kwargs["payload"]))
        return SimpleNamespace(id="job_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_bases, "get_knowledge_base_info", fake_get_knowledge_base_info)
    monkeypatch.setattr(
        knowledge_router.documents,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(document_actions, "parse_file", fake_parse_file)
    _patch_submit_job(monkeypatch, fake_enqueue_unique_by_payload)

    result = await knowledge_router.parse_pending_documents(
        "kb_1",
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert result["job_id"] == "job_1"
    assert captured["ensure"] == ("kb_1", "文档解析")
    assert captured["payload_match"] == {"kb_id": "kb_1", "scope": "pending", "action": "parse"}
    assert captured["payload"]["statuses"] == document_actions.PENDING_PARSE_STATUSES
    assert captured["list_calls"] == [
        {
            "kb_id": "kb_1",
            "statuses": document_actions.PENDING_PARSE_STATUSES,
            "after_file_id": None,
            "limit": knowledge_job_service.DOCUMENT_ACTION_BATCH_SIZE,
        },
        {
            "kb_id": "kb_1",
            "statuses": document_actions.PENDING_PARSE_STATUSES,
            "after_file_id": "file_2",
            "limit": knowledge_job_service.DOCUMENT_ACTION_BATCH_SIZE,
        },
    ]
    assert captured["parsed"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user"},
        {"kb_id": "kb_1", "file_id": "file_2", "operator_id": "uid-user"},
    ]


async def test_reconcile_graph_build_mutates_state_only_after_unique_job_is_created(monkeypatch):
    captured = {}

    class FakeGraphService:
        async def reconcile_vectors(self, kb_id: str, *, all_vectors: bool):
            captured["reconcile"] = (kb_id, all_vectors)
            return {"kb_id": kb_id, "mode": "all_vectors", "reset_records": 2}

        async def build_pending_chunks(self, kb_id: str, *, context):
            captured["build"] = kb_id
            return {"kb_id": kb_id, "success": 1}

    async def fake_has_running_graph_build_job(kb_id: str) -> bool:
        return False

    async def fake_get_knowledge_base_info(kb_id: str) -> KnowledgeBaseDetail:
        return _database_detail()

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        assert "reconcile" not in captured
        return SimpleNamespace(id="job_1"), True

    monkeypatch.setattr(graph_build, "_has_active_job", fake_has_running_graph_build_job)
    monkeypatch.setattr(graph_build.knowledge_bases, "get_knowledge_base_info", fake_get_knowledge_base_info)
    monkeypatch.setattr(knowledge_job_service, "KnowledgeGraphService", FakeGraphService)
    monkeypatch.setattr(graph_build, "submit_knowledge_job", fake_enqueue_unique_by_payload)

    result = await knowledge_router.reconcile_graph_build(
        "kb_1",
        data={"mode": "all_vectors"},
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result == {
        "message": "图谱向量索引修复任务已提交",
        "status": "queued",
        "job_id": "job_1",
        "mode": "all_vectors",
    }
    assert "reconcile" not in captured

    context = FakeBackgroundJobContext(captured["payload"])
    job_result = await knowledge_job_service.run_knowledge_graph(context)

    assert captured["reconcile"] == ("kb_1", True)
    assert captured["build"] == "kb_1"
    assert job_result["reconcile"]["reset_records"] == 2
    assert context.result == job_result


async def test_index_pending_documents_uses_pending_statuses_and_params(monkeypatch):
    captured = {"list_calls": [], "indexed": []}

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> dict:
        captured["ensure"] = (kb_id, operation)
        return await fake_get_knowledge_base_info(kb_id)

    async def fake_get_knowledge_base_info(kb_id: str) -> KnowledgeBaseDetail:
        return _database_detail(pending_index_count=2)

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        captured["list_calls"].append({"kb_id": kb_id, "statuses": statuses, "after_file_id": after_file_id, "limit": limit})
        return ["file_1", "file_2"] if after_file_id is None else []

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None, **_kwargs):
        captured["indexed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id, "params": params})
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["payload_match"] = kwargs["payload_match"]
        await knowledge_job_service.run_knowledge_index(FakeBackgroundJobContext(kwargs["payload"]))
        return SimpleNamespace(id="job_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_bases, "get_knowledge_base_info", fake_get_knowledge_base_info)
    monkeypatch.setattr(
        knowledge_router.documents,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(document_actions, "index_file", fake_index_file)
    _patch_submit_job(monkeypatch, fake_enqueue_unique_by_payload)

    params = {"chunk_preset_id": "general"}
    result = await knowledge_router.index_pending_documents(
        "kb_1",
        payload=knowledge_router.PendingIndexDocumentsRequest(params=params),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["ensure"] == ("kb_1", "文档入库")
    assert captured["payload_match"] == {"kb_id": "kb_1", "scope": "pending", "action": "index"}
    assert captured["payload"]["statuses"] == document_actions.PENDING_INDEX_STATUSES
    assert captured["payload"]["params"] == params
    assert captured["list_calls"][0]["statuses"] == document_actions.PENDING_INDEX_STATUSES
    assert captured["indexed"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user", "params": params},
        {"kb_id": "kb_1", "file_id": "file_2", "operator_id": "uid-user", "params": params},
    ]


@pytest.mark.parametrize(
    ("payload", "error_detail"),
    [
        (knowledge_router.AddUploadedDocumentsRequest(items=[], params={}), "items must not be empty"),
        (
            knowledge_router.AddUploadedDocumentsRequest(
                items=["https://example.com/demo.txt"],
                params={"content_hashes": {"https://example.com/demo.txt": "hash_1"}},
            ),
            "File source must be a MinIO URL",
        ),
        (
            knowledge_router.AddUploadedDocumentsRequest(
                items=["minio://knowledgebases/kb_1/upload/demo.txt"],
                params={},
            ),
            "Missing content_hash for file: minio://knowledgebases/kb_1/upload/demo.txt",
        ),
    ],
)
async def test_add_uploaded_documents_rejects_invalid_payload(monkeypatch, payload, error_detail):
    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            payload,
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == error_detail


async def test_parse_documents_accepts_payload_with_params(monkeypatch):
    """parse_documents 支持传入包含 params 的对象并将参数交给解析入口。"""
    captured = {"parsed": []}

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> KnowledgeBaseDetail:
        return _database_detail()

    async def fake_get_knowledge_base_info(kb_id: str) -> KnowledgeBaseDetail:
        return _database_detail()

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None, **_kwargs):
        captured["parsed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id, "params": params})
        return {"file_id": file_id, "status": "parsed"}

    async def fake_enqueue(name: str, job_type: str, payload: dict):
        captured["payload"] = payload
        await knowledge_job_service.run_knowledge_parse(FakeBackgroundJobContext(payload))
        return SimpleNamespace(id="job_parse_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_bases, "get_knowledge_base_info", fake_get_knowledge_base_info)
    monkeypatch.setattr(document_actions, "parse_file", fake_parse_file)
    _patch_submit_job(monkeypatch, fake_enqueue)

    params = {"ocr_engine": "rapid_ocr"}
    result = await knowledge_router.parse_documents(
        "kb_1",
        payload=knowledge_router.ParseDocumentsRequest(file_ids=["file_1"], params=params),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["payload"]["params"] == params
    assert captured["parsed"] == [{"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user", "params": params}]


async def test_parse_pending_documents_uses_params(monkeypatch):
    """parse_pending_documents 支持接收 params 并传给单文件解析入口。"""
    captured = {"parsed": []}

    async def fake_ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> KnowledgeBaseDetail:
        return _database_detail(pending_parse_count=1)

    async def fake_get_knowledge_base_info(kb_id: str) -> KnowledgeBaseDetail:
        return _database_detail(pending_parse_count=1)

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        return ["file_pending_1"] if after_file_id is None else []

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None, **_kwargs):
        captured["parsed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id, "params": params})
        return {"file_id": file_id, "status": "parsed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        await knowledge_job_service.run_knowledge_parse(FakeBackgroundJobContext(kwargs["payload"]))
        return SimpleNamespace(id="job_pending_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_knowledge_base_supports_documents",
        fake_ensure_knowledge_base_supports_documents,
    )
    monkeypatch.setattr(knowledge_bases, "get_knowledge_base_info", fake_get_knowledge_base_info)
    monkeypatch.setattr(
        knowledge_router.documents,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(document_actions, "parse_file", fake_parse_file)
    _patch_submit_job(monkeypatch, fake_enqueue_unique_by_payload)

    params = {"ocr_engine": "rapid_ocr"}
    result = await knowledge_router.parse_pending_documents(
        "kb_1",
        payload=knowledge_router.PendingParseDocumentsRequest(params=params),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["payload"]["params"] == params
    assert captured["parsed"] == [{"kb_id": "kb_1", "file_id": "file_pending_1", "operator_id": "uid-user", "params": params}]


@pytest.mark.parametrize(
    "scope", [{"file_id": "file"}, {"generation": 2}, {"file_id": "", "generation": 2}, {"file_id": "file", "generation": True}]
)
async def test_graph_worker_rejects_incomplete_file_scope(monkeypatch, scope):
    """不完整的持久任务不能退回全库构建。"""
    service = SimpleNamespace(build_pending_chunks=AsyncMock())
    monkeypatch.setattr(knowledge_job_service, "KnowledgeGraphService", lambda: service)
    with pytest.raises(ValueError, match="范围无效"):
        await knowledge_job_service.run_knowledge_graph(FakeBackgroundJobContext({"kb_id": "kb", **scope}))
    service.build_pending_chunks.assert_not_awaited()
