"""单文件解析收尾及入库补解析。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test.unit.support.knowledge import FakeKnowledgeBase, make_knowledge_file_record
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.runtime import knowledge_base
from yuxi.modules.knowledge.services import document_actions, documents, statistics

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.mark.parametrize(
    ("status", "markdown_file", "needs_parse", "parse_fails"),
    [
        ("uploaded", None, True, False),
        ("parsed", None, True, False),
        ("parsed", "existing.md", False, False),
        ("parsed", None, True, True),
    ],
)
async def test_index_parses_only_when_needed_and_preserves_processing_owner(monkeypatch, status, markdown_file, needs_parse, parse_fails):
    """补解析完成及刷新后才入库，同一文件、作业执行权和参数贯穿两阶段。"""
    steps = []
    params = {"chunk_preset_id": "general"}
    metadata = {"status": status, "is_folder": False, "markdown_file": markdown_file}
    execution_kwargs = {
        "params": params,
        "additional_params": {"type_option": "value"},
        "processing_job_id": "job",
        "processing_owner": "attempt",
    }

    async def load_file_meta(kb_id, file_id):
        assert (kb_id, file_id) == ("kb", "file-1")
        return dict(metadata)

    async def get_kb_config(kb_id):
        assert kb_id == "kb"
        return SimpleNamespace(kb_type="milvus", embedding_model_spec="embed", additional_params={"type_option": "value"})

    async def parse_file(kb_id, file_id, operator_id, **kwargs):
        assert (kb_id, file_id, operator_id) == ("kb", "file-1", "user")
        assert kwargs == {**execution_kwargs, "recover_missing_markdown": True}
        steps.append("parse")
        if parse_fails:
            metadata["status"] = "error_parsing"
            raise ValueError("cannot read document")
        metadata.update(status="parsed", markdown_file="recovered.md")
        return {"status": "parsed"}

    async def index_file(kb_id, file_id, operator_id, **kwargs):
        assert (kb_id, file_id, operator_id) == ("kb", "file-1", "user")
        assert kwargs == {**execution_kwargs, "embedding_model_spec": "embed"}
        assert metadata["markdown_file"] == ("recovered.md" if needs_parse else "existing.md")
        metadata["status"] = "indexed"
        steps.append("index")
        return {"status": "indexed"}

    async def get_executor(kb_type):
        assert kb_type == "milvus"
        return SimpleNamespace(parse_file=parse_file, index_file=index_file)

    async def refresh_stats(kb_id):
        assert kb_id == "kb"
        steps.append(f"refresh:{metadata['status']}")

    monkeypatch.setattr(documents, "load_file_meta", load_file_meta)
    monkeypatch.setattr(knowledge_base, "get_kb_config", get_kb_config)
    monkeypatch.setattr(knowledge_base, "get_executor_for_type", get_executor)
    monkeypatch.setattr(statistics, "refresh_stats", refresh_stats)

    indexing = document_actions.index_file(
        "kb", "file-1", operator_id="user", params=params, processing_job_id="job", processing_owner="attempt"
    )
    if parse_fails:
        with pytest.raises(ValueError, match="cannot read document"):
            await indexing
        assert steps == ["parse", "refresh:error_parsing"]
        return

    result = await indexing
    assert result == {"status": "indexed"}
    assert steps == (["parse", "refresh:parsed"] if needs_parse else []) + ["index", "refresh:indexed"]


@pytest.mark.parametrize("refresh_stats_fails", [False, True])
async def test_parse_file_refreshes_stats_and_keeps_original_error_after_executor_failure(monkeypatch, refresh_stats_fails):
    refreshed = []

    class FakeExecutor:
        async def parse_file(self, *args, **kwargs):
            raise ValueError("parse failed")

    async def get_kb_config(_kb_id: str):
        return KnowledgeBaseConfig(kb_id="kb_1", kb_type="fake")

    if refresh_stats_fails:

        async def refresh_database_stats(_kb_id: str):
            raise RuntimeError("stats failed")
    else:

        async def refresh_database_stats(kb_id: str):
            refreshed.append(kb_id)
            return {}

    monkeypatch.setattr(knowledge_base, "get_kb_config", get_kb_config)
    monkeypatch.setattr(knowledge_base, "get_executor_for_type", AsyncMock(return_value=FakeExecutor()))
    monkeypatch.setattr(statistics, "refresh_stats", refresh_database_stats)

    with pytest.raises(ValueError, match="parse failed"):
        await document_actions.parse_file("kb_1", "file_1")

    if not refresh_stats_fails:
        assert refreshed == ["kb_1"]


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
