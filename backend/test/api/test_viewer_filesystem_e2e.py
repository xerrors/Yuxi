"""lite 模式下 viewer-oriented filesystem 端到端验证。"""

from __future__ import annotations

import asyncio
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


class ViewerFilesystemE2ETester:
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
            json={"agent_id": self.agent_id, "title": f"viewer-fs-e2e-{uuid.uuid4().hex[:8]}", "metadata": {}},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"create thread failed: {resp.status_code} {resp.text}")
        payload = resp.json()
        self.thread_id = str(payload.get("thread_id") or payload.get("id"))

    async def tree(self, path: str) -> list[dict]:
        assert self.headers and self.thread_id and self.agent_id
        resp = await self.client.get(
            "/api/viewer/filesystem/tree",
            params={"thread_id": self.thread_id, "path": path, "agent_id": self.agent_id},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"viewer tree failed on {path}: {resp.status_code} {resp.text}")
        return list(resp.json().get("entries") or [])

    async def file(self, path: str) -> str:
        assert self.headers and self.thread_id and self.agent_id
        resp = await self.client.get(
            "/api/viewer/filesystem/file",
            params={"thread_id": self.thread_id, "path": path, "agent_id": self.agent_id},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"viewer file failed on {path}: {resp.status_code} {resp.text}")
        return str(resp.json().get("content") or "")

    async def download(self, path: str) -> tuple[str, bytes]:
        assert self.headers and self.thread_id and self.agent_id
        resp = await self.client.get(
            "/api/viewer/filesystem/download",
            params={"thread_id": self.thread_id, "path": path, "agent_id": self.agent_id},
            headers=self.headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"viewer download failed on {path}: {resp.status_code} {resp.text}")
        return resp.headers.get("content-disposition", ""), resp.content

    async def run_case(self) -> None:
        await self.login()
        await self.pick_agent()
        await self.create_thread()

        assert self.thread_id
        provider = _get_provider()
        sandbox = await asyncio.to_thread(provider.acquire, self.thread_id)
        try:
            commands = [
                "mkdir -p /mnt/user-data/workspace /mnt/user-data/outputs",
                "printf 'print(42)\\n' > /mnt/user-data/workspace/demo.py",
                "printf 'root-file\\n' > /mnt/user-data/root_file.txt",
                "printf 'viewer-output\\n' > /mnt/user-data/outputs/result.txt",
            ]
            for command in commands:
                result = await asyncio.to_thread(sandbox.execute, command)
                if result.exit_code != 0:
                    raise RuntimeError(f"command failed: {command}\n{result.output}")

            root_paths = {str(e.get("path", "")) for e in await self.tree("/")}
            if "/mnt/user-data/" not in root_paths:
                raise RuntimeError(f"viewer root missing user-data: {sorted(root_paths)}")

            user_data_paths = {str(e.get("path", "")) for e in await self.tree("/mnt/user-data")}
            if "/mnt/user-data/root_file.txt" not in user_data_paths:
                raise RuntimeError(f"viewer user-data missing root_file.txt: {sorted(user_data_paths)}")

            workspace_paths = {str(e.get("path", "")) for e in await self.tree("/mnt/user-data/workspace")}
            if "/mnt/user-data/workspace/demo.py" not in workspace_paths:
                raise RuntimeError(f"viewer workspace missing demo.py: {sorted(workspace_paths)}")

            content = await self.file("/mnt/user-data/workspace/demo.py")
            if content != "print(42)\n":
                raise RuntimeError(f"unexpected viewer file content: {content!r}")

            content_disposition, payload = await self.download("/mnt/user-data/outputs/result.txt")
            if "result.txt" not in content_disposition:
                raise RuntimeError(f"unexpected content-disposition: {content_disposition}")
            if payload != b"viewer-output\n":
                raise RuntimeError(f"unexpected download payload: {payload!r}")

            print("[PASS] Viewer filesystem E2E completed")
            print(f"thread_id={self.thread_id}")
        finally:
            await asyncio.to_thread(provider.destroy, self.thread_id)


async def main() -> None:
    tester = ViewerFilesystemE2ETester()
    try:
        await tester.run_case()
    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(main())
