"""PP-Structure-V3 文档解析器。"""

import base64
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from yuxi.infrastructure.document_parsing import DocumentParserException
from yuxi.infrastructure.document_parsing.artifacts import replace_image_links, save_resource
from yuxi.infrastructure.document_parsing.engines import DocumentEngine
from yuxi.infrastructure.observability.logging import logger

# PP-Structure-V3 协议中的 fileType 取值。
_PDF_FILE_TYPE = 0
_IMAGE_FILE_TYPE = 1
_DEFAULT_TIMEOUT_SECONDS = 300


class PPStructureV3Parser(DocumentEngine):
    """通过 PP-Structure-V3 版面解析服务生成 Markdown。"""

    engine_id = "pp_structure_v3_ocr"

    def __init__(self, server_url: str | None = None):
        """仅连接显式配置的外部服务。"""
        self.server_url = (server_url or "").strip()
        self.base_url = self.server_url.rstrip("/")
        self.endpoint = f"{self.base_url}/layout-parsing"

    def check_health(self) -> dict[str, Any]:
        """检查 PP-Structure-V3 服务健康状态。"""
        if not self.server_url:
            return {"status": "unavailable", "message": "PP-Structure-V3 外部服务地址未配置", "details": {}}
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)

            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "message": "PP-Structure-V3 服务运行正常",
                    "details": {"server_url": self.server_url},
                }
            return {
                "status": "unhealthy",
                "message": f"PP-Structure-V3 服务响应异常: {response.status_code}",
                "details": {"server_url": self.server_url},
            }

        except requests.exceptions.ConnectionError:
            return {
                "status": "unavailable",
                "message": "PP-Structure-V3 服务无法连接,请检查服务是否启动",
                "details": {"server_url": self.server_url},
            }
        except requests.exceptions.Timeout:
            return {
                "status": "timeout",
                "message": "PP-Structure-V3 服务连接超时",
                "details": {"server_url": self.server_url},
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"PP-Structure-V3 健康检查失败: {str(e)}",
                "details": {"server_url": self.server_url, "error": str(e)},
            }

    def process_file(self, file_path: str, output_dir: Path, params: dict[str, Any] | None = None) -> str:
        """调用版面解析 API，保存图片并返回完整 Markdown。"""
        if not os.path.exists(file_path):
            raise DocumentParserException(f"文件不存在: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type")

        try:
            start_time = time.time()
            params = params or {}
            file_type = _PDF_FILE_TYPE if file_ext == ".pdf" else _IMAGE_FILE_TYPE

            logger.info(f"PP-Structure-V3 开始处理: {os.path.basename(file_path)}")
            api_result = self._call_layout_api(
                file_input=file_path,
                file_type=file_type,
                use_table_recognition=params.get("use_table_recognition", True),
                use_formula_recognition=params.get("use_formula_recognition", True),
                use_seal_recognition=params.get("use_seal_recognition", False),
                timeout_seconds=params.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS),
            )
            if api_result.get("errorCode") != 0:
                raise DocumentParserException(
                    f"PP-Structure-V3 API错误: {api_result.get('errorMsg', '未知错误')}",
                    self.get_service_name(),
                    "api_error",
                )

            text = self._parse_api_result(api_result, output_dir)
            processing_time = time.time() - start_time
            logger.info(f"PP-Structure-V3 处理成功: {os.path.basename(file_path)} - {len(text)} 字符 ({processing_time:.2f}s)")

            return text

        except DocumentParserException:
            raise
        except Exception as e:
            processing_time = time.time() - start_time
            error_msg = f"PP-Structure-V3 处理失败: {str(e)}"
            logger.error(f"{error_msg} ({processing_time:.2f}s)")
            raise DocumentParserException(error_msg, self.get_service_name(), "processing_failed") from e

    def _call_layout_api(
        self,
        file_input: str,
        file_type: int | None = None,
        use_table_recognition: bool = True,
        use_formula_recognition: bool = True,
        use_seal_recognition: bool = False,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """调用版面解析 API，返回协议 JSON。"""
        if not self.server_url:
            raise DocumentParserException("PP-Structure-V3 外部服务地址未配置", self.engine_id, "missing_server_url")
        payload = {"file": base64.b64encode(Path(file_input).read_bytes()).decode("utf-8")}
        optional_params = {
            "fileType": file_type,
            "useTableRecognition": use_table_recognition,
            "useFormulaRecognition": use_formula_recognition,
            "useSealRecognition": use_seal_recognition,
        }
        for key, value in optional_params.items():
            if value is not None:
                payload[key] = value

        response = requests.post(
            self.endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        if response.status_code == 200:
            return response.json()

        error_prefix = f"PP-Structure-V3 API请求失败: {response.status_code}"
        raise DocumentParserException(f"{error_prefix}: {response.text}", self.get_service_name(), "api_error")

    def _parse_api_result(self, api_result: dict[str, Any], output_dir: Path) -> str:
        """校验结果结构并保存正文引用的完整图片。"""
        layout_results = api_result["result"]["layoutParsingResults"]
        if not isinstance(layout_results, list):
            raise ValueError("layoutParsingResults 必须是列表")
        text_parts = []
        for page_result in layout_results:
            markdown = page_result["markdown"]
            text = markdown["text"]
            if not isinstance(text, str):
                raise ValueError("Markdown 正文必须是字符串")
            mapping = {}
            for image_path, image_url in (markdown.get("images") or {}).items():
                response = requests.get(image_url, timeout=60)
                response.raise_for_status()
                suffix = Path(image_path).suffix or ".png"
                local_path = save_resource(output_dir, f"images/{uuid4().hex}{suffix}", response.content)
                mapping[str(image_path)] = local_path
                mapping[str(image_url)] = local_path
            text_parts.append(replace_image_links(text, mapping))
        return "\n\n".join(text_parts)
