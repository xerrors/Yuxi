from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import yuxi.infrastructure.document_parsing.engines.paddleocr_api as paddleocr_api
from yuxi.infrastructure.document_parsing import DocumentParserException
from yuxi.infrastructure.document_parsing.artifacts import local_image_links
from yuxi.infrastructure.document_parsing.engines.paddleocr_api import (
    PaddleOCRPPOCRv6Parser,
    PaddleOCRPPStructureV3Parser,
    PaddleOCRVLParser,
)


@dataclass
class FakeResponse:
    status_code: int
    json_body: dict[str, Any] | None = None
    text: str = ""
    content: bytes = b""
    headers: dict[str, str] | None = None

    def json(self) -> dict[str, Any]:
        assert self.json_body is not None
        return self.json_body


def _build_file(tmp_path: Path, suffix: str = ".png") -> Path:
    file_path = tmp_path / f"sample{suffix}"
    file_path.write_bytes(b"fake image")
    return file_path


@pytest.mark.parametrize("parser_type", [PaddleOCRVLParser, PaddleOCRPPStructureV3Parser])
@pytest.mark.parametrize(
    "markdown",
    [
        '![chart](images/chart.png "title")',
        '![chart][fig]\n\n[fig]: images/chart.png "title"',
        '![chart](https://images.example.test/chart.png "title")',
    ],
)
def test_layout_images_preserve_titles_and_reference_links(tmp_path, monkeypatch, parser_type, markdown):
    """版面图片的标题、引用式和远端链接都指向实际本地资源。"""
    monkeypatch.setattr(
        paddleocr_api.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            content=b"chart",
            headers={"Content-Type": "image/png"},
            raise_for_status=lambda: None,
        ),
    )
    item = {"markdown": {"text": markdown, "images": {"images/chart.png": "https://images.example.test/chart.png"}}}
    parser = parser_type(api_token="test-token")
    text = parser._extract_markdown([{"result": {"layoutParsingResults": [item]}}], tmp_path)
    links = local_image_links(text)
    assert len(links) == 1
    assert (tmp_path / links[0]).read_bytes() == b"chart"
    assert '"title"' in text
    assert "https://images.example.test/chart.png" not in text


@pytest.mark.parametrize(
    ("parser_type", "job_id", "result_body", "expected_result", "expected_model", "optional_payload", "expected_payload"),
    [
        pytest.param(
            PaddleOCRVLParser,
            "job-vl",
            {
                "result": {
                    "layoutParsingResults": [{"markdown": {"text": "VL markdown", "images": {}}, "outputImages": {"debug": "ignored"}}]
                }
            },
            "VL markdown",
            "PaddleOCR-VL-1.6",
            {"useChartRecognition": True, "useTextlineOrientation": True, "ignored": True},
            {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": True,
            },
            id="vl-upload",
        ),
        pytest.param(
            PaddleOCRPPStructureV3Parser,
            "job-structure",
            {"result": {"layoutParsingResults": [{"markdown": {"text": "Structure markdown", "images": {}}}]}},
            "Structure markdown",
            "PP-StructureV3",
            None,
            {"useDocOrientationClassify": False, "useDocUnwarping": False, "useChartRecognition": False},
            id="pp-structure-upload",
        ),
        pytest.param(
            PaddleOCRPPOCRv6Parser,
            "job-ocr",
            {"result": {"ocrResults": [{"prunedResult": {"rec_texts": ["PaddleOCR API Test", "", "Invoice total: 123.45"]}}]}},
            "PaddleOCR API Test\nInvoice total: 123.45",
            "PP-OCRv6",
            {"useTextlineOrientation": True, "useChartRecognition": True, "ignored": True},
            {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useTextlineOrientation": True,
            },
            id="pp-ocr-upload",
        ),
    ],
)
def test_paddleocr_submits_model_specific_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parser_type,
    job_id: str,
    result_body: dict[str, Any],
    expected_result: str,
    expected_model: str,
    optional_payload: dict[str, Any] | None,
    expected_payload: dict[str, Any] | None,
) -> None:
    submitted: dict[str, Any] = {}

    def fake_post(url, headers=None, data=None, files=None, json=None, timeout=None):  # noqa: A002
        submitted.update(headers=headers, data=data, files=files, json=json)
        return FakeResponse(200, {"data": {"jobId": job_id}})

    def fake_get(url, headers=None, timeout=None):
        if url.endswith(f"/{job_id}"):
            return FakeResponse(
                200,
                {"data": {"state": "done", "resultUrl": {"jsonUrl": f"https://result.test/{job_id}.json"}}},
            )
        return FakeResponse(200, text=json.dumps(result_body))

    monkeypatch.setattr(paddleocr_api.requests, "post", fake_post)
    monkeypatch.setattr(paddleocr_api.requests, "get", fake_get)

    source = str(_build_file(tmp_path))
    params = {"optional_payload": optional_payload} if optional_payload is not None else None
    result = parser_type(api_token="token").process_file(source, tmp_path, params=params)

    assert result == expected_result
    assert submitted["data"]["model"] == expected_model
    assert json.loads(submitted["data"]["optionalPayload"]) == expected_payload


