"""lite 模式下的 sandbox 端到端验证。

流程：
1. 使用账号 zwj/zwj12138 登录
2. 创建 thread 并上传附件
3. 在进程内获取 sandbox provider，真实起容器并执行命令
4. 用 HTTP filesystem API 验证 root / uploads / workspace / outputs

运行：
    docker compose exec api uv run python test/api/test_sandbox_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _get_provider():
    from yuxi.agents.backends import get_sandbox_provider

    return get_sandbox_provider()


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5050").rstrip("/")
USERNAME = os.getenv("E2E_USERNAME", "zwj")
PASSWORD = os.getenv("E2E_PASSWORD", "zwj12138")
TIMEOUT = httpx.Timeout(180.0, connect=10.0)


class SandboxE2ETester:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=API_BASE_URL, timeout=TIMEOUT, follow_redirects=True)
        self.headers: dict[str, str] | None = None
        self.agent_id: str | None = None
        self.thread_id: str | None = None

    async def close(self):
        await self.client.aclose()

    async def login(self) -> None:
        resp = await self.client.post("/api/auth/token", data={"username": USERNAME, "password": PASSWORD})
        if resp.status_code != 200:
            raise RuntimeError(f"login failed: {resp.status_code} {resp.text}")
        self.headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    async def pick_agent(self) -> None:
        assert self.headers
        default_resp = await self.client.get("/api/chat/default_agent", headers=self.headers)
        if default_resp.status_code == 200 and default_resp.json().get("default_agent_id"):
            self.agent_id = str(default_resp.json()["default_agent_id"])
            return
        agents_resp = await self.client.get("/api/chat/agent", headers=self.headers)
        if agents_resp.status_code != 200:
            raise RuntimeError(f"failed to list agents: {agents_resp.status_code} {agents_resp.text}")
        agents = agents_resp.json().get("agents", [])
        if not agents:
            raise RuntimeError("no available agents")
        self.agent_id = str(agents[0]["id"])

    async def create_thread(self) -> None:
        assert self.headers and self.agent_id
        resp = await self.client.post(
            "/api/chat/thread",
            json={"agent_id": self.agent_id, "title": f"sandbox-e2e-{uuid.uuid4().hex[:8]}", "metadata": {}},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"create thread failed: {resp.status_code} {resp.text}")
        payload = resp.json()
        self.thread_id = str(payload.get("thread_id") or payload.get("id"))

    async def upload_attachment(self, file_name: str, content: bytes) -> dict:
        assert self.headers and self.thread_id
        resp = await self.client.post(
            f"/api/chat/thread/{self.thread_id}/attachments",
            files={"file": (file_name, content, "text/plain")},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"upload attachment failed: {resp.status_code} {resp.text}")
        return resp.json()

    async def ls(self, path: str) -> list[dict]:
        assert self.headers and self.thread_id
        resp = await self.client.get(
            "/api/filesystem/ls",
            params={"thread_id": self.thread_id, "path": path},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"filesystem ls failed on {path}: {resp.status_code} {resp.text}")
        entries = resp.json().get("entries")
        if not isinstance(entries, list):
            raise RuntimeError(f"invalid ls response: {resp.text}")
        return entries

    async def cat(self, path: str) -> str:
        assert self.headers and self.thread_id
        resp = await self.client.get(
            "/api/filesystem/cat",
            params={"thread_id": self.thread_id, "path": path},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"filesystem cat failed on {path}: {resp.status_code} {resp.text}")
        return str(resp.json().get("content") or "")

    async def run_case(self) -> None:
        await self.login()
        await self.pick_agent()
        await self.create_thread()

        attachment_name = f"sandbox_attach_{uuid.uuid4().hex[:6]}.txt"
        attachment_payload = await self.upload_attachment(attachment_name, b"attachment content for sandbox e2e")
        if attachment_payload.get("status") != "parsed":
            raise RuntimeError(
                "uploaded attachment status is not parsed: "
                + json.dumps(attachment_payload, ensure_ascii=False)
            )

        assert self.thread_id
        provider = _get_provider()
        sandbox = await asyncio.to_thread(provider.acquire, self.thread_id)

        commands = [
            "mkdir -p /mnt/user-data/workspace /mnt/user-data/outputs",
            "printf 'print([1, 2, 3])\\n' > /mnt/user-data/workspace/bubble_sort.py",
            "python /mnt/user-data/workspace/bubble_sort.py > /mnt/user-data/outputs/bubble_sort_result.txt",
            "printf 'sandbox-ok\\n' > /mnt/user-data/workspace/marker.txt",
        ]
        for command in commands:
            result = await asyncio.to_thread(sandbox.execute, command)
            if result.exit_code != 0:
                raise RuntimeError(f"command failed: {command}\n{result.output}")

        root_paths = sorted(str(e.get("path", "")) for e in await self.ls("/"))
        if root_paths != ["/mnt/skills/", "/mnt/user-data/"]:
            raise RuntimeError("unexpected root entries: " + json.dumps(root_paths, ensure_ascii=False))

        workspace_paths = {str(e.get("path", "")) for e in await self.ls("/mnt/user-data/workspace")}
        if "/mnt/user-data/workspace/bubble_sort.py" not in workspace_paths:
            raise RuntimeError("workspace file missing: " + json.dumps(sorted(workspace_paths), ensure_ascii=False))

        outputs_paths = {str(e.get("path", "")) for e in await self.ls("/mnt/user-data/outputs")}
        result_file = "/mnt/user-data/outputs/bubble_sort_result.txt"
        if result_file not in outputs_paths:
            raise RuntimeError("output file missing: " + json.dumps(sorted(outputs_paths), ensure_ascii=False))

        attachment_base = attachment_name.rsplit(".", 1)[0].replace("/", "_").replace("\\", "_")
        uploads_paths = {str(e.get("path", "")) for e in await self.ls("/mnt/user-data/uploads/attachments")}
        expected_attachment_path = f"/mnt/user-data/uploads/attachments/{attachment_base}.md"
        if expected_attachment_path not in uploads_paths:
            raise RuntimeError("attachment markdown missing: " + json.dumps(sorted(uploads_paths), ensure_ascii=False))

        content = await self.cat(result_file)
        if "[1, 2, 3]" not in content:
            raise RuntimeError(f"unexpected output file content: {content}")

        print("[PASS] Sandbox E2E completed")
        print(f"thread_id={self.thread_id}")
        print(f"result_file={result_file}")


async def main() -> None:
    tester = SandboxE2ETester()
    try:
        await tester.run_case()
    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(main())
