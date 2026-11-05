"""为 assembled-path E2E 提供最小 OpenAI 兼容确定性响应。"""

from __future__ import annotations

import argparse
import json
import re
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlparse

EXPECTED_OUTPUT = "DETERMINISTIC_AGENT_E2E_OK"
EXPECTED_AUTHORIZATION = "Bearer ci-replay-key"
EXPECTED_MODEL = "deterministic-chat"
EXPECTED_PRELOADED_SKILL_MARKER = "# 图片生成技能"
EXPECTED_PRELOADED_TOOL = "present_artifacts"
EXPECTED_TOOL_CALL_ID = "call-preloaded-tool"
EXPECTED_TOOL_RESULT_MARKER = "已将交付物展示给用户"
BLOCK_BEFORE_RESPONSE_MARKER = "DETERMINISTIC_BLOCK_BEFORE_RESPONSE"
TOOL_ERROR_MARKER = "DETERMINISTIC_TOOL_ERROR"
LARGE_TOOL_RESULT_MARKER = "DETERMINISTIC_LARGE_TOOL_RESULT"
LARGE_TOOL_CALL_ID = "call-large-tool-result"
BLOCKING_REQUEST_TOKENS: set[str] = set()
BLOCKING_REQUEST_TOKENS_LOCK = Lock()
BLOCKING_GATES: dict[str, Event] = {}


def send_json_response(handler, status: int, payload: dict) -> None:
    """发送确定性 JSON 响应。"""
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def send_openai_sse_response(handler, payloads: list[dict], *, headers=(), flush_each: bool = True, after_first_payload=None) -> None:
    """发送 OpenAI SSE 事件，支持首块后的场景专属同步点。"""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    for name, value in headers:
        handler.send_header(name, value)
    handler.end_headers()
    for index, payload in enumerate(payloads):
        handler.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        if flush_each or index == 0 and after_first_payload is not None:
            handler.wfile.flush()
        if index == 0 and after_first_payload is not None:
            after_first_payload()
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()


