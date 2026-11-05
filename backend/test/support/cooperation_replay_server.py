"""统一 Session 协作的确定性模型，检查子会话没有父历史。"""

import json
import re
import shlex
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUTPUT = "DETERMINISTIC_AGENT_E2E_OK"


def capacity_calls(messages, results):
    """重放满槽、兄弟通信和精确取消；工具结果决定下一步。"""
    users = " ".join(str(m["content"]) for m in messages if m["role"] == "user")
    gate = re.search(r'CAP_GATE:(/[^\s"\\]+)', users).group(1)
    if "COOP_CAPACITY_CHILD:" in users:
        if "capacity-delay" not in results:
            target_gate = gate if "COOP_CAPACITY_CHILD:4" in users else gate + "-ready"
            command = "python3 -c " + shlex.quote(
                f"import pathlib,time; p=pathlib.Path({target_gate!r}); "
                "[time.sleep(0.1) for _ in range(900) if not p.exists()]; assert p.exists(); print('released')"
            )
            return [("capacity-delay", "execute", {"command": command})]
        if "COOP_CAPACITY_CHILD:0" in users and "capacity-mail" not in results:
            if "capacity-list" not in results:
                return [("capacity-list", "list_sessions", {})]
            members = json.loads(results["capacity-list"])["sessions"]
            victim = next(member for member in members if member["path"] == "/root/worker4")
            return [
                ("capacity-mail", "send_message", {"target": "/root/worker1", "content": "SIBLING_INFORMATION"}),
                (
                    "capacity-input",
                    "submit_input",
                    {"target": "/root/worker1", "description": "COOP_CAPACITY_FOLLOWUP"},
                ),
                ("capacity-cancel", "cancel_turn", {"target": victim["session_id"], "turn_id": victim["turn_id"]}),
            ]
        if "COOP_CAPACITY_CHILD:0" in users and "capacity-release" not in results:
            return [("capacity-release", "write_file", {"file_path": gate, "content": "released"})]
        return []
    if "capacity-create-0" not in results:
        return [
            (
                f"capacity-create-{i}",
                "create_session",
                {"name": f"worker{i}", "description": f"COOP_CAPACITY_CHILD:{i} CAP_GATE:{gate}"},
            )
            for i in range(5)
        ]
    if "capacity-wait" not in results:
        return [
            (
                "capacity-wait",
                "wait_sessions",
                {
                    "targets": [json.loads(results[f"capacity-create-{i}"])["session_id"] for i in range(5)],
                    "after_cursor": 0,
                    "timeout_seconds": 60,
                },
            )
        ]
    return []


def user_scope_calls(messages, results):
    """独立顶层任务与派发方通过稳定 ID 通信、追加与等待。"""
    users = " ".join(str(message["content"]) for message in messages if message["role"] == "user")
    if "USER_SCOPE_CHILD" in users:
        assert "PRIVATE_ROOT_HISTORY" not in users
        if "USER_SCOPE_WRITE" in users and "user-write" not in results:
            return [
                (
                    "user-write",
                    "execute",
                    {
                        "command": "python3 -c \"from pathlib import Path; Path('cooperation-target-only.txt').write_text('TARGET_PROJECT_VERIFIED'); print(Path.cwd())\""
                    },
                )
            ]
        return []
    if "user-create" not in results:
        existing = re.search(r"USER_SCOPE_EXISTING:([0-9a-f-]+)", users)
        if existing:
            return [("user-create", "submit_input", {"target": existing.group(1), "description": "USER_SCOPE_CHILD USER_SCOPE_WRITE"})]
        return [("user-create", "create_session", {"name": "independent", "description": "USER_SCOPE_CHILD", "mode": "root"})]
    target = json.loads(results["user-create"])
    if "user-wait" not in results:
        return [("user-wait", "wait_inputs", {"input_ids": [target["input_id"]], "timeout_seconds": 60})]
    assert json.loads(results["user-wait"])["results"][0]["output"] == OUTPUT
    if "user-submit" not in results:
        return [
            ("user-message", "send_message", {"target": target["session_id"], "content": "CROSS_TREE_MESSAGE"}),
            ("user-submit", "submit_input", {"target": target["session_id"], "description": "USER_SCOPE_CHILD FOLLOWUP"}),
            ("user-list", "list_sessions", {"scope": "all"}),
        ]
    if "user-followup-wait" not in results:
        followup = json.loads(results["user-submit"])
        return [("user-followup-wait", "wait_inputs", {"input_ids": [followup["input_id"]], "timeout_seconds": 60})]
    if "user-updates" not in results:
        return [("user-updates", "wait_sessions", {"targets": [target["session_id"]], "timeout_seconds": 60})]
    if "user-exact" not in results:
        return [("user-exact", "get_result", {"input_id": target["input_id"]})]
    assert json.loads(results["user-exact"])["output"] == OUTPUT
    assert target["session_id"] in {member["session_id"] for member in json.loads(results["user-list"])["sessions"]}
    return []


