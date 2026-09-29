from __future__ import annotations

import json
import secrets
import time
import uuid
import webbrowser
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from rich.console import Console

from yuxi_cli.client import ClientError, YuxiClient
from yuxi_cli.config import ConfigStore

MAX_MESSAGE_BYTES = 32 * 1024
LOOKUP_FAILURE_TIMEOUT = 60


class ChatWebError(Exception):
    """CLI Web Chat 无法启动或处理请求。"""


class ChatWebServer(ThreadingHTTPServer):
    """仅监听本机并代理 Yuxi Agent 请求的临时 HTTP 服务。"""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        client: YuxiClient,
        agent_slug: str,
        session_token: str,
    ):
        super().__init__(address, ChatRequestHandler)
        self.client = client
        self.agent_slug = agent_slug
        self.session_token = session_token

    @property
    def origin(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class ChatRequestHandler(BaseHTTPRequestHandler):
    """提供单页界面，并将聊天请求转换为浏览器可读的增量事件。"""

    server: ChatWebServer

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/":
            self.send_error(404)
            return

        template = files("yuxi_cli").joinpath("chat.html").read_text(encoding="utf-8")
        page = template.replace(
            "__SESSION_TOKEN__", json.dumps(self.server.session_token)
        ).replace("__AGENT_SLUG__", json.dumps(self.server.agent_slug))
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/chat":
            self.send_error(404)
            return
        if not self._is_local_request():
            self._send_json_error(403, "请求来源无效")
            return
        if not secrets.compare_digest(
            self.headers.get("X-Yuxi-Chat-Token", ""), self.server.session_token
        ):
            self._send_json_error(403, "会话令牌无效")
            return

        try:
            payload = self._read_payload()
            message = str(payload.get("message") or "").strip()
            if not message:
                raise ChatWebError("消息不能为空")
            thread_id = str(payload.get("thread_id") or "").strip() or None
            run = self.server.client.create_agent_chat_run(
                message=message,
                agent_slug=self.server.agent_slug,
                thread_id=thread_id,
                request_id=str(uuid.uuid4()),
            )
            if run.get("kind") == "command":
                command_name = str(run.get("command") or "")
                if command_name == "state":
                    self._write_command_response(run, thread_id=thread_id)
                    return
                if command_name == "approve":
                    run = run.get("run") if isinstance(run.get("run"), dict) else {}
            run_id = str(run.get("run_id") or "").strip()
            if not run_id and run.get("request_events_url"):
                run = self._wait_queued_run(run)
                run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                raise ChatWebError(str(run.get("error") or "远端未返回 run_id"))
        except (ChatWebError, ClientError, json.JSONDecodeError) as exc:
            self._send_json_error(400, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            self._write_event(
                {
                    "type": "meta",
                    "run_id": run_id,
                    "thread_id": run.get("thread_id"),
                }
            )
            for event in _browser_events(
                self.server.client.stream_agent_run_events(run_id),
                thread_id=str(run.get("thread_id") or "") or None,
            ):
                self._write_event(event)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ChatWebError, ClientError) as exc:
            if not run.get("result_url") or not run.get("request_id"):
                self._write_event({"type": "error", "message": str(exc)})
                return
            lookup_failure_started = None
            while True:
                try:
                    result = self.server.client.get_agent_request_result(
                        str(run["request_id"])
                    )
                except ClientError as lookup_error:
                    if (
                        lookup_error.status_code is not None
                        and lookup_error.status_code < 500
                        and lookup_error.status_code != 429
                    ):
                        self._write_event(
                            {"type": "error", "message": str(lookup_error)}
                        )
                        return
                    lookup_failure_started = lookup_failure_started or time.monotonic()
                    if (
                        time.monotonic() - lookup_failure_started
                        >= LOOKUP_FAILURE_TIMEOUT
                    ):
                        self._write_event(
                            {
                                "type": "error",
                                "message": f"结果查询持续失败，request_id={run['request_id']}: {lookup_error}",
                            }
                        )
                        return
                    time.sleep(1)
                    continue
                lookup_failure_started = None
                if result.get("run_id") != run_id:
                    self._write_event(
                        {"type": "error", "message": "请求结果与当前 Run 不匹配"}
                    )
                    return
                status = result.get("status")
                if status == "completed":
                    self._write_event(
                        {"type": "snapshot", "content": result.get("output") or ""}
                    )
                    self._write_event({"type": "done", "status": "completed"})
                    return
                if status == "waiting":
                    self._write_event(
                        {
                            "type": "approval_required",
                            "message": "等待工具审批，请输入 /approve 继续",
                        }
                    )
                    self._write_event({"type": "done", "status": "waiting_approval"})
                    return
                if status in {"failed", "cancelled", "rejected"}:
                    self._write_event(
                        {"type": "error", "message": str(result.get("error") or status)}
                    )
                    return
                time.sleep(1)

    def _write_command_response(
        self, response: dict[str, Any], *, thread_id: str | None
    ) -> None:
        """将不创建 Run 的 Channel command 结果返回给浏览器。"""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self._write_event({"type": "meta", "thread_id": thread_id})
        self._write_event(
            {
                "type": "command",
                "command": response.get("command"),
                "result": response.get("state") or response,
            }
        )
        self._write_event({"type": "done", "status": "completed"})

    def _wait_queued_run(self, response: dict[str, Any]) -> dict[str, Any]:
        """跟随 Request SSE，直到排队请求真正创建 Run。"""
        request_events_url = str(response.get("request_events_url") or "").strip()
        if not request_events_url:
            raise ChatWebError("远端未返回 request_events_url")

        request_id = str(response.get("request_id") or "").strip()
        lookup_failure_started = None
        while True:
            try:
                for event in self.server.client.stream_agent_request_events(
                    request_events_url
                ):
                    try:
                        data = json.loads(event.get("data") or "{}")
                    except json.JSONDecodeError as exc:
                        raise ChatWebError("远端返回了无效的排队事件") from exc
                    if not isinstance(data, dict):
                        continue

                    event_type = event.get("event") or "message"
                    if event_type == "run_created":
                        run_id = str(data.get("run_id") or "").strip()
                        if not run_id:
                            raise ChatWebError("排队事件缺少 run_id")
                        return {
                            **response,
                            "run_id": run_id,
                            "thread_id": data.get("thread_id")
                            or response.get("thread_id"),
                        }
                    if event_type in {"cancelled", "rejected", "failed", "error"}:
                        message = (
                            data.get("message") or data.get("status") or event_type
                        )
                        raise ChatWebError(f"排队请求结束：{message}")
            except ClientError as exc:
                if (
                    exc.status_code is not None
                    and exc.status_code < 500
                    and exc.status_code != 429
                ):
                    raise

            if not request_id:
                raise ChatWebError("排队事件流在创建 Run 前断开，且缺少 request_id")
            try:
                result = self.server.client.get_agent_request_result(request_id)
            except ClientError as exc:
                if (
                    exc.status_code is not None
                    and exc.status_code < 500
                    and exc.status_code != 429
                ):
                    raise
                lookup_failure_started = lookup_failure_started or time.monotonic()
                if time.monotonic() - lookup_failure_started >= LOOKUP_FAILURE_TIMEOUT:
                    raise ChatWebError(
                        f"排队状态查询持续失败，request_id={request_id}: {exc}"
                    ) from exc
                time.sleep(1)
                continue
            lookup_failure_started = None
            if result.get("run_id"):
                return {**response, "run_id": result["run_id"]}
            if result.get("status") in {"cancelled", "rejected", "failed"}:
                raise ChatWebError(
                    f"排队请求结束：{result.get('error') or result['status']}"
                )
            time.sleep(1)

    def _is_local_request(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == self.server.origin

    def _read_payload(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            raise ChatWebError("请求缺少 Content-Length")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ChatWebError("Content-Length 无效") from exc
        if length <= 0 or length > MAX_MESSAGE_BYTES:
            raise ChatWebError("消息过长")

        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ChatWebError("请求内容必须是 JSON 对象")
        return payload

    def _write_event(self, payload: dict[str, Any]) -> None:
        self.wfile.write(
            json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        self.wfile.flush()

    def _send_json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _browser_events(
    events: Iterator[dict[str, str]],
    *,
    thread_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """把远端 Run SSE 压缩为页面需要的文本增量与终态。"""
    saw_terminal = False
    waiting_for_approval = False

    for event in events:
        try:
            data = json.loads(event.get("data") or "{}")
        except json.JSONDecodeError as exc:
            raise ChatWebError("远端返回了无效的流事件") from exc
        if not isinstance(data, dict):
            continue
        if thread_id and data.get("thread_id") not in {None, thread_id}:
            continue

        event_type = event.get("event") or "message"
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        chunks = (
            payload.get("items")
            if isinstance(payload.get("items"), list)
            else [payload.get("chunk")]
        )
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            if (
                chunk.get("status") == "human_approval_required"
                and not waiting_for_approval
            ):
                waiting_for_approval = True
                yield {
                    "type": "approval_required",
                    "message": "等待工具审批，请输入 /approve 继续",
                }
            stream_event = chunk.get("stream_event")
            if (
                isinstance(stream_event, dict)
                and stream_event.get("type") == "message_delta"
            ):
                content = stream_event.get("content")
                if isinstance(content, str) and content:
                    yield {"type": "delta", "content": content}

        if event_type == "error":
            chunk = (
                payload.get("chunk") if isinstance(payload.get("chunk"), dict) else {}
            )
            if chunk.get("retryable") is True or payload.get("retryable") is True:
                continue
            message = (
                chunk.get("error_message")
                or chunk.get("message")
                or data.get("message")
                or "运行失败"
            )
            yield {"type": "error", "message": str(message)}
            return
        elif event_type == "end":
            saw_terminal = True
            status = str(payload.get("status") or "completed")
            if status == "interrupted" and waiting_for_approval:
                yield {"type": "done", "status": "waiting_approval"}
                continue
            if status != "completed":
                yield {"type": "error", "message": f"运行结束：{status}"}
            yield {"type": "done", "status": status}

    if not saw_terminal:
        raise ChatWebError("运行事件流在终态前断开，请重试")


def run_web_chat(
    store: ConfigStore,
    remote_name: str | None,
    agent_slug: str,
    console: Console,
    *,
    no_open: bool = False,
    open_browser: Callable[[str], bool] = webbrowser.open,
) -> None:
    """启动本地 Web Chat，直至用户按下 Ctrl+C。"""
    remote = store.load().get_remote(remote_name)
    if not remote.has_api_key:
        raise ChatWebError("当前 remote 尚未登录，请先运行 yuxi login")

    client = YuxiClient(remote)
    server = ChatWebServer(
        ("127.0.0.1", 0), client, agent_slug, secrets.token_urlsafe(24)
    )
    url = server.origin
    console.print(f"Web Chat: {url}")
    console.print("按 Ctrl+C 退出")
    if not no_open:
        open_browser(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nWeb Chat 已关闭")
    finally:
        server.server_close()
        client.close()
