from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault(
    "YUXI_RUNTIME_DIR", os.path.join(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir()), "yuxi-test-saves")
)

from yuxi.agents.backends.paths import workdir_scope_from_runtime_path
from yuxi.services import attachment_service as service
from yuxi.services import workdir_service

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_tmp_attachment_parse_preserves_http_exception(monkeypatch):
    """服务层保留解析链路抛出的 HTTP 状态、详情和响应头。"""
    from fastapi import HTTPException

    error = HTTPException(503, "parser unavailable", headers={"Retry-After": "30"})
    minio_client = FakeMinioClient()

    async def parse(*args, **kwargs):
        """模拟解析服务暂时不可用。"""
        raise error

    monkeypatch.setattr(service, "get_minio_client", lambda: minio_client)
    monkeypatch.setattr("yuxi.services.ocr_service.parse_document", parse)

    with pytest.raises(HTTPException) as caught:
        await service.parse_tmp_attachment_view(
            object_name="tmp/chat_attachments/user-1/file-1/original/report.pdf",
            parse_method="disable",
            current_uid="user-1",
        )

    assert caught.value is error
    assert minio_client.uploads == []


class FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str | None = None):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._offset = 0

    async def seek(self, offset: int) -> None:
        self._offset = offset

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else min(len(self._content), self._offset + size)
        chunk = self._content[self._offset : end]
        self._offset = end
        return chunk


class FakeMinioClient:
    KB_BUCKETS = {"documents": "knowledgebases"}

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploads: list[dict] = []
        self.deleted: list[tuple[str, str]] = []
        self.deleted_prefixes: list[tuple[str, str]] = []
        self.object_metadata: list[dict] = []

    async def aupload_file(self, bucket_name: str, object_name: str, data: bytes, content_type: str | None = None):
        self.objects[(bucket_name, object_name)] = data
        self.uploads.append(
            {
                "bucket_name": bucket_name,
                "object_name": object_name,
                "data": data,
                "content_type": content_type,
            }
        )
        return SimpleNamespace(
            bucket_name=bucket_name,
            object_name=object_name,
            url=f"http://minio:9000/{bucket_name}/{object_name}",
        )

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        try:
            return self.objects[(bucket_name, object_name)]
        except KeyError as exc:
            raise service.StorageError("missing object") from exc

    async def adownload_response(self, bucket_name: str, object_name: str):
        try:
            content = self.objects[(bucket_name, object_name)]
        except KeyError as exc:
            raise service.StorageError("missing object") from exc

        class Response:
            def __init__(self):
                self.stream = io.BytesIO(content)

            def read(self, size):
                return self.stream.read(size)

            def close(self):
                return None

            def release_conn(self):
                return None

        return Response()

    async def adelete_file(self, bucket_name: str, object_name: str) -> bool:
        self.objects.pop((bucket_name, object_name), None)
        self.deleted.append((bucket_name, object_name))
        return True

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        keys = [key for key in self.objects if key[0] == bucket_name and key[1].startswith(prefix)]
        for key in keys:
            self.objects.pop(key)
        self.deleted_prefixes.append((bucket_name, prefix))
        return len(keys)

    async def alist_object_metadata(self, bucket_name: str, prefix: str) -> list[dict]:
        del bucket_name, prefix
        return list(self.object_metadata)


@dataclass
class FakeConversation:
    id: int = 1
    uid: str = "user-1"
    agent_id: str = "agent-1"
    status: str = "active"
    extra_metadata: dict | None = None


class FakeConversationRepository:
    def __init__(self, db):
        self.conversation = FakeConversation()
        self.attachments: list[dict] = []

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self.conversation

    async def add_attachment(self, conversation_id: int, attachment_info: dict):
        self.attachments.append(attachment_info)
        return attachment_info

    async def add_attachments(self, conversation_id: int, attachment_infos: list[dict]):
        self.attachments.extend(attachment_infos)
        return attachment_infos

    async def get_attachments(self, conversation_id: int):
        return list(self.attachments)

    async def lock_attachments(self, conversation_id: int):
        return list(self.attachments)

    async def remove_attachment(self, conversation_id: int, file_id: str):
        before = len(self.attachments)
        self.attachments = [item for item in self.attachments if item.get("file_id") != file_id]
        return len(self.attachments) != before


