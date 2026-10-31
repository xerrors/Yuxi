"""OCR 引擎契约、声明与惰性实例装配。"""

import hashlib
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, ClassVar


class DocumentEngine(ABC):
    """OCR 引擎统一契约：生成相对引用的 Markdown 并写入调用方目录。"""

    engine_id: ClassVar[str] = ""

    @abstractmethod
    def process_file(self, file_path: str, output_dir: Path, params: dict[str, Any] | None = None) -> str:
        """生成相对引用的 Markdown，并将图片写入调用方的输出目录。"""

    @abstractmethod
    def check_health(self) -> dict[str, Any]:
        """报告引擎可用性，返回 status、message 与可选 details。"""

    def get_service_name(self) -> str:
        """返回 parser 声明的稳定服务标识。"""
        return get_engine_spec(self.engine_id).service_name

    def supports_file_type(self, file_extension: str) -> bool:
        """判断扩展名（可带点）是否由当前引擎声明支持。"""
        return file_extension.lower() in self.get_supported_extensions()

    def get_supported_extensions(self) -> list[str]:
        """返回注册信息中的支持格式。"""
        return list(get_engine_spec(self.engine_id).supported_extensions)


_STANDARD_OCR_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif")
_MINERU_OFFICIAL_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")
_DEEPSEEK_OCR_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp")


@dataclass(frozen=True, slots=True)
class EngineSpec:
    """描述一个 OCR 处理器的装配位置和输入格式。"""

    service_name: str
    display_name: str
    supported_extensions: tuple[str, ...]
    module_path: str
    class_name: str


ENGINE_SPECS = {
    "rapid_ocr": EngineSpec(
        service_name="rapid_ocr",
        display_name="RapidOCR (ONNX)",
        supported_extensions=_STANDARD_OCR_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.rapid_ocr",
        class_name="RapidOCRParser",
    ),
    "mineru_ocr": EngineSpec(
        service_name="mineru_ocr",
        display_name="MinerU 自托管",
        supported_extensions=_STANDARD_OCR_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.mineru",
        class_name="MinerUParser",
    ),
    "mineru_official": EngineSpec(
        service_name="mineru_official",
        display_name="MinerU 官方云服务",
        supported_extensions=_MINERU_OFFICIAL_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.mineru_official",
        class_name="MinerUOfficialParser",
    ),
    "deepseek_ocr": EngineSpec(
        service_name="deepseek_ocr",
        display_name="DeepSeek OCR",
        supported_extensions=_DEEPSEEK_OCR_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.deepseek_ocr",
        class_name="DeepSeekOCRParser",
    ),
    "paddleocr_vl_1_6": EngineSpec(
        service_name="paddleocr_vl_1_6",
        display_name="PaddleOCR-VL-1.6",
        supported_extensions=_STANDARD_OCR_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.paddleocr_api",
        class_name="PaddleOCRVLParser",
    ),
    "paddleocr_pp_ocrv6": EngineSpec(
        service_name="paddleocr_pp_ocrv6",
        display_name="PP-OCRv6",
        supported_extensions=_STANDARD_OCR_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.paddleocr_api",
        class_name="PaddleOCRPPOCRv6Parser",
    ),
    "paddleocr_pp_structure_v3": EngineSpec(
        service_name="paddleocr_pp_structure_v3",
        display_name="PP-StructureV3（云服务）",
        supported_extensions=_STANDARD_OCR_EXTENSIONS,
        module_path="yuxi.infrastructure.document_parsing.engines.paddleocr_api",
        class_name="PaddleOCRPPStructureV3Parser",
    ),
}


def get_engine_spec(engine_id: str) -> EngineSpec:
    """返回指定 OCR 处理器的轻量能力声明。"""
    try:
        return ENGINE_SPECS[engine_id]
    except KeyError as exc:
        raise ValueError(f"不支持的 OCR 引擎: {engine_id}") from exc


def get_ocr_engine_ids() -> tuple[str, ...]:
    """返回按注册顺序排列的 OCR 处理器标识。"""
    return tuple(ENGINE_SPECS)


def get_ocr_engines_for_extension(extension: str) -> tuple[str, ...]:
    """返回能处理指定扩展名的 OCR 处理器。"""
    normalized = extension.lower()
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return tuple(engine_id for engine_id, capability in ENGINE_SPECS.items() if normalized in capability.supported_extensions)


_ENGINE_CACHE: dict[str, tuple[str, DocumentEngine]] = {}
_ENGINE_CACHE_LOCK = threading.Lock()


def get_engine(engine_id: str, **kwargs: Any) -> DocumentEngine:
    """按有效配置惰性加载引擎，配置变化时替换该引擎实例。"""
    spec = get_engine_spec(engine_id)
    fingerprint = _configuration_fingerprint(kwargs)
    # 构造仅装配配置；耗时模型加载由实例自己的锁负责。
    with _ENGINE_CACHE_LOCK:
        cached = _ENGINE_CACHE.get(engine_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        engine_type = getattr(import_module(spec.module_path), spec.class_name)
        engine = engine_type(**kwargs)
        _ENGINE_CACHE[engine_id] = (fingerprint, engine)
        return engine


def _configuration_fingerprint(kwargs: dict[str, Any]) -> str:
    """用稳定排序后的配置生成缓存键。"""
    return hashlib.sha256(repr(sorted(kwargs.items())).encode()).hexdigest()
