"""单节点用户 VM 的持久生命周期与普通账号命令执行。"""

from __future__ import annotations

import base64
import os
import secrets
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import timedelta
from functools import partial
from pathlib import Path

import httpx

from yuxi.infrastructure.runtime_settings import get_int_env
from yuxi.modules.agents.repositories.environment import AgentEnvRepository
from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.modules.agents.runtime.sandbox.context import sandbox_run_env, sandbox_run_owner
from yuxi.modules.agents.runtime.sandbox.cube import CubeAdapter, Environment
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.workspace.paths import ensure_user_workspace, workspace_uid_dirname
from yuxi.modules.workspace.runtime_paths import SHARED_SKILLS_PATH, WORKSPACE_PATH
from yuxi.modules.workspace.usage_changes import workspace_mutation
from yuxi.shared.datetime import format_utc_datetime, utc_now
from yuxi.shared.errors import StateConflictError, UpstreamError

_TRANSITIONAL_STATES = {"creating", "rebuilding", "resuming", "pausing"}


def validate_cube_configuration() -> None:
    """校验 Cube 节点及共享挂载配置。"""
    required = ("CUBE_API_URL", "CUBE_PROXY_NODE_IP")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    roots = [os.environ.get(name, "") for name in ("CUBE_NODE_USER_DATA_DIR", "CUBE_NODE_SKILL_PROJECTION_DIR")]
    root_names = ("CUBE_NODE_USER_DATA_DIR", "CUBE_NODE_SKILL_PROJECTION_DIR")
    bad_roots = [name for name, value in zip(root_names, roots) if not value or not Path(value).is_absolute()]
    if missing or bad_roots:
        raise RuntimeError(f"SANDBOX_BACKEND=cube 缺少或配置了非绝对路径：{', '.join(missing + bad_roots)}")


