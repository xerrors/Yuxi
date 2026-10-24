"""
RapidOCR 解析器 - 纯OCR文字识别

使用 RapidOCR (PP-OCRv6) 进行文字识别
"""

import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

from yuxi.knowledge.parser.base import BaseDocumentProcessor, OCRException
from yuxi.knowledge.parser.capabilities import get_parser_capability
from yuxi.utils import logger

_CAPABILITY = get_parser_capability("rapid_ocr")


class RapidOCRParser(BaseDocumentProcessor):
    """RapidOCR 解析器 - 使用 ONNX 模型进行文字识别"""

    service_name = _CAPABILITY.service_name
    display_name = _CAPABILITY.display_name
    supported_extensions = list(_CAPABILITY.supported_extensions)

    def __init__(self, det_box_thresh: float = 0.3):
        self.ocr = None
        self.det_box_thresh = det_box_thresh

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
            pass

        cache_dir = Path.home() / ".cache" / "rapidocr" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _get_model_params(self) -> dict[str, object]:
        """选择 rapidocr wheel 内自带的 PP-OCRv6 small 模型，避免解析时联网下载。"""
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
        }

    def check_health(self) -> dict:
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

    def _load_model(self):
        """延迟加载 OCR 模型"""
        if self.ocr is not None:
            return

        logger.info("加载 RapidOCR 模型...")

        try:
            self.ocr = RapidOCR(params=self._get_model_params())
            logger.info(f"RapidOCR PP-OCRv6 模型加载成功 (det_box_thresh={self.det_box_thresh})")
        except Exception as e:
            raise OCRException(f"RapidOCR模型加载失败: {str(e)}", self.get_service_name(), "load_failed")

    def process_image(self, image, params: dict | None = None) -> str:
        """
        处理单张图像并提取文本

        Args:
            image: 图像数据,支持:
                  - str: 图像文件路径
                  - PIL.Image: PIL图像对象
                  - numpy.ndarray: numpy图像数组
            params: 处理参数 (当前未使用)

        Returns:
            str: 提取的文本内容
        """
        self._load_model()

        try:
            # 处理不同类型的输入
            if isinstance(image, str):
                image_path = image
                cleanup_needed = False
            else:
                # 创建临时文件
                image_path = self._create_temp_image_file(image)
                cleanup_needed = True

            try:
                # 执行 OCR
                start_time = time.time()
                result = self.ocr(image_path)
                processing_time = time.time() - start_time

                # 提取文本
                if result.txts:
                    text = "\n".join(result.txts)
                    logger.info(
                        f"RapidOCR 成功: {os.path.basename(image_path) if isinstance(image, str) else 'temp_image'}"
                        f" ({processing_time:.2f}s)"
                    )
                    return text
                else:
                    logger.warning(f"RapidOCR 未识别到文本: {image_path}")
                    return ""

            finally:
                # 清理临时文件
                if cleanup_needed and os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                    except Exception as e:
                        logger.warning(f"临时文件清理失败: {image_path} - {e}")

        except Exception as e:
            error_msg = f"图像OCR处理失败: {str(e)}"
            logger.error(error_msg)
            raise OCRException(error_msg, self.get_service_name(), "processing_failed")

    def _create_temp_image_file(self, image) -> str:
        """将图像数据保存为临时文件"""
        try:
            # 使用系统临时目录
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".png", delete=False) as tmp_file:
                temp_path = tmp_file.name

                if isinstance(image, Image.Image):
                    image.save(temp_path)
                elif isinstance(image, np.ndarray):
                    Image.fromarray(image).save(temp_path)
                else:
                    raise ValueError("不支持的图像类型,必须是 PIL.Image 或 numpy.ndarray")

                return temp_path

        except Exception as e:
            raise OCRException(f"临时图像文件创建失败: {str(e)}", self.get_service_name(), "temp_file_error")

    def process_pdf(self, pdf_path: str, params: dict | None = None) -> str:
        """
        处理 PDF 文件并提取文本 (流式处理,避免内存占用)

        Args:
            pdf_path: PDF 文件路径
            params: 处理参数
                - zoom_x: 渲染缩放倍数 (默认 2)

        Returns:
            str: 提取的文本
        """
        if not os.path.exists(pdf_path):
            raise OCRException(f"PDF 文件不存在: {pdf_path}", self.get_service_name(), "file_not_found")

        params = params or {}
        zoom_x = params.get("zoom_x", 2)

        try:
            all_text = []
            pdf_doc = pdfium.PdfDocument(pdf_path)
            total_pages = len(pdf_doc)

            logger.info(f"开始处理 PDF: {os.path.basename(pdf_path)} ({total_pages} 页)")

            # 流式处理每一页,避免一次性加载所有图片到内存
            for page_num in range(total_pages):
                page = pdf_doc[page_num]

                # pypdfium2 仅支持统一缩放（原 zoom_x/zoom_y 默认均为 2）
                img_pil = page.render(scale=zoom_x).to_pil()

                # 立即处理,不保存到列表
                text = self.process_image(img_pil)
                all_text.append(text)

                if (page_num + 1) % 10 == 0:
                    logger.info(f"已处理 {page_num + 1}/{total_pages} 页")

            pdf_doc.close()

            result_text = "\n\n".join(all_text)
            logger.info(f"PDF OCR 完成: {os.path.basename(pdf_path)} - {len(result_text)} 字符")
            return result_text

        except OCRException:
            raise
        except Exception as e:
            error_msg = f"PDF OCR 处理失败: {str(e)}"
            logger.error(error_msg)
            raise OCRException(error_msg, self.get_service_name(), "pdf_processing_failed")

    def process_file(self, file_path: str, params: dict | None = None) -> str:
        """
        处理文件 (PDF 或图像)

        Args:
            file_path: 文件路径
            params: 处理参数

        Returns:
            str: 提取的文本
        """
        file_ext = Path(file_path).suffix.lower()

        if not self.supports_file_type(file_ext):
            raise OCRException(f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type")

        if file_ext == ".pdf":
            return self.process_pdf(file_path, params)
        else:
            return self.process_image(file_path, params)