class FakeDB:
    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


class FailingCommitDB(FakeDB):
    async def commit(self):
        self.commit_count += 1
        raise RuntimeError("commit failed")


class EmptyAgentRunRequestRepository:
    def __init__(self, db):
        del db

    async def get_by_request_id(self, request_id: str):
        del request_id
        return None


class EmptyAgentRunRepository:
    def __init__(self, db):
        del db

    async def get_active_run_by_thread_for_user(self, **kwargs):
        del kwargs
        return None


@pytest.fixture(autouse=True)
def stub_attachment_usage_checks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(service, "AgentRunRequestRepository", EmptyAgentRunRequestRepository)
    monkeypatch.setattr(service, "AgentRunRepository", EmptyAgentRunRepository)


WORKDIR_RELATIVE_PATH = "projects/11111111-1111-4111-8111-111111111111"


def _scope_path(runtime_path: str) -> str:
    return workdir_scope_from_runtime_path(WORKDIR_RELATIVE_PATH, runtime_path)


class FakeWorkdirStorage:
    def __init__(self):
        self.files: dict[str, bytes] = {}


class FakeWorkdir:
    """只向附件用例暴露 Workdir scope，运行时路径仅用于核对持久记录。"""

    relative_path = WORKDIR_RELATIVE_PATH

    def __init__(self, storage: FakeWorkdirStorage):
        self.storage = storage

    def copy_file_from_path(self, scope: str, source_path: str, *, overwrite: bool = True):
        del overwrite
        self.storage.files[scope] = Path(source_path).read_bytes()

    def delete(self, scope: str) -> None:
        if self.storage.files.pop(scope, None) is None:
            raise FileNotFoundError(scope)


class QueuedAgentRunRequestRepository(EmptyAgentRunRequestRepository):
    async def get_by_request_id(self, request_id: str):
        del request_id
        return SimpleNamespace(status="queued")


class ActiveAgentRunRepository(EmptyAgentRunRepository):
    async def get_active_run_by_thread_for_user(self, **kwargs):
        del kwargs
        return SimpleNamespace(id="active-run")


@pytest.mark.asyncio
async def test_upload_tmp_attachment_writes_user_scoped_minio_object(monkeypatch):
    fake_minio = FakeMinioClient()
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    response = await service.upload_tmp_attachment_view(
        file=FakeUpload("demo.pdf", b"pdf-bytes", "application/pdf"),
        current_uid="user-1",
    )

    assert response["object_name"].startswith("tmp/chat_attachments/user-1/")
    assert response["parse_methods"][0] == "disable"
    assert fake_minio.objects[("knowledgebases", response["object_name"])] == b"pdf-bytes"


@pytest.mark.asyncio
async def test_upload_tmp_attachment_cleans_only_expired_user_tmp_groups(monkeypatch):
    fake_minio = FakeMinioClient()
    now = datetime.now(UTC)
    fake_minio.object_metadata = [
        {
            "object_name": "tmp/chat_attachments/user-1/expired/original/old.pdf",
            "last_modified": now - service.TMP_ATTACHMENT_TTL - timedelta(seconds=1),
        },
        {
            "object_name": "tmp/chat_attachments/user-1/recent/original/new.pdf",
            "last_modified": now,
        },
        {
            "object_name": "tmp/chat_attachments/user-2/foreign/original/no.pdf",
            "last_modified": now - service.TMP_ATTACHMENT_TTL - timedelta(seconds=1),
        },
    ]
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    await service.upload_tmp_attachment_view(
        file=FakeUpload("demo.txt", b"text", "text/plain"),
        current_uid="user-1",
    )

    assert fake_minio.deleted_prefixes == [("knowledgebases", "tmp/chat_attachments/user-1/expired/")]


def test_webp_attachment_requires_an_explicit_capable_ocr_engine():
    with pytest.raises(service.HTTPException, match="deepseek_ocr"):
        service._normalize_parse_method("scan.webp", None, "disable")

    assert service._normalize_parse_method("scan.webp", "deepseek_ocr", "disable") == "deepseek_ocr"


