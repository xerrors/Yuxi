"""入库请求遇到未解析或没有正文的文件时先解析，然后只入库一次。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from test.unit.support.knowledge import FakeKnowledgeBase, make_knowledge_file_record
from yuxi.modules.knowledge.manager import KnowledgeBaseManager
from yuxi.modules.knowledge.services import documents

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _manager(monkeypatch, meta, executor):
    manager = KnowledgeBaseManager("/tmp/yuxi-index-unparsed")
    state = {"meta": dict(meta), "parsed": 0}

    async def load_file_meta(_kb_id, _file_id):
        return dict(state["meta"])

    async def parse_file(*_args, **kwargs):
        state["parsed"] += 1
        state["recover"] = kwargs.get("recover_missing_markdown")
        state["meta"] = {"status": "parsed", "is_folder": False, "markdown_file": "parsed.md"}
        return {"status": "parsed"}

    async def get_kb_config(_kb_id):
        return SimpleNamespace(kb_type="milvus", embedding_model_spec="embed", additional_params={})

    async def get_executor(_kb_type):
        return executor

    async def refresh(_kb_id, operation):
        return await operation

    monkeypatch.setattr(documents, "load_file_meta", load_file_meta)
    monkeypatch.setattr(manager, "parse_file", parse_file)
    monkeypatch.setattr(manager, "get_kb_config", get_kb_config)
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", get_executor)
    monkeypatch.setattr(manager, "_run_with_stats_refresh", refresh)
    return manager, state


async def test_index_parses_uploaded_file_before_indexing(monkeypatch):
    class Executor:
        def __init__(self):
            self.calls = 0

        async def index_file(self, *_args, **_kwargs):
            self.calls += 1
            return {"status": "indexed"}

    executor = Executor()
    manager, state = _manager(monkeypatch, {"status": "uploaded", "is_folder": False}, executor)

    result = await manager.index_file("kb", "file-1", operator_id="user")

    assert result == {"status": "indexed"}
    assert state["parsed"] == 1
    assert state["recover"] is True
    assert executor.calls == 1


async def test_index_of_parsed_file_does_not_parse_again(monkeypatch):
    class Executor:
        async def index_file(self, *_args, **_kwargs):
            return {"status": "indexed"}

    manager, state = _manager(
        monkeypatch,
        {"status": "parsed", "is_folder": False, "markdown_file": "parsed.md"},
        Executor(),
    )

    await manager.index_file("kb", "file-1")

    assert state["parsed"] == 0


async def test_index_parses_once_when_markdown_is_missing(monkeypatch):
    """可入库状态却没有正文时先解析，执行器只被调用一次。"""

    class Executor:
        def __init__(self):
            self.calls = 0

        async def index_file(self, *_args, **_kwargs):
            self.calls += 1
            return {"status": "indexed"}

    executor = Executor()
    manager, state = _manager(
        monkeypatch,
        {"status": "parsed", "is_folder": False, "markdown_file": None},
        executor,
    )

    result = await manager.index_file("kb", "file-1")

    assert result == {"status": "indexed"}
    assert state["parsed"] == 1
    assert state["recover"] is True
    assert executor.calls == 1


class _ClaimRepository:
    """按生产条件更新的参数决定能否领取，不自行放宽状态。"""

    def __init__(self, record):
        self.record = record

    async def get_by_file_id(self, file_id: str, *, include_deleted: bool = False):
        record = self.record
        if record.file_id != file_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    async def update_fields_if_status(
        self,
        *,
        kb_id: str,
        file_id: str,
        allowed_statuses: set[str],
        data: dict,
        missing_markdown_statuses: set[str] | None = None,
        processing_params: dict | None = None,
        kb_additional_params: dict | None = None,
        **_kwargs,
    ):
        record = self.record
        if record.kb_id != kb_id or record.file_id != file_id or record.deleted_at is not None:
            return None
        matched = record.status in allowed_statuses
        if missing_markdown_statuses and record.status in missing_markdown_statuses and not record.is_folder and not record.markdown_file:
            matched = True
        if not matched:
            return None
        if kb_additional_params is not None:
            from yuxi.modules.knowledge.utils.kb_utils import resolve_processing_params

            record.processing_params = resolve_processing_params(kb_additional_params, record.processing_params, processing_params)
        for key, value in data.items():
            setattr(record, key, value)
        return record


def _parse_executor(monkeypatch, record):
    @asynccontextmanager
    async def projection_lock(*_args, **_kwargs):
        yield

    async def parse_to_hosted_markdown(**_kwargs):
        return "正文"

    monkeypatch.setattr("yuxi.modules.knowledge.repositories.projections.knowledge_projection_lock", projection_lock)
    monkeypatch.setattr("yuxi.modules.knowledge.repositories.files.KnowledgeFileRepository", lambda: _ClaimRepository(record))
    monkeypatch.setattr(
        "yuxi.modules.knowledge.base.get_minio_client",
        lambda: SimpleNamespace(KB_BUCKETS={"images": "images"}),
    )
    monkeypatch.setattr("yuxi.modules.documents.service.parse_to_hosted_markdown", parse_to_hosted_markdown)
    executor = FakeKnowledgeBase("/tmp/yuxi-recovery-parse")

    async def save_markdown(kb_id, file_id, attempt_id, content):
        assert content == "正文"
        return f"minio://knowledgebases/{kb_id}/{file_id}/{attempt_id}.md"

    executor.save_markdown_artifact = save_markdown
    return executor


async def test_recovery_parse_claims_index_status_without_markdown(monkeypatch):
    """可入库但没有正文时，补解析能领取并写出正文。普通解析领取做不到。"""

    record = make_knowledge_file_record(status="parsed", markdown_file=None)
    executor = _parse_executor(monkeypatch, record)

    with pytest.raises(ValueError, match="Cannot parse file with status 'parsed'"):
        await executor.parse_file("db", "file-1", additional_params={})
    assert record.status == "parsed"
    assert record.markdown_file is None

    result = await executor.parse_file("db", "file-1", additional_params={}, recover_missing_markdown=True)

    assert result["status"] == "parsed"
    assert result["markdown_file"] == record.markdown_file
    assert record.markdown_file.startswith("minio://knowledgebases/db/file-1/")


async def test_recovery_parse_leaves_markdown_and_folders_unclaimed(monkeypatch):
    """已有正文或目录不能被补解析领取。"""

    parsed = make_knowledge_file_record(status="parsed", markdown_file="parsed.md")
    with pytest.raises(ValueError, match="Cannot parse file with status 'parsed'"):
        await _parse_executor(monkeypatch, parsed).parse_file(
            "db",
            "file-1",
            additional_params={},
            recover_missing_markdown=True,
        )
    assert parsed.status == "parsed"
    assert parsed.markdown_file == "parsed.md"

    folder = make_knowledge_file_record(status="done", is_folder=True, markdown_file=None, file_id="folder-1")
    with pytest.raises(ValueError, match="Cannot parse file with status 'done'"):
        await _parse_executor(monkeypatch, folder).parse_file(
            "db",
            "folder-1",
            additional_params={},
            recover_missing_markdown=True,
        )
    assert folder.status == "done"
    assert folder.markdown_file is None
