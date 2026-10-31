"""Sandbox runner 的容器生命周期、挂载边界、执行与清理。"""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import os
import re
import socket
import stat
import threading
import time
from pathlib import Path, PurePosixPath
from weakref import WeakValueDictionary

from yuxi.infrastructure.filesystem import open_directory_fd
from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.modules.workspace.paths import normalize_workdir_path, workspace_uid_dirname
from yuxi.modules.workspace.runtime_paths import PERSONAL_SKILLS_PATH, normalize_runtime_path
from yuxi.sandbox_runner.engine import DockerEngine, EngineError, ExecDeadlineExceeded
from yuxi.sandbox_runner.settings import RunnerSettings
from yuxi.shared.datetime import utc_now

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
COMMAND_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_COMMAND_BYTES = 4 * 1024 * 1024
MAX_STDIN_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_BUDGET = 160 * 1024 * 1024
MAX_ENV_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 3600
RESERVED_ENV_KEYS = frozenset({"YUXI_EXECUTION_ID", "YUXI_RUN_ID", "YUXI_UID"})
CONTAINER_NAME_PREFIX = "yuxi-sandbox-"

_EXEC_WRAPPER = """import os,signal,subprocess,sys,time
from pathlib import Path
if os.getpgrp()!=os.getpid():
    os.setpgid(0,0)
path=sys.argv[1]
if path:
    fd=os.open(path,os.O_RDONLY)
    os.dup2(fd,0)
    os.close(fd)
    os.unlink(path)
# 父进程保留可信身份，子命令 exec/env -i 不会移除可定位的进程组 leader。
process=subprocess.Popen(sys.argv[3:])
try:
    code=process.wait(timeout=int(sys.argv[2]))
except subprocess.TimeoutExpired:
    code=124
def followers():
    '''读取本命令组中除可信 leader 外尚未结束的进程。'''
    found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid(): continue
        try:
            if os.getpgid(int(p.name))==os.getpgrp():
                stat=(p/'stat').read_text().rsplit(')',1)[1].split()
                if stat[0] not in ('Z','X'): found.append(int(p.name))
        except (OSError,ValueError): pass
    return found
# leader 退出前确认同组后台进程停止，子进程清空环境也不能逃过收尾。
for sig in (signal.SIGTERM,signal.SIGKILL):
    for pid in followers():
        try: os.kill(pid,sig)
        except ProcessLookupError: pass
    time.sleep(.1)
if followers(): raise RuntimeError('ordinary child processes remain')
sys.exit(code if code>=0 else 128-code)
"""

_KILL_SCRIPT = """import os,signal,sys,time
marker=("YUXI_EXECUTION_ID="+sys.argv[1]).encode()
def groups():
    found=set()
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        pid=int(name)
        if pid==os.getpid():
            continue
        try:
            env=open('/proc/%d/environ'%pid,'rb').read().split(b'\\0')
        except OSError:
            continue
        if marker not in env:
            continue
        try:
            found.add(os.getpgid(pid))
        except OSError:
            pass
    return found
targets=groups()
def live_groups():
    found=set()
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            data=open('/proc/'+name+'/stat').read()
            fields=data[data.rfind(')')+2:].split()
            group=int(fields[2])
        except (OSError,ValueError,IndexError):
            continue
        if fields[0] not in ('Z','X') and group in targets:
            found.add(group)
    return found
def signal_all(sig):
    failed=False
    for group in live_groups():
        try:
            os.killpg(group,sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            failed=True
    return failed
if not targets:
    sys.exit(3)
signal_all(signal.SIGTERM)
for _ in range(10):
    if not live_groups():
        sys.exit(0)
    time.sleep(0.1)
if signal_all(signal.SIGKILL):
    sys.exit(1)
time.sleep(0.2)
sys.exit(1 if live_groups() else 0)
"""


class SandboxError(RuntimeError):
    """runner 对调用方暴露的受控错误。"""

    status_code = 500


class SandboxValidationError(SandboxError):
    """请求参数或路径不满足挂载与执行契约。"""

    status_code = 400


class SandboxConflictError(SandboxError):
    """Run、owner、标签或容器状态冲突。"""

    status_code = 409


class SandboxCapacityError(SandboxError):
    """受管容器数量达到部署上限。"""

    status_code = 429