def depth_calls(messages, results):
    """按真实工具回执推进三层派发，第三层必须被执行边界拒绝。"""
    users = " ".join(str(message["content"]) for message in messages if message["role"] == "user")
    depth = int(re.search(r"COOP_DEPTH:(\d+)", users).group(1))
    if "depth-create" not in results:
        return [("depth-create", "create_session", {"name": f"depth-{depth + 1}", "description": f"{OUTPUT} COOP_DEPTH:{depth + 1}"})]
    if depth == 2:
        assert "协作深度 3" in str(results["depth-create"]), "第三层协作没有因深度被拒绝"
        return []
    created = json.loads(results["depth-create"])
    if "depth-wait" not in results:
        return [("depth-wait", "wait_inputs", {"input_ids": [created["input_id"]], "timeout_seconds": 60})]
    assert all(item["terminal"] for item in json.loads(results["depth-wait"])["results"])
    return []


def parallel_wait_completion(messages, results):
    """两个子任务分别阻塞，父模型同轮等待两份精确结果。"""
    users = " ".join(str(message["content"]) for message in messages if message["role"] == "user")
    gate = re.search(r'WAIT_GATE:(/[^\s"\\]+)', users).group(1)
    child = re.search(r"COOP_PARALLEL_WAIT_CHILD:([01])", users)
    if child:
        index = int(child.group(1))
        output = f"PARALLEL_CHILD_RESULT_{index}"
        if "parallel-delay" not in results:
            command = "python3 -c " + shlex.quote(
                "from pathlib import Path\nimport time\n"
                f"gate = Path({f'{gate}-{index}'!r})\nstarted = Path({f'{gate}-{index}-started'!r})\n"
                "temporary = started.with_suffix('.tmp')\ntemporary.write_text('started')\ntemporary.replace(started)\n"
                "deadline = time.monotonic() + 120\n"
                "while not gate.exists() and time.monotonic() < deadline:\n    time.sleep(0.05)\n"
                f"assert gate.exists(), 'parallel wait gate was not released'\nprint({output!r})"
            )
            return [("parallel-delay", "execute", {"command": command})], output
        assert output in str(results["parallel-delay"]), "子任务没有通过自己的阻塞点"
        return [], output
    if "parallel-create-0" not in results:
        return [
            (
                f"parallel-create-{index}",
                "create_session",
                {"name": f"parallel{index}", "description": f"COOP_PARALLEL_WAIT_CHILD:{index} WAIT_GATE:{gate}"},
            )
            for index in range(2)
        ], OUTPUT
    created = [json.loads(results[f"parallel-create-{index}"]) for index in range(2)]
    if "parallel-wait-0" not in results and "parallel-wait-1" not in results:
        return [
            (f"parallel-wait-{index}", "wait_inputs", {"input_ids": [created[index]["input_id"]], "timeout_seconds": 180})
            for index in range(2)
        ], OUTPUT
    for index in range(2):
        wait = json.loads(results[f"parallel-wait-{index}"])
        assert wait["wait_timed_out"] is False, "并行等待不能以超时冒充任务完成"
        assert len(wait["results"]) == 1
        result = wait["results"][0]
        assert result["input_id"] == created[index]["input_id"] and result["terminal"], "等待结果不属于指定 Input"
        assert result["output"] == f"PARALLEL_CHILD_RESULT_{index}", "等待结果被另一个并行中断覆盖"
    return [], OUTPUT


