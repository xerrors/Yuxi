from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from yuxi_cli.config import Remote, build_url


class ClientError(Exception):
    def __init__(self, message: str, *, error_code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


@dataclass
class CLIAuthSession:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

    @property
    def authorize_path(self) -> str:
        params = urlencode({"user_code": self.user_code})
        separator = "&" if "?" in self.verification_uri else "?"
        return f"{self.verification_uri}{separator}{params}"


class YuxiClient:
    def __init__(self, remote: Remote, timeout: float = 30.0):
        self.remote = remote
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> YuxiClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def health(self) -> dict:
        return self._request("GET", "/system/health", auth=False)

    def discovery(self) -> dict:
        return self._request("GET", "/system/discovery", auth=False)

    def me(self, api_key: str | None = None) -> dict:
        return self._request("GET", "/auth/me", api_key=api_key)

    def create_cli_session(self) -> CLIAuthSession:
        data = self._request("POST", "/auth/cli/sessions", json={}, auth=False)
        return CLIAuthSession(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            expires_in=int(data.get("expires_in") or 600),
            interval=int(data.get("interval") or 2),
        )

    def exchange_cli_token(self, device_code: str) -> dict:
        return self._request("POST", "/auth/cli/sessions/token", json={"device_code": device_code}, auth=False)

    def delete_api_key(self, api_key_id: str) -> dict:
        return self._request("DELETE", f"/user/apikey/{api_key_id}")

    def get_database(self, kb_id: str) -> dict:
        return self._request("GET", f"/knowledge/databases/{kb_id}")

    def list_databases(self) -> dict:
        return self._request("GET", "/knowledge/databases")

    def get_knowledge_base_types(self) -> dict:
        return self._request("GET", "/knowledge/types")

    def get_supported_file_types(self) -> dict:
        return self._request("GET", "/knowledge/files/supported-types")

    def knowledge_document_exists(self, kb_id: str, filename: str) -> bool:
        data = self._request(
            "GET",
            f"/knowledge/databases/{kb_id}/documents/exists",
            params={"filename": filename},
        )
        return bool(data.get("exists"))

    def upload_knowledge_file(self, kb_id: str, path: Path, *, timeout_seconds: float = 300) -> dict:
        with path.open("rb") as fp:
            return self._request(
                "POST",
                "/knowledge/files/upload",
                params={"kb_id": kb_id},
                files={"file": (path.name, fp, "application/octet-stream")},
                timeout=timeout_seconds,
            )

    def add_uploaded_documents(self, kb_id: str, items: list[str], params: dict) -> dict:
        return self._request(
            "POST",
            f"/knowledge/databases/{kb_id}/documents/add",
            json={"items": items, "params": params},
        )

    def list_external_databases(self, api_key: str | None = None) -> dict:
        """列出 external 知识库，也可使用尚未保存的 Key 验证访问。"""
        return self._request("GET", "/v1/knowledge/databases/external", api_key=api_key)

    def list_agents(self) -> dict:
        """读取当前用户可调用的主 Agent。"""
        return self._request("GET", "/agent")

    def get_agent(self, agent_slug: str) -> dict:
        """按 slug 读取当前用户可见的 Agent 配置。"""
        return self._request("GET", f"/agent/{quote(agent_slug, safe='')}")

    def list_external_files(
        self,
        kb_id: str,
        *,
        query: str | None = None,
        offset: int = 0,
        limit: int = 100,
        status: str = "all",
    ) -> dict:
        params: dict[str, Any] = {"offset": offset, "limit": limit, "status": status}
        if query:
            params["query"] = query
        return self._request("GET", f"/v1/knowledge/databases/external/{kb_id}/files", params=params)

    def retrieve_external(
        self,
        kb_id: str,
        *,
        query: str,
        file_name: str | None = None,
        options: dict | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"/v1/knowledge/databases/external/{kb_id}/retrieve",
            json={"query": query, "file_name": file_name, "options": options or {}},
        )

    def open_external_file(self, kb_id: str, file_id: str, *, offset: int = 0, limit: int = 200) -> dict:
        return self._request(
            "GET",
            f"/v1/knowledge/databases/external/{kb_id}/files/{file_id}/open",
            params={"offset": offset, "limit": limit},
        )

    def find_external_file(
        self,
        kb_id: str,
        file_id: str,
        *,
        patterns: list[str],
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ) -> dict:
        return self._request(
            "POST",
            f"/v1/knowledge/databases/external/{kb_id}/files/{file_id}/find",
            json={
                "patterns": patterns,
                "use_regex": use_regex,
                "case_sensitive": case_sensitive,
                "max_windows": max_windows,
                "window_size": window_size,
            },
        )

    def run_agent_eval(
        self,
        *,
        query: str,
        agent_slug: str,
        evaluation: dict,
        meta: dict | None = None,
        image_content: str | None = None,
        model_spec: str | None = None,
        timeout_seconds: float = 900,
    ) -> dict:
        payload = {
            "query": query,
            "agent_slug": agent_slug,
            "evaluation": evaluation,
            "meta": meta or {},
            "image_content": image_content,
            "model_spec": model_spec,
        }
        return self._request("POST", "/agent-invocation/eval/runs", json=payload, timeout=timeout_seconds)

    def create_agent_chat_run(
        self,
        *,
        message: str,
        agent_slug: str,
        thread_id: str | None,
        request_id: str,
    ) -> dict:
        """通过纯文本 Channel 入口发送 CLI Chat 消息。"""
        return self._request(
            "POST",
            "/agent-invocation/channel/messages",
            json={
                "channel": "cli",
                "account_id": self.remote.name,
                "chat_id": "cli" if thread_id else request_id,
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "message_id": request_id,
                "request_id": request_id,
                "message": {"type": "text", "text": message},
            },
        )

    def stream_agent_run_events(self, run_id: str) -> Iterator[dict[str, str]]:
        """读取 Agent Run SSE，并逐条返回解析后的事件。"""
        yield from self._stream_events(f"/agent/runs/{run_id}/events", params={"verbose": "false"})

    def stream_agent_request_events(self, request_events_url: str) -> Iterator[dict[str, str]]:
        """读取 Agent Request SSE，直到请求派发或进入终态。"""
        path = request_events_url.strip()
        if not path:
            raise ClientError("request_events_url 不能为空")
        if path.startswith("http://") or path.startswith("https://"):
            raise ClientError("request_events_url 必须是相对路径")
        if path.startswith("/api/"):
            path = path[4:]
        yield from self._stream_events(path)

    def get_agent_request_result(self, request_id: str) -> dict:
        """按请求 ID 读取持久状态，供事件流断开后恢复。"""
        return self._request("GET", "/agent/request-result", params={"request_id": request_id})

    def _stream_events(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, str]]:
        """连接远端 SSE 接口并返回解析后的事件。"""
        headers = {}
        if self.remote.api_key:
            headers["Authorization"] = f"Bearer {self.remote.api_key}"
        url = f"{self.remote.api_base_url}{path if path.startswith('/') else f'/{path}'}"

        try:
            with self.client.stream(
                "GET",
                url,
                headers=headers,
                params=params,
                timeout=None,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    error_code, error_message = _parse_http_error(response)
                    raise ClientError(
                        error_message,
                        error_code=error_code,
                        status_code=response.status_code,
                    )
                yield from _iter_sse_events(response.iter_lines())
        except ClientError:
            raise
        except httpx.HTTPError as exc:
            raise ClientError(f"运行事件流连接失败: {exc}") from exc

    def authorize_url(self, session: CLIAuthSession) -> str:
        return build_url(self.remote.url, session.authorize_path)

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        api_key: str | None = None,
        json: Any | None = None,
        params: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        headers = {}
        token = api_key if api_key is not None else self.remote.api_key
        if auth and token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.remote.api_base_url}{path if path.startswith('/') else f'/{path}'}"
        request_kwargs: dict[str, Any] = {"headers": headers}
        if params is not None:
            request_kwargs["params"] = params
        if files is not None:
            request_kwargs["files"] = files
        if data is not None:
            request_kwargs["data"] = data
        if json is not None:
            request_kwargs["json"] = json
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        try:
            response = self.client.request(method, url, **request_kwargs)
        except httpx.HTTPError as exc:
            # 网络层错误（连接失败、超时等）没有 HTTP 状态码，视为可重试的瞬时错误。
            raise ClientError(f"请求远程失败: {exc}") from exc

        if response.status_code >= 400:
            error_code, error_message = _parse_http_error(response)
            raise ClientError(error_message, error_code=error_code, status_code=response.status_code)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise ClientError("远程响应不是 JSON") from exc
        if not isinstance(data, dict):
            raise ClientError("远程响应格式无效")
        return data


def _parse_http_error(response: httpx.Response) -> tuple[str | None, str]:
    """解析远程错误，返回 (机器可读 error code, 人类可读 message)。"""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text.strip()

    if isinstance(detail, dict):
        error = detail.get("error")
        message = detail.get("message")
        if error and message:
            return str(error), f"{error}: {message}"
        if error:
            return str(error), str(error)
        if message:
            return None, str(message)
    if detail:
        return None, str(detail)
    return None, f"HTTP {response.status_code}"


def _iter_sse_events(lines: Iterator[str]) -> Iterator[dict[str, str]]:
    """解析 SSE 文本行，忽略 heartbeat，并保留 event、data 与 id。"""
    event: dict[str, str] = {}
    data_lines: list[str] = []

    for line in lines:
        if not line:
            if data_lines:
                event["data"] = "\n".join(data_lines)
                yield event
            event = {}
            data_lines = []
            continue
        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field in {"event", "id"}:
            event[field] = value

    if data_lines:
        event["data"] = "\n".join(data_lines)
        yield event