def test_paddleocr_poll_handles_pending_running_and_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_path = _build_file(tmp_path)
    states = iter(["pending", "running", "done"])
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        paddleocr_api.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"data": {"jobId": "job-poll"}}),
    )

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/job-poll"):
            state = next(states)
            data: dict[str, Any] = {"state": state}
            if state == "done":
                data["resultUrl"] = {"jsonUrl": "https://result.test/poll"}
            return FakeResponse(200, {"data": data})
        row = {"result": {"ocrResults": [{"prunedResult": {"rec_texts": ["done text"]}}]}}
        return FakeResponse(200, text=json.dumps(row))

    monkeypatch.setattr(paddleocr_api.requests, "get", fake_get)
    monkeypatch.setattr(paddleocr_api.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    parser = PaddleOCRPPOCRv6Parser(api_token="token")
    result = parser.process_file(str(file_path), tmp_path, params={"poll_interval_seconds": 0.25})

    assert result == "done text"
    assert sleep_calls == [0.25, 0.25]


def test_paddleocr_failed_job_raises_parser_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_path = _build_file(tmp_path)

    monkeypatch.setattr(
        paddleocr_api.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"data": {"jobId": "job-failed"}}),
    )
    monkeypatch.setattr(
        paddleocr_api.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            200,
            {"data": {"state": "failed", "errorMsg": "quota exceeded"}},
        ),
    )

    parser = PaddleOCRVLParser(api_token="token")
    with pytest.raises(DocumentParserException, match="quota exceeded"):
        parser.process_file(str(file_path), tmp_path)


@pytest.mark.parametrize("parser_type", [PaddleOCRVLParser, PaddleOCRPPOCRv6Parser, PaddleOCRPPStructureV3Parser])
def test_paddleocr_missing_token_health_and_parse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parser_type) -> None:
    file_path = _build_file(tmp_path)
    monkeypatch.delenv("PADDLEOCR_API_TOKEN", raising=False)
    parser = parser_type()

    health = parser.check_health()

    assert health["status"] == "unavailable"
    assert "PADDLEOCR_API_TOKEN" in health["message"]
    with pytest.raises(DocumentParserException, match="PADDLEOCR_API_TOKEN"):
        parser.process_file(str(file_path), tmp_path)


def test_paddleocr_configured_token_health_does_not_submit_job() -> None:
    parser = PaddleOCRVLParser(api_token="token")

    health = parser.check_health()

    assert health["status"] == "configured"
    assert "解析时验证" in health["message"]
    assert health["details"]["model"] == "PaddleOCR-VL-1.6"


def test_paddleocr_vl_saves_markdown_images_locally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_path = _build_file(tmp_path)

    monkeypatch.setattr(
        paddleocr_api.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"data": {"jobId": "job-images"}}),
    )

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/job-images"):
            return FakeResponse(
                200,
                {"data": {"state": "done", "resultUrl": {"jsonUrl": "https://result.test/images"}}},
            )
        if url == "https://image.test/table.png":
            return FakeResponse(200, content=b"image-bytes", headers={"Content-Type": "image/png"})
        row = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "before ![](images/table.png) after",
                            "images": {"images/table.png": "https://image.test/table.png"},
                        }
                    }
                ]
            }
        }
        return FakeResponse(200, text=json.dumps(row))

    monkeypatch.setattr(paddleocr_api.requests, "get", fake_get)
    parser = PaddleOCRVLParser(api_token="token")
    result = parser.process_file(str(file_path), tmp_path)
    from yuxi.infrastructure.document_parsing.artifacts import local_image_links

    [relative] = local_image_links(result)
    assert (tmp_path / relative).read_bytes() == b"image-bytes"
    assert result == f"before ![]({relative}) after"
    assert not relative.startswith("/api/")


@pytest.mark.parametrize("source", ["https://example.test/document.pdf", "directory"])
def test_paddle_input_requires_a_local_file(tmp_path, source):
    """远端地址与目录不能绕过统一输入暂存边界。"""
    if source == "directory":
        source = str(tmp_path)
    parser = PaddleOCRVLParser(api_token="test-token")
    with pytest.raises(DocumentParserException, match="文件不存在"):
        parser.process_file(source, tmp_path)