@contextmanager
def serve_replay_handler(handler_class, *, host: str = "0.0.0.0", api_host: str = "api"):
    """启动临时 HTTP replay，并在退出时关闭服务线程。"""
    server = ThreadingHTTPServer((host, 0), handler_class)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{api_host}:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def validate_request(authorization: str | None, request: dict) -> str | None:
    """拒绝没有走预期模型适配契约的 replay 请求。"""

    if authorization != EXPECTED_AUTHORIZATION:
        return "invalid_authorization"
    if request.get("model") != EXPECTED_MODEL:
        return "invalid_model"
    if request.get("stream") is not True:
        return "stream_required"
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return "messages_required"
    serialized_messages = json.dumps(messages, ensure_ascii=False)
    if EXPECTED_OUTPUT not in serialized_messages:
        return "expected_input_missing"
    feedback_flow = "DETERMINISTIC_FEEDBACK" in serialized_messages
    if not feedback_flow and EXPECTED_PRELOADED_SKILL_MARKER not in serialized_messages:
        return "preloaded_skill_missing"
    if "DETERMINISTIC_FROZEN_CONFIG" in serialized_messages:
        system_messages = " ".join(str(message.get("content", "")) for message in messages if message.get("role") == "system")
        if "FROZEN_SESSION_PROMPT" not in system_messages or "CHANGED_DEFINITION_PROMPT" in system_messages:
            return "session_config_snapshot_mismatch"
    bound_roots = re.findall(r"DETERMINISTIC_BOUND_ROOT:([0-9a-f]+)", serialized_messages)
    if bound_roots and f"BOUND_SKILL_{bound_roots[-1]}" not in serialized_messages:
        return "bound_root_snapshot_mismatch"
    user_messages = [message for message in messages if message.get("role") == "user"]
    if "DETERMINISTIC_ATTACHMENT_BATCH" in serialized_messages:
        batch = [message for message in user_messages if "DETERMINISTIC_ATTACHMENT_BATCH" in json.dumps(message.get("content"))]
        markers = [re.findall(r"BATCH_INPUT_[01]_MESSAGE_[01]", json.dumps(message.get("content"))) for message in batch]
        if markers != [[f"BATCH_INPUT_{input_index}_MESSAGE_{position}"] for input_index in range(2) for position in range(2)]:
            return "attachment_batch_messages_missing_or_reordered"
        for index, message in enumerate(batch):
            content = message.get("content")
            images = (
                [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]
                if isinstance(content, list)
                else []
            )
            if len(images) != (1 if index % 2 == 0 else 0):
                return "attachment_batch_image_position_mismatch"
    for message in user_messages:
        content = json.dumps(message.get("content"), ensure_ascii=False)
        anchor = re.search(r"DETERMINISTIC_ATTACHMENT_ANCHOR:([a-z0-9.-]+)", content)
        if anchor:
            contexts = attachment_contexts(content)
            expected = anchor.group(1)
            if expected == "none" and contexts:
                return "attachment_on_non_anchor_message"
            if expected != "none" and (len(contexts) != 1 or expected not in contexts[0]):
                return "attachment_anchor_missing"
            if expected != "none" and any(name != expected for name in re.findall(r"batch-[01]\.txt", contexts[0])):
                return "attachment_cross_input_anchor"
    current_content = json.dumps(user_messages[-1].get("content"), ensure_ascii=False)
    forbidden = re.findall(r"DETERMINISTIC_ATTACHMENT_ABSENT:([a-z0-9.-]+)", current_content)
    contexts = attachment_contexts(current_content)
    if any(name in context for name in forbidden for context in contexts):
        return "future_attachment_in_current_input"
    if "DETERMINISTIC_PNG_INPUT" in serialized_messages:
        image_urls = [
            part.get("image_url", {}).get("url", "")
            for message in user_messages
            for part in message.get("content", [])
            if isinstance(message.get("content"), list) and isinstance(part, dict) and part.get("type") == "image_url"
        ]
        if not any(url.startswith("data:image/png;base64,") for url in image_urls):
            return "png_input_mime_changed"
    tools = request.get("tools")
    tool_names = {
        item.get("function", {}).get("name") for item in tools or [] if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
    if feedback_flow:
        feedback_tool = next(
            (item["function"] for item in tools or [] if item.get("function", {}).get("name") == "mark_for_improvement"), None
        )
        if feedback_tool is None or set(feedback_tool["parameters"]["properties"]) != {"reason"}:
            return "feedback_tool_schema_mismatch"
        if any(message.get("tool_call_id") != "call-improvement" for message in tool_messages):
            return "feedback_call_id_mismatch"
        if (
            "DETERMINISTIC_FEEDBACK_INVALID" not in serialized_messages
            and tool_messages
            and not any('"saved": true' in str(message["content"]) for message in tool_messages)
        ):
            return "feedback_not_saved"
        return None
    if "DETERMINISTIC_CREATE_MCP" in serialized_messages and "effect_probe" not in tool_names:
        return "creation_mcp_tool_missing"
    if "DETERMINISTIC_CANCEL_FOLLOWUP" in serialized_messages:
        return None
    question_flow = "DETERMINISTIC_ASK_USER" in serialized_messages
    if question_flow:
        if "ask_user_question" not in tool_names:
            return "ask_user_question_missing"
        if any(message.get("tool_call_id") != "call-ask-user" for message in tool_messages):
            return "ask_user_question_result_mismatch"
        return None
    if EXPECTED_PRELOADED_TOOL not in tool_names:
        return "preloaded_tool_missing"
    if LARGE_TOOL_RESULT_MARKER in serialized_messages and "execute" not in tool_names:
        return "execute_tool_missing"
    if tool_messages and not any(
        (
            message.get("tool_call_id") == EXPECTED_TOOL_CALL_ID
            and (EXPECTED_TOOL_RESULT_MARKER in str(message.get("content", "")) or TOOL_ERROR_MARKER in serialized_messages)
        )
        or (
            LARGE_TOOL_RESULT_MARKER in serialized_messages
            and message.get("tool_call_id") == LARGE_TOOL_CALL_ID
            and "Tool result too large" in str(message.get("content", ""))
        )
        for message in tool_messages
    ):
        return "tool_execution_result_missing"
    return None


def attachment_contexts(content: str) -> list[str]:
    """线性提取闭合的附件标签，保留非贪婪匹配的嵌套与未闭合语义。"""
    opening, closing = "<attachment_context>", "</attachment_context>"
    contexts = []
    cursor = 0
    while True:
        start = content.find(opening, cursor)
        if start < 0:
            return contexts
        start += len(opening)
        end = content.find(closing, start)
        if end < 0:
            return contexts
        contexts.append(content[start:end])
        cursor = end + len(closing)


def _stream_payloads(model: str, messages: list[dict]) -> list[dict]:
    serialized_messages = json.dumps(messages, ensure_ascii=False)
    common = {
        "id": "chatcmpl-yuxi-deterministic",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    tool_results = {message.get("tool_call_id"): message.get("content") for message in messages if message.get("role") == "tool"}
    if "DETERMINISTIC_CANCEL_FOLLOWUP" in serialized_messages:
        return [
            {
                **common,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": EXPECTED_OUTPUT}, "finish_reason": None}],
            },
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        ]
    if tool_results:
        return [
            {
                **common,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": EXPECTED_OUTPUT},
                        "finish_reason": None,
                    }
                ],
            },
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        ]

    large_result = LARGE_TOOL_RESULT_MARKER in serialized_messages
    tool_call_id = LARGE_TOOL_CALL_ID if large_result else EXPECTED_TOOL_CALL_ID
    tool_name = "execute" if large_result else EXPECTED_PRELOADED_TOOL
    if "DETERMINISTIC_FEEDBACK" in serialized_messages:
        tool_call_id, tool_name = "call-improvement", "mark_for_improvement"
        tool_arguments = json.dumps(
            {
                "reason": " "
                if "DETERMINISTIC_FEEDBACK_INVALID" in serialized_messages
                else "多次尝试后确认批量读取更可靠；依据是本轮工具记录。"
            },
            ensure_ascii=False,
        )
    elif "DETERMINISTIC_ASK_USER_INVALID" in serialized_messages:
        tool_call_id, tool_name = "call-ask-user", "ask_user_question"
        tool_arguments = json.dumps({"questions": [{"question": "选择风格？", "multi_select": "false"}]})
    elif "DETERMINISTIC_ASK_USER" in serialized_messages:
        tool_call_id, tool_name = "call-ask-user", "ask_user_question"
        tool_arguments = json.dumps(
            {
                "questions": [
                    {"question_id": "q-1", "question": "第一题？"},
                    {"question_id": "q-2", "question": "第二题？"},
                ]
            },
            ensure_ascii=False,
        )
    elif large_result:
        tool_arguments = json.dumps({"command": "yes X | head -c 13000"})
    elif TOOL_ERROR_MARKER in serialized_messages:
        tool_arguments = "{}"
    else:
        tool_arguments = '{"filepaths": []}'

    payloads = [
        {
            **common,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": tool_arguments,
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            **common,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        },
    ]

    return payloads


