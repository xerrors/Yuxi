"""通过独立沙盒 HTTP 验证原生 grep 与模型工具结果。"""

import os
import uuid
from types import SimpleNamespace

import pytest
from agent_sandbox import Sandbox
from deepagents.backends import CompositeBackend
from langgraph.prebuilt.tool_node import ToolRuntime

import yuxi.agents.backends.sandbox.backend as backend_module
from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend
from yuxi.agents.backends.composite import create_agent_filesystem_middleware


@pytest.mark.asyncio
async def test_native_grep_http_and_model_tool(monkeypatch):
    """回读真实文件匹配与 ToolMessage，覆盖传输、过滤和沙盒路径语义。"""
    url = os.environ.get("TEST_SANDBOX_URL")
    if not url:
        pytest.skip("TEST_SANDBOX_URL requires an isolated sandbox")
    client = Sandbox(base_url=url)
    root = f"/tmp/yuxi-grep-{uuid.uuid4().hex}"
    user_root, skills_root = f"{root}/user-data", f"{root}/skills"
    client.shell.exec_command(command=f"mkdir -p {user_root}/nested {skills_root} {root}/outside")
    try:
        for path, content in {
            f"{user_root}/note:one.txt": "PEARL one\nPEARL two\n",
            f"{user_root}/nested/code.py": "a.b\naXb\n",
            f"{user_root}/.hidden": "HIDDEN pearl\n",
            f"{user_root}/long.txt": "LONG " + "x" * 31000 + "\n",
            f"{skills_root}/skill.md": "PEARL skill\n",
            f"{root}/outside/secret": "CONTAINER target\n",
        }.items():
            client.file.write_file(file=path, content=content)
        client.shell.exec_command(command=f"ln -s {root}/outside {user_root}/link")
        monkeypatch.setattr(backend_module, "_USER_DATA_ROOT", user_root)
        monkeypatch.setattr(backend_module, "_SKILLS_ROOT", skills_root)
        monkeypatch.setattr(backend_module, "get_sandbox_provider", lambda: object())
        backend = ProvisionerSandboxBackend(thread_id="probe", uid="probe")
        monkeypatch.setattr(backend, "_get_connection", lambda: SimpleNamespace(sandbox_url=url))
        monkeypatch.setattr(backend_module, "sandbox_provisioner_token", lambda: "probe-token")

        result = backend.grep("PEARL", max_count=3)
        assert result.error is None
        assert {(m["path"], m["line"], m["text"]) for m in result.matches} == {
            (f"{user_root}/note:one.txt", 1, "PEARL one"),
            (f"{user_root}/note:one.txt", 2, "PEARL two"),
            (f"{skills_root}/skill.md", 1, "PEARL skill"),
        }
        capped = backend.grep("PEARL", max_count=1)
        assert len(capped.matches) == 1 and capped.truncated
        literal = await backend.agrep("a.b", path=user_root, glob="nested/**/*.py")
        assert literal.matches == [{"path": f"{user_root}/nested/code.py", "line": 1, "text": "a.b"}]
        special_root = f"{user_root}/special[1]{{a,b}}*?"
        client.shell.exec_command(command=f"mkdir -p '{special_root}/nested'")
        client.file.write_file(file=f"{special_root}/nested/code.py", content="SPECIAL match\n")
        special = backend.grep("SPECIAL", path=special_root, glob="nested/**/*.py")
        assert special.matches == [{"path": f"{special_root}/nested/code.py", "line": 1, "text": "SPECIAL match"}]
        assert backend.grep("ABSENT").matches == []
        assert backend.grep("a.b", path=user_root, glob="*.py").matches == literal.matches
        client.file.write_file(file=f"{user_root}/many.txt", content="MANY match\n" * 501)
        default_cap = backend.grep("MANY", path=user_root)
        assert len(default_cap.matches) == 500 and default_cap.truncated
        explicit_cap = backend.grep("MANY", path=user_root, max_count=501)
        assert len(explicit_cap.matches) == 501
        assert backend.grep("HIDDEN", path=user_root).matches == []
        assert backend.grep("HIDDEN", path=user_root, glob=".*").matches
        assert backend.grep("LONG", path=user_root).matches[0]["text"] == "LONG " + "x" * 31000
        assert backend.grep("CONTAINER", path=f"{user_root}/link").matches[0]["text"] == "CONTAINER target"
        assert backend.grep("CONTAINER", path=f"{root}/outside").error
        assert backend.grep("CONTAINER", path=user_root, glob="../outside/*").error
        backend._max_output_bytes = 128
        assert backend.grep("LONG", path=user_root).error == "grep output exceeded sandbox limit"
        backend._max_output_bytes = 262144
        middleware = create_agent_filesystem_middleware(
            backend=CompositeBackend(default=backend, routes={}, artifacts_root=f"{user_root}/outputs")
        )
        tool = next(tool for tool in middleware.tools if tool.name == "grep")
        runtime = ToolRuntime(
            state={}, context=None, config={}, stream_writer=lambda _: None, tool_call_id="grep-probe", store=None
        )
        message = await tool.coroutine(pattern="PEARL", output_mode="content", max_count=1, runtime=runtime)
        assert message.status == "success"
        assert "PEARL" in message.content and "note:one.txt" in message.content
        client.shell.exec_command(command=f"rm -rf {skills_root}")
        missing_root = await backend.agrep("PEARL")
        assert missing_root.error is None and len(missing_root.matches) == 2
    finally:
        client.shell.exec_command(command=f"rm -rf {root}")