@pytest.mark.asyncio
async def test_parse_tmp_attachment_uses_selected_method_and_uploads_markdown(monkeypatch):
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    fake_minio.objects[("knowledgebases", object_name)] = b"pdf-bytes"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    parse_calls = []

    async def fake_parse(source: str, params: dict | None = None) -> str:
        parse_calls.append({"source": source, "params": params})
        return "# parsed"

    from yuxi.services import ocr_service

    monkeypatch.setattr(ocr_service, "parse_document", fake_parse)

    response = await service.parse_tmp_attachment_view(
        object_name=object_name,
        parse_method="disable",
        current_uid="user-1",
    )

    assert parse_calls == [
        {
            "source": f"minio://knowledgebases/{object_name}",
            "params": {"ocr_engine": "disable"},
        }
    ]
    assert response["parsed_object_name"] == "tmp/chat_attachments/user-1/tmp-1/parsed/demo.md"
    assert fake_minio.objects[("knowledgebases", response["parsed_object_name"])] == b"# parsed"


@pytest.fixture
def confirm_attachment_env(monkeypatch: pytest.MonkeyPatch):
    """构造 confirm 流程所需的 MinIO 与仓库假实现，并挂载到 service 模块。"""
    fake_minio = FakeMinioClient()
    fake_repo = FakeConversationRepository(db=None)
    backend = FakeWorkdirStorage()

    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda db: fake_repo)

    async def resolve_binding(**kwargs):
        del kwargs
        return SimpleNamespace(workdir=FakeWorkdir(backend))

    monkeypatch.setattr(workdir_service, "resolve_authorized_conversation_workdir", resolve_binding)
    fake_repo.workdir_backend = backend

    return fake_minio, fake_repo


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_writes_realtime_workdir(confirm_attachment_env):
    fake_minio, fake_repo = confirm_attachment_env
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    parsed_object = "tmp/chat_attachments/user-1/tmp-1/parsed/demo.md"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"
    fake_minio.objects[("knowledgebases", parsed_object)] = b"# parsed"

    response = await service.confirm_tmp_thread_attachments_view(
        thread_id="thread-1",
        attachments=[
            {
                "file_type": "application/pdf",
                "object_name": original_object,
                "parsed_object_name": parsed_object,
            }
        ],
        db=FakeDB(),
        current_uid="user-1",
    )

    [attachment] = response["attachments"]
    assert attachment["status"] == "parsed"
    stored = fake_repo.attachments[0]
    assert set(stored) == {
        "file_id",
        "file_name",
        "file_type",
        "file_size",
        "status",
        "uploaded_at",
        "path",
        "original_path",
    }
    assert stored["original_path"].startswith(
        "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/"
    )
    assert stored["path"].startswith(
        "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/attachments/"
    )
    assert fake_repo.workdir_backend.files[_scope_path(stored["original_path"])] == b"pdf-bytes"
    assert fake_repo.workdir_backend.files[_scope_path(stored["path"])] == b"# parsed"
    assert fake_minio.deleted_prefixes == [("knowledgebases", "tmp/chat_attachments/user-1/tmp-1/")]