class ReplayHandler(BaseHTTPRequestHandler):
    """只实现测试所需的 health 与 chat completions 协议。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/release-blocking":
            token = parse_qs(parsed.query).get("token", [""])[0]
            with BLOCKING_REQUEST_TOKENS_LOCK:
                BLOCKING_GATES.setdefault(token, Event()).set()
            self._write_json(200, {"released": True})
            return
        if parsed.path == "/health":
            self._write_json(200, {"status": "ok"})
            return
        if parsed.path == "/blocking-started":
            token = (parse_qs(parsed.query).get("token") or [""])[0]
            with BLOCKING_REQUEST_TOKENS_LOCK:
                started = token in BLOCKING_REQUEST_TOKENS
            self._write_json(200, {"started": started})
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._write_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            self._write_json(400, {"error": "invalid_json"})
            return

        request_error = validate_request(self.headers.get("authorization"), request)
        if request_error:
            self._write_json(422, {"error": request_error})
            return

        messages = request["messages"]
        serialized_messages = json.dumps(messages, ensure_ascii=False)
        last_user = max(index for index, message in enumerate(messages) if message.get("role") == "user")
        current_input = json.dumps(messages[last_user], ensure_ascii=False)
        if "DETERMINISTIC_RATE_LIMIT" in current_input:
            messages = [message for message in messages[:last_user] if message.get("role") == "system"] + messages[last_user:]
            has_tool_result = any(message.get("role") == "tool" for message in messages)
            if has_tool_result or "RATE_LIMIT_FIRST_CALL" in current_input:
                self._write_json(
                    429,
                    {"error": {"message": "DETERMINISTIC_RATE_LIMIT exhausted", "type": "rate_limit_error"}},
                )
                return
        blocking_match = re.search(rf"{BLOCK_BEFORE_RESPONSE_MARKER}:([0-9a-f-]+)", serialized_messages)
        model = str(request["model"])
        payloads = _stream_payloads(model, messages)
        if blocking_match:
            token = blocking_match.group(1)

            def wait_after_first_payload() -> None:
                with BLOCKING_REQUEST_TOKENS_LOCK:
                    BLOCKING_REQUEST_TOKENS.add(token)
                    gate = BLOCKING_GATES.setdefault(token, Event())
                gate.wait(60)

            after_first_payload = wait_after_first_payload
        else:
            after_first_payload = None
        send_openai_sse_response(
            self,
            payloads,
            headers=(("Cache-Control", "no-cache"), ("Connection", "close")),
            after_first_payload=after_first_payload,
        )
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status: int, payload: dict) -> None:
        send_json_response(self, status, payload)


@contextmanager
def permission_replay_server():
    """提供带可控阻塞点的权限场景 OpenAI replay。"""
    entered, release = Event(), Event()
    observations = []
    action = {}

    class Handler(BaseHTTPRequestHandler):
        """返回工具调用、终态文本或指定模型错误。"""

        def do_POST(self):  # noqa: N802
            """按测试动作返回 OpenAI SSE 或错误响应。"""
            request = json.loads(self.rfile.read(int(self.headers["content-length"])))
            observations.append(request)
            assert self.headers["authorization"] == "Bearer ci-replay-key"
            assert request["model"] == "deterministic-chat"
            failure = action.get("failure")
            if failure and len(observations) == 1:
                entered.set()
                assert release.wait(60)
                send_json_response(
                    self,
                    503 if failure == "network" else 400,
                    {
                        "error": {
                            "message": "service unavailable" if failure == "network" else "context length exceeded",
                            "type": "server_error" if failure == "network" else "invalid_request_error",
                            "code": "service_unavailable" if failure == "network" else "context_length_exceeded",
                        }
                    },
                )
                return
            if action.get("plain") and not request.get("stream"):
                send_json_response(
                    self,
                    200,
                    {
                        "id": "chatcmpl-summary",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "deterministic-chat",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Summary"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                )
                return
            has_result = any(message["role"] == "tool" for message in request["messages"])
            if action.get("plain"):
                delta = {"role": "assistant", "content": "PERMISSION_CHECK_COMPLETE"}
            elif not has_result:
                assert action["name"] in {tool["function"]["name"] for tool in request["tools"]}
                entered.set()
                assert release.wait(60)
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-permission",
                            "type": "function",
                            "function": {"name": action["name"], "arguments": json.dumps(action["arguments"])},
                        }
                    ],
                }
            else:
                delta = {"role": "assistant", "content": "PERMISSION_CHECK_COMPLETE"}
            common = {
                "id": "chatcmpl-permission",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "deterministic-chat",
            }
            send_openai_sse_response(
                self,
                [
                    {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {
                        **common,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop" if has_result or action.get("plain") else "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                ],
                flush_each=False,
            )

        def log_message(self, *args):
            """测试不输出请求日志。"""

    with serve_replay_handler(Handler) as url:
        try:
            yield {
                "url": url,
                "entered": entered,
                "release": release,
                "requests": observations,
                "action": action,
            }
        finally:
            release.set()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), ReplayHandler).serve_forever()


if __name__ == "__main__":
    main()
