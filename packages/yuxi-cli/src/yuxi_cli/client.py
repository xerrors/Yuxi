from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