@pytest.mark.asyncio
async def test_parse_tmp_attachment_uses_object_name_for_type_validation(monkeypatch):
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/demo.docx"
    fake_minio.objects[("knowledgebases", object_name)] = b"docx-bytes"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    with pytest.raises(service.HTTPException) as exc_info:
        await service.parse_tmp_attachment_view(
            object_name=object_name,
            parse_method="disable",
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert "PDF 和图片" in exc_info.value.detail


@pytest.mark.asyncio
async def test_parse_tmp_attachment_handles_url_metacharacters(monkeypatch):
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/q1?.pdf"
    fake_minio.objects[("knowledgebases", object_name)] = b"pdf-bytes"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    parse_calls = []

    async def fake_parse(source: str, params: dict | None = None) -> str:
        parse_calls.append(source)
        return "# parsed"

    from yuxi.services import ocr_service

    monkeypatch.setattr(ocr_service, "parse_document", fake_parse)

    response = await service.parse_tmp_attachment_view(
        object_name=object_name,
        parse_method="disable",
        current_uid="user-1",
    )

    assert parse_calls == ["minio://knowledgebases/tmp/chat_attachments/user-1/tmp-1/original/q1%3F.pdf"]
    assert response["parsed_object_name"] == "tmp/chat_attachments/user-1/tmp-1/parsed/q1?.md"


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_rejects_non_parsed_object(confirm_attachment_env):
    fake_minio, fake_repo = confirm_attachment_env
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"

    with pytest.raises(service.HTTPException) as exc_info:
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[
                {
                    "file_type": "application/pdf",
                    "object_name": original_object,
                    "parsed_object_name": original_object,
                }
            ],
            db=None,
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert fake_repo.attachments == []
    assert fake_repo.workdir_backend.files == {}


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_rolls_back_workdir_on_commit_failure(confirm_attachment_env):
    fake_minio, fake_repo = confirm_attachment_env
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    parsed_object = "tmp/chat_attachments/user-1/tmp-1/parsed/demo.md"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"
    fake_minio.objects[("knowledgebases", parsed_object)] = b"# parsed"
    db = FailingCommitDB()

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[{"object_name": original_object, "parsed_object_name": parsed_object}],
            db=db,
            current_uid="user-1",
        )

    assert db.commit_count == 1
    assert db.rollback_count == 1
    assert fake_repo.workdir_backend.files == {}
    assert fake_minio.deleted_prefixes == []


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_validates_batch_before_commit(confirm_attachment_env):
    fake_minio, fake_repo = confirm_attachment_env
    valid_object = "tmp/chat_attachments/user-1/tmp-1/original/valid.pdf"
    missing_object = "tmp/chat_attachments/user-1/tmp-2/original/missing.pdf"
    fake_minio.objects[("knowledgebases", valid_object)] = b"pdf-bytes"

    with pytest.raises(service.HTTPException) as exc_info:
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[
                {"object_name": valid_object},
                {"object_name": missing_object},
            ],
            db=None,
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert fake_repo.attachments == []


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_keeps_duplicate_names_separate(confirm_attachment_env):
    fake_minio, fake_repo = confirm_attachment_env
    first_object = "tmp/chat_attachments/user-1/tmp-1/original/report.pdf"
    second_object = "tmp/chat_attachments/user-1/tmp-2/original/report.pdf"
    fake_minio.objects[("knowledgebases", first_object)] = b"first"
    fake_minio.objects[("knowledgebases", second_object)] = b"second"

    response = await service.confirm_tmp_thread_attachments_view(
        thread_id="thread-1",
        attachments=[
            {"object_name": first_object},
            {"object_name": second_object},
        ],
        db=FakeDB(),
        current_uid="user-1",
    )

    first, second = response["attachments"]
    assert first["original_path"] != second["original_path"]
    first_record, second_record = fake_repo.attachments
    assert fake_repo.workdir_backend.files[_scope_path(first_record["original_path"])] == b"first"
    assert fake_repo.workdir_backend.files[_scope_path(second_record["original_path"])] == b"second"


@pytest.mark.asyncio
async def test_store_attachment_normalizes_persisted_file_name(monkeypatch):
    del monkeypatch
    backend = FakeWorkdirStorage()

    record = await service._store_attachment(
        workdir=FakeWorkdir(backend),
        file_id="file-1",
        file_name=" report.txt",
        file_type="text/plain",
        file_content=b"content",
    )

    assert record["file_name"] == "report.txt"
    assert (
        record["original_path"]
        == "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/file-1_report.txt"
    )
    assert backend.files[_scope_path(record["original_path"])] == b"content"


