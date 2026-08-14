from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault(
    "SAVE_DIR", os.path.join(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir()), "yuxi-test-saves")
)

from yuxi.services import attachment_service as service

pytestmark = pytest.mark.unit


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


@pytest.mark.asyncio
async def test_upload_tmp_attachment_writes_user_scoped_minio_object(monkeypatch):
    fake_minio = FakeMinioClient()
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    response = await service.upload_tmp_attachment_view(
        file=FakeUpload("demo.pdf", b"pdf-bytes", "application/pdf"),
        current_uid="user-1",
    )

    assert response["bucket_name"] == "knowledgebases"
    assert response["object_name"].startswith("tmp/chat_attachments/user-1/")
    assert response["parse_methods"][0] == "disable"
    assert fake_minio.objects[("knowledgebases", response["object_name"])] == b"pdf-bytes"


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

    monkeypatch.setattr(service, "parse_document", fake_parse)

    response = await service.parse_tmp_attachment_view(
        object_name=object_name,
        file_name="demo.pdf",
        parse_method="disable",
        bucket_name="knowledgebases",
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

    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda db: fake_repo)

    async def noop_invalidate(thread_id: str):
        return None

    monkeypatch.setattr(service, "invalidate_mention_cache", noop_invalidate)

    return fake_minio, fake_repo


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_persists_objects_without_local_paths(confirm_attachment_env):
    fake_minio, fake_repo = confirm_attachment_env
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    parsed_object = "tmp/chat_attachments/user-1/tmp-1/parsed/demo.md"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"
    fake_minio.objects[("knowledgebases", parsed_object)] = b"# parsed"

    response = await service.confirm_tmp_thread_attachments_view(
        thread_id="thread-1",
        attachments=[
            {
                "file_name": "demo.pdf",
                "file_type": "application/pdf",
                "bucket_name": "knowledgebases",
                "object_name": original_object,
                "parsed_object_name": parsed_object,
                "truncated": False,
            }
        ],
        db=FakeDB(),
        current_uid="user-1",
    )

    [attachment] = response["attachments"]
    assert attachment["status"] == "parsed"
    stored = fake_repo.attachments[0]
    assert stored["original_object_name"].startswith("threads/thread-1/attachments/")
    assert stored["markdown_object_name"].startswith("threads/thread-1/attachments/")
    assert "storage_path" not in stored
    assert fake_minio.objects[("knowledgebases", stored["original_object_name"])] == b"pdf-bytes"
    assert fake_minio.objects[("knowledgebases", stored["markdown_object_name"])] == b"# parsed"
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
            file_name="demo.pdf",
            parse_method="disable",
            bucket_name="knowledgebases",
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

    monkeypatch.setattr(service, "parse_document", fake_parse)

    response = await service.parse_tmp_attachment_view(
        object_name=object_name,
        file_name="ignored.pdf",
        parse_method="disable",
        bucket_name="knowledgebases",
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
                    "file_name": "demo.pdf",
                    "file_type": "application/pdf",
                    "bucket_name": "knowledgebases",
                    "object_name": original_object,
                    "parsed_object_name": original_object,
                }
            ],
            db=None,
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert fake_repo.attachments == []


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
                {"file_name": "valid.pdf", "bucket_name": "knowledgebases", "object_name": valid_object},
                {"file_name": "missing.pdf", "bucket_name": "knowledgebases", "object_name": missing_object},
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
            {"file_name": "report.pdf", "bucket_name": "knowledgebases", "object_name": first_object},
            {"file_name": "report.pdf", "bucket_name": "knowledgebases", "object_name": second_object},
        ],
        db=FakeDB(),
        current_uid="user-1",
    )

    first, second = response["attachments"]
    assert first["original_path"] != second["original_path"]
    first_record, second_record = fake_repo.attachments
    assert first_record["original_object_name"] != second_record["original_object_name"]
    assert fake_minio.objects[("knowledgebases", first_record["original_object_name"])] == b"first"
    assert fake_minio.objects[("knowledgebases", second_record["original_object_name"])] == b"second"


