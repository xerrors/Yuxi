import pytest
from pypdf import PdfWriter
from yuxi.knowledge.parser.document_limits import validate_document_limits, validate_local_document

LIMITS = {"max_file_bytes": 100000, "max_ocr_pages": 2, "version": 7}


@pytest.mark.parametrize("key", LIMITS)
@pytest.mark.parametrize("bad", [True, False, 0, -1, "2", 2.5, None])
def test_snapshot_validates_integer_bounds(key, bad):
    if key == "version" and type(bad) is int and bad == 0:
        assert validate_document_limits({**LIMITS, key: bad})[key] == 0
        return
    with pytest.raises(ValueError):
        validate_document_limits({**LIMITS, key: bad})


def test_snapshot_is_copied_and_legacy_is_preserved():
    assert validate_document_limits(None) is None
    result = validate_document_limits(LIMITS)
    assert result == LIMITS and result is not LIMITS


def test_pdf_page_boundary(tmp_path):
    path = tmp_path / "file.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=72, height=72)
    writer.write(path)
    validate_local_document(path, LIMITS)
    with pytest.raises(ValueError, match="OCR limit"):
        validate_local_document(path, {**LIMITS, "max_ocr_pages": 1})


def test_byte_boundary(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(b"123")
    validate_local_document(path, {**LIMITS, "max_file_bytes": 3})
    with pytest.raises(ValueError, match="bytes"):
        validate_local_document(path, {**LIMITS, "max_file_bytes": 2})


@pytest.mark.asyncio
async def test_service_ignores_forged_params_and_propagates_trusted_snapshot(monkeypatch):
    from yuxi.services.ocr_service import parse_document
    from yuxi.knowledge.parser import unified

    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        return "parsed"

    monkeypatch.setattr(unified, "parse_resolved_document", parse)
    forged = {"_document_limits": {**LIMITS, "max_ocr_pages": 999999}}
    await parse_document("file.txt", forged)
    assert "_document_limits" not in calls[-1]["params"]
    await parse_document("file.txt", forged, document_limits=LIMITS)
    assert calls[-1]["params"]["_document_limits"] == LIMITS
    assert forged["_document_limits"]["max_ocr_pages"] == 999999


@pytest.mark.asyncio
async def test_minio_stream_stops_at_limit_and_removes_temp(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace
    from yuxi.knowledge.parser import unified
    from unittest.mock import AsyncMock

    stream = io.BytesIO(b"123456")

    class Response:
        closed = False
        released = False

        def read(self, size):
            assert size <= 1024 * 1024
            return stream.read(size)

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    response = Response()
    from yuxi.storage.minio import client

    monkeypatch.setattr(
        client, "get_minio_client", lambda: SimpleNamespace(adownload_response=AsyncMock(return_value=response))
    )
    from yuxi.knowledge.utils import kb_utils

    monkeypatch.setattr(kb_utils, "parse_minio_url", lambda _: ("bucket", "doc.txt"))
    monkeypatch.setattr(kb_utils, "is_minio_url", lambda _: True)
    monkeypatch.setattr(unified.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ValueError, match="byte limit"):
        await unified.parse_resolved_document(
            "http://minio/bucket/doc.txt", {"_document_limits": {**LIMITS, "max_file_bytes": 3}}
        )
    assert response.closed and response.released
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ingest", "ids", "pending"])
@pytest.mark.parametrize("snapshot", [None, {**LIMITS, "version": 0}])
async def test_worker_forwards_top_level_snapshot_only(monkeypatch, kind, snapshot):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from yuxi.services import knowledge_task_service as service

    parse = AsyncMock(return_value={"file_id": "f1", "status": "parsed"})
    runtime = SimpleNamespace(
        parse_file=parse,
        add_file_record=AsyncMock(return_value={"file_id": "f1"}),
        update_file_params=AsyncMock(),
        list_document_file_ids_by_statuses=AsyncMock(side_effect=[["f1"], []]),
    )
    monkeypatch.setattr(service, "knowledge_base", runtime)
    payload = {
        "kb_id": "kb",
        "operator_id": "u",
        "items": ["doc.txt"],
        "file_ids": ["f1"],
        "scope": "pending" if kind == "pending" else "files",
        "statuses": ["uploaded"],
        "count": 1,
        "params": {"_document_limits": {**LIMITS, "max_ocr_pages": 99999}},
    }
    if snapshot is not None:
        payload["document_limits"] = snapshot
    context = SimpleNamespace(
        payload=payload,
        task_id="t",
        worker_id="w",
        set_progress=AsyncMock(),
        raise_if_cancelled=AsyncMock(),
        set_result=AsyncMock(),
    )
    result = await (service.run_knowledge_ingest(context) if kind == "ingest" else service.run_knowledge_parse(context))
    assert result["failed"] == 0
    assert parse.await_count == 1
    kwargs = parse.await_args.kwargs
    if snapshot is None:
        assert "document_limits" not in kwargs
    else:
        assert kwargs["document_limits"] == snapshot


@pytest.mark.asyncio
async def test_pdf_limits_preserve_structure_diagnostic(tmp_path):
    """坏页槽在pypdf页数遍历前保留既有可修复的结构诊断。"""
    from pypdf.generic import ArrayObject, NameObject, NullObject, NumberObject
    from yuxi.knowledge.parser.base import DocumentParserException
    from yuxi.knowledge.parser.unified import parse_resolved_document

    path = tmp_path / "broken.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pages = writer.root_object["/Pages"]
    pages[NameObject("/Kids")] = ArrayObject([NullObject()])
    pages[NameObject("/Count")] = NumberObject(1)
    writer.write(path)
    with pytest.raises(DocumentParserException) as caught:
        await parse_resolved_document(str(path), {"_document_limits": LIMITS})
    assert caught.value.status_code == "invalid_pdf_page_tree"
    assert "PDF" in str(caught.value)
