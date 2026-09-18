"""KnowledgeBase.update_file_markdown 的行为断言（第一阶段：只放开 parsed）。

覆盖点（按重要性）：
1. 「期望版本 + 允许状态」是同一条条件更新的等值条件——两个并发保存只有一个能命中；
   版本落空时返回 409，且不写对象。
2. 保存顺序为先条件更新、再覆盖产物：反过来会在条件落空时留下「产物已更新、版本未变」
   的组合，且接口返回 409 的同时新内容已经持久化。
3. 第一阶段只接受 parsed：已入库状态不能借编辑接口绕过「重新入库」链路。
4. 各类非法输入在落库与落盘之前就被拒绝（失败不留痕）。
5. 并发冲突（409）与参数错误（400）用不同异常区分。
"""

import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from yuxi.knowledge.base import (
    FileStatus,
    KBFileStateConflictError,
    KnowledgeBase,
    parse_file_revision,
)


class FakeKnowledgeBase(KnowledgeBase):
    """最小可实例化的 executor，用于验证基类里的通用流程。"""

    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        return {}


def make_meta(**overrides):
    meta = {
        "file_id": "file_1",
        "kb_id": "kb_1",
        "filename": "a.pdf",
        "is_folder": False,
        "status": FileStatus.PARSED,
        "markdown_file": "http://minio/knowledgebases/kb_1/parsed/file_1.md",
        "updated_at": "2026-09-18T10:00:00.000000Z",
    }
    meta.update(overrides)
    return meta


def make_record(meta):
    """伪造条件更新返回的 ORM 记录（_file_record_to_meta 只做属性读取）。"""
    return types.SimpleNamespace(
        file_id=meta["file_id"],
        kb_id=meta["kb_id"],
        filename=meta.get("filename"),
        original_filename=meta.get("original_filename"),
        file_type=meta.get("file_type"),
        parent_id=meta.get("parent_id"),
        path=meta.get("path"),
        minio_url=meta.get("minio_url"),
        markdown_file=meta.get("markdown_file"),
        status=meta.get("status"),
        content_type=meta.get("content_type"),
        content_hash=meta.get("content_hash"),
        file_size=meta.get("file_size"),
        chunk_count=meta.get("chunk_count", 0),
        token_count=meta.get("token_count", 0),
        processing_params=meta.get("processing_params"),
        is_folder=meta.get("is_folder", False),
        error_message=None,
        created_by=None,
        created_at=None,
        updated_by=meta.get("updated_by"),
        updated_at=datetime(2026, 9, 18, 10, 0, 1, tzinfo=UTC),
    )


@pytest.fixture
def kb(tmp_path):
    return FakeKnowledgeBase(str(tmp_path))


REVISION = "2026-09-18T10:00:00.000000Z"


def wire(monkeypatch, kb, *, meta, cas_result=...):
    """把 meta、产物写入与假仓库接好，并返回 (cas_calls, events) 供断言。"""
    events: list[str] = []
    cas_calls: list[dict] = []
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))

    async def save(*_args, **_kwargs):
        events.append("save")
        return "http://minio/.../file_1.md"

    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)

    record = make_record({**meta, "status": FileStatus.PARSED})

    async def cas(**kwargs):
        events.append("cas")
        cas_calls.append(kwargs)
        return record if cas_result is ... else cas_result

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda *a, **kw: types.SimpleNamespace(update_fields_if_status=cas),
    )
    return cas_calls, events


@pytest.mark.asyncio
async def test_待入库文件保存后状态保持待入库(monkeypatch, kb):
    """核心路径：parsed 文件覆盖产物后仍是 parsed，用户继续走既有「入库」。"""
    meta = make_meta()
    cas_calls, _ = wire(monkeypatch, kb, meta=meta)

    result = await kb.update_file_markdown(
        "kb_1", "file_1", "新内容", operator_id="u1", revision=REVISION
    )

    assert len(cas_calls) == 1
    cas = cas_calls[0]
    # 原子性契约：允许状态与期望版本必须同时出现在同一条条件更新里
    assert cas["allowed_statuses"] == {FileStatus.PARSED}
    assert cas["expected_updated_at"] == datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
    assert cas["data"] == {"updated_by": "u1"}
    assert result["status"] == FileStatus.PARSED