@pytest.mark.asyncio
async def test_materialize_attachment_record_restores_missing_local_cache(monkeypatch, tmp_path: Path):
    fake_minio = FakeMinioClient()
    original_object = "threads/thread-1/attachments/file-1/original/demo.pdf"
    markdown_object = "threads/thread-1/attachments/file-1/parsed/demo.md"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"
    fake_minio.objects[("knowledgebases", markdown_object)] = b"# parsed"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    def fake_uploads_dir(thread_id: str) -> Path:
        path = tmp_path / thread_id / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(service, "ensure_thread_dirs", lambda thread_id, uid: fake_uploads_dir(thread_id))
    monkeypatch.setattr(service, "sandbox_uploads_dir", fake_uploads_dir)
    attachment = {
        "bucket_name": "knowledgebases",
        "original_object_name": original_object,
        "markdown_object_name": markdown_object,
        "original_path": "/home/gem/user-data/uploads/file-1_demo.pdf",
        "path": "/home/gem/user-data/uploads/attachments/file-1_demo.md",
    }

    await service.materialize_attachment_record("thread-1", "user-1", attachment)

    assert (tmp_path / "thread-1" / "uploads" / "file-1_demo.pdf").read_bytes() == b"pdf-bytes"
    assert (tmp_path / "thread-1" / "uploads" / "attachments" / "file-1_demo.md").read_text() == "# parsed"


def test_delete_materialized_attachment_files_removes_only_target(monkeypatch, tmp_path: Path):
    uploads_dir = tmp_path / "thread-1" / "uploads"
    attachments_dir = uploads_dir / "attachments"
    attachments_dir.mkdir(parents=True)
    original = uploads_dir / "file-1_demo.pdf"
    markdown = attachments_dir / "file-1_demo.md"
    unrelated = uploads_dir / "keep.txt"
    original.write_bytes(b"pdf")
    markdown.write_text("parsed", encoding="utf-8")
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(service, "sandbox_uploads_dir", lambda _thread_id: uploads_dir)

    service._delete_materialized_attachment_files(
        "thread-1",
        {
            "original_path": "/home/gem/user-data/uploads/file-1_demo.pdf",
            "path": "/home/gem/user-data/uploads/attachments/file-1_demo.md",
            "markdown_object_name": "threads/thread-1/attachments/file-1/parsed/demo.md",
        },
    )

    assert not original.exists()
    assert not markdown.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_delete_recorded_objects_is_best_effort():
    class FailingMinio:
        async def adownload_file(self, _bucket_name, object_name):
            return object_name.encode()

        async def adelete_file(self, _bucket_name, _object_name):
            raise service.StorageError("storage unavailable")

    await service._delete_recorded_objects(
        {"original_object_name": "threads/thread-1/attachments/file-1/original/demo.pdf"},
        "knowledgebases",
        FailingMinio(),
    )


@pytest.mark.asyncio
async def test_delete_thread_attachment_rejects_active_thread_run(monkeypatch):
    fake_repo = FakeConversationRepository(db=None)
    fake_repo.attachments = [{"file_id": "file-1", "file_name": "demo.pdf"}]

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_active_run_by_thread_for_user(self, **kwargs):
            assert kwargs == {
                "agent_slug": "agent-1",
                "conversation_thread_id": "thread-1",
                "uid": "user-1",
            }
            return SimpleNamespace(id="run-1")

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(service, "AgentRunRepository", RunRepo)

    with pytest.raises(service.HTTPException) as exc:
        await service.delete_thread_attachment_view(
            thread_id="thread-1",
            file_id="file-1",
            db=FakeDB(),
            current_uid="user-1",
        )

    assert exc.value.status_code == 409
    assert fake_repo.attachments[0]["file_id"] == "file-1"
