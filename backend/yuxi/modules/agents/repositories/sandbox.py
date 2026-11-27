"""用户环境查询与跨进程生命周期锁。"""

import os
from contextlib import contextmanager
from typing import NamedTuple

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import select

from yuxi.infrastructure.postgres.connection import postgres_conninfo
from yuxi.modules.agents.models.sandbox import UserSandbox
from yuxi.modules.agents.runtime_locks import user_environment_lock_key
from yuxi.shared.errors import StateConflictError


class RegisteredSandbox(NamedTuple):
    """已登记 VM 的身份和持久状态。"""

    generation: int
    sandbox_id: str
    state: str

    @property
    def identity(self) -> tuple[int, str]:
        """休眠和恢复沿用同一实例身份。"""
        return self.generation, self.sandbox_id


async def registered_sandbox(db, uid: str) -> RegisteredSandbox | None:
    """读取已登记 VM，不创建、唤醒或暴露执行凭据。"""
    query = select(UserSandbox.generation, UserSandbox.sandbox_id, UserSandbox.state).where(UserSandbox.uid == uid)
    row = (await db.execute(query)).one_or_none()
    return RegisteredSandbox(*row) if row and row.sandbox_id else None


class SandboxRepository:
    """同步 executor 使用真实 PostgreSQL，不保存进程内业务状态。"""

    def connect(self):
        """取得独立短连接，凭据不进入日志。"""
        url = postgres_conninfo(os.environ.get("POSTGRES_URL", ""))
        if not url:
            raise RuntimeError("POSTGRES_URL is required for user environments")
        return psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5)

    @contextmanager
    def user_lock(self, uid: str, *, wait: bool = True):
        """外部调用期间持 session 锁，提交操作记录后仍保持互斥。"""
        with self.connect() as connection:
            key = user_environment_lock_key(uid)
            function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
            locked = connection.execute(f"SELECT {function}(hashtextextended(%s, 0)) AS locked", (key,)).fetchone()
            if not wait and not locked["locked"]:
                yield None
                return
            try:
                yield connection
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def environment(self, connection, uid: str):
        """读取该用户唯一环境。"""
        return connection.execute("SELECT * FROM user_sandboxes WHERE uid=%s", (uid,)).fetchone()

    def active_workspace_uids(self, uids: list[str]) -> set[str]:
        """只查标记用户，仍可能挂载可写目录的环境继续参与统计。"""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT uid FROM user_sandboxes WHERE uid=ANY(%s) AND (state <> 'paused' OR error IS NOT NULL)",
                (uids,),
            )
            return {row["uid"] for row in rows}

    def run_owner(self, run_id: str):
        """只读 Run 的执行归属与清理责任，不取得生命周期行锁。"""
        with self.connect() as connection:
            return connection.execute(
                "SELECT uid,status,worker_id,lease_expires_at,runtime_cleanup_pending FROM agent_runs WHERE id=%s",
                (run_id,),
            ).fetchone()

    def save_environment(self, connection, row: dict) -> None:
        """按固定列写入环境引用，不允许调用方拼接列名。"""
        connection.execute(
            "INSERT INTO user_sandboxes (uid,generation,sandbox_id,traffic_token,envd_token,state,operation_id,"
            "last_active_at,paused_at,error) "
            "VALUES (%(uid)s,%(generation)s,%(sandbox_id)s,%(traffic_token)s,%(envd_token)s,"
            "%(state)s,%(operation_id)s,%(last_active_at)s,%(paused_at)s,%(error)s) "
            "ON CONFLICT (uid) DO UPDATE SET generation=EXCLUDED.generation,sandbox_id=EXCLUDED.sandbox_id,"
            "traffic_token=EXCLUDED.traffic_token,envd_token=EXCLUDED.envd_token,state=EXCLUDED.state,"
            "operation_id=EXCLUDED.operation_id,last_active_at=EXCLUDED.last_active_at,paused_at=EXCLUDED.paused_at,error=EXCLUDED.error",
            row,
        )

    def active_runs(self, connection, uid: str) -> list[dict]:
        """取消收尾和 runtime cleanup 同样占用环境。"""
        return connection.execute(
            "SELECT id,thread_id,turn_id,status,worker_id,lease_expires_at FROM agent_runs "
            "WHERE uid=%s AND (status IN ('running','cancel_requested') OR runtime_cleanup_pending) ORDER BY created_at",
            (uid,),
        ).fetchall()

    def commands(self, connection, uid: str, *, run_id: str | None = None) -> list[dict]:
        """查询尚未确认停止的命令。"""
        if run_id is not None:
            return connection.execute("SELECT * FROM sandbox_commands WHERE uid=%s AND run_id=%s", (uid, run_id)).fetchall()
        return connection.execute("SELECT * FROM sandbox_commands WHERE uid=%s", (uid,)).fetchall()

    def add_command(self, connection, row: dict) -> None:
        """外部启动前先保存 owner 与代次。"""
        if row["run_id"]:
            self.require_run_owner(connection, row["uid"], row["run_id"], row["owner"])
        connection.execute(
            "INSERT INTO sandbox_commands(id,uid,generation,run_id,owner,kind,phase,deadline) "
            "VALUES (%(id)s,%(uid)s,%(generation)s,%(run_id)s,%(owner)s,%(kind)s,'starting',%(deadline)s)",
            row,
        )

    def require_run_owner(self, connection, uid: str, run_id: str, worker_id: str) -> None:
        """在有副作用的来宾操作前校验当前 Run、执行者和租约。"""
        run = connection.execute(
            "SELECT uid,status,worker_id,lease_expires_at > now() AS lease_valid FROM agent_runs WHERE id=%s",
            (run_id,),
        ).fetchone()
        if not run or run["uid"] != uid or run["status"] != "running" or run["worker_id"] != worker_id:
            raise StateConflictError("Run 已取消或执行 owner 已改变", code="lease_lost")
        if not run["lease_valid"]:
            raise StateConflictError("Run 执行租约已过期", code="lease_lost")

    def command_started(self, command_id: str, pgid: int) -> None:
        """保存真实启动事件的进程组。"""
        with self.connect() as connection:
            connection.execute("UPDATE sandbox_commands SET pgid=%s,phase='running' WHERE id=%s", (pgid, command_id))

    def finish_command(self, uid: str, command_id: str) -> None:
        """仅确认完成后移除句柄，并更新时间。"""
        with self.user_lock(uid) as connection:
            self.remove_command(connection, uid, command_id)

    def mark_cleanup(self, uid: str, command_id: str) -> None:
        """断流保留清理责任，不能当成终态。"""
        with self.connect() as connection:
            connection.execute("UPDATE sandbox_commands SET phase='cleanup' WHERE uid=%s AND id=%s", (uid, command_id))

    def remove_command(self, connection, uid: str, command_id: str) -> None:
        """在现有用户锁内完成句柄清理和活动时间更新。"""
        connection.execute("DELETE FROM sandbox_commands WHERE uid=%s AND id=%s", (uid, command_id))
        connection.execute("UPDATE user_sandboxes SET last_active_at=now() WHERE uid=%s", (uid,))

    def stale_commands(self) -> list[dict]:
        """查找超时、失去 Run owner 或已结束执行的残留命令。"""
        with self.connect() as connection:
            return connection.execute(
                "SELECT c.* FROM sandbox_commands c LEFT JOIN agent_runs r ON r.id=c.run_id "
                "WHERE c.phase='cleanup' OR c.deadline<=now() OR (c.run_id IS NOT NULL AND "
                "(r.id IS NULL OR r.worker_id IS DISTINCT FROM c.owner OR r.status NOT IN ('running','cancel_requested') "
                "OR r.lease_expires_at IS NULL OR r.lease_expires_at<=now()))",
            ).fetchall()

    def environments(self) -> list[dict]:
        """供单节点周期维护遍历持久环境。"""
        with self.connect() as connection:
            return connection.execute("SELECT * FROM user_sandboxes ORDER BY uid").fetchall()
