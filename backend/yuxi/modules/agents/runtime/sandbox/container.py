"""每 Run 一个用完即弃容器的轻量执行后端。"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from datetime import datetime

import httpx

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_int_env
from yuxi.modules.agents.repositories.environment import AgentEnvRepository
from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.modules.agents.runtime.sandbox.context import sandbox_run_env, sandbox_run_owner
from yuxi.modules.workspace.paths import normalize_workdir_path, workspace_uid_dirname
from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH
from yuxi.shared.datetime import format_utc_datetime, utc_now
from yuxi.shared.errors import StateConflictError, UpstreamError

MAX_STDIN_BYTES = 100 * 1024 * 1024


class SandboxRunnerClient:
    """通过窄 HTTP 契约访问持有 Docker socket 的独立 runner。"""

    def __init__(self, *, base_url: str | None = None, token: str | None = None):
        """地址与令牌只从部署配置取得，不接受模型输入。"""
        self.base_url = (base_url or os.environ.get("SANDBOX_RUNNER_URL", "http://sandbox-runner:8090")).rstrip("/")
        self.token = token if token is not None else os.environ.get("SANDBOX_RUNNER_TOKEN", "")

    def health(self) -> dict:
        """通过认证端点读取 runner 的 Docker、镜像和配置就绪状态。"""
        payload = self._json("GET", "/v1/health", timeout=10)
        if payload.get("ok") is not True:
            raise UpstreamError("容器执行服务健康检查返回无效结果")
        return payload

    def fetch_skills(self, *, uid: str, request_id: str, args: list[str]) -> dict:
        """专用 CLI 获取请求不创建业务 Run，也不接受任意 shell 命令。"""
        return self._json("POST", f"/v1/skill-fetches/{request_id}", timeout=390, payload={"uid": uid, "args": args})

    def execute(
        self,
        *,
        uid: str,
        run_id: str,
        owner: str,
        command_id: str,
        command: str,
        cwd: str,
        workdir: str | None,
        timeout_seconds: int,
        output_budget: int,
        stdin_data: bytes | None,
        python: bool,
        envs: dict[str, str],
    ) -> dict:
        """执行一条命令，runner 在超时、取消或容器删除时结束进程组。"""
        payload = {
            "uid": uid,
            "owner": owner,
            "command_id": command_id,
            "command": command,
            "cwd": cwd,
            "workdir": workdir,
            "timeout_seconds": timeout_seconds,
            "output_budget": output_budget,
            "stdin_b64": base64.b64encode(stdin_data).decode() if stdin_data is not None else None,
            "python": python,
            "envs": envs,
        }
        return self._json(
            "POST",
            f"/v1/runs/{run_id}/exec",
            timeout=timeout_seconds + 90,
            payload=payload,
            read_timeout=timeout_seconds + 90,
            write_timeout=90,
        )

    def cancel_exec(self, *, uid: str, run_id: str, owner: str, command_id: str) -> bool:
        """只终止指定命令的进程组，不影响同 Run 其他命令。"""
        payload = self._json(
            "POST",
            f"/v1/runs/{run_id}/exec/{command_id}/cancel",
            timeout=15,
            payload={"uid": uid, "owner": owner},
        )
        return payload.get("cancelled") is True

    def remove_run(self, *, uid: str, run_id: str, owner: str | None = None, timeout: int = 60) -> dict:
        """删除 Run 容器；owner 存在时只允许当前 attempt 删除。"""
        params = {"uid": uid}
        if owner:
            params["owner"] = owner
        return self._json("DELETE", f"/v1/runs/{run_id}", timeout=timeout, params=params, allow_404=True)

    def remove_container(self, container_id: str) -> None:
        """清理 label 异常但仍由 runner 标记为受管的容器。"""
        self._request("DELETE", f"/v1/containers/{container_id}", timeout=30, allow_404=True)

    def list(self) -> list[dict]:
        """列出 runner 标记为受管的全部容器。"""
        payload = self._json("GET", "/v1/runs", timeout=15)
        containers = payload.get("containers") if isinstance(payload, dict) else None
        if not isinstance(containers, list) or any(not isinstance(item, dict) for item in containers):
            raise UpstreamError("容器执行服务返回了无效的容器列表")
        return containers

    def stats(self) -> dict[str, int]:
        """读取受管容器计数。"""
        payload = self._json("GET", "/v1/stats", timeout=15)
        active = payload.get("active_containers")
        total = payload.get("total_containers")
        if not isinstance(active, int) or not isinstance(total, int) or active < 0 or total < active:
            raise UpstreamError("容器执行服务返回了无效的统计结果")
        return {"active_vms": active, "total_vms": total}

    def _json(
        self,
        method: str,
        path: str,
        *,
        timeout: int,
        payload: dict | None = None,
        params: dict | None = None,
        allow_404: bool = False,
        read_timeout: int | None = None,
        write_timeout: int | None = None,
    ) -> dict:
        """只接受 JSON 对象，避免把协议错误当成执行成功。"""
        response = self._request(
            method,
            path,
            timeout=timeout,
            payload=payload,
            params=params,
            allow_404=allow_404,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
        )
        if not response.content:
            return {}
        try:
            value = response.json()
        except json.JSONDecodeError as exc:
            raise UpstreamError("容器执行服务返回了无效 JSON") from exc
        if not isinstance(value, dict):
            raise UpstreamError("容器执行服务返回了无效结果")
        return value

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: int,
        payload: dict | None = None,
        params: dict | None = None,
        allow_404: bool = False,
        read_timeout: int | None = None,
        write_timeout: int | None = None,
    ) -> httpx.Response:
        """统一认证、超时与错误分类，不把敏感请求体写进错误。"""
        if not self.token.strip():
            raise UpstreamError("SANDBOX_RUNNER_TOKEN 未配置")
        request_timeout = httpx.Timeout(
            connect=min(5, timeout),
            read=read_timeout or timeout,
            write=write_timeout or timeout,
            pool=10,
        )
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=request_timeout,
                trust_env=False,
                headers={"Authorization": f"Bearer {self.token}"},
            ) as client:
                request_kwargs = {"params": params}
                if payload is not None:
                    request_kwargs["json"] = payload
                response = client.request(method, path, **request_kwargs)
        except httpx.TimeoutException as exc:
            raise UpstreamError("容器执行服务响应超时") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("容器执行服务不可用") from exc
        if response.status_code == 404 and allow_404:
            return response
        if response.status_code == 429:
            raise StateConflictError("容器执行并发已达上限，请稍后重试", code="sandbox_capacity")
        if response.status_code == 409:
            raise StateConflictError(self._error_detail(response, "容器状态冲突"), code="sandbox_conflict")
        if response.status_code in {401, 403}:
            raise UpstreamError("容器执行服务认证失败")
        if response.status_code >= 400:
            raise UpstreamError(self._error_detail(response, f"容器执行服务拒绝请求（HTTP {response.status_code}）"))
        return response

    @staticmethod
    def _error_detail(response: httpx.Response, fallback: str) -> str:
        """只返回 runner 的受控短消息，不回传请求或凭据。"""
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return fallback
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:300]
        return fallback


class ContainerSandboxProvider:
    """每个 Run 懒创建容器，Run 内复用，终态删除。"""

    backend = "container"

    def __init__(self, client: SandboxRunnerClient | None = None, repository: SandboxRepository | None = None):
        """runner 持有 Docker socket，本进程只保存无状态 HTTP 客户端。"""
        self.client = client or SandboxRunnerClient()
        self.repository = repository or SandboxRepository()

    def health(self) -> dict:
        """启动 readiness 核对 runner、Docker、镜像和挂载根。"""
        return self.client.health()

    def status(self, uid: str) -> dict:
        """状态只读取 runner 与数据库，不创建容器。"""
        with self.repository.connect() as connection:
            runs = self.repository.active_runs(connection, uid)
        containers: list[dict] = []
        error = None
        try:
            safe_uid = workspace_uid_dirname(uid)
            containers = [item for item in self.client.list() if item.get("labels", {}).get("yuxi.uid") == safe_uid]
            if any(item.get("state") != "running" for item in containers):
                error = "存在已停止的容器，下一次执行会重建本任务环境"
        except Exception:
            error = "容器执行服务不可用，无法读取任务环境"
        running = [item for item in containers if item.get("state") == "running"]
        return {
            "backend": "container",
            "state": "running" if running else "not_created",
            "generation": 1,
            "error": error,
            "active_runs": [{key: run[key] for key in ("id", "thread_id", "turn_id", "status")} for run in runs],
            "active_operations": 0,
            "busy": bool(runs or running),
            "last_active_at": None,
            "paused_at": None,
            "idle_timeout_seconds": 0,
            "idle_deadline_at": None,
            "server_time": format_utc_datetime(utc_now()),
        }

    def active_workspace_uids(self, uids: list[str]) -> set[str]:
        """容器删除前仍可能有 stdin 清理，包含独立的 Skill 获取容器。"""
        active = {item.get("labels", {}).get("yuxi.uid") for item in self.client.list()}
        return {uid for uid in uids if workspace_uid_dirname(uid) in active}

    def rebuild(self, uid: str) -> dict:
        """容器按 Run 创建，没有可重建的用户级环境。"""
        raise StateConflictError("容器沙盒按任务创建，不支持重建；请重新发起任务", code="sandbox_ephemeral")

    def disk_usage(self, uid: str) -> dict:
        """没有持久 Home，磁盘事实由宿主 Workspace 拥有。"""
        raise UpstreamError("容器沙盒没有持久主目录，磁盘用量由宿主工作区管理")

    def vm_stats(self) -> dict[str, int]:
        """把受管容器计数映射到系统概览的现有字段。"""
        return self.client.stats()

    def execute(
        self,
        uid: str,
        command: str,
        cwd: str,
        timeout: int,
        cancelled: threading.Event | None = None,
        *,
        stdin_data: bytes | None = None,
        output_budget: int | None = None,
        python: bool = False,
        workdir: str | None = None,
    ):
        """在所属 Run 的容器内执行命令；没有活动 Run 时显式拒绝。"""
        cancelled = cancelled or threading.Event()
        run_id, owner = self._run_context()
        if not isinstance(command, str) or not command:
            raise ValueError("command must be non-empty text")
        if stdin_data is not None and len(stdin_data) > MAX_STDIN_BYTES:
            raise ValueError("stdin exceeds transfer limit")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        resolved_workdir = _resolve_workdir(cwd, workdir)
        if resolved_workdir is None:
            raise StateConflictError("容器沙盒命令缺少已绑定的 Workdir", code="sandbox_workdir_required")
        budget = output_budget if output_budget is not None else get_int_env("SANDBOX_MAX_OUTPUT_BYTES", 262144)
        envs = {} if python else sandbox_run_env.get()
        if envs is None:
            envs = AgentEnvRepository.load_for_sync_runtime(uid)
        if cancelled.is_set():
            raise StateConflictError("命令在启动前已取消")
        command_id = uuid.uuid4().hex
        finished = threading.Event()

        def watch() -> None:
            """取消只发送本命令的取消请求，不删除同 Run 其他命令的容器。"""
            while not finished.wait(0.1):
                if not cancelled.is_set():
                    continue
                logged = False
                while not finished.is_set():
                    try:
                        if self.client.cancel_exec(uid=uid, run_id=run_id, owner=owner, command_id=command_id):
                            return
                    except Exception:
                        if not logged:
                            logger.warning("Container command cancellation was not acknowledged: run={} command={}", run_id, command_id)
                            logged = True
                    finished.wait(0.2)
                return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            payload = self.client.execute(
                uid=uid,
                run_id=run_id,
                owner=owner,
                command_id=command_id,
                command=command,
                cwd=cwd,
                workdir=resolved_workdir,
                timeout_seconds=timeout,
                output_budget=budget,
                stdin_data=stdin_data,
                python=python,
                envs=dict(envs),
            )
        finally:
            finished.set()
            watcher.join()
        output = payload.get("output")
        exit_code = payload.get("exit_code")
        truncated = payload.get("truncated")
        if not isinstance(output, str) or not isinstance(exit_code, int) or not isinstance(truncated, bool):
            raise UpstreamError("容器执行服务返回了无效执行结果")
        if cancelled.is_set():
            return output, 143, truncated
        return output, exit_code, truncated

    def cleanup_run(self, uid: str, run_id: str, owner: str | None = None) -> None:
        """确认 Run 容器已删除；owner 存在时避免误删新 attempt 的容器。"""
        try:
            self.client.remove_run(uid=uid, run_id=run_id, owner=owner)
        except Exception as exc:
            raise UpstreamError("Run 容器清理未确认") from exc

    def maintain(self) -> None:
        """按 Docker label、Run 终态和 lease 对账并清理孤儿容器。"""
        containers = self.client.list()
        now = utc_now()
        failures = 0
        for container in containers:
            labels = container.get("labels") or {}
            reason = self._orphan_reason(container, labels, now)
            if reason is None:
                continue
            try:
                run_id = labels.get("yuxi.run_id")
                # 名称可被新 attempt 或同 attempt 的重建复用，快照只拥有这个具体容器。
                self.client.remove_container(str(container.get("id", "")))
                logger.info("Removed orphan sandbox container: reason={} run={}", reason, run_id or "-")
            except Exception:
                failures += 1
                logger.exception("Failed to remove orphan sandbox container: run={}", labels.get("yuxi.run_id"))
        if failures:
            raise UpstreamError(f"容器沙盒清理未完成：{failures} 项，请核对 runner")

    def _run_context(self) -> tuple[str, str]:
        """Run/attempt 身份只来自可信执行上下文，模型不能覆盖。"""
        owner = sandbox_run_owner.get()
        if owner is None:
            raise StateConflictError("容器沙盒命令必须属于一个活动 Run", code="sandbox_run_required")
        run_id, attempt_owner = owner
        if not run_id or not attempt_owner:
            raise StateConflictError("容器沙盒命令缺少 Run owner", code="sandbox_run_required")
        return str(run_id), str(attempt_owner)

    def _orphan_reason(self, container: dict, labels: dict, now: datetime) -> str | None:
        """返回孤儿原因；None 表示仍由活动 Run 拥有。"""
        if labels.get("yuxi.purpose") == "skill-fetch":
            # 获取容器由同步 HTTP 请求的 finally 和 runner TTL 拥有，不关联虚构的 Run。
            try:
                expires_at = float(labels.get("yuxi.expires_at") or 0)
            except ValueError:
                expires_at = 0
            return "skill_fetch_expired" if expires_at <= now.timestamp() else None

        run_id = str(labels.get("yuxi.run_id") or "")
        safe_uid = str(labels.get("yuxi.uid") or "")
        owner = str(labels.get("yuxi.owner") or "")
        if not run_id or not safe_uid:
            return "missing_labels"
        state = str(container.get("state") or "").lower()
        try:
            created_at = float(labels.get("yuxi.created_at") or 0)
        except ValueError:
            created_at = 0
        if state in {"exited", "dead", "removing"}:
            return "not_running"
        if state != "running":
            # created/restarting 可能正处于 create 与 start 之间；给启动窗口，避免误删。
            if created_at <= 0 or now.timestamp() - created_at >= 60:
                return "not_running"
            return None
        row = self.repository.run_owner(run_id)
        if row is None:
            return "run_missing"
        if workspace_uid_dirname(str(row["uid"])) != safe_uid:
            return "uid_mismatch"
        if row["status"] not in {"running", "cancel_requested"}:
            return "run_terminal"
        if row["worker_id"] != owner:
            return "owner_changed"
        if row["lease_expires_at"] is None or row["lease_expires_at"] <= now:
            return "lease_expired"
        return None


def _resolve_workdir(cwd: str, workdir: str | None) -> str | None:
    """把可信 Workdir 标识规范化为相对路径，不从模型参数推断宿主路径。"""
    if workdir:
        return normalize_workdir_path(workdir)
    normalized = str(cwd or "").rstrip("/")
    prefix = WORKSPACE_PATH.rstrip("/")
    if normalized.startswith(f"{prefix}/"):
        return normalize_workdir_path(normalized[len(prefix) + 1 :])
    return None


__all__ = ["ContainerSandboxProvider", "SandboxRunnerClient"]
