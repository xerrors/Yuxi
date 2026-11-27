from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yuxi.modules.agents.services.attachments as service
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.workspace.runtime_paths import workdir_scope_from_runtime_path
from yuxi.shared.errors import DomainError, InvalidInputError, ResourceNotFoundError, StateConflictError, UpstreamError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_attachment_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """附件测试的模型凭据与运行目录只作用于当前用例。"""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("YUXI_RUNTIME_DIR", str(tmp_path / "runtime"))


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

    async def adownload_file(self, bucket_name: str, object_name: str, *, max_bytes=None) -> bytes:
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

    async def astat_file(self, bucket_name, object_name):
        data = self.objects.get((bucket_name, object_name))
        return len(data) if data is not None else None


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
        keys = [key for key in self.storage.files if key == scope or key.startswith(scope + "/")]
        if not keys:
            raise FileNotFoundError(scope)
        for key in keys:
            del self.storage.files[key]

    def copy_directory_from(self, source, target_path, *, max_file_bytes):
        for item in source.list_directory("/"):
            if not item["is_dir"]:
                self.storage.files[f"{target_path}/{item['name']}"] = source.read_file(f"/{item['name']}", max_file_bytes)


@pytest.mark.asyncio
async def test_store_attachment_normalizes_persisted_file_name(monkeypatch):
    del monkeypatch
    backend = FakeWorkdirStorage()

    record = await service._store_attachment(
        workdir=FakeWorkdir(backend),
        file_id="file-1",
        file_name=" report.txt",
        file_content=b"content",
    )

    assert backend.files["/uploads/file-1_report.txt"] == b"content"
    assert record["original_path"] == "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/uploads/file-1_report.txt"
    assert backend.files[_scope_path(record["original_path"])] == b"content"


