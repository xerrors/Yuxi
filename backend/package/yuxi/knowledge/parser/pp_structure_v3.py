"""
PP-Structure-V3 文档解析器

使用 PP-Structure-V3 进行文档版面解析和内容提取
"""

import base64
import os
import time
from pathlib import Path
from typing import Any

import requests

from yuxi.knowledge.parser.base import BaseDocumentProcessor, DocumentParserException
from yuxi.utils import logger


class PPStructureV3Parser(BaseDocumentProcessor):
    """PP-Structure-V3 文档解析器 - 使用 PP-Structure-V3 进行版面解析"""

    service_name = "pp_structure_v3_ocr"
    display_name = "PP-Structure-V3"
    supported_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"]

    def __init__(self, server_url: str | None = None):
        self.server_url = server_url or os.getenv("PADDLEX_URI") or "http://localhost:8080"
        self.base_url = self.server_url.rstrip("/")
        self.endpoint = f"{self.base_url}/layout-parsing"

    def _encode_file_to_base64(self, file_path: str) -> str:
        """将文件编码为Base64"""
        with open(file_path, "rb") as file:
            encoded = base64.b64encode(file.read()).decode("utf-8")
            return encoded

    def _process_file_input(self, file_input: str) -> str:
        """处理文件输入：本地文件路径、URL或Base64内容"""
        # 检查是否为本地文件路径
        if os.path.exists(file_input):
            logger.info(f"📁 检测到本地文件: {file_input}")
            logger.info(f"📏 文件大小: {os.path.getsize(file_input) / 1024 / 1024:.2f} MB")
            return self._encode_file_to_base64(file_input)

        # 检查是否为URL
        elif file_input.startswith(("http://", "https://")):
            logger.info(f"🌐 检测到URL: {file_input}")
            return file_input

        # 否则假设为Base64编码内容
        else:
            logger.info(f"📝 假设为Base64编码内容，长度: {len(file_input)} 字符")
            return file_input

    def _call_layout_api(
        self,
        file_input: str,
        file_type: int | None = None,
        use_table_recognition: bool = True,
        use_formula_recognition: bool = True,
        use_seal_recognition: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        """调用PP-Structure-V3版面解析API"""
        # 处理文件输入
        processed_file_input = self._process_file_input(file_input)
        payload = {"file": processed_file_input}

        # 添加核心参数
        optional_params = {
            "fileType": file_type,
            "useTableRecognition": use_table_recognition,
            "useFormulaRecognition": use_formula_recognition,
            "useSealRecognition": use_seal_recognition,
        }

        # 添加非空参数
        for key, value in optional_params.items():
            if value is not None:
                payload[key] = value

        timeout_seconds = kwargs.pop("timeout_seconds", 300)

        # 添加其他kwargs参数
        for key, value in kwargs.items():
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
        else:
            error_msg = f"PP-Structure-V3 API请求失败: {response.status_code}"
            try:
                error_result = response.json()
                raise DocumentParserException(f"{error_msg}: {error_result}", self.get_service_name(), "api_error")
            except Exception:
                raise DocumentParserException(f"{error_msg}: {response.text}", self.get_service_name(), "api_error")

    def _parse_api_result(self, api_result: dict[str, Any], file_path: str) -> dict[str, Any]:
        """解析API返回结果"""
        # 基本信息
        parsed_result = {
            "success": True,
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "log_id": api_result.get("logId"),
            "total_pages": 0,
            "pages": [],
            "full_text": "",
            "summary": {},
        }

        result_data = api_result.get("result", {})
        layout_results = result_data.get("layoutParsingResults", [])

        # 数据信息
        parsed_result["total_pages"] = len(layout_results)

        # 统计信息
        total_tables = 0
        total_formulas = 0
        all_text_content = []

        # 解析每页结果
        for page_result in layout_results:
            # Markdown内容
            if "markdown" in page_result:
                markdown = page_result["markdown"]
                if markdown.get("text"):
                    all_text_content.append(markdown["text"])

            # 详细识别结果
            if "prunedResult" in page_result:
                pruned = page_result["prunedResult"]

                # 表格识别
                table_result = pruned.get("table_result", [])
                total_tables += len(table_result)

                # 公式识别
                formula_result = pruned.get("formula_result", [])
                total_formulas += len(formula_result)

        # 汇总全文内容
        parsed_result["full_text"] = "\n\n".join(all_text_content)

        # 汇总统计信息
        parsed_result["summary"] = {
            "total_tables": total_tables,
            "total_formulas": total_formulas,
            "total_characters": len(parsed_result["full_text"]),
        }

        return parsed_result

    def check_health(self) -> dict:
        """检查 PP-Structure-V3 服务健康状态"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)

            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "message": "PP-Structure-V3 服务运行正常",
                    "details": {"server_url": self.server_url},
                }
            else:
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

    def process_file(self, file_path: str, params: dict | None = None) -> str:
        """
        使用 PP-Structure-V3 处理文档

        Args:
            file_path: 文件路径
            params: 处理参数
                - use_table_recognition: 启用表格识别 (默认: True)
                - use_formula_recognition: 启用公式识别 (默认: True)
                - use_seal_recognition: 启用印章识别 (默认: False)

        Returns:
            str: 提取的 Markdown 文本
        """
        if not os.path.exists(file_path):
            raise DocumentParserException(f"文件不存在: {file_path}", self.get_service_name(), "file_not_found")

        file_ext = Path(file_path).suffix.lower()
        if not self.supports_file_type(file_ext):
            raise DocumentParserException(
                f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type"
            )

        # 先检查服务健康状态
        health = self.check_health()
        if health["status"] != "healthy":
            raise DocumentParserException(
                f"PP-Structure-V3 服务不可用: {health['message']}", self.get_service_name(), health["status"]
            )

        try:
            start_time = time.time()
            params = params or {}

            # 判断文件类型
            file_type = 0 if file_ext == ".pdf" else 1

            logger.info(f"PP-Structure-V3 开始处理: {os.path.basename(file_path)}")

            # 调用API
            api_result = self._call_layout_api(
                file_input=file_path,
                file_type=file_type,
                use_table_recognition=params.get("use_table_recognition", True),
                use_formula_recognition=params.get("use_formula_recognition", True),
                use_seal_recognition=params.get("use_seal_recognition", False),
                timeout_seconds=params.get("timeout_seconds", 300),
            )

            # 检查API调用是否成功
            if api_result.get("errorCode") != 0:
                raise DocumentParserException(
                    f"PP-Structure-V3 API错误: {api_result.get('errorMsg', '未知错误')}",
                    self.get_service_name(),
                    "api_error",
                )

            # 解析结果
            result = self._parse_api_result(api_result, file_path)
            text = result.get("full_text", "")

            processing_time = time.time() - start_time
            logger.info(
                f"PP-Structure-V3 处理成功: {os.path.basename(file_path)} - {len(text)} 字符 ({processing_time:.2f}s)"
            )

            # 记录统计信息
            summary = result.get("summary", {})
            if summary:
                logger.info(f"  统计: {summary.get('total_tables', 0)} 表格, {summary.get('total_formulas', 0)} 公式")

            return text

        except DocumentParserException:
            raise
        except Exception as e:
            processing_time = time.time() - start_time
            error_msg = f"PP-Structure-V3 处理失败: {str(e)}"
            logger.error(f"{error_msg} ({processing_time:.2f}s)")
            raise DocumentParserException(error_msg, self.get_service_name(), "processing_failed")
