"""MiniCPM-OCR（vLLM OpenAI 兼容服务）解析器。"""

import base64
import io
import os
import time
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import requests

from yuxi.infrastructure.document_parsing import DocumentParserException
from yuxi.infrastructure.document_parsing.engines import DocumentEngine
from yuxi.infrastructure.document_parsing.pdf_utils import PDFIUM_LOCK, render_pdf_page_image
from yuxi.infrastructure.observability.logging import logger

DEFAULT_API_URL = "http://127.0.0.1:18499/v1/chat/completions"
DEFAULT_MODEL = "minicpm-ocr"
DEFAULT_PROMPT = "Text Recognition:"

# 官方 vLLM 部署建议的解码参数，偏离会明显影响复现质量。
DEFAULT_TEMPERATURE = 0.01
DEFAULT_TOP_P = 0.001
DEFAULT_TOP_K = 1
DEFAULT_REPETITION_PENALTY = 1.0
DEFAULT_NO_REPEAT_NGRAM_SIZE = 100
DEFAULT_MAX_SLICE_NUMS = 9
DEFAULT_DOWNSAMPLE_MODE = "4x"


class MiniCPMOCRParser(DocumentEngine):
    """通过自托管 vLLM 的 MiniCPM-OCR 模型逐页解析文档。"""

    engine_id = "minicpm_ocr"

    MIME_TYPE_MAP = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }

    def __init__(self, api_key: str | None = None, api_url: str | None = None, model: str | None = None):
        """使用配置中心传入的服务地址、模型名和可选凭据初始化解析器。"""

        self.api_key = (api_key or "").strip()
        self.api_url = (api_url or DEFAULT_API_URL).strip().rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()

        self.headers = {"Content-Type": "application/json"}
        # vLLM 未启用 --api-key 时不发送 Authorization，避免空 Bearer 触发 401。
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    def check_health(self) -> dict[str, Any]:
        """探测 OpenAI 兼容服务的模型列表端点。"""
        try:
            response = requests.get(self._models_url(), headers=self.headers, timeout=10)

            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "message": "MiniCPM-OCR service is available",
                    "details": {"api_url": self.api_url, "model": self.model},
                }
            if response.status_code == 401:
                return {"status": "unhealthy", "message": "Invalid API Key", "details": {"error_code": "401"}}
            return {
                "status": "unhealthy",
                "message": f"API Error: {response.status_code}",
                "details": {"status_code": response.status_code},
            }
        except Exception as e:
            return {"status": "unavailable", "message": f"Connection failed: {str(e)}", "details": {"error": str(e)}}

    def process_file(self, file_path: str, output_dir: Path, params: dict[str, Any] | None = None) -> str:
        """逐页调用 MiniCPM-OCR，返回拼接后的识别文本。"""
        if not os.path.exists(file_path):
            raise DocumentParserException(f"File not found: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(f"Unsupported file type: {file_ext}", self.get_service_name(), "unsupported_file_type")

        try:
            start_time = time.time()
            logger.info(f"MiniCPM-OCR starting: {os.path.basename(file_path)}")

            params = params or {}
            if file_ext == ".pdf":
                content = self._process_pdf(file_path, params)
            else:
                content = self._process_image(file_path, params)

            processing_time = time.time() - start_time
            logger.info(f"MiniCPM-OCR finished: {os.path.basename(file_path)} - {len(content)} chars ({processing_time:.2f}s)")

            return content

        except DocumentParserException:
            raise
        except Exception as e:
            error_msg = f"MiniCPM-OCR failed: {str(e)}"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), "processing_failed") from e

    def _process_pdf(self, file_path: str, params: dict[str, Any]) -> str:
        """把 PDF 每页渲染为 PNG 后交给远端识别。"""
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(file_path)
        try:
            full_text: list[str] = []

            with PDFIUM_LOCK:
                total_pages = len(pdf)
            logger.info(f"Processing PDF with {total_pages} pages")

            # pypdfium2 的 scale 是 72dpi 的倍数，200dpi 对应 scale=200/72
            scale = int(params.get("pdf_dpi", 200)) / 72

            for i in range(total_pages):
                logger.debug(f"Processing page {i + 1}/{total_pages}")
                with render_pdf_page_image(pdf, i, scale=scale) as image, io.BytesIO() as buffer:
                    image.save(buffer, format="PNG")
                    img_bytes = buffer.getvalue()

                page_text = self._call_api(img_bytes, "image/png", params)
                full_text.append(page_text)

            return "\n\n".join(full_text)
        finally:
            with PDFIUM_LOCK:
                pdf.close()

    def _process_image(self, file_path: str, params: dict[str, Any]) -> str:
        """读取单张图片并调用远端识别。"""
        mime_type = self._get_mime_type(file_path)
        with open(file_path, "rb") as stream:
            file_content = stream.read()
        return self._call_api(file_content, mime_type, params)

    def _call_api(self, data_bytes: bytes, mime_type: str, params: dict[str, Any]) -> str:
        """提交 data URL 并校验完整结束的识别正文。"""
        encoded_string = base64.b64encode(data_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{encoded_string}"

        # 图片必须位于文本之前，官方示例按此顺序训练与验证。
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": params.get("prompt") or DEFAULT_PROMPT},
                ],
            }
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(params.get("max_tokens", 8192)),
            "temperature": float(params.get("temperature", DEFAULT_TEMPERATURE)),
            "top_p": float(params.get("top_p", DEFAULT_TOP_P)),
            "top_k": int(params.get("top_k", DEFAULT_TOP_K)),
            "repetition_penalty": float(params.get("repetition_penalty", DEFAULT_REPETITION_PENALTY)),
            "no_repeat_ngram_size": int(params.get("no_repeat_ngram_size", DEFAULT_NO_REPEAT_NGRAM_SIZE)),
            # 关闭 thinking，否则正文可能被写入 reasoning_content 而 content 为空。
            "chat_template_kwargs": {"enable_thinking": False},
            # 官方要求每请求携带切片参数，服务端需开启 --trust-request-mm-kwargs。
            "mm_processor_kwargs": {
                "downsample_mode": params.get("downsample_mode") or DEFAULT_DOWNSAMPLE_MODE,
                "max_slice_nums": int(params.get("max_slice_nums", DEFAULT_MAX_SLICE_NUMS)),
            },
        }

        response = requests.post(
            self.api_url,
            headers=self.headers,
            json=payload,
            timeout=int(params.get("timeout_seconds", 180)),
        )

        if response.status_code != 200:
            error_msg = f"API Error {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise DocumentParserException(error_msg, self.get_service_name(), f"http_{response.status_code}")

        result = response.json()
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise DocumentParserException("OCR 响应未完整结束", self.get_service_name(), "incomplete_result")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise DocumentParserException("OCR 结果正文必须是字符串", self.get_service_name(), "invalid_result")

        return content.strip()

    def _models_url(self) -> str:
        """把请求地址还原为同服务的模型列表端点。"""
        base = self.api_url
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        return f"{base}/models"

    def _get_mime_type(self, file_path: str) -> str:
        """按扩展名返回图片 MIME，未知扩展名按 JPEG 处理。"""
        file_ext = Path(file_path).suffix.lower()
        return self.MIME_TYPE_MAP.get(file_ext, "image/jpeg")