class CooperationReplayHandler(BaseHTTPRequestHandler):
    """只接受协作测试声明的模型契约。"""

    def do_GET(self):
        """暴露确定性 fixture 的就绪事实。"""
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        """根据已提交的工具结果推进固定场景。"""
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        messages = request["messages"]
        serialized = json.dumps(messages, ensure_ascii=False)
        child = any(
            m["role"] == "user"
            and any(marker in str(m["content"]) for marker in ("COOPERATION_CHILD:", "COOP_CAPACITY_CHILD:", "COOP_PARALLEL_WAIT_CHILD:"))
            for m in messages
        )
        summary = (
            request.get("stream") is False
            and not request.get("tools")
            and len(messages) == 1
            and messages[0]["role"] == "user"
            and str(messages[0]["content"]).startswith("COOPERATION_SUMMARY_FIXTURE\n")
        )
        tools = {item["function"]["name"] for item in request.get("tools", [])}
        error = None
        if self.headers.get("authorization") != "Bearer ci-replay-key":
            error = "authorization"
        elif request["model"] != "deterministic-chat" or (not summary and request.get("stream") is not True):
            error = "model contract"
        elif not summary and (
            not {
                "create_session",
                "send_message",
                "submit_input",
                "cancel_turn",
                "wait_sessions",
                "wait_inputs",
                "get_result",
                "list_sessions",
                "list_agents",
            }
            <= tools
        ):
            error = "uniform tools missing"
        elif not summary and child and "PRIVATE_ROOT_HISTORY" in serialized:
            error = "parent context inherited"
        elif (
            not summary
            and child
            and "SELECTED_AGENT_CHILD" in serialized
            and ("COOP_TARGET_CONFIG" not in serialized or "COOP_PARENT_CONFIG" in serialized)
        ):
            error = "selected agent configuration missing"
        if error:
            body = json.dumps({"error": error}).encode()
            self.send_response(422)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if summary:
            self.write_summary_completion()
            return
        results = {m.get("tool_call_id"): m.get("content") for m in messages if m["role"] == "tool"}
        calls = []
        completion = OUTPUT
        if "USER_SCOPE_" in serialized:
            calls = user_scope_calls(messages, results)
        elif "COOP_PARALLEL_WAIT_" in serialized:
            calls, completion = parallel_wait_completion(messages, results)
        elif "COOP_DEPTH:" in serialized:
            calls = depth_calls(messages, results)
        elif "COOP_CAPACITY" in serialized:
            calls = capacity_calls(messages, results)
        else:
            calls = self.shared_sandbox_calls(serialized, child, results)
        self.write_completion(calls, content=completion)

    def shared_sandbox_calls(self, serialized, child, results):
        """按父子消息与工具结果验证同一共享路径。"""
        calls = []
        runtime_probe = re.search(r'COOP_RUNTIME:(/[^\s"\\]+)', serialized).group(1)
        if child:
            if "child-write" not in results:
                path = re.search(r'COOPERATION_CHILD:(/[^\s"\\]+)', serialized).group(1)
                calls = [
                    (
                        "child-probe",
                        "execute",
                        {"command": f"test -f {runtime_probe} && printf shared_runtime_verified"},
                    ),
                    ("child-write", "write_file", {"file_path": path, "content": "cooperation verified"}),
                ]
            elif "shared_runtime_verified" not in str(results.get("child-probe")):
                raise AssertionError("child did not read the dispatcher's shared file")
        elif "root-prepare" not in results:
            calls = [("root-prepare", "execute", {"command": f"printf shared > {runtime_probe}"})]
        elif "SELECT_AGENT" in serialized and "agent-directory" not in results:
            calls = [("agent-directory", "list_agents", {})]
        elif "create-worker" not in results:
            path = re.search(r'COOPERATION_ROOT:(/[^\s"\\]+)', serialized).group(1)
            arguments = {
                "name": "worker",
                "description": f"{OUTPUT} COOPERATION_CHILD:{path} COOP_RUNTIME:{runtime_probe}",
            }
            if "SELECT_AGENT" in serialized:
                directory = json.loads(results["agent-directory"])["agents"]
                target_id = re.search(r"SELECT_AGENT:([\w-]+)", serialized).group(1)
                target = next(role for role in directory if role["id"] == target_id and role["description"] == "COOP_TARGET_ROLE")
                arguments["agent_id"] = target["id"]
                arguments["description"] += " SELECTED_AGENT_CHILD"
            calls = [
                (
                    "create-worker",
                    "create_session",
                    arguments,
                )
            ]
            if "PARALLEL_QUESTION" in serialized:
                calls.append(
                    (
                        "parallel-question",
                        "ask_user_question",
                        {"questions": [{"question_id": "confirm", "question": "继续验证？"}]},
                    )
                )
        elif "wait-worker" not in results:
            created = json.loads(results["create-worker"])
            calls = [
                (
                    "wait-worker",
                    "wait_inputs",
                    {"input_ids": [created["input_id"]], "timeout_seconds": 60},
                )
            ]
        elif "exact-result" not in results:
            created = json.loads(results["create-worker"])
            wait = json.loads(results["wait-worker"])
            if not all(item["terminal"] for item in wait["results"]):
                raise AssertionError("task wait resumed before its submitted input finished")
            calls = [("exact-result", "get_result", {"input_id": created["input_id"]})]
        elif json.loads(results["exact-result"])["output"] != OUTPUT:
            raise AssertionError("exact child result was not returned")
        return calls

    def write_summary_completion(self):
        """响应明确标记的主动压缩请求，保持非流式 OpenAI completion 格式。"""
        body = json.dumps(
            {
                "id": "chatcmpl-cooperation-summary",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "deterministic-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "COOPERATION_SUMMARY_OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def write_completion(self, calls, *, content=OUTPUT):
        """返回固定 streaming 模型响应。"""
        common = {
            "id": "chatcmpl-cooperation",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "deterministic-chat",
        }
        delta = {"role": "assistant"}
        if calls:
            delta["tool_calls"] = [
                {
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for index, (call_id, name, args) in enumerate(calls)
            ]
        else:
            delta["content"] = content
        payloads = [
            {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for payload in payloads:
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *_args):
        """避免测试日志包含模型消息。"""


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8766), CooperationReplayHandler).serve_forever()
