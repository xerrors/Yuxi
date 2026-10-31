"""以官方 Tasks 字段和独立产物验证自托管解析契约。"""

import io
import json
import zipfile

import pytest
import httpx

from yuxi.infrastructure.document_parsing import DocumentParserException, ParseOptions
from yuxi.infrastructure.document_parsing.parser import parse
from yuxi.infrastructure.document_parsing.engines.mineru import MinerUParser
from yuxi.infrastructure.document_parsing.engines.pp_structure_v3 import PPStructureV3Parser
from yuxi.infrastructure.document_parsing.mineru_options import migrate_mineru_options


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """固定协议回放在 HTTP transport 边界，保存原始上传字节而非调用次数。"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("markdown.md", "# Parsed\n![chart](images/chart.png)")
        output.writestr("images/chart.png", b"fixture-image")
    state = {"uploaded": None, "manifest": None, "canceled": False, "archive": archive.getvalue(), "calls": []}
    base = "http://mineru.test/prefix"

    async def request(request):
        """按固定远端契约响应，允许用例注入单个协议缺陷。"""
        method, url = request.method, str(request.url)
        state["calls"].append((method, url))
        status_code = 200
        body = {}
        if url == base + "/v1/tasks" and method == "POST":
            state["manifest"] = json.loads(request.content)
            if state.get("create_timeout"):
                raise httpx.ReadTimeout("creation outcome unknown")
            status_code = state.get("create_status", 202)
            body = {"task_id": "task-1", "status": "waiting-file", "files": [{"file_id": "file-1", "status": "waiting-file"}]}
            body.update(state.get("receipt", {}))
        elif url.endswith("/cancel"):
            state["canceled"] = True
            status_code = state.get("cancel_status", 200)
        elif url.endswith("/uploads/file-1"):
            state["uploaded"] = request.content
        elif url.endswith("/v1/tasks/task-1"):
            status = state.get("status") or ("completed" if state["uploaded"] or state.get("cached") else "waiting-file")
            file = {"file_id": "file-1", "status": status}
            if status == "waiting-file":
                file["upload"] = {"url": state.get("upload_url", base + "/uploads/file-1"), "headers": {}}
            if status == "completed":
                file["result_url"] = state.get("result_url", base + "/result")
            if status in {"failed", "canceled"}:
                file["error"] = {"code": "parse_failed", "message": "fixture parsing failure"}
            file.update(state.get("file", {}))
            body = {"task_id": state.get("task_id", "task-1"), "status": state.get("parent_status", status), "files": [file]}
        elif url.endswith("/result"):
            status_code = state.get("download_status", 200)
            return httpx.Response(status_code, content=state["archive"])
        elif url.endswith("/v1/health"):
            body = state.get("health", {"features": {"task_output_formats": ["default"]}})
        elif url.endswith("/v1/tiers"):
            body = {"data": [{"id": "standard"}, {"id": "advanced"}]}
        else:
            pytest.fail(f"unexpected protocol call: {method} {url}")
        return httpx.Response(status_code, json=body)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(request), **kwargs))
    source = tmp_path / "report.pdf"
    source.write_bytes(b"source-document")
    return state, MinerUParser(base + "/"), source, tmp_path / "output"


@pytest.mark.parametrize(
    ("params", "tier"), [({}, "standard"), ({"tier": "advanced"}, "advanced"), ({"backend": "hybrid-engine", "effort": "high"}, "advanced")]
)
def test_tasks_upload_and_publish_complete_result(wire, params, tier):
    """请求字段与返回的真实 Markdown/图片字节共同证明完整转换。"""
    state, parser, source, output = wire
    text = parser.process_file(str(source), output, {**params, "page_range": "1-2", "ocr_mode": "ocr"})
    assert state["manifest"] == {
        "files": [{"file_name": "report.pdf", "bytes": 15}],
        "tier": tier,
        "ocr_mode": "ocr",
        "page_range": "1-2",
        "output_formats": ["default"],
    }
    assert state["uploaded"] == b"source-document"
    assert text == "# Parsed\n![chart](images/images/chart.png)"
    assert (output / "images/images/chart.png").read_bytes() == b"fixture-image"
    assert not state["canceled"]


def test_cached_source_needs_no_upload(wire):
    """服务器复用源文件时仍通过查询取产物，不等待不存在的上传链接。"""
    state, parser, source, output = wire
    state["cached"] = True
    assert "Parsed" in parser.process_file(str(source), output)
    assert state["uploaded"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("has_markdown", [True, False])
async def test_blank_document_is_distinct_from_missing_document(wire, has_markdown):
    """空正文可以发布，缺少正文文件的归档必须失败并清理输出。"""
    state, parser, source, output = wire
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as result:
        result.writestr("markdown.md" if has_markdown else "metadata.json", "")
    state["archive"] = archive.getvalue()
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    options = ParseOptions("mineru_ocr", {"server_url": parser.server_url})
    if has_markdown:
        await parse(source, output, options)
        assert (output / "document.md").read_text() == ""
    else:
        with pytest.raises(DocumentParserException, match="未找到 .md 文件"):
            await parse(source, output, options)
        assert not output.exists()


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ({"task_id": "other-task"}, "身份不匹配"),
        ({"file": {"file_id": "other-file"}}, "身份不匹配"),
        ({"file": {"status": "surprise"}}, "未知状态"),
        ({"status": "completed", "parent_status": "running"}, "终态不一致"),
        ({"status": "failed"}, "fixture parsing failure"),
        ({"status": "canceled"}, "fixture parsing failure"),
        ({"upload_url": "http://other.test/upload"}, "非同源"),
        ({"cached": True, "result_url": "http://other.test/result"}, "非同源"),
        ({"cached": True, "result_url": None}, "非同源"),
        ({"cached": True, "archive": b"invalid zip"}, "zip"),
        ({"create_status": 200}, "HTTP 请求失败"),
        ({"cached": True, "download_status": 302}, "HTTP 请求失败"),
        ({"receipt": {"task_id": ""}}, "task_id"),
        ({"receipt": {"files": []}}, "unpack"),
    ],
)
def test_protocol_failure_never_publishes_success(wire, fault, message):
    """身份、状态、地址或归档不满足协议时不能产出成功正文。"""
    state, parser, source, output = wire
    state.update(fault)
    with pytest.raises(DocumentParserException, match=message):
        parser.process_file(str(source), output)
    assert not (output / "document.md").exists()


def test_timeout_cancels_known_task_and_preserves_timeout(wire):
    """即使远端取消失败，等待超时仍是明确结局。"""
    state, parser, source, output = wire
    state.update(status="running", cancel_status=500)
    with pytest.raises(DocumentParserException, match="超时") as error:
        parser.process_file(str(source), output, {"timeout_seconds": 0.001})
    assert error.value.status_code == "timeout"
    assert state["canceled"]


def test_unknown_creation_outcome_is_not_retried(wire):
    """创建超时没有可靠任务身份，不能重试或猜测取消对象。"""
    state, parser, source, output = wire
    state["create_timeout"] = True
    with pytest.raises(DocumentParserException, match="超时"):
        parser.process_file(str(source), output)
    assert state["calls"] == [("POST", "http://mineru.test/prefix/v1/tasks")]
    assert not state["canceled"]


@pytest.mark.parametrize(
    "params",
    [
        {"tier": "invalid"},
        {"ocr_mode": "invalid"},
        {"formula_enable": False},
        {"start_page_id": 2},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"backend": "pipeline"},
        {"effort": "extreme"},
    ],
)
def test_invalid_or_removed_options_fail_before_submission(wire, params):
    """不能把不可支持的旧选项静默丢弃或创建错误档位的任务。"""
    state, parser, source, output = wire
    with pytest.raises(DocumentParserException):
        parser.process_file(str(source), output, params)
    assert state["manifest"] is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"backend": "hybrid-engine", "effort": "high"}, "advanced"),
        ({"backend": "hybrid-engine", "effort": "medium"}, "standard"),
        ({"backend": "vlm-engine", "effort": "high"}, "standard"),
        ({"tier": "basic", "backend": "old-unknown"}, "basic"),
    ],
)
def test_legacy_options_migrate_once_and_preserve_other_fields(values, expected):
    """只迁移拥有明确语义的旧字段，显式新档位和其他字段不被覆盖。"""
    source = {**values, "server_url": "https://example.com/prefix"}
    result = migrate_mineru_options(source)
    assert result == {"tier": expected, "server_url": source["server_url"]}
    assert migrate_mineru_options(result) == result
    assert source == {**values, "server_url": "https://example.com/prefix"}


def test_health_checks_tasks_and_requested_tier(wire):
    """旧 API 的 health 成功不能证明 Tasks 可用。"""
    state, parser, _, _ = wire
    assert parser.check_health()["status"] == "healthy"
    parser.tier = "basic"
    assert "未开放" in parser.check_health()["message"]
    state["health"] = {"features": {"output_formats": ["zip"]}}
    assert "4.1" in parser.check_health()["message"]
    state["health"] = []
    assert parser.check_health()["status"] == "unhealthy"


def test_unconfigured_paddlex_never_contacts_localhost(monkeypatch):
    """移除内置服务后，缺配置不能意外调用其他 localhost 服务。"""
    monkeypatch.setattr("requests.post", lambda *a, **kw: pytest.fail("must not contact a default service"))
    parser = PPStructureV3Parser()
    assert parser.check_health()["status"] == "unavailable"
    with pytest.raises(DocumentParserException, match="地址未配置"):
        parser._call_layout_api("unused.pdf")


@pytest.mark.parametrize("slow_stage", ["create", "download"])
def test_total_deadline_interrupts_continuously_dripping_http(tmp_path, slow_stage):
    """持续有字节到达也必须超时，不能把 socket 空闲时限当成总预算。"""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    observed = []

    class DrippingServer(BaseHTTPRequestHandler):
        """故意每 20ms 返回一个字节，使完整响应远超任务预算。"""

        def respond(self, status, data, drip=False):
            """记录真实传输阶段，不用 mock 计数证明网络超时。"""
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if drip:
                observed.append(slow_stage)
                try:
                    for byte in data:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(data)

        def do_POST(self):
            """创建回执可在正文接收阶段超时。"""
            self.rfile.read(int(self.headers["Content-Length"]))
            self.respond(202, json.dumps({"task_id": "one", "files": [{"file_id": "one"}]}).encode(), slow_stage == "create")

        def do_GET(self):
            """完成状态之后的 ZIP 下载同样受总预算约束。"""
            if self.path == "/result":
                self.respond(200, b"slow-data" * 100, True)
                return
            self.respond(
                200,
                json.dumps(
                    {
                        "task_id": "one",
                        "status": "completed",
                        "files": [
                            {
                                "file_id": "one",
                                "status": "completed",
                                "result_url": f"http://{self.headers['Host']}/result",
                            }
                        ],
                    }
                ).encode(),
            )

        def log_message(self, *args):
            """测试无需访问日志。"""

    server = ThreadingHTTPServer(("127.0.0.1", 0), DrippingServer)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    source = tmp_path / "slow.pdf"
    source.write_bytes(b"source")
    output = tmp_path / "parsed"
    try:
        started = time.monotonic()
        with pytest.raises(DocumentParserException, match="超时") as error:
            MinerUParser(f"http://127.0.0.1:{server.server_port}").process_file(str(source), output, {"timeout_seconds": 0.15})
        elapsed = time.monotonic() - started
        assert error.value.status_code == "timeout"
        assert observed == [slow_stage]
        assert elapsed < 0.8, elapsed
        assert not output.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_synchronous_archive_extraction_cannot_publish_after_deadline(wire, monkeypatch):
    """同步解包阻塞事件循环时，返回前的时限检查仍须拒绝晚到结果。"""
    import time

    _, parser, source, output = wire

    def delayed_extract(*args):
        """恢复同步阶段超预算但成功发布的缺陷。"""
        time.sleep(0.03)
        return "late markdown"

    monkeypatch.setattr("yuxi.infrastructure.document_parsing.engines.mineru.extract_markdown_archive", delayed_extract)
    with pytest.raises(DocumentParserException, match="超时") as error:
        parser.process_file(str(source), output, {"timeout_seconds": 0.01})
    assert error.value.status_code == "timeout"
