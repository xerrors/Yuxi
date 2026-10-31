"""通过 MinerU 4.1 Tasks 协议生成完整 Markdown 目录。"""

import asyncio
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from yuxi.infrastructure.document_parsing import DocumentParserException
from yuxi.infrastructure.document_parsing.artifacts import extract_markdown_archive
from yuxi.infrastructure.document_parsing.engines import DocumentEngine
from yuxi.infrastructure.observability.logging import logger


class MinerUParser(DocumentEngine):
    """自托管单文件 Tasks 客户端，业务终态由调用方拥有。"""

    engine_id = "mineru_ocr"

    def __init__(self, server_url: str | None = None, tier: str = "standard"):
        """接收已解析的服务地址与默认档位。"""
        self.server_url = (server_url or "http://localhost:30001").rstrip("/")
        self.tier = tier

    def check_health(self) -> dict[str, Any]:
        """核验 Tasks 协议和所选档位，不提交真实解析任务。"""
        try:
            asyncio.run(self._check_health())
            return {"status": "healthy", "message": "MinerU Tasks 服务可用", "details": {"tier": self.tier}}
        except (httpx.HTTPError, DocumentParserException, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return {"status": "unhealthy", "message": f"MinerU 服务检查失败: {exc}", "details": {}}

    async def _check_health(self) -> None:
        """两个探测共享总时限，缓慢响应也不能无限延长检查。"""
        async with asyncio.timeout(5), httpx.AsyncClient(follow_redirects=False) as client:
            health = (await self._request(client, "GET", f"{self.server_url}/v1/health")).json()
            tiers = (await self._request(client, "GET", f"{self.server_url}/v1/tiers")).json()
            if "default" not in health.get("features", {}).get("task_output_formats", []):
                raise ValueError("服务不支持 MinerU 4.1 Tasks 默认产物")
            if self.tier not in {item["id"] for item in tiers["data"]}:
                raise ValueError(f"服务未开放 {self.tier} 档位")

    def process_file(self, file_path: str, output_dir: Path, params: dict[str, Any] | None = None) -> str:
        """在线程入口运行异步协议；创建结果未知时不自动重试。"""
        source = Path(file_path)
        if not source.is_file():
            raise DocumentParserException("文件不存在", self.engine_id, "file_not_found")
        if not self.supports_file_type(source.suffix.lower()):
            raise DocumentParserException(f"不支持的文件类型: {source.suffix}", self.engine_id, "unsupported_file_type")
        try:
            options = params or {}
            manifest = self._build_manifest(source, options)
            budget = float(options.get("timeout_seconds", os.environ.get("MINERU_TIMEOUT", 1800)))
            if not math.isfinite(budget) or budget <= 0:
                raise ValueError("MinerU timeout_seconds 必须为有限正数")
            return asyncio.run(self._process_task(source, output_dir, manifest, budget))
        except DocumentParserException:
            raise
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise DocumentParserException("MinerU 任务处理超时", self.engine_id, "timeout") from exc
        except Exception as exc:
            raise DocumentParserException(f"MinerU 解析失败: {exc}", self.engine_id, "processing_failed") from exc

    async def _process_task(self, source: Path, output_dir: Path, manifest: dict, budget: float) -> str:
        """创建、上传、轮询和下载共享总时限，清理拥有独立的短时限。"""
        task_url = None
        terminal = False
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
            try:
                async with asyncio.timeout(budget) as timer:
                    receipt = (await self._request(client, "POST", f"{self.server_url}/v1/tasks", expected=202, json=manifest)).json()
                    task_id = receipt["task_id"]
                    if not isinstance(task_id, str) or not task_id:
                        raise ValueError("MinerU 未返回有效 task_id")
                    task_url = f"{self.server_url}/v1/tasks/{quote(task_id, safe='')}"
                    [created_file] = receipt["files"]
                    file_id = created_file["file_id"]
                    if not isinstance(file_id, str) or not file_id:
                        raise ValueError("MinerU 未返回有效 file_id")

                    # 创建回执只绑定身份；查询快照决定上传和结果，不重放不确定的创建或上传。
                    uploaded = False
                    while True:
                        detail = (await self._request(client, "GET", task_url)).json()
                        [file] = detail["files"]
                        if detail["task_id"] != task_id or file["file_id"] != file_id:
                            raise ValueError("MinerU 返回的任务或文件身份不匹配")
                        state = file["status"]
                        if state == "completed":
                            if detail["status"] != "completed":
                                raise ValueError("MinerU 单文件任务终态不一致")
                            terminal = True
                            result = await self._download_result(client, self._same_origin_url(file["result_url"]), output_dir)
                            # 本地解包是同步工作，返回前仍须确认没有超过本次预算。
                            if asyncio.get_running_loop().time() >= timer.when():
                                raise TimeoutError("MinerU 结果处理超时")
                            return result
                        if state in {"failed", "canceled"}:
                            terminal = True
                            error = file.get("error") or detail.get("error") or {}
                            raise DocumentParserException(
                                f"MinerU 解析{state}: {error.get('message', state)}", self.engine_id, "parsing_failed"
                            )
                        if state not in {"waiting-file", "pending", "running"}:
                            raise ValueError(f"MinerU 返回未知状态: {state}")
                        upload = file.get("upload")
                        if state == "waiting-file" and upload and not uploaded:
                            await self._request(
                                client,
                                "PUT",
                                self._same_origin_url(upload["url"]),
                                content=self._source_chunks(source),
                                headers={**upload["headers"], "Content-Length": str(manifest["files"][0]["bytes"])},
                            )
                            uploaded = True
                            continue
                        await asyncio.sleep(1)
            finally:
                # 超时或协议错误后尽力取消已知任务；清理失败不得覆盖原始结局。
                if task_url and not terminal:
                    try:
                        async with asyncio.timeout(10):
                            await self._request(client, "POST", f"{task_url}/cancel")
                    except Exception:
                        logger.warning("MinerU 远端任务取消未确认，服务端任务可能继续运行")

    async def _source_chunks(self, source: Path):
        """分块上传原始字节，避免将整个文档复制进内存。"""
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk

    def _build_manifest(self, source: Path, params: dict[str, Any]) -> dict[str, Any]:
        """从本次解析参数构造 4.1 Tasks 请求。"""
        tier = params.get("tier", self.tier)
        ocr_mode = params.get("ocr_mode", "auto")
        if tier not in {"flash", "basic", "standard", "advanced"}:
            raise ValueError("不支持的 MinerU tier")
        if ocr_mode not in {"auto", "txt", "ocr"}:
            raise ValueError("不支持的 MinerU ocr_mode")
        return {
            "files": [{"file_name": source.name, "bytes": source.stat().st_size}],
            "tier": tier,
            "ocr_mode": ocr_mode,
            "page_range": params.get("page_range", "all"),
            "output_formats": ["default"],
        }

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, *, expected: int = 200, **kwargs) -> httpx.Response:
        """拒绝重定向和非协议状态码，网络读取由调用方总时限约束。"""
        response = await client.request(method, url, **kwargs)
        if response.status_code != expected:
            raise DocumentParserException(f"MinerU HTTP 请求失败: {response.status_code}", self.engine_id, "http_error")
        return response

    def _same_origin_url(self, url: str) -> str:
        """自托管上传和产物必须来自配置的服务，不能重定向到其他主机。"""
        target, origin = urlsplit(url), urlsplit(self.server_url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc) or target.username or target.password or target.fragment:
            raise ValueError("MinerU 返回了非同源的上传或结果地址")
        return url

    async def _download_result(self, client: httpx.AsyncClient, url: str, output_dir: Path) -> str:
        """流式接收 ZIP，复用归档路径及图片完整性校验。"""
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise DocumentParserException(f"MinerU HTTP 请求失败: {response.status_code}", self.engine_id, "http_error")
            with tempfile.NamedTemporaryFile(suffix=".zip") as archive:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    archive.write(chunk)
                archive.flush()
                return extract_markdown_archive(archive.name, output_dir)
