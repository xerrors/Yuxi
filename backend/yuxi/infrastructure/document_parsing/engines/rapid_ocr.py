"""RapidOCR 本地 ONNX 文字识别解析器。"""

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

from yuxi.infrastructure.document_parsing import OCRException
from yuxi.infrastructure.document_parsing.engines import DocumentEngine
from yuxi.infrastructure.document_parsing.pdf_utils import PDFIUM_LOCK, render_pdf_page_image
from yuxi.infrastructure.observability.logging import logger

#: onnxruntime CPU 推理线程上限：库默认 -1 会按核数全开，高核共享主机上一篇扫描 PDF 就能占满全部 CPU。
#: rapidocr 在该值超过实际核数时自动忽略，小规格宿主自然退回默认行为。
OCR_INTRA_OP_NUM_THREADS = 4


class RapidOCRParser(DocumentEngine):
    """RapidOCR 解析器，使用本地 ONNX 模型进行文字识别。"""

    engine_id = "rapid_ocr"

    def __init__(self, det_box_thresh: float = 0.3):
        self.ocr: RapidOCR | None = None
        self.det_box_thresh = det_box_thresh
        self._model_lock = threading.Lock()

    def process_file(self, file_path: str, output_dir: Path, params: dict[str, Any] | None = None) -> str:
        """按扩展名选择 PDF 或单图 OCR。"""
        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise OCRException(f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type")

        if file_ext == ".pdf":
            return self.process_pdf(file_path, params)
        return self.process_image(file_path)

    def check_health(self) -> dict[str, Any]:
        """报告本地组件状态，避免选择器刷新时重复加载模型。"""
        return {
            "status": "healthy",
            "message": "RapidOCR PP-OCRv6 组件可用",
            "details": {
                "ocr_version": "PP-OCRv6",
                "engine": "onnxruntime",
                "det_box_thresh": self.det_box_thresh,
            },
        }

    def process_image(self, image: Image.Image | np.ndarray | str) -> str:
        """直接识别文件或内存图像，保留输入的 RGB 颜色语义。"""
        self._load_model()
        try:
            # 旧数组经 PNG 作为 RGB 读取；PIL 输入让依赖继续执行相同颜色转换。
            source = Image.fromarray(image) if isinstance(image, np.ndarray) else image
            result = self.ocr(source)
            return "\n".join(result.txts) if result.txts else ""
        except Exception as exc:
            raise OCRException(f"图像OCR处理失败: {exc}", self.get_service_name(), "processing_failed") from exc

    def process_pdf(self, pdf_path: str, params: dict[str, Any] | None = None) -> str:
        """逐页渲染并 OCR，原生资源在锁内释放后再消费复制图像。"""
        if not os.path.exists(pdf_path):
            raise OCRException(f"PDF 文件不存在: {pdf_path}", self.get_service_name(), "file_not_found")

        params = params or {}
        zoom_x = params.get("zoom_x", 2)

        try:
            all_text: list[str] = []
            with PDFIUM_LOCK:
                pdf_doc = pdfium.PdfDocument(pdf_path)
            try:
                with PDFIUM_LOCK:
                    total_pages = len(pdf_doc)

                logger.info(f"开始处理 PDF: {os.path.basename(pdf_path)} ({total_pages} 页)")

                # 原生资源在锁内释放，耗时 OCR 只消费已复制的图像并在锁外执行。
                for page_num in range(total_pages):
                    with render_pdf_page_image(pdf_doc, page_num, scale=zoom_x) as image:
                        text = self.process_image(image)
                    all_text.append(text)

                    if (page_num + 1) % 10 == 0:
                        logger.info(f"已处理 {page_num + 1}/{total_pages} 页")
            finally:
                with PDFIUM_LOCK:
                    pdf_doc.close()

            result_text = "\n\n".join(all_text)
            logger.info(f"PDF OCR 完成: {os.path.basename(pdf_path)} - {len(result_text)} 字符")
            return result_text

        except OCRException:
            raise
        except Exception as e:
            error_msg = f"PDF OCR 处理失败: {str(e)}"
            logger.error(error_msg)
            raise OCRException(error_msg, self.get_service_name(), "pdf_processing_failed") from e

    @staticmethod
    def _resolve_model_dir() -> Path:
        """获取并确保可写的 RapidOCR 模型存放目录。"""
        env_dir = os.getenv("RAPIDOCR_MODEL_DIR")
        if env_dir:
            model_dir = Path(env_dir).expanduser().resolve()
            model_dir.mkdir(parents=True, exist_ok=True)
            return model_dir

        try:
            import rapidocr

            default_dir = Path(rapidocr.__file__).resolve().parent / "models"
            if default_dir.exists() and os.access(default_dir, os.W_OK):
                return default_dir
        except Exception:
            # 本地安装布局差异或不可写时退回用户缓存。
            pass

        cache_dir = Path.home() / ".cache" / "rapidocr" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _get_model_params(self) -> dict[str, object]:
        """返回 PP-OCRv6 ONNX 检测与识别参数（模型由 rapidocr wheel 自带）。"""
        model_dir = self._resolve_model_dir()
        return {
            "Global.model_root_dir": str(model_dir),
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.SMALL,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.box_thresh": self.det_box_thresh,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "EngineConfig.onnxruntime.intra_op_num_threads": OCR_INTRA_OP_NUM_THREADS,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }

    def _load_model(self) -> None:
        """同一实例的首次初始化串行执行，失败后允许下一次重试。"""
        with self._model_lock:
            if self.ocr is not None:
                return
            try:
                self.ocr = RapidOCR(params=self._get_model_params())
            except Exception as exc:
                raise OCRException(f"RapidOCR模型加载失败: {exc}", self.get_service_name(), "load_failed") from exc