class CubeSandboxProvider:
    """PostgreSQL 拥有状态，进程内仅保留无状态客户端。"""

    backend = "cube"

    def __init__(self, adapter: CubeAdapter | None = None, repository: SandboxRepository | None = None):
        """装配真实执行与持久边界。"""
        self.adapter = adapter or CubeAdapter()
        self.repository = repository or SandboxRepository()

    def health(self) -> dict:
        """启动 readiness 同时核对 Cube 配置、控制面、模板和真实数据代理。"""
        validate_cube_configuration()
        return self.adapter.health()

    def vm_stats(self) -> dict[str, int]:
        """读取执行节点 VM 计数，Cube 适配器拥有远端查询。"""
        return self.adapter.vm_stats()

    def get(self, uid: str) -> Environment:
        """显式创建或恢复用户原实例，不静默替换损坏环境。"""
        with self.repository.user_lock(uid) as connection:
            return self._ensure(connection, uid)

    def prepare_instance(self, uid: str, *, expected: tuple[int, str] | None = None) -> tuple[int, str]:
        """核对预期实例和执行租约后创建或唤醒 VM，只返回代次与实例 ID。"""
        with self.repository.user_lock(uid, wait=False) as connection:
            if connection is None:
                raise StateConflictError("环境正在操作，请重试", code="sandbox_busy")
            if expected is not None:
                row = self.repository.environment(connection, uid)
                if row is None or (row["generation"], row["sandbox_id"]) != expected:
                    raise StateConflictError("环境状态已改变，请重试", code="sandbox_changed")
            self._require_run_owner(connection, uid)
            environment = self._ensure(connection, uid)
            return self.repository.environment(connection, uid)["generation"], environment.sandbox_id

    def run_guest_program(
        self,
        uid: str,
        generation: int,
        *,
        user: str,
        command: str,
        arguments: list[str],
        timeout: float,
        stdin_data: bytes | None = None,
        touch_idle: bool = False,
    ) -> tuple[int, bytes]:
        """在当前运行的目标 VM 执行受信内部入口；只有显式成功操作延长空闲计时。"""
        with self.repository.user_lock(uid, wait=False) as connection:
            if connection is None:
                raise StateConflictError("环境正在操作，请重试", code="sandbox_busy")
            row = self.repository.environment(connection, uid)
            if row is None or row["generation"] != generation or row["state"] != "running" or not row["sandbox_id"]:
                raise StateConflictError("环境状态已改变，请重试", code="sandbox_changed")
            require_owner = partial(self._require_run_owner, connection, uid)
            require_owner()
            try:
                exit_code, output = self.adapter.run_guest_program(
                    self._reference(row),
                    user=user,
                    command=command,
                    arguments=arguments,
                    timeout=timeout,
                    stdin_data=stdin_data,
                    before_start=require_owner,
                )
            except (OSError, RuntimeError, ValueError, httpx.HTTPError):
                raise UpstreamError("Cube 来宾程序执行未确认", code="sandbox_service_unavailable") from None
            if touch_idle and exit_code == 0:
                row["last_active_at"] = utc_now()
                self.repository.save_environment(connection, row)
            return exit_code, output

    def status(self, uid: str) -> dict:
        """查询状态不触碰 connect，因此不唤醒 VM。"""
        with self.repository.connect() as connection:
            row = self.repository.environment(connection, uid)
            runs = self.repository.active_runs(connection, uid)
            commands = self.repository.commands(connection, uid)
        state = row["state"] if row else "not_created"
        error = row["error"] if row else None
        if row and state in _TRANSITIONAL_STATES:
            # 拿不到锁说明过渡操作仍在其他进程进行，保持原状态；拿到锁仍是过渡态说明
            # 操作进程已中断。这里只改响应不落库，持久失败由 _ensure 的显式拒绝和 rebuild 承担。
            with self.repository.user_lock(uid, wait=False) as connection:
                if connection is not None:
                    row = self.repository.environment(connection, uid)
                    if row and row["state"] in _TRANSITIONAL_STATES:
                        state, error = "failed", "上次环境操作已中断，请核对执行节点后重建"
                    else:
                        state, error = (row["state"], row["error"]) if row else ("not_created", None)
        rebuild_required = state == "failed"
        if row and row["sandbox_id"] and state in {"running", "paused"}:
            try:
                state = self.adapter.state(self._reference(row)).lower()
            except Exception:
                state, error = "failed", "暂时无法读取 Cube 实例状态，请检查执行节点后刷新"
        busy = bool(runs or commands) or state in _TRANSITIONAL_STATES
        idle_seconds = get_int_env("SANDBOX_IDLE_SECONDS", 300)
        deadline = (
            row["last_active_at"] + timedelta(seconds=idle_seconds) if row and state == "running" and not busy and not error else None
        )
        return {
            "backend": self.backend,
            "state": state,
            "generation": row["generation"] if row else 0,
            "error": error,
            "rebuild_required": rebuild_required,
            "active_runs": [{key: run[key] for key in ("id", "thread_id", "turn_id", "status")} for run in runs],
            "active_operations": len(commands),
            "busy": busy,
            "last_active_at": format_utc_datetime(row["last_active_at"]) if row else None,
            "paused_at": format_utc_datetime(row["paused_at"]) if row and state == row["state"] == "paused" else None,
            "idle_timeout_seconds": idle_seconds,
            "idle_deadline_at": format_utc_datetime(deadline) if deadline is not None else None,
            "server_time": format_utc_datetime(utc_now()),
        }

    def active_workspace_uids(self, uids: list[str]) -> set[str]:
        """按持久环境状态识别尚未确认休眠的 Workspace 写入者。"""
        return self.repository.active_workspace_uids(uids)

    def rebuild(self, uid: str, *, failed_generation: int | None = None) -> dict:
        """空闲时替换 VM，宿主 Workspace 与 Skills 不参与删除。"""
        with self.repository.user_lock(uid) as connection:
            if self.repository.active_runs(connection, uid) or self.repository.commands(connection, uid):
                raise StateConflictError("仍有 Run 或操作正在使用环境，请先停止并等待清理完成", code="sandbox_busy")
            row = self.repository.environment(connection, uid)
            if failed_generation is not None and (
                not row or row["generation"] != failed_generation or row["state"] not in _TRANSITIONAL_STATES | {"failed"}
            ):
                raise StateConflictError("原故障环境已改变，请刷新状态", code="sandbox_changed")
            if row:
                row["state"] = "rebuilding"
                self.repository.save_environment(connection, row)
                try:
                    if row["sandbox_id"]:
                        self.adapter.destroy(self._reference(row))
                    else:
                        # 控制面曾断连，先核对该操作的真实实例，避免重复创建。
                        for instance in self.adapter.find_created(row["operation_id"]):
                            sandbox_id = instance.get("sandboxID") or instance.get("sandboxId") or instance.get("sandbox_id")
                            if not sandbox_id:
                                raise RuntimeError("Cube 实例身份缺失")
                            self.adapter.destroy(Environment(sandbox_id, "", row["envd_token"]))
                except Exception as exc:
                    self._failed(connection, row, exc)
                    raise UpstreamError("旧环境删除未确认，重建未继续") from exc
            self._create(connection, uid, generation=row["generation"] + 1 if row else 1)
        return self.status(uid)

    def disk_usage(self, uid: str) -> dict:
        """手动读取主目录所在磁盘，沿用普通命令的恢复与清理语义。"""
        output, exit_code, truncated = self.execute(
            uid,
            "import os; s=os.statvfs('/home/user'); print(s.f_blocks*s.f_frsize, (s.f_blocks-s.f_bfree)*s.f_frsize, s.f_bavail*s.f_frsize)",
            "/home/user",
            timeout=10,
            output_budget=256,
            python=True,
        )
        if exit_code != 0 or truncated:
            raise UpstreamError("磁盘用量读取失败，请重试")
        try:
            total, used, available = map(int, output.split())
            if total <= 0 or used < 0 or available < 0 or used + available > total:
                raise ValueError("invalid disk usage")
        except ValueError as exc:
            raise UpstreamError("磁盘用量读取失败，请重试") from exc
        return {"used_bytes": used, "available_bytes": available}

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
        """流式运行独立进程组，取消不依赖 Python 线程终止。"""
        cancelled = cancelled or threading.Event()
        command_id = uuid.uuid4().hex
        with self.repository.user_lock(uid) as connection:
            environment = self._ensure(connection, uid)
            generation = self.repository.environment(connection, uid)["generation"]
            self._add_command(connection, uid, generation, command_id, "file" if python else "process", timeout + 60)
        budget = output_budget if output_budget is not None else get_int_env("SANDBOX_MAX_OUTPUT_BYTES", 262144)
        output = bytearray()
        truncated = False
        stopped = False
        pgid = None
        finished = threading.Event()

        def stop():
            """超时或取消只终止本次命令组。"""
            cancelled.set()
            try:
                self.adapter.cancel(environment, pgid, command_id)
            except Exception:
                self.repository.mark_cleanup(uid, command_id)

        def watch():
            """同时响应超时和协程取消，命令流阻塞也能发送终止信号。"""
            deadline = time.monotonic() + timeout
            while not finished.wait(0.1):
                if cancelled.is_set() or time.monotonic() >= deadline:
                    stop()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            envs = {} if python else sandbox_run_env.get()
            if envs is None:
                envs = AgentEnvRepository.load_for_sync_runtime(uid)
            if cancelled.is_set():
                raise StateConflictError("命令在启动前已取消")
            for event in self.adapter.stream(
                environment, command, cwd, command_id, envs, timeout=timeout, stdin_data=stdin_data, python=python
            ):
                if "start" in event:
                    pgid = int(event["start"]["pid"])
                    self.repository.command_started(command_id, pgid)
                if cancelled.is_set():
                    return output.decode("utf-8", errors="replace"), 143, truncated
                data = event.get("data", {})
                for channel in ("stdout", "stderr"):
                    if channel in data:
                        raw = base64.b64decode(data[channel], validate=True)
                        remaining = max(0, budget - len(output))
                        output.extend(raw[:remaining])
                        truncated |= len(raw) > remaining
                if "end" in event:
                    exit_code = int(event["end"]["exitCode"])
                    # shell 终态不保证普通后台子进程退出，明确收尾同一组。
                    self.adapter.cancel(environment, pgid, command_id, terminal_confirmed=True)
                    stopped = True
            if not stopped:
                raise RuntimeError("命令未取得可靠终态")
            return output.decode("utf-8", errors="replace"), exit_code, truncated
        finally:
            finished.set()
            watcher.join()
            if not stopped:
                try:
                    self.adapter.cancel(environment, pgid, command_id)
                    stopped = True
                except Exception:
                    self.repository.mark_cleanup(uid, command_id)
            if stopped:
                self.repository.finish_command(uid, command_id)

    def cleanup_run(self, uid: str, run_id: str, owner: str | None = None) -> None:
        """确认指定 Run 的命令已停止，保留用户 VM 和其他 Run 的命令。"""
        with self.repository.connect() as connection:
            commands = self.repository.commands(connection, uid, run_id=run_id)
        for command in commands:
            if owner is None or command["owner"] == owner:
                self._cleanup_command(command)

    def maintain(self) -> None:
        """清理失联句柄，再暂停所有使用者均空闲的环境。"""
        failures = []
        for command in self.repository.stale_commands():
            try:
                self._cleanup_command(command)
            except Exception as exc:
                failures.append(exc)
        idle_seconds = get_int_env("SANDBOX_IDLE_SECONDS", 300)
        for candidate in self.repository.environments():
            with self.repository.user_lock(candidate["uid"], wait=False) as connection:
                if connection is None:
                    continue
                row = self.repository.environment(connection, candidate["uid"])
                if row["state"] != "running" or (utc_now() - row["last_active_at"]).total_seconds() < idle_seconds:
                    continue
                if self.repository.active_runs(connection, row["uid"]) or self.repository.commands(connection, row["uid"]):
                    continue
                row["state"] = "pausing"
                self.repository.save_environment(connection, row)
                try:
                    paused = self.adapter.pause(self._reference(row))
                    row.update(state="paused", paused_at=utc_now() if paused else None, error=None)
                    self.repository.save_environment(connection, row)
                except Exception as exc:
                    self._failed(connection, row, exc)
                    failures.append(exc)
        if failures:
            raise UpstreamError(f"环境维护未完成：{len(failures)} 项，请核对执行节点")

    def _ensure(self, connection, uid: str) -> Environment:
        """在用户锁内核对已登记实例，只对首次使用执行创建。"""
        row = self.repository.environment(connection, uid)
        if row is None:
            return self._create(connection, uid, generation=1)
        if row["state"] not in {"running", "paused"}:
            raise StateConflictError("环境操作未完成或已失败，请核对后在设置中显式重建", code="sandbox_unavailable")
        environment = self._reference(row)
        try:
            state = self.adapter.state(environment).lower()
            self._require_run_owner(connection, uid)
            if state not in {"running", "paused"}:
                raise RuntimeError("Cube 实例未就绪")
            needs_marker = state == "paused" or row["state"] == "paused"
            with workspace_mutation(uid) if needs_marker else nullcontext():
                if state == "paused":
                    row["state"] = "resuming"
                    self.repository.save_environment(connection, row)
                    self.adapter.connect(environment).close()
                row.update(state="running", last_active_at=utc_now(), paused_at=None, error=None)
                self.repository.save_environment(connection, row)
            return environment
        except StateConflictError:
            raise
        except Exception as exc:
            self._failed(connection, row, exc)
            raise UpstreamError("原用户环境无法恢复；不会自动创建空环境") from exc

    def _create(self, connection, uid: str, *, generation: int) -> Environment:
        """先持久化操作身份，再创建并在初始化前保存实例引用。"""
        safe_uid = workspace_uid_dirname(uid)
        ensure_user_workspace(uid)
        get_user_skills_root_dir(uid).mkdir(parents=True, exist_ok=True)
        roots = [os.environ.get(name, "") for name in ("CUBE_NODE_USER_DATA_DIR", "CUBE_NODE_SKILL_PROJECTION_DIR")]
        if not all(root and Path(root).is_absolute() for root in roots):
            raise RuntimeError("Cube 节点的用户文件与 Skill 投影根必须配置为绝对路径")
        mounts = [
            {"hostPath": f"{roots[0]}/shared/{safe_uid}/workspace", "mountPath": WORKSPACE_PATH, "readOnly": False},
            {"hostPath": f"{roots[1]}/{safe_uid}", "mountPath": SHARED_SKILLS_PATH, "readOnly": True},
        ]
        row = dict(
            uid=uid,
            generation=generation,
            sandbox_id=None,
            traffic_token="",
            envd_token=secrets.token_urlsafe(32),
            state="creating",
            operation_id=uuid.uuid4().hex,
            last_active_at=utc_now(),
            paused_at=None,
            error=None,
        )
        with workspace_mutation(uid):
            self._require_run_owner(connection, uid)
            self.repository.save_environment(connection, row)

            def created(reference):
                """Cube 返回实例即落盘，初始化失败也可显式回收。"""
                row.update(sandbox_id=reference.sandbox_id, traffic_token=reference.traffic_token)
                self.repository.save_environment(connection, row)

            try:
                environment = self.adapter.create(
                    mounts=mounts,
                    metadata={"yuxi-uid": safe_uid, "yuxi-operation": row["operation_id"], "yuxi-generation": str(generation)},
                    envd_token=row["envd_token"],
                    on_created=created,
                )
                row.update(state="running", last_active_at=utc_now())
                self.repository.save_environment(connection, row)
                return environment
            except Exception as exc:
                self._failed(connection, row, exc)
                raise UpstreamError("运行环境创建失败，请联系管理员检查 Cube 节点后重建环境", code="sandbox_create_failed") from exc

    def _require_run_owner(self, connection, uid: str) -> None:
        """调用来自 Worker 时复核可信 Run 上下文，独立用户操作沿用其授权入口。"""
        if owner := sandbox_run_owner.get():
            self.repository.require_run_owner(connection, uid, *owner)

    def _add_command(self, connection, uid, generation, command_id, kind, timeout):
        """可信上下文决定 Run/attempt，独立 Skill 操作仍登记占用。"""
        owner = sandbox_run_owner.get()
        self.repository.add_command(
            connection,
            dict(
                id=command_id,
                uid=uid,
                generation=generation,
                run_id=owner[0] if owner else None,
                owner=owner[1] if owner else None,
                kind=kind,
                deadline=utc_now() + timedelta(seconds=timeout),
            ),
        )

    def _cleanup_command(self, command: dict) -> None:
        """以持久句柄核对代次；目标环境已更替时移除不再可作用的句柄。"""
        uid = command["uid"]
        with self.repository.user_lock(uid) as connection:
            row = self.repository.environment(connection, uid)
            current = next((item for item in self.repository.commands(connection, uid) if item["id"] == command["id"]), None)
            if current is None:
                return
            if row is None or row["generation"] != command["generation"]:
                # 重建先销毁旧 VM 再递增代次，旧句柄的 uuid 标识不可能命中新环境；
                # 保留只会永久阻塞该用户休眠并让维护循环反复失败。
                self.repository.remove_command(connection, uid, command["id"])
                return
            self.adapter.cancel(self._reference(row), command["pgid"], command["id"])
            self.repository.remove_command(connection, uid, command["id"])

    def _failed(self, connection, row, exc) -> None:
        """仅保存脱敏诊断，不把 SDK 响应凭据写入业务错误。"""
        state = {"resuming": "paused", "pausing": "running"}.get(row["state"], row["state"])
        if state not in {"running", "paused"}:
            state = "failed"
        row.update(state=state, error=f"{type(exc).__name__}: Cube 操作失败，请核对执行节点")
        self.repository.save_environment(connection, row)

    @staticmethod
    def _reference(row: dict) -> Environment:
        """控制凭据只在执行侧装配。"""
        return Environment(row["sandbox_id"], row["traffic_token"], row["envd_token"])