@pytest.fixture(autouse=True)
def isolate_attachment_workspace(tmp_path, monkeypatch):
    """隔离真实文件边界，避免不同测试共享解析目录。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    service.ensure_user_workspace("user-1")


def _write_parse_result(output_dir, markdown):
    """构造可回读的解析产物。"""
    from yuxi.infrastructure.document_parsing import ParseResult

    output_dir.mkdir()
    path = output_dir / "document.md"
    path.write_text(markdown)
    return ParseResult(output_dir, path, ())


@pytest.mark.asyncio
async def test_cancelled_attachment_write_reclaims_uncommitted_original(tmp_path, monkeypatch):
    """用真实文件边界验证取消不会留下尚未提交的原附件。"""
    import asyncio
    import threading

    from yuxi.modules.workspace.filesystem import Workspace
    from yuxi.modules.workspace.paths import ensure_user_workspace
    from yuxi.modules.workspace.workdir import Workdir

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    ensure_user_workspace("cancel-user")
    workdir = Workdir("projects/cancel", Workspace("cancel-user"))
    started, release = threading.Event(), threading.Event()
    real_copy = Workdir.copy_file_from_path

    def copy(self, *args, **kwargs):
        started.set()
        assert release.wait(5)
        return real_copy(self, *args, **kwargs)

    monkeypatch.setattr(Workdir, "copy_file_from_path", copy)
    task = asyncio.create_task(
        service._store_attachment(workdir=workdir, file_id="file-id", file_name="input.txt", file_content=b"original")
    )
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(FileNotFoundError):
        workdir.read_file("/uploads/file-id_input.txt", 1024)


class FakeAttachmentRepository:
    """只隔离持久化；文件内容经过真实存储用例回读。"""

    def __init__(self, db):
        self.db = db

    async def create(self, **values):
        record = AgentAttachment(**values)
        self.db.records[record.id] = record
        return record

    async def get_for_scope(self, file_id, uid, app_id, *, lock=False):
        record = self.db.records.get(file_id)
        return record if record and (record.uid, record.app_id) == (uid, app_id) else None

    async def expired_drafts(self, uid, app_id):
        return []


@pytest.fixture
def draft_env(monkeypatch):
    """附件记录与内容分开回读；锁的并发语义由真实 PG 验证。"""
    client = FakeMinioClient()
    db = SimpleNamespace(records={}, get=AsyncMock(return_value=None), commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(service, "get_minio_client", lambda: client)
    monkeypatch.setattr(service, "AttachmentRepository", FakeAttachmentRepository)
    return client, db


@pytest.mark.asyncio
async def test_draft_upload_has_one_record_and_content_without_manifest(draft_env):
    """上传只保存一份二进制，文件信息由附件行独占。"""
    client, db = draft_env
    result = await service.upload_draft_file(
        file_content=b"hello",
        filename="report.txt",
        content_type="text/plain",
        scope=service.ActorScope("user-1", None),
        db=db,
    )
    assert result["status"] == "draft" and result["bytes"] == 5
    assert result["expires_at"] - result["created_at"] == 24 * 3600
    assert not {"object_name", "parsed_source", "path", "session_id"} & result.keys()
    [record] = db.records.values()
    assert (record.uid, record.app_id, record.input_id) == ("user-1", None, None)
    assert list(client.objects.values()) == [b"hello"]
    assert client.objects["knowledgebases", record.object_name] == b"hello"
    assert not any(name.endswith("manifest.json") for _, name in client.objects)


@pytest.mark.asyncio
async def test_draft_oversize_has_no_record_or_content(draft_env):
    """超限在真实上传边界失败，没有持久副作用。"""
    client, db = draft_env
    with pytest.raises(DomainError, match="附件过大"):
        await service.upload_draft_file(
            file_content=b"x" * (service.MAX_ATTACHMENT_SIZE_BYTES + 1),
            filename="large.bin",
            content_type=None,
            scope=service.ActorScope("user-1", None),
            db=db,
        )
    assert not db.records and not client.objects


@pytest.mark.asyncio
async def test_draft_database_failure_removes_unowned_content(draft_env):
    """行提交失败时撤回上传内容，不能留下没有 Owner 的文件。"""
    client, db = draft_env
    db.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError, match="commit failed"):
        await service.upload_draft_file(
            file_content=b"hello",
            filename="report.txt",
            content_type="text/plain",
            scope=service.ActorScope("user-1", None),
            db=db,
        )
    assert client.objects == {}


@pytest.mark.asyncio
async def test_draft_isolated_by_user_and_app_and_rejects_expiry(draft_env):
    """拒绝其他作用域和过期草稿，不依赖对象路径猜测归属。"""
    _, db = draft_env
    scope = service.ActorScope("user-1", "app-a")
    result = await service.upload_draft_file(file_content=b"pdf", filename="report.pdf", content_type="application/pdf", scope=scope, db=db)
    for other in (
        service.ActorScope("user-2", "app-a"),
        service.ActorScope("user-1", "app-b"),
        service.ActorScope("user-1", None),
    ):
        with pytest.raises(DomainError) as error:
            await service.get_draft_file(file_id=result["id"], scope=other, db=db)
        assert isinstance(error.value, ResourceNotFoundError)
    db.records[result["id"]].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(DomainError) as error:
        await service.get_draft_file(file_id=result["id"], scope=scope, db=db)
    assert isinstance(error.value, InvalidInputError)


@pytest.mark.asyncio
async def test_bound_draft_cannot_be_deleted_or_reparsed(draft_env):
    """绑定后的来源只能由统一准备链路消费。"""
    client, db = draft_env
    scope = service.ActorScope("user-1", None)
    result = await service.upload_draft_file(file_content=b"pdf", filename="report.pdf", content_type="application/pdf", scope=scope, db=db)
    db.records[result["id"]].status = "preparing"
    before = dict(client.objects)
    for operation in (
        service.delete_draft_file(file_id=result["id"], scope=scope, db=db),
        service.parse_draft_file(file_id=result["id"], parse_method="disable", scope=scope, db=db),
    ):
        with pytest.raises(DomainError) as error:
            await operation
        assert isinstance(error.value, StateConflictError)
    assert client.objects == before


@pytest.mark.asyncio
async def test_private_parse_records_complete_directory_and_preserves_failure(draft_env, monkeypatch):
    """解析只更新附件行；失败保留原件与上一份成功解析内容。"""
    _, db = draft_env
    scope = service.ActorScope("user-1", None)
    uploaded = await service.upload_draft_file(file_content=b"pdf", filename="q1?.pdf", content_type="application/pdf", scope=scope, db=db)
    sources = []

    async def parse(source, output, params):
        sources.append(source)
        return _write_parse_result(output, "完整文档")

    monkeypatch.setattr("yuxi.modules.documents.service.parse", parse)
    result = await service.parse_draft_file(file_id=uploaded["id"], parse_method="disable", scope=scope, db=db)
    assert result["status"] == "parsed" and sources[0].endswith("/original/q1%3F.pdf")
    record = db.records[uploaded["id"]]
    source_path = record.parsed_source
    assert service.Workspace(scope.uid).read_authorized_file("/" + source_path, 1024).decode() == "完整文档"
    unavailable = UpstreamError("parser unavailable")

    async def fail(*args, **kwargs):
        raise unavailable

    monkeypatch.setattr("yuxi.modules.documents.service.parse", fail)
    with pytest.raises(DomainError) as error:
        await service.parse_draft_file(file_id=uploaded["id"], parse_method="disable", scope=scope, db=db)
    assert error.value is unavailable and record.parsed_source == source_path


def test_webp_requires_explicit_capable_engine():
    """WebP 不静默使用不支持该格式的默认解析器。"""
    with pytest.raises(DomainError):
        service._normalize_parse_method("image.webp", "rapid_ocr", "rapid_ocr")


@pytest.mark.asyncio
async def test_binding_rejects_input_owned_by_other_scope():
    """替代调用不能把同用户其他 APP 的 Input 用作绑定目标。"""
    from yuxi.modules.agents.scope import ActorScope

    with pytest.raises(ValueError, match="身份不一致"):
        await service.stage_input_attachments(
            db=object(),
            scope=ActorScope(uid="user", app_id="app-a"),
            input_item=SimpleNamespace(uid="user", app_id="app-b"),
            file_ids=["file"],
        )
