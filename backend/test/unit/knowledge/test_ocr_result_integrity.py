"""协议失败必须阻止发布解析产物，合法空白页仍可解析。"""

from types import SimpleNamespace

import pytest

from yuxi.infrastructure.document_parsing import DocumentParserException, ParseOptions
from yuxi.infrastructure.document_parsing.engines.deepseek_ocr import DeepSeekOCRParser
from yuxi.infrastructure.document_parsing.engines.paddleocr_api import PaddleOCRPPOCRv6Parser, PaddleOCRVLParser
from yuxi.infrastructure.document_parsing.engines.pp_structure_v3 import PPStructureV3Parser
from yuxi.infrastructure.document_parsing.parser import parse


def test_deepseek_requires_resolved_credentials(monkeypatch):
    """适配器只消费调用方解析后的凭据，不私自读取部署环境。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "environment-fixture")
    assert DeepSeekOCRParser(api_key="resolved-fixture").api_key == "resolved-fixture"
    with pytest.raises(DocumentParserException, match="未配置"):
        DeepSeekOCRParser()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "content"),
    [("length", "partial"), ("content_filter", "partial"), (None, "partial"), ("stop", None), ("stop", ["text"])],
)
async def test_incomplete_deepseek_response_does_not_publish(tmp_path, monkeypatch, finish_reason, content):
    """不完整正文不能成为成功的 document.md。"""
    source = tmp_path / "scan.png"
    source.write_bytes(b"image")
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]},
    )
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response)
    output = tmp_path / "parsed"
    with pytest.raises(DocumentParserException):
        await parse(source, output, ParseOptions("deepseek_ocr", {"api_key": "fixture-key"}))
    assert not output.exists()


@pytest.mark.parametrize("content", ["complete text", ""])
def test_complete_deepseek_response_preserves_text(monkeypatch, content):
    """正常结束允许明确的空白正文。"""
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200, json=lambda: {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        ),
    )
    assert DeepSeekOCRParser(api_key="fixture-key")._call_api(b"image", "image/png", {}) == content


@pytest.mark.parametrize("engine", ["paddleocr_vl_1_6", "paddleocr_pp_ocrv6", "pp_structure_v3_ocr"])
@pytest.mark.parametrize("result", [None, {}, {"layoutParsingResults": [{"markdown": {"text": 123}}], "ocrResults": [{}]}])
@pytest.mark.asyncio
async def test_missing_protocol_structure_does_not_publish(tmp_path, monkeypatch, engine, result):
    """缺失结果结构与合法空白文档有不同结局。"""
    source = tmp_path / "scan.png"
    source.write_bytes(b"image")
    body = {"errorCode": 0}
    if result is not None:
        body["result"] = result
    monkeypatch.setattr(PPStructureV3Parser, "check_health", lambda self: {"status": "healthy"})
    monkeypatch.setattr(PPStructureV3Parser, "_call_layout_api", lambda *args, **kwargs: body)
    for cls in (PaddleOCRVLParser, PaddleOCRPPOCRv6Parser):
        monkeypatch.setattr(cls, "_submit_job", lambda *args, **kwargs: "job")
        monkeypatch.setattr(cls, "_poll_job_result", lambda *args, **kwargs: "https://example.test/result")
        monkeypatch.setattr(cls, "_download_jsonl", lambda *args, **kwargs: [body])
    output = tmp_path / "parsed"
    with pytest.raises(DocumentParserException):
        await parse(source, output, ParseOptions(engine, {} if engine == "pp_structure_v3_ocr" else {"api_token": "fixture-key"}))
    assert not output.exists()


@pytest.mark.parametrize("cls,field", [(PaddleOCRVLParser, "layoutParsingResults"), (PaddleOCRPPOCRv6Parser, "ocrResults")])
def test_explicit_empty_result_is_valid(tmp_path, cls, field):
    """明确存在的空结果集合表示空白文档。"""
    assert cls(api_token="fixture-key")._extract_markdown([{"result": {field: []}}], tmp_path) == ""
    assert PPStructureV3Parser()._parse_api_result({"result": {"layoutParsingResults": []}}, tmp_path) == ""