@pytest.mark.asyncio
async def test_delete_thread_attachment_updates_live_workdir_even_during_runtime(monkeypatch):
    fake_repo = FakeConversationRepository(db=None)
    backend = FakeWorkdirStorage()
    original = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/file-1_demo.pdf"
    parsed = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/attachments/file-1_demo.md"
    backend.files = {_scope_path(original): b"pdf", _scope_path(parsed): b"markdown"}
    fake_repo.attachments = [{"file_id": "file-1", "file_name": "demo.pdf", "original_path": original, "path": parsed}]

    async def resolve_binding(**kwargs):
        del kwargs
        return SimpleNamespace(workdir=FakeWorkdir(backend))

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(workdir_service, "resolve_authorized_conversation_workdir", resolve_binding)
    result = await service.delete_thread_attachment_view(
        thread_id="thread-1", file_id="file-1", db=FakeDB(), current_uid="user-1"
    )

    assert result == {"message": "附件已删除"}
    assert fake_repo.attachments == []
    assert backend.files == {}


@pytest.mark.asyncio
async def test_delete_thread_attachment_rejects_queued_request_use(monkeypatch):
    fake_repo = FakeConversationRepository(db=None)
    backend = FakeWorkdirStorage()
    original = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/file-1_demo.pdf"
    backend.files = {_scope_path(original): b"pdf"}
    attachment = {
        "file_id": "file-1",
        "file_name": "demo.pdf",
        "original_path": original,
        "path": original,
        "request_id": "request-1",
    }
    fake_repo.attachments = [attachment]

    async def resolve_binding(**kwargs):
        del kwargs
        return SimpleNamespace(workdir=FakeWorkdir(backend))

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(workdir_service, "resolve_authorized_conversation_workdir", resolve_binding)
    monkeypatch.setattr(service, "AgentRunRequestRepository", QueuedAgentRunRequestRepository)

    with pytest.raises(service.HTTPException) as exc_info:
        await service.delete_thread_attachment_view(
            thread_id="thread-1", file_id="file-1", db=FakeDB(), current_uid="user-1"
        )

    assert exc_info.value.status_code == 409
    assert fake_repo.attachments == [attachment]
    assert backend.files == {_scope_path(original): b"pdf"}


@pytest.mark.asyncio
async def test_delete_thread_attachment_rejects_active_thread_run(monkeypatch):
    fake_repo = FakeConversationRepository(db=None)
    backend = FakeWorkdirStorage()
    original = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/file-1_demo.pdf"
    backend.files = {_scope_path(original): b"pdf"}
    attachment = {"file_id": "file-1", "file_name": "demo.pdf", "original_path": original, "path": original}
    fake_repo.attachments = [attachment]

    async def resolve_binding(**kwargs):
        del kwargs
        return SimpleNamespace(workdir=FakeWorkdir(backend))

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(workdir_service, "resolve_authorized_conversation_workdir", resolve_binding)
    monkeypatch.setattr(service, "AgentRunRepository", ActiveAgentRunRepository)

    with pytest.raises(service.HTTPException) as exc_info:
        await service.delete_thread_attachment_view(
            thread_id="thread-1", file_id="file-1", db=FakeDB(), current_uid="user-1"
        )

    assert exc_info.value.status_code == 409
    assert fake_repo.attachments == [attachment]
    assert backend.files == {_scope_path(original): b"pdf"}


@pytest.mark.asyncio
async def test_delete_thread_attachment_does_not_delete_bytes_before_metadata_commit(monkeypatch):
    fake_repo = FakeConversationRepository(db=None)
    backend = FakeWorkdirStorage()
    original = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/file-1_demo.pdf"
    backend.files = {_scope_path(original): b"pdf"}
    fake_repo.attachments = [
        {"file_id": "file-1", "file_name": "demo.pdf", "original_path": original, "path": original}
    ]

    async def fail_remove(_conversation_id: int, _file_id: str):
        raise RuntimeError("database unavailable")

    fake_repo.remove_attachment = fail_remove

    async def resolve_binding(**kwargs):
        del kwargs
        return SimpleNamespace(workdir=FakeWorkdir(backend))

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(workdir_service, "resolve_authorized_conversation_workdir", resolve_binding)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.delete_thread_attachment_view(
            thread_id="thread-1",
            file_id="file-1",
            db=FakeDB(),
            current_uid="user-1",
        )

    assert backend.files == {_scope_path(original): b"pdf"}
