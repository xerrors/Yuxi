"""原生媒体 parser、供应商响应和标准 MCP 内容块负控。"""

import base64
import io
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import ValidationError

from yuxi.modules.knowledge.media import processing
from yuxi.modules.knowledge.media.mcp import media_read_result
from yuxi.modules.knowledge.media.schemas import MediaAsset, MediaManifest, SearchMediaInput
from yuxi.modules.models import media
from yuxi.shared.errors import ContentTooLargeError, InvalidInputError, InvalidOperationError, UpstreamError


def image_bytes(color="red"):
    """独立像素夹具不包含与题目同源的 OCR 文本。"""
    output = io.BytesIO()
    Image.new("RGB", (8, 12), color).save(output, "PNG")
    return output.getvalue()


def test_decodes_actual_image_and_rejects_false_image_or_animation():
    """格式来自实际字节；任意文本和未覆盖动画不能作为静态原图。"""
    decoded = processing.prepare_image(image_bytes())
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0) and image.size == (8, 12)
    with pytest.raises(InvalidInputError):
        processing.prepare_image(b"not an image.png")
    output = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(output, "GIF")
    with pytest.raises(InvalidOperationError):
        processing.prepare_image(output.getvalue())


@pytest.mark.parametrize(
    "data",
    [
        b"#EXTM3U\nhttp://127.0.0.1/secret",
        b"ffconcat version 1.0\nfile /etc/passwd",
        b"<MPD><BaseURL>file:///secret</BaseURL></MPD>",
        b"fake.mp4",
    ],
)
def test_playlists_and_extension_only_media_are_rejected_before_process(data):
    """恢复按扩展名或自动探测 playlist 的缺陷会让这些负控变红。"""
    with pytest.raises(InvalidInputError, match="容器"):
        processing.inspect_container(data)


@pytest.mark.parametrize("mutation", ["duration", "resolution", "frames", "channels", "multiple_audio", "nonfinite"])
def test_decode_budget_is_applied_to_actual_stream_parameters(mutation):
    """时长、帧预算、并行音轨和采样参数都不能被容器标签绕过。"""
    probe = {
        "format": {"duration": "4"},
        "streams": [
            {"codec_type": "video", "width": 640, "height": 480, "avg_frame_rate": "25/1"},
            {"codec_type": "audio", "channels": 1, "sample_rate": "16000"},
        ],
    }
    assert processing.validate_probe(probe) == ("video", 4000, True)
    if mutation == "duration":
        probe["format"]["duration"] = "601"
    elif mutation == "resolution":
        probe["streams"][0]["width"] = 100000
    elif mutation == "frames":
        probe["streams"][0]["avg_frame_rate"] = "120/1"
    elif mutation == "channels":
        probe["streams"][1]["channels"] = 100
    elif mutation == "multiple_audio":
        probe["streams"].append(probe["streams"][1])
    else:
        probe["format"]["duration"] = "nan"
    with pytest.raises((ContentTooLargeError, InvalidInputError)):
        processing.validate_probe(probe)


def test_manifest_rejects_gap_duplicate_and_missing_timestamp():
    """独立时间边界 oracle 禁止漏段仍标记完整。"""
    asset = {
        "asset_id": "audio-1",
        "segment_id": "s1",
        "kind": "audio",
        "object": {"bucket": "b", "object_name": "k/media/f/a/audio.wav"},
        "sha256": "a" * 64,
        "size": 100,
        "mime_type": "audio/wav",
        "start_ms": 0,
        "end_ms": 12000,
    }
    manifest = {
        "kb_id": "k",
        "file_id": "f",
        "source_revision": "src2:a",
        "source_sha256": "b" * 64,
        "source_mime_type": "audio/wav",
        "modality": "audio",
        "duration_ms": 12000,
        "config": {},
        "coverage": "complete",
        "assets": [asset],
    }
    assert MediaManifest.model_validate(manifest).duration_ms == 12000
    with pytest.raises(ValidationError, match="完整原件"):
        MediaManifest.model_validate({**manifest, "duration_ms": 13000})
    with pytest.raises(ValidationError, match="重复"):
        MediaManifest.model_validate({**manifest, "assets": [asset, asset]})
    with pytest.raises(ValidationError, match="时间码"):
        MediaAsset.model_validate({**asset, "start_ms": None})
    assert SearchMediaInput(kb_id="k", query="q", modality="audio", file_ids=[]).file_ids == []