@pytest.mark.asyncio
async def test_保存顺序是先条件更新再覆盖产物(monkeypatch, kb):
    """顺序反了会出现「条件落空但产物已更新」，且 409 时内容已经持久化。"""
    cas_calls, events = wire(monkeypatch, kb, meta=make_meta())

    await kb.update_file_markdown(
        "kb_1", "file_1", "新内容", operator_id="u1", revision=REVISION
    )

    assert events == ["cas", "save"]


@pytest.mark.asyncio
async def test_版本落空时返回冲突且不写产物(monkeypatch, kb):
    """文件在编辑期间被改过（另一个编辑者、重新解析、状态推进）：条件更新返回 None。

    这正是并发保存的判定点：两个请求带同一个期望版本时，只有一条 UPDATE 能命中。
    """
    cas_calls, events = wire(monkeypatch, kb, meta=make_meta(), cas_result=None)

    with pytest.raises(KBFileStateConflictError, match="已被其他人修改"):
        await kb.update_file_markdown(
            "kb_1", "file_1", "我的内容", operator_id="u1", revision=REVISION
        )

    assert events == ["cas"]
    assert len(cas_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", ["", "   ", "not-a-time"])
async def test_修订标识缺失或非法时拒绝且不落盘(monkeypatch, kb, revision):
    """修订是条件更新的期望版本，没有它就没有并发保护，属调用方错误（400）。"""
    cas_calls, events = wire(monkeypatch, kb, meta=make_meta())

    with pytest.raises(ValueError, match="修订标识"):
        await kb.update_file_markdown("kb_1", "file_1", "我的内容", operator_id="u1", revision=revision)

    assert events == []
    assert cas_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [FileStatus.PARSING, FileStatus.INDEXING])
async def test_正在解析或入库时拒绝(monkeypatch, kb, status):
    cas_calls, events = wire(monkeypatch, kb, meta=make_meta(status=status))

    with pytest.raises(KBFileStateConflictError, match="正在解析或入库中"):
        await kb.update_file_markdown("kb_1", "file_1", "新内容", operator_id="u1", revision=REVISION)

    assert events == []
    assert cas_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [FileStatus.INDEXED, FileStatus.ERROR_INDEXING, "done", FileStatus.ERROR_PARSING],
)
async def test_已入库与无产物状态不支持编辑(monkeypatch, kb, status):
    """第一阶段只放开 parsed：已入库内容的修改要走「重新入库」链路，不能从这里绕。"""
    cas_calls, events = wire(monkeypatch, kb, meta=make_meta(status=status))

    with pytest.raises(KBFileStateConflictError, match="不支持编辑解析产物"):
        await kb.update_file_markdown("kb_1", "file_1", "新内容", operator_id="u1", revision=REVISION)

    assert events == []
    assert cas_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "content", "reason"),
    [
        ({"is_folder": True}, "内容", "文件夹"),
        ({"markdown_file": None}, "内容", "尚未生成解析结果"),
        ({}, "", "不能为空"),
        ({}, "   \n  ", "不能为空"),
        ({}, "x" * (6 * 1024 * 1024), "超出限制"),
    ],
)
async def test_非法输入在落库与落盘前被拒绝(monkeypatch, kb, overrides, content, reason):
    """失败不留痕：这些分支都不应产生条件更新或对象写入。"""
    cas_calls, events = wire(monkeypatch, kb, meta=make_meta(**overrides))

    with pytest.raises(ValueError, match=reason):
        await kb.update_file_markdown("kb_1", "file_1", content, operator_id="u1", revision=REVISION)

    assert events == []
    assert cas_calls == []


def test_修订标识解析兼容_Z_后缀与朴素时间():
    """前端原样回传 utc_isoformat 的输出（带 Z）；朴素时间按 UTC 解释。"""
    assert parse_file_revision("2026-09-18T10:00:00.000000Z") == datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
    assert parse_file_revision("2026-09-18T10:00:00+00:00") == datetime(
        2026, 9, 18, 10, 0, 0, tzinfo=UTC
    )
    assert parse_file_revision("2026-09-18T18:00:00") == datetime(2026, 9, 18, 18, 0, 0, tzinfo=UTC)
