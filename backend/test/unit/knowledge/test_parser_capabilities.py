from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from yuxi.infrastructure.document_parsing import (
    IMAGE_FILE_EXTENSIONS,
    ParseOptions,
    SUPPORTED_FILE_EXTENSIONS,
    is_supported_file_extension,
)
from yuxi.infrastructure.document_parsing.engines import ENGINE_SPECS, get_ocr_engines_for_extension

pytestmark = pytest.mark.unit


def test_capability_metadata_covers_upload_and_ocr_formats() -> None:
    assert ".webp" in IMAGE_FILE_EXTENSIONS
    assert ".webp" in SUPPORTED_FILE_EXTENSIONS
    assert ".doc" not in SUPPORTED_FILE_EXTENSIONS
    assert ".ppt" not in SUPPORTED_FILE_EXTENSIONS
    assert get_ocr_engines_for_extension("webp") == ("deepseek_ocr",)
    assert get_ocr_engines_for_extension("docx") == ()
    assert get_ocr_engines_for_extension("ppt") == ()
    assert is_supported_file_extension("report.PPTX")
    assert not is_supported_file_extension("legacy.doc")


def test_capability_lookup_does_not_load_concrete_parser_modules() -> None:
    package_dir = Path(__file__).resolve().parents[3]
    script = f"""
import sys
sys.path.insert(0, {str(package_dir)!r})
from yuxi.infrastructure.document_parsing.engines import get_engine_spec

get_engine_spec("rapid_ocr")
assert "yuxi.infrastructure.document_parsing.engines.rapid_ocr" not in sys.modules
assert "docling" not in sys.modules
"""

    subprocess.run([sys.executable, "-c", script], check=True)


def test_knowledge_router_import_does_not_load_docling_or_ocr_provider() -> None:
    backend_dir = Path(__file__).resolve().parents[3]
    script = f"""
import sys
sys.path.insert(0, {str(backend_dir)!r})
import yuxi.api.routers.knowledge

assert "docling" not in sys.modules
assert "yuxi.infrastructure.document_parsing.engines.rapid_ocr" not in sys.modules
assert "yuxi.infrastructure.document_parsing.engines.mineru" not in sys.modules
"""

    subprocess.run([sys.executable, "-c", script], check=True)


@pytest.mark.asyncio
async def test_parse_document_rejects_ocr_engine_without_format_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yuxi.modules.documents.service as ocr_service

    source = tmp_path / "scan.webp"
    source.write_bytes(b"not an image")

    async def _resolve_rapid_ocr(*args, **kwargs) -> dict[str, str]:
        del args, kwargs
        return ParseOptions(ocr_engine="rapid_ocr")

    monkeypatch.setattr(ocr_service, "resolve_parse_options", _resolve_rapid_ocr)

    with pytest.raises(ValueError, match=r"不支持文件类型 \.webp"):
        await ocr_service.parse(str(source), tmp_path / "parsed", params={"ocr_engine": "rapid_ocr"})


def test_capability_registry_declares_shipping_processors() -> None:
    assert tuple(ENGINE_SPECS) == (
        "rapid_ocr",
        "mineru_ocr",
        "mineru_official",
        "deepseek_ocr",
        "paddleocr_vl_1_6",
        "paddleocr_pp_ocrv6",
        "paddleocr_pp_structure_v3",
    )


def test_concurrent_engine_configuration_returns_each_requested_instance(monkeypatch):
    """并发请求共享相同配置实例，各自保留请求的配置。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from yuxi.infrastructure.document_parsing import engines

    class ConfiguredEngine:
        """保留构造配置，作为返回实例的独立断言依据。"""

        def __init__(self, server_url):
            self.server_url = server_url

    monkeypatch.setattr(engines, "_ENGINE_CACHE", {})
    spec = engines.get_engine_spec("mineru_ocr")
    monkeypatch.setattr(engines, "import_module", lambda _module: SimpleNamespace(**{spec.class_name: ConfiguredEngine}))
    start = Barrier(2)

    def resolve(url):
        """同时从正式装配入口请求配置。"""
        start.wait(5)
        return engines.get_engine("mineru_ocr", server_url=url)

    with ThreadPoolExecutor(max_workers=2) as pool:
        urls = ["https://first.example.test", "https://second.example.test"]
        actual = list(pool.map(resolve, urls))
    assert [engine.server_url for engine in actual] == urls


def test_cold_engine_requests_share_instance(monkeypatch):
    """首次装配阻塞时，后续请求必须等待并复用已装配实例。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from yuxi.infrastructure.document_parsing import engines

    entered, second_entered, release = Event(), Event(), Event()
    original_get_spec = engines.get_engine_spec
    calls = []

    def get_spec(engine_id):
        """确认两个调用均已进入真实 get_engine，而不是尚未得到调度。"""
        calls.append(engine_id)
        if len(calls) == 2:
            second_entered.set()
        return original_get_spec(engine_id)

    class Engine:
        """人为延长装配，复现冷缓存下的竞争。"""

        def __init__(self):
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(engines, "_ENGINE_CACHE", {})
    monkeypatch.setattr(engines, "get_engine_spec", get_spec)
    monkeypatch.setattr(engines, "import_module", lambda _: SimpleNamespace(RapidOCRParser=Engine))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engines.get_engine, "rapid_ocr")
        try:
            assert entered.wait(5)
            second = pool.submit(engines.get_engine, "rapid_ocr")
            assert second_entered.wait(5)
        finally:
            release.set()
        assert first.result(5) is second.result(5)
