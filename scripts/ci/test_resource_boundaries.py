"""验证临时托管探针拒绝普通机器及 self-hosted runner。"""

import base64
import copy
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HostedProbeTests(unittest.TestCase):
    """安装与真实 VM 探针不能误用于持久环境。"""

    def test_probe_rejects_non_hosted_environment_before_side_effects(self):
        """恢复缺失的托管边界时，负控必须失败。"""
        with tempfile.TemporaryDirectory() as root:
            self._check_rejection(Path(root))

    def test_system_environment_requires_runner_temp_before_writing(self):
        """遗漏托管临时目录时拒绝，不能覆盖已有环境文件。"""
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".env"
            path.write_text("existing-fixture\n")
            env = {**os.environ, "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
            env.pop("RUNNER_TEMP", None)
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/ci/prepare_system_tests_env.sh")],
                cwd=root, env=env, capture_output=True, text=True, timeout=5, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RUNNER_TEMP", result.stderr)
            self.assertEqual(path.read_text(), "existing-fixture\n")

    def _check_rejection(self, root: Path) -> None:
        """替换副作用命令，guard 回归也不能安装到本机。"""
        calls = root / "calls"
        for command in ("sudo", "curl"):
            shim = root / command
            shim.write_text(f"#!/bin/sh\nprintf '%s\\n' called >> '{calls}'\nexit 99\n")
            shim.chmod(0o755)
        for script in ("probe_kvm.py", "probe_cube_workspace.py", "probe_hosted_cube.sh", "prepare_system_tests_env.sh"):
            for github, runner in (("false", "github-hosted"), ("true", "self-hosted"), ("", "")):
                with self.subTest(script=script, github=github, runner=runner):
                    result = subprocess.run(
                        ["bash" if script.endswith(".sh") else sys.executable, str(ROOT / "scripts/ci" / script)],
                        env={
                            **os.environ, "GITHUB_ACTIONS": github, "RUNNER_ENVIRONMENT": runner,
                            "PATH": f"{root}:{os.environ['PATH']}",
                            "CUBE_API_URL": "http://127.0.0.1:1", "CUBE_PROXY_NODE_IP": "127.0.0.1",
                        },
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("仅允许在 GitHub 临时托管执行器运行", result.stderr)
                    self.assertFalse(calls.exists(), "guard 拒绝前调用了副作用命令")


class ServiceIntegrationTests(unittest.TestCase):
    """迁移后的数据库检查在托管 job 中拥有实际执行入口。"""

    def assert_service_inventory(self, workflow: str, script: str) -> None:
        """保留原工程门禁拥有的 Schema、FIFO 与 lease 选择器。"""
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("run: bash scripts/ci/run_service_integration.sh", workflow)
        command = re.search(r"(?m)^uv run --locked --group test pytest (.*)", script.replace("\\\n", ""))
        self.assertIsNotNone(command, "缺少实际 pytest 命令")
        selectors = shlex.split(command.group(1), comments=True)
        self.assertIn("test/integration/api/test_error_information_boundary.py", selectors)
        for name in ("schema_migration_version", "agent_input_concurrency", "agent_run_lease"):
            self.assertIn(f"test/integration/services/test_{name}.py", selectors)

    def test_service_inventory_is_wired(self):
        """执行入口与迁移后的关键检查共同存在。"""
        self.assert_service_inventory(
            (ROOT / ".github/workflows/service-integration.yml").read_text(),
            (ROOT / "scripts/ci/run_service_integration.sh").read_text(),
        )

    def test_service_inventory_rejects_missing_error_boundary(self):
        """去掉真实 HTTP 信息边界回归时接线门禁失败。"""
        workflow = (ROOT / ".github/workflows/service-integration.yml").read_text()
        script = (ROOT / "scripts/ci/run_service_integration.sh").read_text()
        with self.assertRaises(AssertionError):
            self.assert_service_inventory(workflow, script.replace("test/integration/api/test_error_information_boundary.py", ""))

    def test_service_setup_selects_container_without_reachable_execution_nodes(self):
        """初始化与 pytest 使用相同配置，不能隐式依赖 Cube 或现成 runner。"""
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "backend").mkdir()
            (base / "scripts/ci").mkdir(parents=True)
            script = base / "scripts/ci/run_service_integration.sh"
            script.write_text((ROOT / "scripts/ci/run_service_integration.sh").read_text())
            uv = base / "uv"
            uv.write_text(
                f"#!{sys.executable}\n"
                "import json, os\n"
                "keys = ('SANDBOX_BACKEND', 'SANDBOX_RUNNER_URL', 'SANDBOX_RUNNER_TOKEN', 'CUBE_API_URL', 'TEST_BASE_URL')\n"
                "with open(os.environ['ENV_LOG'], 'a') as log:\n"
                "    log.write(json.dumps({key: os.environ.get(key) for key in keys}) + '\\n')\n"
            )
            uv.chmod(0o755)
            log = base / "env.jsonl"
            subprocess.run(
                ["bash", str(script)],
                env={
                    "PATH": f"{base}:{os.environ['PATH']}", "POSTGRES_URL": "fixture",
                    "REDIS_URL": "fixture", "YUXI_SKILL_DATA_DIR": root, "ENV_LOG": str(log),
                    "SANDBOX_BACKEND": "cube", "SANDBOX_RUNNER_URL": "http://existing-runner:8090",
                },
                capture_output=True, text=True, timeout=5, check=True,
            )
            environments = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(environments), 2)
            for env in environments:
                self.assertEqual(env["SANDBOX_BACKEND"], "container")
                for key in ("SANDBOX_RUNNER_URL", "CUBE_API_URL", "TEST_BASE_URL"):
                    self.assertEqual(env[key], "http://127.0.0.1:1")
                self.assertGreaterEqual(len(env["SANDBOX_RUNNER_TOKEN"]), 16)

    def test_service_setup_requires_explicit_resource_locations(self):
        """遗漏任一资源位置时不得初始化默认数据库或 Skill 目录。"""
        with tempfile.TemporaryDirectory() as root:
            calls = Path(root) / "calls"
            uv = Path(root) / "uv"
            uv.write_text(f"#!/bin/sh\nprintf '%s\\n' called >> '{calls}'\nexit 99\n")
            uv.chmod(0o755)
            resources = {"POSTGRES_URL": "fixture", "REDIS_URL": "fixture", "YUXI_SKILL_DATA_DIR": root}
            for missing in resources:
                with self.subTest(missing=missing):
                    result = subprocess.run(
                        ["bash", str(ROOT / "scripts/ci/run_service_integration.sh")],
                        env={"PATH": f"{root}:{os.environ['PATH']}", **{k: v for k, v in resources.items() if k != missing}},
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(missing, result.stderr)
                    self.assertFalse(calls.exists(), "位置校验前执行了初始化")

    def test_service_setup_rejects_local_dotenv_before_initialization(self):
        """本地部署配置不能覆盖显式传入的测试资源。"""
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "backend").mkdir()
            (base / "backend/.env").write_text("POSTGRES_URL=deployment-sentinel\n")
            (base / "scripts/ci").mkdir(parents=True)
            script = base / "scripts/ci/run_service_integration.sh"
            script.write_text((ROOT / "scripts/ci/run_service_integration.sh").read_text())
            calls = base / "calls"
            uv = base / "uv"
            uv.write_text(f"#!/bin/sh\nprintf '%s\\n' called >> '{calls}'\nexit 99\n")
            uv.chmod(0o755)
            result = subprocess.run(
                ["bash", str(script)],
                env={
                    "PATH": f"{root}:{os.environ['PATH']}", "POSTGRES_URL": "fixture",
                    "REDIS_URL": "fixture", "YUXI_SKILL_DATA_DIR": root,
                },
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("拒绝加载 backend/.env", result.stderr)
            self.assertFalse(calls.exists(), "dotenv 拒绝前执行了初始化")

    def test_service_inventory_rejects_lost_execution_or_selectors(self):
        """删除选择器、注释执行入口或恢复 self-hosted 时必须拒绝。"""
        workflow = (ROOT / ".github/workflows/service-integration.yml").read_text()
        script = (ROOT / "scripts/ci/run_service_integration.sh").read_text()
        for name in ("schema_migration_version", "agent_input_concurrency", "agent_run_lease"):
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self.assert_service_inventory(workflow, script.replace(f"test_{name}.py", "removed.py"))
        for broken in (
            workflow.replace("runs-on: ubuntu-24.04", "runs-on: self-hosted"),
            workflow.replace("run: bash scripts/ci/run_service_integration.sh", "run: true"),
        ):
            with self.assertRaises(AssertionError):
                self.assert_service_inventory(broken, script)
        with self.assertRaises(AssertionError):
            self.assert_service_inventory(workflow, script.replace("uv run --locked --group test pytest", "# pytest"))


class RuntimeTopologyTests(unittest.TestCase):
    """常规系统拓扑使用 container，真实 Cube 只由手动探针验证。"""

    def assert_milvus_e2e_is_required(self, workflow: str) -> None:
        """检索 E2E 必须在独立拓扑中执行并由最终结果汇总。"""
        background = workflow.split("  background-job-worker-path:\n", 1)[1].split("\n  cooperation-e2e:\n", 1)[0]
        selector = "test/e2e/test_milvus_text_retrieval_e2e.py"
        self.assertEqual(workflow.count(selector), 1)
        step = background.split("      - name: Verify Milvus 3 text retrieval through worker\n", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("run: docker compose exec -T", step)
        self.assertIn(selector, step)
        self.assertNotIn("if:", step)
        self.assertNotIn("continue-on-error:", step)
        result = workflow.split("\n  result:\n", 1)[1]
        self.assertIn(
            "needs: [background-job-worker-path, cooperation-e2e, lifecycle-worker-e2e, runtime-contracts, sandbox-isolation]",
            result,
        )
        self.assertIn("scripts/ci/gate.py check-jobs", result)

    def test_milvus_e2e_is_required_in_background_topology(self):
        """移动检索测试后，遗漏执行入口或结果汇总必须拒绝。"""
        workflow = (ROOT / ".github/workflows/system-tests.yml").read_text()
        self.assert_milvus_e2e_is_required(workflow)
        for original, replacement in (
            ("test/e2e/test_milvus_text_retrieval_e2e.py", "test/e2e/removed.py"),
            (
                "run: docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api "
                "uv run --no-sync --no-dev pytest --junitxml=test/ci-reports/system-19.xml",
                "run: true #",
            ),
            (
                "needs: [background-job-worker-path, cooperation-e2e, lifecycle-worker-e2e, runtime-contracts, sandbox-isolation]",
                "needs: [cooperation-e2e, lifecycle-worker-e2e, runtime-contracts, sandbox-isolation]",
            ),
        ):
            with self.subTest(original=original), self.assertRaises(AssertionError):
                self.assert_milvus_e2e_is_required(workflow.replace(original, replacement))

    REPLAY_JOBS = ("cooperation-e2e", "lifecycle-worker-e2e", "runtime-contracts")

    def assert_toggle_and_replay_wiring(self, workflow: str) -> None:
        """每个运行 pytest 的拓扑 job 启动共享重放；Knowledge 开关只在契约 job 且先于重放启动。"""
        toggle = "      - name: Verify optional Knowledge deployment and chat recovery"
        replay = "      - name: Start deterministic OpenAI-compatible replay"
        self.assertEqual(workflow.count(toggle), 1)
        self.assertEqual(workflow.count(replay), 3)
        jobs = re.split(r"(?m)^  ([\w-]+):\n", workflow.split("\njobs:\n", 1)[1])
        bodies = dict(zip(jobs[1::2], jobs[2::2], strict=True))
        for name in self.REPLAY_JOBS:
            self.assertIn(replay, bodies[name])
        self.assertNotIn(replay, bodies["background-job-worker-path"])
        self.assertNotIn(replay, bodies["sandbox-isolation"])
        contracts = bodies["runtime-contracts"]
        self.assertLess(contracts.index(toggle), contracts.index(replay), "Knowledge 开关会重建 api 容器，必须先于共享模型重放启动")

    def test_replay_and_knowledge_switch_wiring(self):
        """重放归属与开关顺序必须与实际工作流一致。"""
        self.assert_toggle_and_replay_wiring((ROOT / ".github/workflows/system-tests.yml").read_text())

    def test_knowledge_switch_or_replay_wiring_broken_is_rejected(self):
        """删除开关、删除重放或将开关移到重放启动之后时，接线检查必须失败。"""
        workflow = (ROOT / ".github/workflows/system-tests.yml").read_text()
        toggle = "      - name: Verify optional Knowledge deployment and chat recovery"
        replay = "      - name: Start deterministic OpenAI-compatible replay"
        without_toggle = workflow.replace(toggle, "      - name: removed-toggle")
        without_replay = workflow.replace(replay, "      - name: removed-replay", 1)
        start = workflow.index(toggle)
        end = workflow.index("      - name:", start + 1)
        switch = workflow[start:end]
        without_switch_block = workflow[:start] + workflow[end:]
        late_target = without_switch_block.index("      - name: Verify worker shipping health")
        late_switch = without_switch_block[:late_target] + switch + without_switch_block[late_target:]
        for broken in (without_toggle, without_replay, late_switch):
            with self.subTest(broken=broken[:80]), self.assertRaises(AssertionError):
                self.assert_toggle_and_replay_wiring(broken)

    TOPOLOGY_JOBS = ("background-job-worker-path", "cooperation-e2e", "lifecycle-worker-e2e", "runtime-contracts")

    def assert_hosted_jobs(self, workflow: str) -> None:
        """四个拓扑 job 均准备 container 镜像和 runner，保留构建缓存；沙盒 job 复用镜像但不建拓扑。"""
        jobs = re.split(r"(?m)^  [\w-]+:\n", workflow.split("\njobs:\n", 1)[1])[1:]
        self.assertEqual(len(jobs), 6)
        for job in jobs[:4]:
            self.assertIn("runs-on: ubuntu-24.04", job)
            self.assertNotIn("probe_kvm.py", job)
            self.assertNotIn("probe_hosted_cube.sh", job)
            self.assertNotIn("test_cube_worker_recovery.sh", job)
            self.assertLess(job.index("bash scripts/ci/prepare_system_tests_env.sh"), job.index("docker compose up -d"))
            start = re.search(r"(?m)^        run: docker compose up -d (.+)$", job)
            self.assertIsNotNone(start)
            self.assertIn("sandbox-runner", shlex.split(start.group(1)))
            self.assertIn("id: sandbox-cache", job)
            saves = list(re.finditer(r"(?m)^        uses: actions/cache/save@v4$", job))
            self.assertEqual(len(saves), 3)
            for save in saves:
                self.assertLess(job.index("bash scripts/ci/build_topology_images.sh"), save.start())
                self.assertLess(save.start(), job.index("docker compose up -d"))
        sandbox = jobs[4]
        self.assertIn("runs-on: ubuntu-24.04", sandbox)
        self.assertNotIn("self-hosted", sandbox)
        self.assertIn("id: sandbox-cache", sandbox)
        self.assertLess(
            sandbox.index("bash scripts/ci/prepare_system_tests_env.sh"),
            sandbox.index("scripts/ci/build_topology_images.sh"),
        )
        self.assertIn("--sandbox-only", sandbox)
        self.assertNotIn("buildkit-api-", sandbox, "沙盒 job 不需要 api 镜像及其缓存")
        self.assertIn("SANDBOX_CONTAINER_TEST=1", sandbox)
        self.assertIn("test/integration/sandbox/test_container_sandbox.py", sandbox)

    def test_runtime_jobs_use_container(self):
        """遗漏 runner、镜像缓存或恢复真实 Cube 安装时拒绝。"""
        workflow = (ROOT / ".github/workflows/system-tests.yml").read_text()
        self.assert_hosted_jobs(workflow)
        for original in ("runs-on: ubuntu-24.04", "sandbox-runner", "id: sandbox-cache", "uses: actions/cache/save@v4"):
            for name in self.TOPOLOGY_JOBS:
                with self.subTest(original=original, job=name), self.assertRaises((AssertionError, ValueError)):
                    before, section = workflow.split(f"  {name}:\n", 1)
                    self.assert_hosted_jobs(before + f"  {name}:\n" + section.replace(original, "removed", 1))
        for original in ("SANDBOX_CONTAINER_TEST=1", "--sandbox-only", "id: sandbox-cache", "runs-on: ubuntu-24.04"):
            with self.subTest(original=original, job="sandbox-isolation"), self.assertRaises((AssertionError, ValueError)):
                before, section = workflow.split("  sandbox-isolation:\n", 1)
                self.assert_hosted_jobs(before + "  sandbox-isolation:\n" + section.replace(original, "removed", 1))
        with self.assertRaises(AssertionError):
            self.assert_hosted_jobs(workflow.replace("steps:\n", "steps:\n      - run: bash scripts/ci/probe_hosted_cube.sh\n", 1))

    def test_cube_probe_only_runs_manually(self):
        """Cube 安装不能重新进入普通 push 或 PR 路径。"""
        workflow = (ROOT / ".github/workflows/cube-hosted-probe.yml").read_text()
        self.assert_manual_probe(workflow)
        for event in ("push", "pull_request", "schedule"):
            with self.subTest(event=event), self.assertRaises(AssertionError):
                self.assert_manual_probe(workflow.replace("on:\n", f"on:\n  {event}:\n"))

    def assert_real_provider_probe_uses_hosted_container(self, workflow: str) -> None:
        """真实供应商探针使用与环境准备脚本一致的临时托管拓扑。"""
        self.assert_manual_probe(workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertLess(workflow.index("Require real-provider credential"), workflow.index("Prepare isolated Compose environment"))
        self.assertIn("bash scripts/ci/prepare_system_tests_env.sh", workflow)
        self.assertIn("docker compose up -d --build postgres redis minio etcd milvus graph sandbox-runner api worker", workflow)
        self.assertIn("secrets.SILICONFLOW_API_KEY", workflow)
        self.assertNotIn("CUBE_API_KEY", workflow)

    def test_real_provider_probe_uses_hosted_container(self):
        """恢复 self-hosted 或遗漏沙盒时，手动探针接线检查失败。"""
        workflow = (ROOT / ".github/workflows/real-provider-probe.yml").read_text()
        self.assert_real_provider_probe_uses_hosted_container(workflow)
        for original, replacement in (
            ("runs-on: ubuntu-24.04", "runs-on: self-hosted"),
            ("graph sandbox-runner api worker", "graph api worker"),
            ("minio etcd milvus graph", "minio"),
            ("secrets.SILICONFLOW_API_KEY", "vars.SILICONFLOW_API_KEY"),
        ):
            with self.subTest(original=original), self.assertRaises(AssertionError):
                self.assert_real_provider_probe_uses_hosted_container(workflow.replace(original, replacement))

    def test_real_provider_probe_requires_credential(self):
        """实际凭据准入脚本在密钥缺失时退出，不能启动拓扑。"""
        workflow = (ROOT / ".github/workflows/real-provider-probe.yml").read_text()
        step = workflow.split("      - name: Require real-provider credential\n", 1)[1].split("\n      - ", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        for credential, expected in (("", 1), ("fixture-provider-key", 0)):
            with self.subTest(configured=bool(credential)):
                result = subprocess.run(
                    ["bash", "-c", script], env={"SILICONFLOW_API_KEY": credential},
                    capture_output=True, text=True, timeout=5, check=False,
                )
                self.assertEqual(result.returncode, expected)
                if not credential:
                    self.assertIn("repository secret is required", result.stderr)

    def assert_manual_probe(self, workflow: str) -> None:
        """读取实际事件声明，手动入口必须保留。"""
        events = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  ([\w-]+):", events), ["workflow_dispatch"])

    def test_environment_matches_real_compose_mounts_and_runner(self):
        """执行环境生成脚本并展开真实 Compose，证明认证与挂载一致。"""
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / ".env.template").write_text((ROOT / ".env.template").read_text())
            env = {k: v for k, v in os.environ.items() if not k.startswith(("CUBE_", "YUXI_", "SANDBOX_", "COMPOSE_"))}
            env.pop("SILICONFLOW_API_KEY", None)
            env.update(GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted", RUNNER_TEMP=root)
            env.update(
                JWT_SECRET_KEY="ci-jwt-secret-at-least-thirty-two-characters",
                API_KEY_DERIVATION_SECRET="ci-api-key-derivation-secret-at-least-32-chars",
            )
            subprocess.run(["bash", str(ROOT / "scripts/ci/prepare_system_tests_env.sh")], cwd=base, env=env, check=True)
            self.assertEqual((base / ".env").stat().st_mode & 0o777, 0o600)
            config = json.loads(subprocess.check_output(
                ["docker", "compose", "--env-file", str(base / ".env"), "-f", str(ROOT / "docker-compose.yml"),
                 "config", "--format", "json"],
                env={**env, "YUXI_ENV_FILE": str(base / ".env")}, text=True,
            ))
            for name in ("api", "worker"):
                for key in ("SILICONFLOW_API_KEY", "WEB_SEARCH_PROVIDER", "DOUBAO_SEARCH_API_KEY", "TAVILY_API_KEY", "DEEPSEEK_API_KEY"):
                    self.assertEqual(config["services"][name]["environment"][key], "", f"{name}.{key} 必须保留模板空值")
            api_key = config["services"]["api"]["environment"]["YUXI_ENCRYPTION_KEY"]
            self.assertEqual(len(base64.urlsafe_b64decode(api_key)), 32)
            self.assertEqual(api_key, config["services"]["worker"]["environment"]["YUXI_ENCRYPTION_KEY"])
            self.assert_container_state(config)
            probe_env = {**env, "SILICONFLOW_API_KEY": "fixture-provider-key"}
            subprocess.run(["bash", str(ROOT / "scripts/ci/prepare_system_tests_env.sh")], cwd=base, env=probe_env, check=True)
            probe_config = json.loads(subprocess.check_output(
                ["docker", "compose", "--env-file", str(base / ".env"), "-f", str(ROOT / "docker-compose.yml"),
                 "config", "--format", "json"],
                env={**probe_env, "YUXI_ENV_FILE": str(base / ".env")}, text=True,
            ))
            for name in ("api", "worker"):
                self.assertEqual(probe_config["services"][name]["environment"]["SILICONFLOW_API_KEY"], "fixture-provider-key")
            for target in ("/app/user-data", "/app/skill-projections"):
                broken = copy.deepcopy(config)
                next(m for m in broken["services"]["worker"]["volumes"] if m["target"] == target)["source"] = "/wrong-checkout"
                with self.subTest(target=target), self.assertRaises(AssertionError):
                    self.assert_container_state(broken)
            for name in ("api", "worker"):
                broken = copy.deepcopy(config)
                broken["services"][name]["environment"]["SANDBOX_BACKEND"] = "cube"
                with self.subTest(backend=name), self.assertRaises(AssertionError):
                    self.assert_container_state(broken)
            broken = copy.deepcopy(config)
            broken["services"]["sandbox-runner"]["environment"]["SANDBOX_RUNNER_TOKEN"] = "wrong-token"
            with self.assertRaises(AssertionError):
                self.assert_container_state(broken)
            broken = copy.deepcopy(config)
            del broken["services"]["sandbox-runner"]
            with self.assertRaises(KeyError):
                self.assert_container_state(broken)

    def test_image_builder_requires_and_builds_sandbox_image(self):
        """真实 shell 必须构建 sandbox 镜像，profile 遗漏时在构建前失败。"""
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            docker = base / "docker"
            docker.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = compose ]; then cat "$BUILD_CONFIG"; else printf "%s\\n" "$*" >> "$BUILD_LOG"; fi\n'
            )
            docker.chmod(0o700)
            config = {"services": {name: {"image": f"test-{name}"} for name in ("api", "minio", "storage-init", "sandbox-image")}}
            config_file = base / "config.json"
            log = base / "build.log"
            env = {
                **os.environ, "PATH": f"{base}:{os.environ['PATH']}",
                "BUILD_CONFIG": str(config_file), "BUILD_LOG": str(log),
            }
            for configured in (True, False):
                with self.subTest(configured=configured):
                    log.unlink(missing_ok=True)
                    value = copy.deepcopy(config)
                    if not configured:
                        del value["services"]["sandbox-image"]
                    config_file.write_text(json.dumps(value))
                    result = subprocess.run(
                        ["bash", str(ROOT / "scripts/ci/build_topology_images.sh"), "", "", str(base / "sandbox-cache")],
                        env=env, capture_output=True, text=True, timeout=10, check=False,
                    )
                    if not configured:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(log.exists())
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    commands = [shlex.split(line) for line in log.read_text().splitlines()]
                    self.assertEqual(len(commands), 4)
                    sandbox = next(command for command in commands if "docker/sandbox.Dockerfile" in command)
                    self.assertEqual(sandbox[sandbox.index("--tag") + 1], "test-sandbox-image")
                    self.assertIn(f"type=local,dest={base}/sandbox-cache,mode=max", sandbox)
                    self.assertIn("--load", sandbox)

    def test_image_builder_sandbox_only_mode(self):
        """--sandbox-only 只构建 sandbox 镜像，遗漏配置时仍在构建前失败。"""
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            docker = base / "docker"
            docker.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = compose ]; then cat "$BUILD_CONFIG"; else printf "%s\\n" "$*" >> "$BUILD_LOG"; fi\n'
            )
            docker.chmod(0o700)
            config = {"services": {name: {"image": f"test-{name}"} for name in ("api", "minio", "storage-init", "sandbox-image")}}
            config_file = base / "config.json"
            log = base / "build.log"
            env = {
                **os.environ, "PATH": f"{base}:{os.environ['PATH']}",
                "BUILD_CONFIG": str(config_file), "BUILD_LOG": str(log),
            }
            for configured in (True, False):
                with self.subTest(configured=configured):
                    log.unlink(missing_ok=True)
                    value = copy.deepcopy(config)
                    if not configured:
                        del value["services"]["sandbox-image"]
                    config_file.write_text(json.dumps(value))
                    result = subprocess.run(
                        ["bash", str(ROOT / "scripts/ci/build_topology_images.sh"), "", "",
                         str(base / "sandbox-cache"), "--sandbox-only"],
                        env=env, capture_output=True, text=True, timeout=10, check=False,
                    )
                    if not configured:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(log.exists())
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    commands = [shlex.split(line) for line in log.read_text().splitlines()]
                    self.assertEqual(len(commands), 1)
                    self.assertIn("docker/sandbox.Dockerfile", commands[0])
                    self.assertIn(f"type=local,dest={base}/sandbox-cache,mode=max", commands[0])

    def assert_container_state(self, config: dict) -> None:
        """API、worker 和 runner 共享认证与物理数据，socket 只归 runner。"""
        services = config["services"]
        runner = services["sandbox-runner"]
        token = runner["environment"]["SANDBOX_RUNNER_TOKEN"]
        self.assertGreaterEqual(len(token), 16)
        self.assertEqual(runner["environment"]["SANDBOX_CONTAINER_IMAGE"], services["sandbox-image"]["image"])
        for name in ("api", "worker"):
            service = services[name]
            self.assertEqual(service["environment"]["SANDBOX_BACKEND"], "container")
            self.assertEqual(service["environment"]["SANDBOX_RUNNER_URL"], "http://sandbox-runner:8090")
            self.assertEqual(service["environment"]["SANDBOX_RUNNER_TOKEN"], token)
            self.assertEqual(service["environment"]["CUBE_API_URL"], "")
            for target, runner_target in (("/app/user-data", "/sandbox-data"), ("/app/skill-projections", "/sandbox-skill-projections")):
                mount = next(m for m in service["volumes"] if m["target"] == target)
                runner_mount = next(m for m in runner["volumes"] if m["target"] == runner_target)
                self.assertEqual(mount["source"], runner_mount["source"])
            self.assertFalse(any(m["target"] == "/var/run/docker.sock" for m in service["volumes"]))
        self.assertTrue(any(m["target"] == "/var/run/docker.sock" for m in runner["volumes"]))


if __name__ == "__main__":
    unittest.main()
