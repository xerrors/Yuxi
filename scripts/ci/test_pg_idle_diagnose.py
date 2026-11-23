import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "pg-idle-in-transaction-diagnose.sh"
PASSWORD = "fixture-only-do-not-print"
INTERNAL = f"postgresql+asyncpg://postgres:{PASSWORD}@postgres:5432/yuxi"
SINGLE_PROJECT = "db local postgres\napi local api\nworker local worker"

# Docker 替身执行真实容器内检查代码，只替换容器环境、DNS 和 psql。
DOCKER = r"""#!/usr/bin/env python3
import json
import os
import socket
import sys

args = sys.argv[1:]
with open(os.environ["COMMANDS"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args[0] == "ps":
    if "-q" in args:
        print("api\nworker")
    else:
        print(os.environ["CONTAINERS"])
elif args[0] == "inspect":
    if ".Name" in args[2]:
        print("172.18.0.60 /worker\n172.18.0.6 /api")
    else:
        print("172.18.0.2")
elif args[0] == "exec":
    cid, command, *values = args[1:]
    if command == "printenv":
        print({"POSTGRES_USER": "postgres", "POSTGRES_DB": "yuxi"}[values[0]])
    elif command == "python3":
        settings = json.loads(os.environ["APP_SETTINGS"]).get(cid)
        os.environ.pop("POSTGRES_URL", None)
        if settings and settings["url"] is not None:
            os.environ["POSTGRES_URL"] = settings["url"]
        socket.getaddrinfo = lambda *a, **kw: [
            (None, None, None, None, (ip, 5432)) for ip in settings["ips"]
        ]
        sys.argv = ["-c", *values[2:]]
        exec(values[1])
    elif command == "env" and "psql" in values:
        if "-At" in values:
            sql = values[-1]
            if os.environ.get("LIMITED_STATS") == "1" and "pg_read_all_stats" in sql:
                raise SystemExit(0)
            # 同时存在 600 秒的活跃事务与 8 秒的空闲事务；
            # 没限定空闲状态的摘要会在输出里暴露过长的年龄。
            age = 8 if "FROM pg_stat_activity WHERE state='idle in transaction'" in sql else 600
            print(f"1|{age}|3|100")
        else:
            print("fixture diagnostic rows")
    else:
        raise SystemExit(f"unexpected command: {args!r}")
else:
    raise SystemExit(f"unexpected command: {args!r}")
"""


class PgIdleDiagnoseTests(unittest.TestCase):
    def run_diagnose(
        self,
        *args,
        containers=SINGLE_PROJECT,
        settings=None,
        project=None,
        limited_stats=False,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docker = root / "docker"
            docker.write_text(DOCKER)
            docker.chmod(0o755)
            commands = root / "commands.jsonl"
            env = {
                **os.environ,
                "PATH": f"{root}{os.pathsep}{os.defpath}",
                "COMMANDS": str(commands),
                "CONTAINERS": containers,
                "LIMITED_STATS": str(int(limited_stats)),
                "APP_SETTINGS": json.dumps(
                    settings
                    if settings is not None
                    else {
                        name: {"url": INTERNAL, "ips": ["172.18.0.2"]}
                        for name in ("api", "worker")
                    }
                ),
            }
            env.pop("COMPOSE_PROJECT", None)
            env.pop("PG_SERVICE", None)
            if project:
                env["COMPOSE_PROJECT"] = project
            result = subprocess.run(
                ["bash", str(SCRIPT), *args],
                env=env,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            calls = (
                [json.loads(line) for line in commands.read_text().splitlines()]
                if commands.exists()
                else []
            )
            self.assertNotIn(
                PASSWORD, result.stdout + result.stderr + json.dumps(calls)
            )
            return result, calls

    def test_ambiguous_stack_requires_explicit_project(self):
        # 第二个项目仅运行外部数据库的 API，也不能被无视。
        containers = SINGLE_PROJECT + "\nother-api other api"
        result, calls = self.run_diagnose(containers=containers)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("多个候选 Compose 项目", result.stderr)
        self.assertFalse(any(call[0] == "exec" for call in calls))

        for args, env_project in [(("-p", "local"), None), ((), "local")]:
            with self.subTest(args=args, project=env_project):
                result, calls = self.run_diagnose(
                    *args, containers=containers, project=env_project
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("project=local", result.stdout)
                self.assertFalse(any("other-api" in call for call in calls))

    def test_external_or_unverified_app_target_never_reaches_sql(self):
        settings_by_case = [
            (INTERNAL.replace("@postgres:", "@external.example:"), ["172.18.0.2"]),
            (INTERNAL.replace(":5432/", ":6543/"), ["172.18.0.2"]),
            (INTERNAL.replace("/yuxi", "/otherdb"), ["172.18.0.2"]),
            (INTERNAL + "?host=external.example", ["172.18.0.2"]),
            (None, ["172.18.0.2"]),
            (INTERNAL, ["172.18.0.8"]),
            (INTERNAL, ["172.18.0.2", "172.18.0.8"]),
            (INTERNAL, []),
        ]
        for case, (url, ips) in enumerate(settings_by_case):
            with self.subTest(case=case):
                result, calls = self.run_diagnose(
                    settings={
                        "api": {"url": INTERNAL, "ips": ["172.18.0.2"]},
                        "worker": {"url": url, "ips": ips},
                    }
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("未采集", result.stderr)
                self.assertFalse(any("psql" in call for call in calls))

    def test_unverified_or_ambiguous_postgres_container_never_reaches_sql(self):
        for containers in [
            "db local postgres",
            SINGLE_PROJECT + "\nreplica local postgres",
        ]:
            with self.subTest(containers=containers):
                result, calls = self.run_diagnose(containers=containers)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("psql" in call for call in calls))

    def test_idle_age_ignores_active_transactions_and_client_count_is_explicit(self):
        result, calls = self.run_diagnose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "idleInTxn=1 空闲会话最长事务龄=8s | 集群客户端连接=3/100", result.stdout
        )
        self.assertIn("不能证明数据库容量不足", result.stdout)
        queries = [call for call in calls if "psql" in call]
        self.assertEqual(len(queries), 4)
        self.assertIn("WHERE backend_type='client backend'", queries[0][-1])
        for call in queries:
            self.assertEqual(call[:2], ["exec", "db"])
            self.assertEqual(call[call.index("-h") + 1], "/var/run/postgresql")
            self.assertIn("PGHOSTADDR", call)
            self.assertIn("PGSERVICE", call)

    def test_zero_watch_does_not_touch_docker(self):
        result, calls = self.run_diagnose("-w", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("正整数秒", result.stderr)
        self.assertEqual(calls, [])

    def test_insufficient_stats_privileges_do_not_look_healthy(self):
        result, calls = self.run_diagnose(limited_stats=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pg_read_all_stats", result.stderr)
        self.assertNotIn("idleInTxn=", result.stdout)
        self.assertEqual(sum("psql" in call for call in calls), 1)

    def test_whois_compares_exact_container_address(self):
        result, _ = self.run_diagnose("--whois", "172.18.0.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "172.18.0.6 /api")
