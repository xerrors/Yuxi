"""个人助理真实执行与浏览器验收使用的确定性模型。"""

import json
import re
import time
from http.server import ThreadingHTTPServer

from test.support.cooperation_replay_server import CooperationReplayHandler
from test.support.openai_replay_server import send_json_response


class PersonalReplayHandler(CooperationReplayHandler):
    """验证实际工具目录，区分静默执行、用户发送和普通任务。"""

    def do_POST(self):
        """只按当前用户输入及真实工具结果推进场景。"""
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.headers.get("authorization") != "Bearer ci-replay-key":
            send_json_response(self, 401, {"error": "invalid_test_auth"})
            return
        messages = request["messages"]
        if request.get("stream") is False:
            self.write_summary_completion()
            return
        last = max(index for index, message in enumerate(messages) if message["role"] == "user")
        user = json.dumps(messages[last]["content"], ensure_ascii=False)
        system = "\n".join(str(message.get("content", "")) for message in messages if message["role"] == "system")
        results = {message.get("tool_call_id"): message.get("content") for message in messages[last + 1 :] if message["role"] == "tool"}
        functions = {tool["function"]["name"]: tool["function"] for tool in request.get("tools", [])}
        if "PERSONAL_TASK_FAIL" in user:
            send_json_response(self, 400, {"error": {"message": "test task failure", "type": "invalid_request_error"}})
            return
        if any(message["role"] == "user" and "PERSONAL_TASK_ASK" in str(message["content"]) for message in messages):
            if not any(message["role"] == "tool" and message.get("tool_call_id") == "task-question" for message in messages):
                self.write_completion(
                    [
                        (
                            "task-question",
                            "ask_user_question",
                            {
                                "questions": [
                                    {
                                        "question_id": "task-choice",
                                        "question": "请确认任务的输出格式",
                                        "options": [{"label": "简短记录", "value": "short"}, {"label": "详细说明", "value": "long"}],
                                    }
                                ]
                            },
                        )
                    ]
                )
            else:
                self.write_completion([], content="PERSONAL_ANSWERED_TASK_OK")
            return
        if "PERSONAL_TASK_WORK" in user:
            if "send_to_user" in functions or "PERSONAL_PRIVATE_PREFERENCE" in system:
                send_json_response(self, 422, {"error": "executor_inherited_assistant_configuration"})
                return
            if "task-write" not in results:
                workdir = re.search(r"当前 Project Workdir 为 ([^，\s]+)", system).group(1)
                self.write_completion(
                    [
                        (
                            "task-write",
                            "write_file",
                            {"file_path": workdir + "/personal-task-proof.txt", "content": "PERSONAL_EXECUTOR_FILE_OK"},
                        )
                    ]
                )
            else:
                self.write_completion([], content="PERSONAL_EXECUTOR_RESULT_OK")
            return
        if "send_to_user" in functions:
            forbidden = {"execute", "ask_user_question", "wait_inputs", "wait_sessions", "activate_skill", "query_kb"}
            if forbidden.intersection(functions) or set(functions["create_session"]["parameters"]["properties"]) != {
                "name",
                "description",
                "agent_id",
            }:
                send_json_response(self, 422, {"error": "personal_tool_boundary_failed"})
                return
            if "PERSONAL_SILENT" in user:
                self.write_completion([], content="PERSONAL_INTERNAL_SILENT_CONTENT")
                return
            if "PERSONAL_BLOCK" in user:
                time.sleep(6)
                self.write_completion([], content="PERSONAL_INTERNAL_BLOCK_CONTENT")
                return
            if "PERSONAL_DELEGATE" in user and "delegate-create" not in results:
                fail = "FAIL_TASK" in user
                ask = "ASK_TASK" in user
                self.write_completion(
                    [
                        (
                            "delegate-create",
                            "create_session",
                            {
                                "name": "personal_failure" if fail else "personal_question" if ask else "personal_research",
                                "description": "PERSONAL_TASK_FAIL"
                                if fail
                                else "PERSONAL_TASK_ASK"
                                if ask
                                else "PERSONAL_TASK_WORK 请在当前工作目录保存任务记录",
                            },
                        )
                    ]
                )
                return
            if "user-send" not in results:
                text = (
                    "任务已委派，可在右侧查看完整轨迹。"
                    if "PERSONAL_DELEGATE" in user
                    else ("已收到新的引导。" if "PERSONAL_FOLLOW" in user else "这是通过 send_to_user 发出的消息。")
                )
                self.write_completion([("user-send", "send_to_user", {"content": text})])
                return
            self.write_completion([], content="PERSONAL_INTERNAL_FINAL_CONTENT")
            return
        self.write_completion([], content="ORDINARY_CHAT_RESULT_OK")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8777), PersonalReplayHandler).serve_forever()