class ContainerManager:
    """只由 runner 进程装配，是容器生命周期的唯一 Owner。"""

    def __init__(self, settings: RunnerSettings, engine: DockerEngine | None = None):
        """校验镜像与挂载根，缓存不可变的镜像 ID。"""
        self.settings = settings
        self.repository = SandboxRepository()
        self.engine = engine or DockerEngine(settings.socket_path)
        self._exec_lock = threading.Lock()
        self._lock_guard = threading.Lock()
        self._run_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
        self._capacity_lock = threading.Lock()
        self._execs: dict[tuple[str, str], dict] = {}
        self.local_data_root = self._local_root(settings.data_mount)
        self.local_skill_root = self._local_skill_root(settings.skill_mount)
        self.host_data_root = self._host_root(settings.data_mount, settings.host_data_dir)
        self.host_skill_root = self._host_skill_root(settings.skill_mount, settings.host_skill_dir)
        # 数据槽位已有单 runner 约束；宿主路径使 Compose 与直接运行的身份一致。
        self.scope = hashlib.sha256(os.path.normpath(str(self.host_data_root)).encode()).hexdigest()[:24]
        identity = self.engine.image_identity(settings.image)
        self.image_id = identity["id"]
        self.image_reference = settings.image
        self.image_pinned = self._verify_image(identity)
        if not self.image_pinned and self.settings.environment == "production":
            raise SandboxError("生产环境必须固定 sandbox 镜像 digest")
        self._engine_version = self.engine.version()

    def health(self) -> dict:
        """确认 daemon 与启动时固定的镜像仍然可用。"""
        self.engine.ping()
        # run_owner("") 不取行锁，用一次真实查询证明 repository 与 PostgreSQL 连通。
        self.repository.run_owner("")
        identity = self.engine.image_identity(self.image_id)
        if identity["id"] != self.image_id:
            raise SandboxError("sandbox 镜像 ID 已改变")
        return {
            "ok": True,
            "docker_version": self._engine_version.get("Version"),
            "image": self.image_id,
            "image_reference": self.image_reference,
            "image_pinned": self.image_pinned,
            "network": self.settings.network,
            "max_containers": self.settings.max_containers,
        }

    def create(self, *, uid: str, run_id: str, owner: str, workdir: str | None) -> dict:
        """幂等确保 Run 容器运行，返回容器标识与标签。"""
        normalized = self._validate_request(uid=uid, run_id=run_id, owner=owner, workdir=workdir)
        with self._run_lock(run_id):
            self._validate_run_owner(uid, run_id, owner)
            existing = self._inspect_existing(run_id)
            if existing is not None:
                self._assert_labels(existing, uid=uid, run_id=run_id, owner=owner, workdir=normalized)
                if self._container_running(existing):
                    return self._summary(existing)
                self.engine.remove_container(existing["Id"])
            with self._capacity_lock:
                if self._running_count() >= self.settings.max_containers:
                    raise SandboxCapacityError("容器并发已达到部署上限")
                config = self._container_config(uid=uid, run_id=run_id, owner=owner, workdir=normalized)
                created = self.engine.create_container(name=self._container_name(run_id), config=config)
                container_id = created["Id"]
                try:
                    self.engine.start_container(container_id)
                    # HOME 为 tmpfs；必须在挂载完成后、接受用户命令前建立 CLI 入口。
                    exit_code, _ = self.engine.run_probe(
                        container_id,
                        cmd=["/bin/sh", "-ec", f"mkdir -p /home/user/.agents && ln -s {PERSONAL_SKILLS_PATH} /home/user/.agents/skills"],
                        env=[],
                        workdir="/home/user",
                        user=config["User"],
                    )
                    if exit_code != 0:
                        raise SandboxError("个人 Skill 目录映射失败")
                except Exception:
                    self.engine.remove_container(container_id)
                    raise
            info = self.engine.inspect_container(container_id)
            if info is None or not self._container_running(info):
                raise SandboxError("容器启动后未运行")
            return self._summary(info)

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
        stdin_b64: str | None,
        python: bool,
        envs: dict[str, str],
    ) -> dict:
        """确保容器后执行命令，返回合并输出、退出码与截断。"""
        normalized = self._validate_request(uid=uid, run_id=run_id, owner=owner, workdir=workdir)
        self._validate_command(command_id, command, timeout_seconds, output_budget)
        normalized_cwd = self._container_cwd(cwd)
        stdin_data = self._decode_stdin(stdin_b64)
        if stdin_data is not None and normalized is None:
            raise SandboxValidationError("stdin 需要已绑定的 Workdir 挂载")
        trusted_env = self._trusted_env(envs, command_id=command_id, run_id=run_id, uid=uid)
        container = self.create(uid=uid, run_id=run_id, owner=owner, workdir=normalized)
        container_id = container["id"]
        safe_uid = workspace_uid_dirname(uid)
        local_workdir = None
        if normalized:
            local_workdir, _ = self._validated_workdir(safe_uid, normalized)
        stdin_local = None
        stdin_path = ""
        if stdin_data is not None and local_workdir is not None:
            filename = f".yuxi-stdin-{run_id}-{command_id}"
            stdin_local = local_workdir / filename
            stdin_path = f"/home/user/workspace/{normalized}/{filename}"
        key = (run_id, command_id)
        with self._exec_lock:
            if sum(1 for item in self._execs.values() if item["run_id"] == run_id) >= self.settings.max_execs:
                raise SandboxCapacityError("同一 Run 的并发命令已达到上限")
            if key in self._execs:
                raise SandboxConflictError("command_id 已存在")
            self._execs[key] = {
                "run_id": run_id,
                "container_id": container_id,
                "uid": uid,
                "owner": owner,
                "cancel": threading.Event(),
                "timed_out": False,
                "exec_id": None,
                "started": False,
            }
        timer = None
        try:
            if stdin_local is not None and stdin_data is not None:
                self._write_stdin(stdin_local, stdin_data)
            executable = "/usr/bin/python3" if python else "/bin/bash"
            args = ["-c", command] if python else ["-lc", command]
            cmd = ["/usr/bin/python3", "-I", "-c", _EXEC_WRAPPER, stdin_path, str(timeout_seconds), executable, *args]
            exec_id = self.engine.create_exec(
                container_id,
                cmd=cmd,
                env=trusted_env,
                workdir=normalized_cwd,
                user=f"{self.settings.run_uid}:{self.settings.run_gid}",
            )
            with self._exec_lock:
                entry = self._execs.get(key)
                if entry is not None:
                    entry["exec_id"] = exec_id
                    cancel_before_start = entry["cancel"].is_set()
                else:
                    cancel_before_start = True
            if cancel_before_start:
                return {"output": "", "exit_code": 143, "truncated": False}
            with self._exec_lock:
                entry = self._execs.get(key)
                if entry is not None:
                    entry["started"] = True
            timer = threading.Timer(timeout_seconds, self._timeout, args=(run_id, command_id))
            timer.daemon = True
            timer.start()
            try:
                # 读取窗口与截止时间都比命令超时多留余量，给容器内收尾和取消确认留出时间。
                output, truncated = self.engine.start_exec(
                    exec_id,
                    read_timeout=timeout_seconds + 30,
                    output_budget=output_budget,
                    deadline=time.monotonic() + timeout_seconds + 5,
                )
                exit_code = self.engine.inspect_exec(exec_id)
            except ExecDeadlineExceeded as exc:
                if not self._cancel_until_ack(run_id, command_id):
                    self.engine.remove_container(container_id)
                    raise SandboxError("命令停止未确认，已销毁本 Run 容器；同 Run 的其他命令也会失败") from exc
                with self._exec_lock:
                    entry = self._execs.get(key)
                    if entry is not None:
                        entry["timed_out"] = True
                return {"output": exc.output.decode("utf-8", errors="replace"), "exit_code": 124, "truncated": exc.truncated}
            except Exception as exc:
                with self._exec_lock:
                    entry = self._execs.get(key) or {}
                    timed_out = bool(entry.get("timed_out"))
                    cancelled = bool(entry.get("cancel") and entry["cancel"].is_set())
                if not self._cancel_until_ack(run_id, command_id):
                    self.engine.remove_container(container_id)
                    raise SandboxError("命令停止未确认，已销毁本 Run 容器；同 Run 的其他命令也会失败") from exc
                if timed_out or cancelled:
                    return {"output": "", "exit_code": 124 if timed_out else 137, "truncated": False}
                raise self._translate_error(exc) from exc
            with self._exec_lock:
                timed_out = bool((self._execs.get(key) or {}).get("timed_out"))
            return {
                "output": output.decode("utf-8", errors="replace"),
                "exit_code": 124 if timed_out else exit_code,
                "truncated": truncated,
            }
        finally:
            if timer is not None:
                timer.cancel()
            with self._exec_lock:
                self._execs.pop(key, None)
            if stdin_local is not None:
                self._delete_stdin_file(stdin_local)

    def cancel_exec(self, *, uid: str, run_id: str, owner: str, command_id: str, raise_on_failure: bool = True) -> bool:
        """只杀掉带本次执行标识的容器内进程组。"""
        with self._exec_lock:
            entry = self._execs.get((run_id, command_id))
            if entry is None:
                return False
            if entry["uid"] != uid or entry["owner"] != owner:
                raise SandboxConflictError("命令不属于当前 Run attempt")
            entry["cancel"].set()
            container_id = entry["container_id"]
            exec_id = entry["exec_id"]
            started = entry["started"]
        if exec_id is None or not started:
            return False
        try:
            exit_code, output = self.engine.run_probe(
                container_id,
                cmd=["/usr/bin/python3", "-I", "-c", _KILL_SCRIPT, command_id],
                env=[],
                workdir="/tmp",
                user=f"{self.settings.run_uid}:{self.settings.run_gid}",
                timeout=15,
            )
        except EngineError as exc:
            if raise_on_failure:
                raise SandboxError("容器内命令取消失败") from exc
            return False
        if exit_code == 3:
            # 进程尚未出现或已经结束；调用方需要继续重试或读取真实终态。
            return False
        if exit_code != 0 and raise_on_failure:
            raise SandboxError(f"容器内命令组未终止：{output.strip()[:200]}")
        return exit_code == 0

    def remove_run(self, *, uid: str, run_id: str, owner: str | None = None) -> dict:
        """幂等删除 Run 容器；owner 存在时只删除同 attempt 的容器。"""
        self._validate_identifier(run_id, "run_id")
        safe_uid = workspace_uid_dirname(self._validate_uid(uid))
        with self._run_lock(run_id):
            info = self._inspect_existing(run_id)
            if info is None:
                return {"deleted": False, "reason": "not_found"}
            labels = self._labels(info)
            if labels.get("yuxi.scope") != self.scope:
                raise SandboxConflictError("拒绝删除其他部署或未标注部署的容器")
            if labels.get("yuxi.uid") != safe_uid or labels.get("yuxi.managed") != "true":
                raise SandboxConflictError("容器标签与请求用户不匹配")
            if owner and labels.get("yuxi.owner") != owner:
                return {"deleted": False, "reason": "owner_mismatch"}
            self._cleanup_stdin_dir(labels, run_id)
            self.engine.remove_container(info["Id"])
            return {"deleted": True}

    def remove_container(self, container_id: str) -> dict:
        """按容器 ID 删除受管容器，用于 label 异常兜底。"""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", container_id):
            raise SandboxValidationError("container_id 格式非法")
        info = self.engine.inspect_container(container_id)
        if info is None:
            return {"deleted": False, "reason": "not_found"}
        labels = self._labels(info)
        if labels.get("yuxi.managed") != "true" or labels.get("yuxi.scope") != self.scope:
            raise SandboxConflictError("拒绝删除非本部署管理的容器")
        self._cleanup_stdin_dir(labels, str(labels.get("yuxi.run_id") or ""))
        self.engine.remove_container(container_id)
        return {"deleted": True}

    def list_containers(self) -> dict:
        """返回受管容器、状态和完整 label。"""
        result = []
        for info in self.engine.list_containers(scope=self.scope):
            result.append(
                {
                    "id": info.get("Id", ""),
                    "state": str(info.get("State", "unknown")).lower(),
                    "labels": info.get("Labels") or {},
                }
            )
        return {"containers": result}

    def remove_all(self) -> int:
        """关闭 runner 时移除全部受管容器，避免长命令阻塞进程退出。"""
        removed = 0
        for info in self.engine.list_containers(scope=self.scope):
            labels = info.get("Labels") or {}
            self._cleanup_stdin_dir(labels, str(labels.get("yuxi.run_id") or ""))
            self.engine.remove_container(info["Id"])
            removed += 1
        return removed

    def stats(self) -> dict[str, int]:
        """运行中与全部受管容器数量。"""
        containers = self.engine.list_containers(scope=self.scope)
        running = sum(str(item.get("State", "")).lower() == "running" for item in containers)
        return {"active_containers": running, "total_containers": len(containers)}

    def remove_expired(self) -> int:
        """删除超过最大存活时间的受管容器，兜底 worker 长时间失联。"""
        removed = 0
        now = time.time()
        for info in self.engine.list_containers(scope=self.scope):
            labels = info.get("Labels") or {}
            try:
                created_at = float(labels.get("yuxi.created_at") or 0)
            except ValueError:
                created_at = 0
            if created_at <= 0 or now - created_at <= self.settings.max_lifetime_seconds:
                continue
            self._cleanup_stdin_dir(labels, str(labels.get("yuxi.run_id") or ""))
            self.engine.remove_container(info["Id"])
            removed += 1
        return removed

    def _container_config(self, *, uid: str, run_id: str, owner: str, workdir: str | None) -> dict:
        """装配固定镜像、固定用户、资源限制和最小挂载。"""
        mounts = []
        safe_uid = workspace_uid_dirname(uid)
        personal_parts = ("shared", safe_uid, "workspace", "agents", "skills")
        try:
            personal_fd = open_directory_fd(self.local_data_root, personal_parts, create=True)
            try:
                # runner 可用 root 建目录，但个人文件必须由沙盒普通账号写入。
                if os.geteuid() == 0:
                    os.fchown(personal_fd, self.settings.run_uid, self.settings.run_gid)
            finally:
                os.close(personal_fd)
        except OSError as exc:
            raise SandboxValidationError("个人 Skill 目录不可用或包含符号链接") from exc
        mounts.append(
            {
                "Type": "bind",
                "Source": str(self.host_data_root.joinpath(*personal_parts)),
                "Target": PERSONAL_SKILLS_PATH,
                "ReadOnly": False,
            }
        )
        if workdir and f"/home/user/workspace/{workdir}" != PERSONAL_SKILLS_PATH:
            _, host_workdir = self._validated_workdir(safe_uid, workdir)
            mounts.append(
                {
                    "Type": "bind",
                    "Source": str(host_workdir),
                    "Target": f"/home/user/workspace/{workdir}",
                    "ReadOnly": False,
                }
            )
        host_skills = self._host_skills_for(safe_uid)
        if host_skills is not None:
            mounts.append({"Type": "bind", "Source": str(host_skills), "Target": "/home/user/skills", "ReadOnly": True})
        labels = {
            "yuxi.managed": "true",
            "yuxi.scope": self.scope,
            "yuxi.uid": safe_uid,
            "yuxi.run_id": run_id,
            "yuxi.owner": owner,
            "yuxi.workdir": workdir or "",
            "yuxi.created_at": str(int(time.time())),
        }
        return {
            "Image": self.image_id,
            "Cmd": ["/bin/sh", "-c", "trap 'exit 0' TERM INT; while true; do sleep 3600; done"],
            "Env": ["HOME=/home/user", "TMPDIR=/tmp", "LANG=C.UTF-8"],
            "WorkingDir": "/home/user",
            "User": f"{self.settings.run_uid}:{self.settings.run_gid}",
            "Labels": labels,
            "HostConfig": {
                "Mounts": mounts,
                "Tmpfs": {
                    "/home/user": (
                        f"rw,exec,nosuid,nodev,size={self.settings.home_bytes},mode=0755,uid={self.settings.run_uid},gid={self.settings.run_gid}"
                    ),
                    "/tmp": f"rw,noexec,nosuid,nodev,size={self.settings.tmp_bytes}",
                },
                "NetworkMode": self.settings.network,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "PidsLimit": self.settings.pids_limit,
                "Memory": self.settings.memory_bytes,
                "MemorySwap": self.settings.memory_bytes,
                "NanoCpus": int(self.settings.cpus * 1_000_000_000),
                "Init": True,
                "AutoRemove": False,
                "IpcMode": "private",
            },
        }

    def _validate_run_owner(self, uid: str, run_id: str, owner: str) -> None:
        """在创建锁内核对持久归属，拒绝清理后才到达的旧执行请求。"""
        # 清理已提交 Run 状态后才取同一创建锁；这里不能再取 PG 行锁，否则与清理互等。
        row = self.repository.run_owner(run_id)
        if row is None or str(row["uid"]) != uid:
            raise SandboxConflictError("Run 与用户环境不匹配")
        if row["status"] != "running" or row["worker_id"] != owner or row["runtime_cleanup_pending"]:
            raise SandboxConflictError("Run 已停止、待清理或执行 owner 已改变")
        if row["lease_expires_at"] is None or row["lease_expires_at"] <= utc_now():
            raise SandboxConflictError("Run 执行租约已过期")

    def _validate_request(self, *, uid: str, run_id: str, owner: str, workdir: str | None) -> str | None:
        """校验 uid、Run、owner 与 Workdir 标识，返回规范化 Workdir。"""
        self._validate_uid(uid)
        self._validate_identifier(run_id, "run_id")
        if not owner or len(owner) > 256 or "\0" in owner:
            raise SandboxValidationError("owner 非法")
        if not workdir:
            return None
        try:
            return normalize_workdir_path(workdir)
        except ValueError as exc:
            raise SandboxValidationError("workdir 非法") from exc

    @staticmethod
    def _validate_command(command_id: str, command: str, timeout_seconds: int, output_budget: int) -> None:
        """校验命令执行参数的标识格式、命令体与超时、输出预算上限。"""
        if not COMMAND_ID_PATTERN.fullmatch(command_id):
            raise SandboxValidationError("command_id 格式非法")
        if not isinstance(command, str) or not command:
            raise SandboxValidationError("command 不能为空")
        if "\0" in command:
            raise SandboxValidationError("command 包含非法字符")
        if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise SandboxValidationError("command 超过长度限制")
        if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise SandboxValidationError(f"timeout_seconds 必须在 1 到 {MAX_TIMEOUT_SECONDS} 之间")
        if not 1 <= output_budget <= MAX_OUTPUT_BUDGET:
            raise SandboxValidationError("output_budget 超出允许范围")

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        """只接受可安全用于容器名与 label 的 Run ID。"""
        if not isinstance(value, str) or not RUN_ID_PATTERN.fullmatch(value):
            raise SandboxValidationError(f"{name} 格式非法")

    @staticmethod
    def _validate_uid(uid: str) -> str:
        """uid 只作为身份值，目录名由 Workspace Owner 派生。"""
        value = str(uid or "").strip()
        if not value or len(value) > 128 or "\0" in value:
            raise SandboxValidationError("uid 非法")
        return value

    @staticmethod
    def _container_cwd(cwd: str) -> str:
        """复用 runtime 路径契约，保留 runner 的 cwd 错误。"""
        try:
            return normalize_runtime_path(cwd)
        except ValueError:
            pass
        raise SandboxValidationError("cwd 非法")

    def _validated_workdir(self, safe_uid: str, workdir: str) -> tuple[Path, Path]:
        """逐段 no-follow 打开 Workdir，返回 runner 本地路径与 Docker 宿主路径。"""
        relative = PurePosixPath(workdir)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise SandboxValidationError("workdir 非法")
        try:
            fd = open_directory_fd(self.local_data_root, ("shared", safe_uid, "workspace", *relative.parts))
            os.close(fd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise SandboxConflictError("已绑定的 Workdir 不存在") from exc
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise SandboxValidationError("Workdir 包含符号链接或非目录组件") from exc
            raise
        local = self.local_data_root / "shared" / safe_uid / "workspace" / workdir
        host = self.host_data_root / "shared" / safe_uid / "workspace" / workdir
        return local, host

    def _host_skills_for(self, safe_uid: str) -> Path | None:
        """只有已存在的授权投影才挂载，避免 Docker 创建空宿主目录。"""
        if self.host_skill_root is None or self.local_skill_root is None:
            return None
        local = self.local_skill_root / safe_uid
        try:
            info = os.lstat(local)
        except OSError:
            return None
        if not stat.S_ISDIR(info.st_mode):
            return None
        return self.host_skill_root / safe_uid

    def _assert_labels(self, info: dict, *, uid: str, run_id: str, owner: str, workdir: str | None) -> None:
        """同名容器必须属于同一 uid、Run、attempt 和挂载配置。"""
        labels = self._labels(info)
        expected = {
            "yuxi.managed": "true",
            "yuxi.scope": self.scope,
            "yuxi.uid": workspace_uid_dirname(uid),
            "yuxi.run_id": run_id,
            "yuxi.owner": owner,
            "yuxi.workdir": workdir or "",
        }
        for key, value in expected.items():
            if labels.get(key) != value:
                raise SandboxConflictError(f"同名容器标签冲突：{key}")

    def _inspect_existing(self, run_id: str) -> dict | None:
        """按确定性名称读取本 Run 容器。"""
        return self.engine.inspect_container(self._container_name(run_id))

    def _container_name(self, run_id: str) -> str:
        """容器名绑定部署与 Run，同 daemon 上的独立槽位互不冲突。"""
        return f"{CONTAINER_NAME_PREFIX}{self.scope}-{run_id}"

    @staticmethod
    def _labels(info: dict) -> dict:
        """读取 inspect 的 Labels，缺失时返回空字典。"""
        config = info.get("Config") or {}
        labels = config.get("Labels") or {}
        return labels if isinstance(labels, dict) else {}

    @staticmethod
    def _container_running(info: dict) -> bool:
        """只把 Docker 明确报告 running 视为可复用。"""
        return bool((info.get("State") or {}).get("Running"))

    def _running_count(self) -> int:
        """统计受管运行中容器，作为全局并发上限。"""
        return sum(str(item.get("State", "")).lower() == "running" for item in self.engine.list_containers(scope=self.scope))

    def _summary(self, info: dict) -> dict:
        """返回调用方需要的容器身份，不暴露宿主路径。"""
        return {
            "id": info["Id"],
            "name": self._container_name(self._labels(info).get("yuxi.run_id", "")),
            "state": "running" if self._container_running(info) else "stopped",
            "labels": self._labels(info),
            "image": self.image_id,
        }

    def _run_lock(self, run_id: str) -> threading.Lock:
        """同一 Run 的创建/复用串行，Run 不再被引用后锁自动回收。"""
        with self._lock_guard:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._run_locks[run_id] = lock
            return lock

    def _timeout(self, run_id: str, command_id: str) -> None:
        """timer 线程标记超时并尽力取消，主执行流负责返回 124。"""
        with self._exec_lock:
            entry = self._execs.get((run_id, command_id))
            if entry is None:
                return
            entry["timed_out"] = True
        self._cancel_until_ack(run_id, command_id)

    def _cancel_until_ack(self, run_id: str, command_id: str) -> bool:
        """重试到 runner 确认杀死了进程组或执行流已经结束。"""
        for _ in range(50):
            with self._exec_lock:
                entry = self._execs.get((run_id, command_id))
                if entry is None:
                    return True
                uid = entry["uid"]
                owner = entry["owner"]
            try:
                if self.cancel_exec(uid=uid, run_id=run_id, owner=owner, command_id=command_id, raise_on_failure=False):
                    return True
            except Exception:
                pass
            with self._exec_lock:
                if (run_id, command_id) not in self._execs:
                    return True
            time.sleep(0.1)
        return False

    def _decode_stdin(self, value: str | None) -> bytes | None:
        """解码有界 stdin；非法 base64 直接拒绝。"""
        if value is None:
            return None
        if not isinstance(value, str):
            raise SandboxValidationError("stdin_b64 必须是字符串")
        if len(value) > (MAX_STDIN_BYTES + 2) // 3 * 4 + 4:
            raise SandboxValidationError("stdin 超过大小限制")
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SandboxValidationError("stdin_b64 不是合法 base64") from exc
        if len(data) > MAX_STDIN_BYTES:
            raise SandboxValidationError("stdin 超过大小限制")
        return data

    def _trusted_env(self, envs: dict[str, str], *, command_id: str, run_id: str, uid: str) -> list[str]:
        """校验用户环境，并让 runner 标识覆盖同名用户键。"""
        if not isinstance(envs, dict):
            raise SandboxValidationError("envs 必须是对象")
        result = []
        total = 0
        for key, value in envs.items():
            if not isinstance(key, str) or not ENV_KEY_PATTERN.fullmatch(key):
                raise SandboxValidationError("环境变量名非法")
            if key in RESERVED_ENV_KEYS:
                continue
            if not isinstance(value, str) or "\0" in value or len(value) > 32768:
                raise SandboxValidationError("环境变量值非法")
            item = f"{key}={value}"
            total += len(item.encode("utf-8")) + 1
            if total > MAX_ENV_BYTES:
                raise SandboxValidationError("环境变量总量超过限制")
            result.append(item)
        result.extend(
            [
                f"YUXI_EXECUTION_ID={command_id}",
                f"YUXI_RUN_ID={run_id}",
                f"YUXI_UID={uid}",
            ]
        )
        return result

    def _write_stdin(self, path: Path, data: bytes) -> None:
        """把命令输入写入 Workdir 的隐藏临时文件，容器用户可读后立即删除。"""
        try:
            path.unlink(missing_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise SandboxConflictError("无法创建命令输入临时文件") from exc
        try:
            os.fchown(fd, self.settings.run_uid, self.settings.run_gid)
        except OSError as exc:
            os.close(fd)
            path.unlink(missing_ok=True)
            raise SandboxConflictError("runner 无法设置命令输入文件身份，请以可 chown 的身份运行 runner") from exc
        try:
            offset = 0
            while offset < len(data):
                offset += os.write(fd, data[offset:])
        finally:
            os.close(fd)

    @staticmethod
    def _delete_stdin_file(path: Path) -> None:
        """删除已消费或未启动的 stdin 临时文件。"""
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _cleanup_stdin_dir(self, labels: dict, run_id: str) -> None:
        """容器清理时只移除本 Run 遗留的 stdin 文件，不影响共享 Workdir 的其他 Run。"""
        workdir = str(labels.get("yuxi.workdir") or "")
        safe_uid = str(labels.get("yuxi.uid") or "")
        if not workdir or not safe_uid:
            return
        try:
            directory, _ = self._validated_workdir(safe_uid, workdir)
        except SandboxError:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        prefix = f".yuxi-stdin-{run_id}-"
        for entry in entries:
            if not entry.name.startswith(prefix):
                continue
            try:
                info = entry.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                self._delete_stdin_file(entry)

    def _translate_error(self, exc: Exception) -> SandboxError:
        """把 Docker 的容器消失/状态错误转换成 runner 契约。"""
        if isinstance(exc, SandboxError):
            return exc
        if isinstance(exc, EngineError) and exc.status_code == 404:
            return SandboxConflictError("Run 容器已被删除，请重试任务")
        return SandboxError("容器执行失败")

    def _local_root(self, mount: str) -> Path:
        """读取 runner 内可见的数据根，用于本地 stdin 与 no-follow 校验。"""
        root = Path(mount)
        if not root.is_dir():
            raise SandboxError(f"数据挂载根不存在：{root}")
        return root.resolve()

    def _local_skill_root(self, mount: str) -> Path | None:
        """Skill 投影可选；缺失时不影响文件工具与命令执行。"""
        root = Path(mount)
        return root.resolve() if root.is_dir() else None

    def _host_root(self, mount: str, override: str | None) -> Path:
        """得到 Docker daemon 视角的宿主数据根。"""
        if override:
            return Path(override)
        source = self._self_mount_source(mount)
        if source is None:
            raise SandboxError(f"无法从 runner 自身挂载解析宿主路径：{mount}")
        return Path(source)

    def _host_skill_root(self, mount: str, override: str | None) -> Path | None:
        """得到只读 Skill 投影的宿主路径。"""
        if override:
            return Path(override)
        if self.local_skill_root is None:
            return None
        source = self._self_mount_source(mount)
        return Path(source) if source else None

    def _self_mount_source(self, destination: str) -> str | None:
        """通过自身容器 inspect 反查 bind mount 的宿主 Source。"""
        hostname = socket.gethostname()
        info = self.engine.inspect_container(hostname)
        if info is None:
            return None
        for mount in info.get("Mounts") or []:
            if mount.get("Destination") == destination and mount.get("Source"):
                return str(mount["Source"])
        return None

    def _verify_image(self, identity: dict) -> bool:
        """优先按显式 digest 校验；仅本地构建且未配置 digest 时固定当前 ID。"""
        expected = self.settings.image_digest
        if "@sha256:" in self.settings.image:
            expected = "sha256:" + self.settings.image.split("@sha256:", 1)[1]
        if not expected:
            return False
        if identity["id"] == expected:
            return True
        for digest in identity.get("repo_digests") or []:
            if isinstance(digest, str) and digest.endswith(f"@{expected}"):
                return True
        raise SandboxError("sandbox 镜像 digest 与配置不匹配")


__all__ = [
    "ContainerManager",
    "SandboxCapacityError",
    "SandboxConflictError",
    "SandboxError",
    "SandboxValidationError",
]
