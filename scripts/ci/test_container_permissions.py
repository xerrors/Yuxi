"""在宿主用真实 Docker 验证镜像文件与存储初始化权限。"""

import json
import os
import re
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    """执行受测命令并保留可诊断的失败输出。"""
    return subprocess.run(args, capture_output=True, text=True, timeout=120, check=False, **kwargs)


def container_shell(image, command, *, state=None, user="0:0"):
    """在合成镜像中运行检查，可选挂载临时状态目录。"""
    mounts = ["-v", f"{state}:/state"] if state is not None else []
    return run("docker", "run", "--rm", "--user", user, *mounts, "--entrypoint", "sh", image, "-ec", command)


class ContainerPermissionTests(unittest.TestCase):
    """只处理临时合成目录、镜像与 Compose project。"""

    def test_api_image_files_are_readable_but_not_writable_by_runtime_user(self):
        """600/700 宿主输入必须成为 root 持有的可读项目文件。"""
        source = (ROOT / "docker/api.Dockerfile").read_text(encoding="utf-8")
        recipe = source[source.index("# 复制项目配置文件") : source.index("RUN uv sync")]
        entrypoint = next(line for line in source.splitlines() if line.startswith("COPY ") and "docker/api-entrypoint.sh" in line)
        with tempfile.TemporaryDirectory(prefix="yuxi-image-permissions-") as directory:
            context = Path(directory)
            for name in (
                "pyproject.toml",
                ".python-version",
                "uv.lock",
                "README.md",
                "MANIFEST.in",
                "yuxi/sub/module.py",
            ):
                path = context / "backend" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic-source\n")
                path.chmod(0o600)
            for path in (context / "backend").rglob("*"):
                if path.is_dir():
                    path.chmod(0o700)
            script = context / "docker/api-entrypoint.sh"
            script.parent.mkdir()
            script.write_text('#!/bin/sh\nexec "$@"\n')
            script.chmod(0o600)
            for broken in (False, True):
                with self.subTest(broken=broken):
                    image = "yuxi-permissions-test:" + uuid.uuid4().hex
                    fragment = re.sub(r"--chmod=0?644", "--chmod=0600", recipe) if broken else recipe
                    dockerfile = f"FROM alpine:3.20\nWORKDIR /app\n{fragment}\n{entrypoint}\nUSER 1000:1000\n"
                    try:
                        result = run(
                            "docker",
                            "build",
                            "-t",
                            image,
                            "-f",
                            "-",
                            str(context),
                            input=dockerfile,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        result = container_shell(
                            image,
                            "cat /app/pyproject.toml /app/yuxi/sub/module.py; test ! -w /app/pyproject.toml; "
                            "test -x /usr/local/bin/yuxi-entrypoint; "
                            'stat -c "%u:%g %a" /app/pyproject.toml /app/yuxi/sub /app/yuxi/sub/module.py',
                            user="1000:1000",
                        )
                        if broken:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn("Permission denied", result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(
                                result.stdout.splitlines(),
                                [
                                    "synthetic-source",
                                    "synthetic-source",
                                    "0:0 644",
                                    "0:0 755",
                                    "0:0 644",
                                ],
                            )
                    finally:
                        run("docker", "image", "rm", image)
            for path in context.rglob("*"):
                if path.is_dir():
                    path.chmod(0o700)

    def test_storage_initializer_preserves_existing_files_and_rejects_readonly_mount(
        self,
    ):
        """真实 Compose 准备根目录，保留历史文件，写入受阻时失败。"""
        with tempfile.TemporaryDirectory(prefix="yuxi-storage-permissions-") as directory:
            state = Path(directory) / "state"
            state.mkdir()
            project = "yuxi-permissions-" + uuid.uuid4().hex[:10]
            env = Path(directory) / "fixture.env"
            values = {
                "COMPOSE_PROJECT_NAME": project,
                "YUXI_STATE_DIR": str(state),
                "YUXI_ENV_FILE": str(env),
                "JWT_SECRET_KEY": "j" * 64,
                "API_KEY_DERIVATION_SECRET": "a" * 64,
                "YUXI_INSTANCE_ID": project,
                "POSTGRES_PASSWORD": "test-postgres",
                "NEO4J_PASSWORD": "test-neo4j",
                "MINIO_ACCESS_KEY": "test-access",
                "MINIO_SECRET_KEY": "test-secret",
                "CUBE_API_URL": "http://example.invalid:3000",
                "CUBE_PROXY_NODE_IP": "example.invalid",
                "CUBE_NODE_USER_DATA_DIR": "/data/shared/test/threads",
                "CUBE_NODE_SKILL_PROJECTION_DIR": "/data/shared/test/skills",
            }
            env.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
            compose = [
                "docker",
                "compose",
                "--env-file",
                str(env),
                "-f",
                str(ROOT / "docker-compose.prod.yml"),
            ]
            conflict = patch.dict(
                os.environ,
                {
                    "COMPOSE_PROJECT_NAME": project + "-wrong",
                    "YUXI_STATE_DIR": str(Path(directory) / "unrelated-state"),
                    "YUXI_ENV_FILE": str(env),
                },
            )
            conflict.start()
            self.addCleanup(conflict.stop)
            compose_env = {**os.environ, **values}
            config = run(*compose, "config", "--format", "json", env=compose_env)
            self.assertEqual(config.returncode, 0, config.stderr)
            resolved = json.loads(config.stdout)
            self.assertEqual(resolved["name"], project)
            initializer = resolved["services"]["storage-init"]
            self.assertTrue(all(volume["source"].startswith(str(state) + "/") for volume in initializer["volumes"]))
            image = initializer["image"]
            readonly_container = project + "-readonly"
            try:
                built = run(*compose, "build", "storage-init", env=compose_env)
                self.assertEqual(built.returncode, 0, built.stderr)
                seed = container_shell(
                    image,
                    "mkdir -p /state/yuxi/threads/legacy /state/milvus/milvus/data/cache/87/growing_mmap; "
                    "printf sentinel > /state/yuxi/threads/legacy/file; "
                    "printf milvus-sentinel > /state/milvus/milvus/data/cache/87/growing_mmap/file; "
                    "chown 1234:1235 /state/yuxi/threads/legacy /state/yuxi/threads/legacy/file "
                    "/state/milvus/milvus/data/cache/87/growing_mmap /state/milvus/milvus/data/cache/87/growing_mmap/file; "
                    "chmod 0711 /state/yuxi/threads/legacy /state/milvus/milvus/data/cache/87/growing_mmap; "
                    "chmod 0600 /state/yuxi/threads/legacy/file /state/milvus/milvus/data/cache/87/growing_mmap/file",
                    state=state,
                )
                self.assertEqual(seed.returncode, 0, seed.stderr)
                blocked = container_shell(
                    image,
                    "mkdir /state/milvus/milvus/data/cache/87/growing_mmap/segment",
                    state=state,
                    user="999:999",
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("Permission denied", blocked.stderr)
                for _ in range(2):
                    prepared = run(*compose, "run", "--rm", "--no-deps", "storage-init", env=compose_env)
                    self.assertEqual(prepared.returncode, 0, prepared.stderr)
                writable = container_shell(
                    image,
                    "mkdir /state/milvus/milvus/data/cache/87/growing_mmap/segment; "
                    "cat /state/milvus/milvus/data/cache/87/growing_mmap/file",
                    state=state,
                    user="999:999",
                )
                self.assertEqual(writable.returncode, 0, writable.stderr)
                self.assertEqual(writable.stdout, "milvus-sentinel")
                inspected = container_shell(
                    image,
                    'stat -c "%u:%g %a" /state/yuxi/threads /state/yuxi/skill-sources /state/yuxi/skill-projections '
                    "/state/milvus/milvus /state/milvus/logs /state/yuxi/threads/legacy /state/yuxi/threads/legacy/file "
                    "/state/milvus/milvus/data/cache/87/growing_mmap /state/milvus/milvus/data/cache/87/growing_mmap/file; "
                    "cat /state/yuxi/threads/legacy/file /state/milvus/milvus/data/cache/87/growing_mmap/file",
                    state=state,
                )
                self.assertEqual(inspected.returncode, 0, inspected.stderr)
                self.assertEqual(
                    inspected.stdout.splitlines(),
                    ["1000:1000 700"] * 3
                    + ["999:999 755"] * 2
                    + ["1234:1235 711", "1234:1235 600", "999:999 711", "999:999 600", "sentinelmilvus-sentinel"],
                )
                readonly = Path(directory) / "readonly.json"
                readonly.write_text(
                    json.dumps(
                        {
                            "services": {
                                "storage-init": {
                                    "volumes": [
                                        {
                                            "type": "bind",
                                            "source": str(state / "yuxi/threads"),
                                            "target": "/app/user-data",
                                            "read_only": True,
                                        }
                                    ]
                                }
                            }
                        }
                    )
                )
                denied = run(
                    *compose, "-f", str(readonly), "run", "--name", readonly_container, "--no-deps", "storage-init", env=compose_env
                )
                inspected_mounts = run("docker", "inspect", readonly_container)
                self.assertEqual(inspected_mounts.returncode, 0, inspected_mounts.stderr)
                mounts = json.loads(inspected_mounts.stdout)[0]["Mounts"]
                self.assertFalse(next(mount["RW"] for mount in mounts if mount["Destination"] == "/app/user-data"))
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn("Read-only file system", denied.stderr)
            finally:
                run("docker", "rm", "-f", readonly_container)
                cleanup_owner = "0:0" if os.name == "nt" else f"{os.getuid()}:{os.getgid()}"
                cleaned = container_shell(
                    image,
                    f"chmod -R u+rwX /state; chown -R {cleanup_owner} /state",
                    state=state,
                )
                self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
                run("docker", "image", "rm", image)
