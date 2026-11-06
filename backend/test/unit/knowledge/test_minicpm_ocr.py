"""MiniCPM-OCR 协议参数、完整性与健康检查。"""

from types import SimpleNamespace

import pytest

from yuxi.infrastructure.document_parsing import DocumentParserException, ParseOptions
from yuxi.infrastructure.document_parsing.engines.minicpm_ocr import MiniCPMOCRParser
from yuxi.infrastructure.document_parsing.parser import parse


def _response(status_code: int = 200, body: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, json=lambda: body or {}, text="fixture-body")


def test_minicpm_request_carries_official_decoding_parameters(monkeypatch):
    """请求必须携带切片参数并关闭 thinking，图片位于文本之前。"""
    captured: dict = {}

    def post(url, **kwargs):
        captured.update(kwargs)
        captured["url"] = url
        return _response(body={"choices": [{"finish_reason": "stop", "message": {"content": "识别结果"}}]})

    monkeypatch.setattr("requests.post", post)

    parser = MiniCPMOCRParser(api_url="http://ocr.test/v1/chat/completions", model="minicpm-ocr")
    assert parser._call_api(b"image", "image/png", {}) == "识别结果"

    payload = captured["json"]
    assert captured["url"] == "http://ocr.test/v1/chat/completions"
    assert payload["model"] == "minicpm-ocr"
    assert payload["mm_processor_kwargs"] == {"downsample_mode": "4x", "max_slice_nums": 9}
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "Text Recognition:"}
    # vLLM 未启用 --api-key 时不能发送空 Bearer。
    assert "Authorization" not in captured["headers"]


def test_minicpm_passes_configured_credentials_and_model(monkeypatch):
    """配置中心解析出的凭据和模型名原样进入请求。"""
    captured: dict = {}
    body = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}

    def post(url, **kwargs):
        captured.update(kwargs)
        return _response(body=body)

    monkeypatch.setattr("requests.post", post)

    parser = MiniCPMOCRParser(api_key="fixture-key", api_url="http://ocr.test/v1/chat/completions", model="custom-ocr")
    parser._call_api(b"image", "image/jpeg", {"max_slice_nums": 4, "downsample_mode": "16x", "prompt": "Table Recognition:"})

    assert captured["headers"]["Authorization"] == "Bearer fixture-key"
    assert captured["json"]["model"] == "custom-ocr"
    assert captured["json"]["mm_processor_kwargs"] == {"downsample_mode": "16x", "max_slice_nums": 4}
    assert captured["json"]["messages"][0]["content"][1]["text"] == "Table Recognition:"


@pytest.mark.parametrize(
    ("finish_reason", "content"),
    [("length", "partial"), ("content_filter", "partial"), (None, "partial"), ("stop", None), ("stop", ["text"])],
)
@pytest.mark.asyncio
async def test_incomplete_minicpm_response_does_not_publish(tmp_path, monkeypatch, finish_reason, content):
    """不完整正文不能成为成功的 document.md。"""
    source = tmp_path / "scan.png"
    source.write_bytes(b"image")
    response = _response(body={"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]})
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response)

    output = tmp_path / "parsed"
    with pytest.raises(DocumentParserException):
        await parse(source, output, ParseOptions("minicpm_ocr", {"api_url": "http://ocr.test/v1/chat/completions"}))
    assert not output.exists()


@pytest.mark.parametrize("content", ["complete text", ""])
def test_complete_minicpm_response_preserves_text(monkeypatch, content):
    """正常结束允许明确的空白正文。"""
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: _response(body={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}),
    )
    assert MiniCPMOCRParser(api_url="http://ocr.test/v1/chat/completions")._call_api(b"image", "image/png", {}) == content


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(200, "healthy"), (401, "unhealthy"), (503, "unhealthy")],
)
def test_minicpm_health_reads_models_endpoint(monkeypatch, status_code, expected):
    """健康检查从 chat/completions 还原同服务的模型列表端点。"""
    captured: dict = {}

    def get(url, **kwargs):
        captured["url"] = url
        return _response(status_code=status_code, body={"data": [{"id": "minicpm-ocr"}]})

    monkeypatch.setattr("requests.get", get)

    parser = MiniCPMOCRParser(api_key="fixture-key", api_url="http://ocr.test/v1/chat/completions")
    assert parser.check_health()["status"] == expected
    assert captured["url"] == "http://ocr.test/v1/models"


def test_minicpm_health_reports_unavailable_on_connection_error(monkeypatch):
    """连接失败返回 unavailable，不向调用方泄露异常细节以外的状态。"""

    def get(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("requests.get", get)
    result = MiniCPMOCRParser(api_url="http://ocr.test/v1/chat/completions").check_health()
    assert result["status"] == "unavailable"
    assert "connection refused" in result["message"]