@pytest.mark.asyncio
async def test_media_protocol_rejects_incompatible_models_and_invalid_vectors(monkeypatch):
    """文本模型不能声称具有原生媒体协议，乱序/缺失/NaN响应拒绝。"""
    info = SimpleNamespace(model_type="embedding", model_id="BAAI/bge-m3")
    monkeypatch.setattr(media.model_cache, "get_model_info", lambda spec: info)
    with pytest.raises(InvalidOperationError, match="协议"):
        media.NativeMediaModel("configured", "embedding")
    info.model_id = "Qwen/Qwen3-VL-Embedding-8B"
    model = media.NativeMediaModel("configured", "embedding")
    for invalid in [{"data": []}, {"data": [{"index": 4, "embedding": [1, 2]}]}, {"data": [{"index": 0, "embedding": [float("nan")]}]}]:
        monkeypatch.setattr(model, "_post", AsyncMock(return_value=invalid))
        with pytest.raises(UpstreamError):
            await model.embed([{"image": "data:image/png;base64,AAAA"}])


@pytest.mark.asyncio
async def test_rerank_requires_complete_unique_bounded_results(monkeypatch):
    """重复索引、缺行、非有限评分不能伪装重排成功。"""
    info = SimpleNamespace(model_type="rerank", model_id="Qwen/Qwen3-VL-Reranker-8B")
    monkeypatch.setattr(media.model_cache, "get_model_info", lambda spec: info)
    model = media.NativeMediaModel("configured", "rerank")
    for rows in [
        [],
        [{"index": 0, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.7}],
        [{"index": 0, "relevance_score": float("inf")}, {"index": 1, "relevance_score": 0.7}],
    ]:
        monkeypatch.setattr(model, "_post", AsyncMock(return_value={"results": rows}))
        with pytest.raises(UpstreamError):
            await model.rerank("red", [{"image": "red"}, {"image": "blue"}])


@pytest.mark.parametrize("mime,expected", [("image/png", "image"), ("audio/wav", "audio"), ("video/mp4", "resource")])
def test_mcp_raw_content_round_trips_exact_bytes(mime, expected):
    """MCP 结果的二进制块可独立解码，不把观察文本当原件。"""
    original = b"exact original bytes"
    result = media_read_result(
        {"kb_id": "k", "file_id": "f", "revision": "media2:r", "asset_id": "s", "media_bytes": original, "mime_type": mime}
    )
    block = result.content[1]
    assert block.type == expected
    encoded = block.resource.blob if expected == "resource" else block.data
    assert base64.b64decode(encoded) == original
    assert "media_bytes" not in result.structuredContent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "purpose,endpoint,model_type,model_id",
    [("embedding", "embeddings", "embedding", "Qwen/Qwen3-VL-Embedding-8B"), ("rerank", "rerank", "rerank", "Qwen/Qwen3-VL-Reranker-8B")],
)
@pytest.mark.parametrize("full_endpoint", [False, True])
async def test_model_cache_root_and_full_type_endpoints_are_called_exactly_once(
    monkeypatch, httpx_mock, purpose, endpoint, model_type, model_id, full_endpoint
):
    """恢复盲目追加endpoint会请求错误地址，真实ModelInfo两种契约都必须成立。"""
    expected = f"https://provider.example.test/v1/{endpoint}?routing=media"
    base = f"https://provider.example.test/v1{'/' + endpoint if full_endpoint else ''}?routing=media"
    info = SimpleNamespace(
        model_type=model_type,
        model_id=model_id,
        base_url=base,
        api_key="synthetic-test-key",
        headers={},
        spec="test:" + model_id,
        dimension=4096,
    )
    monkeypatch.setattr(media.model_cache, "get_model_info", lambda spec: info)
    model = media.NativeMediaModel(info.spec, purpose)
    httpx_mock.add_response(url=expected, method="POST", json={"data": "independent response"})
    assert await model._post(endpoint, {"input": "test"}) == {"data": "independent response"}
    assert media.media_endpoint_url(base, endpoint) == expected


def test_derived_asset_total_budget_prevents_many_small_files_from_expanding_memory(tmp_path, monkeypatch):
    """每个文件都合法仍必须拒绝累计产物越界。"""
    monkeypatch.setattr(processing, "MAX_DERIVED_BYTES", 16)
    first, second = tmp_path / "one", tmp_path / "two"
    first.write_bytes(b"a" * 9)
    second.write_bytes(b"b" * 9)
    processing.require_derived_budget([{"path": first}])
    with pytest.raises(ContentTooLargeError, match="累计"):
        processing.require_derived_budget([{"path": first}, {"path": second}])


def test_expired_total_decode_budget_does_not_start_another_process(tmp_path):
    """逐段超时之外还有总预算，过期后新子进程不能再产生输出。"""
    marker = tmp_path / "forbidden-output"
    with pytest.raises(ContentTooLargeError, match="总时间预算"):
        processing._run(
            [sys.executable, "-c", "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()"],
            timeout=30,
            deadline=time.monotonic() - 1,
        )
    assert not marker.exists()
