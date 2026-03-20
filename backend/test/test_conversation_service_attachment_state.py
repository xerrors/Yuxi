from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from yuxi.services import conversation_service as svc


class _DummyUpload:
    def __init__(self, *, filename: str, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    async def seek(self, offset: int) -> int:
        return self._buffer.seek(offset)


def test_build_state_files_only_parsed_and_with_content():
    attachments = [
        {
            "status": "parsed",
            "file_path": "/attachments/a.md",
            "markdown": "line1\nline2",
            "uploaded_at": "2026-02-20T00:00:00+00:00",
        },
        {
            "status": "pending",
            "file_path": "/attachments/b.md",
            "markdown": "ignored",
        },
        {
            "status": "parsed",
            "file_path": "/attachments/c.md",
            "markdown": "",
        },
    ]

    files = svc._build_state_files(attachments)

    assert list(files.keys()) == ["/attachments/a.md"]
    assert files["/attachments/a.md"]["content"] == ["line1", "line2"]
    assert files["/attachments/a.md"]["created_at"] == "2026-02-20T00:00:00+00:00"


@pytest.mark.asyncio
async def test_sync_thread_attachment_state_updates_graph(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    fake_state = SimpleNamespace(
        values={
            "files": {
                "/attachments/old.md": {"content": ["old"]},
                "/work/result.md": {"content": ["keep"]},
            }
        }
    )

    class FakeGraph:
        async def aget_state(self, config):
            captured["read_config"] = config
            return fake_state

        async def aupdate_state(self, *, config, values):
            captured["write_config"] = config
            captured["write_values"] = values

    class FakeAgent:
        async def get_graph(self):
            return FakeGraph()

    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _agent_id: FakeAgent())

    attachments = [
        {
            "status": "parsed",
            "file_path": "/attachments/resume.md",
            "markdown": "hello\nworld",
            "uploaded_at": "2026-02-20T00:00:00+00:00",
        }
    ]
    await svc._sync_thread_attachment_state(
        thread_id="thread-1",
        user_id="u1",
        agent_id="ChatbotAgent",
        attachments=attachments,
    )

    assert captured["read_config"] == {"configurable": {"thread_id": "thread-1", "user_id": "u1"}}
    assert captured["write_config"] == {"configurable": {"thread_id": "thread-1", "user_id": "u1"}}
    assert captured["write_values"]["attachments"] == attachments
    assert "/attachments/resume.md" in captured["write_values"]["files"]
    assert captured["write_values"]["files"]["/attachments/old.md"] is None
    assert "/work/result.md" not in captured["write_values"]["files"]


@pytest.mark.asyncio
async def test_sync_thread_attachment_state_skips_when_agent_missing(monkeypatch: pytest.MonkeyPatch):
    warnings: list[str] = []
    fake_logger = SimpleNamespace(
        warning=lambda message: warnings.append(message),
    )

    monkeypatch.setattr(svc, "logger", fake_logger)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _agent_id: None)

    await svc._sync_thread_attachment_state(
        thread_id="thread-1",
        user_id="u1",
        agent_id="MissingAgent",
        attachments=[],
    )

    assert any("agent not found" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_convert_upload_to_markdown_returns_conversion_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_aparse(source: str, params=None) -> str:
        return "converted markdown"

    monkeypatch.setattr(svc, "_ensure_workdir", lambda: tmp_path)
    monkeypatch.setattr(svc.Parser, "aparse", _fake_aparse)

    payload = b"hello attachment"
    upload = _DummyUpload(filename="note.txt", content_type="text/plain", data=payload)

    result = await svc._convert_upload_to_markdown(upload)

    assert result.file_name == "note.txt"
    assert result.file_type == "text/plain"
    assert result.file_size == len(payload)
    assert result.markdown == "converted markdown"
    assert result.truncated is False


@pytest.mark.asyncio
async def test_convert_upload_to_markdown_truncates_content(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_aparse(source: str, params=None) -> str:
        return "x" * (svc.MAX_ATTACHMENT_MARKDOWN_CHARS + 200)

    monkeypatch.setattr(svc, "_ensure_workdir", lambda: tmp_path)
    monkeypatch.setattr(svc.Parser, "aparse", _fake_aparse)

    upload = _DummyUpload(filename="note.md", content_type="text/markdown", data=b"hello")

    result = await svc._convert_upload_to_markdown(upload)

    assert result.truncated is True
    assert f"超出 {svc.MAX_ATTACHMENT_MARKDOWN_CHARS} 字符限制" in result.markdown


@pytest.mark.asyncio
async def test_convert_upload_to_markdown_rejects_unsupported_extension():
    upload = _DummyUpload(filename="note.pdf", content_type="application/pdf", data=b"pdf")

    with pytest.raises(ValueError, match="不支持的文件类型"):
        await svc._convert_upload_to_markdown(upload)
