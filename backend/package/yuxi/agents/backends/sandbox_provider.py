"""Yuxi AIO 风格沙盒 provider。"""

from __future__ import annotations

import atexit
import hashlib
import os
import signal
import threading
import time
from pathlib import Path

from yuxi import config as sys_config
from yuxi.services.skill_service import get_skills_root_dir
from yuxi.utils.logging_config import logger

from .local_container_backend import LocalContainerBackend
from .remote_sandbox_backend import RemoteSandboxBackend
from .sandbox_backend import YuxiSandboxBackend
from .sandbox_backend_base import SandboxBackend
from .sandbox_config import (
    IDLE_CHECK_INTERVAL,
    LARGE_TOOL_RESULTS_DIR,
    OUTPUTS_DIR,
    SKILLS_PATH,
    THREADS_DIR,
    UPLOADS_DIR,
    USER_DATA_PATH,
    WORKSPACE_DIR,
    get_container_prefix,
    get_idle_timeout,
    get_max_replicas,
    get_sandbox_base_port,
    get_sandbox_image,
    get_sandbox_provisioner_url,
)
from .sandbox_info import SandboxInfo


class YuxiSandboxProvider:
    def __init__(self):
        self._lock = threading.Lock()
        self._sandboxes: dict[str, YuxiSandboxBackend] = {}
        self._sandbox_infos: dict[str, SandboxInfo] = {}
        self._thread_sandboxes: dict[str, str] = {}
        self._thread_locks: dict[str, threading.Lock] = {}
        self._last_activity: dict[str, float] = {}
        self._warm_pool: dict[str, tuple[SandboxInfo, float]] = {}
        self._shutdown_called = False
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

        self._idle_timeout = get_idle_timeout()
        self._max_replicas = get_max_replicas()
        self._backend: SandboxBackend = self._create_backend()

        atexit.register(self.shutdown)
        self._register_signal_handlers()
        if self._idle_timeout > 0:
            self._start_idle_checker()

    def _create_backend(self) -> SandboxBackend:
        provisioner_url = get_sandbox_provisioner_url()
        if provisioner_url:
            logger.info(f"Using remote sandbox backend: {provisioner_url}")
            return RemoteSandboxBackend(provisioner_url)

        return LocalContainerBackend(
            image=get_sandbox_image(),
            base_port=get_sandbox_base_port(),
            container_prefix=get_container_prefix(),
        )

    def _register_signal_handlers(self):
        try:
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            self._original_sigint = signal.getsignal(signal.SIGINT)

            def signal_handler(signum, frame):
                self.shutdown()
                original = self._original_sigterm if signum == signal.SIGTERM else self._original_sigint
                if callable(original):
                    original(signum, frame)
                elif original == signal.SIG_DFL:
                    signal.signal(signum, signal.SIG_DFL)
                    signal.raise_signal(signum)

            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
        except ValueError:
            logger.debug("Could not register signal handlers (not main thread)")

    def _start_idle_checker(self):
        self._idle_checker_thread = threading.Thread(
            target=self._idle_checker_loop,
            name="sandbox-idle-checker",
            daemon=True,
        )
        self._idle_checker_thread.start()

    def _idle_checker_loop(self):
        while not self._idle_checker_stop.wait(timeout=IDLE_CHECK_INTERVAL):
            try:
                self._cleanup_idle_sandboxes()
            except Exception as exc:
                logger.error(f"Error in idle checker loop: {exc}")

    def _cleanup_idle_sandboxes(self):
        current_time = time.time()
        active_to_destroy: list[str] = []
        warm_to_destroy: list[tuple[str, SandboxInfo]] = []

        with self._lock:
            for sandbox_id, last_activity in self._last_activity.items():
                if current_time - last_activity > self._idle_timeout:
                    active_to_destroy.append(sandbox_id)
            for sandbox_id, (info, released_at) in list(self._warm_pool.items()):
                if current_time - released_at > self._idle_timeout:
                    warm_to_destroy.append((sandbox_id, info))
                    del self._warm_pool[sandbox_id]

        for sandbox_id in active_to_destroy:
            self.destroy_by_key(sandbox_id)

        for sandbox_id, info in warm_to_destroy:
            try:
                self._backend.destroy(info)
                logger.info(f"Destroyed idle warm sandbox {sandbox_id}")
            except Exception as exc:
                logger.warning(f"Failed to destroy idle warm sandbox {sandbox_id}: {exc}")

    def _get_thread_lock(self, thread_id: str) -> threading.Lock:
        with self._lock:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    @staticmethod
    def _deterministic_sandbox_id(thread_id: str) -> str:
        return hashlib.sha256(thread_id.encode()).hexdigest()[:8]

    def _get_thread_user_data_dir(self, thread_id: str) -> Path:
        root = (Path(sys_config.save_dir) / THREADS_DIR / thread_id / "user-data").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _resolve_mount_source(path: Path) -> str:
        resolved_path = path.resolve()
        host_project_dir = os.getenv("YUXI_HOST_PROJECT_DIR", "").strip()
        app_root = Path("/app")
        if host_project_dir and (resolved_path == app_root or resolved_path.is_relative_to(app_root)):
            host_root = Path(host_project_dir).resolve()
            return str(host_root / resolved_path.relative_to(app_root))
        return str(resolved_path)

    def ensure_thread_dirs(self, thread_id: str) -> Path:
        user_data_dir = self._get_thread_user_data_dir(thread_id)
        for name in (WORKSPACE_DIR, OUTPUTS_DIR, UPLOADS_DIR, LARGE_TOOL_RESULTS_DIR):
            (user_data_dir / name).mkdir(parents=True, exist_ok=True)
        (user_data_dir / UPLOADS_DIR / "attachments").mkdir(parents=True, exist_ok=True)
        return user_data_dir

    def get_thread_user_data_dir(self, thread_id: str) -> Path:
        return self.ensure_thread_dirs(thread_id)

    def _get_extra_mounts(self, thread_id: str) -> list[tuple[str, str, bool]]:
        mounts = [(self._resolve_mount_source(self.ensure_thread_dirs(thread_id)), USER_DATA_PATH, False)]
        try:
            skills_path = get_skills_root_dir()
            if skills_path.exists():
                mounts.append((self._resolve_mount_source(skills_path), SKILLS_PATH, True))
        except Exception as exc:
            logger.warning(f"Could not setup skills mount: {exc}")
        return mounts

    def acquire(self, thread_id: str) -> YuxiSandboxBackend:
        thread_lock = self._get_thread_lock(thread_id)
        with thread_lock:
            return self._acquire_internal(thread_id)

    def _acquire_internal(self, thread_id: str) -> YuxiSandboxBackend:
        sandbox_id = self._deterministic_sandbox_id(thread_id)

        with self._lock:
            existing_id = self._thread_sandboxes.get(thread_id)
            if existing_id and existing_id in self._sandboxes:
                self._last_activity[existing_id] = time.time()
                return self._sandboxes[existing_id]

            if sandbox_id in self._warm_pool:
                info, _ = self._warm_pool.pop(sandbox_id)
                return self._register_sandbox(thread_id, sandbox_id, info)

        info = self._backend.discover(sandbox_id)
        if info is not None:
            return self._register_sandbox(thread_id, sandbox_id, info)

        with self._lock:
            total = len(self._sandboxes) + len(self._warm_pool)
        if total >= self._max_replicas:
            self._evict_oldest_warm()

        info = self._backend.create(
            thread_id=thread_id,
            sandbox_id=sandbox_id,
            extra_mounts=self._get_extra_mounts(thread_id),
        )
        return self._register_sandbox(thread_id, sandbox_id, info)

    def _register_sandbox(self, thread_id: str, sandbox_id: str, info: SandboxInfo) -> YuxiSandboxBackend:
        backend = YuxiSandboxBackend(
            sandbox_key=sandbox_id,
            container_name=info.container_name,
            thread_id=thread_id,
            host_user_data_dir=self.ensure_thread_dirs(thread_id),
            skills_host_path=get_skills_root_dir(),
        )
        with self._lock:
            self._sandboxes[sandbox_id] = backend
            self._sandbox_infos[sandbox_id] = info
            self._thread_sandboxes[thread_id] = sandbox_id
            self._last_activity[sandbox_id] = time.time()
        return backend

    def get(self, thread_id: str) -> YuxiSandboxBackend | None:
        with self._lock:
            sandbox_id = self._thread_sandboxes.get(thread_id)
            return self._sandboxes.get(sandbox_id) if sandbox_id else None

    def release(self, thread_id: str) -> None:
        with self._lock:
            sandbox_id = self._thread_sandboxes.pop(thread_id, None)
            if not sandbox_id:
                return
            info = self._sandbox_infos.pop(sandbox_id, None)
            self._sandboxes.pop(sandbox_id, None)
            self._last_activity.pop(sandbox_id, None)
            if info is not None:
                self._warm_pool[sandbox_id] = (info, time.time())

    def destroy(self, thread_id: str) -> None:
        sandbox_id = None
        info = None
        with self._lock:
            sandbox_id = self._thread_sandboxes.pop(thread_id, None) or self._deterministic_sandbox_id(thread_id)
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            self._last_activity.pop(sandbox_id, None)
            warm_info, _ = self._warm_pool.pop(sandbox_id, (None, None))
            if info is None:
                info = warm_info
        if info is not None:
            self._backend.destroy(info)

    def destroy_by_key(self, sandbox_id: str) -> None:
        info = None
        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            self._last_activity.pop(sandbox_id, None)
            self._warm_pool.pop(sandbox_id, None)
            thread_ids = [
                thread_id
                for thread_id, mapped_id in self._thread_sandboxes.items()
                if mapped_id == sandbox_id
            ]
            for thread_id in thread_ids:
                del self._thread_sandboxes[thread_id]
        if info is not None:
            self._backend.destroy(info)

    def _evict_oldest_warm(self) -> str | None:
        with self._lock:
            if not self._warm_pool:
                return None
            sandbox_id = min(self._warm_pool, key=lambda key: self._warm_pool[key][1])
            info, _ = self._warm_pool.pop(sandbox_id)
        self._backend.destroy(info)
        return sandbox_id

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            active_ids = list(self._sandboxes.keys())
            warm_items = list(self._warm_pool.items())
            self._warm_pool.clear()

        self._idle_checker_stop.set()
        if self._idle_checker_thread and self._idle_checker_thread.is_alive():
            self._idle_checker_thread.join(timeout=5)

        for sandbox_id in active_ids:
            try:
                self.destroy_by_key(sandbox_id)
            except Exception as exc:
                logger.warning(f"Failed to destroy sandbox {sandbox_id} during shutdown: {exc}")

        for sandbox_id, (info, _) in warm_items:
            try:
                self._backend.destroy(info)
            except Exception as exc:
                logger.warning(f"Failed to destroy warm sandbox {sandbox_id} during shutdown: {exc}")


_sandbox_provider: YuxiSandboxProvider | None = None


def get_sandbox_provider() -> YuxiSandboxProvider:
    global _sandbox_provider
    if _sandbox_provider is None:
        _sandbox_provider = YuxiSandboxProvider()
    return _sandbox_provider
