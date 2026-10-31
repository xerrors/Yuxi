"""发布事件与应用、CLI 发布边界的回归检查。"""

import re
import unittest
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


class ReleaseWorkflowTests(unittest.TestCase):
    """阻止候选检查缺失和应用 Release 误触发 CLI 上传。"""

    def assert_cold_build_budget(self, workflow: str) -> None:
        """检查 Runtime job 具有覆盖冷缓存构建的最小预算。"""
        jobs = re.split(r"(?m)^  ([\w-]+):\n", workflow.split("\njobs:\n", 1)[1])
        self.assertGreater(len(jobs), 1, "Runtime workflow 缺少 job")
        for name, body in zip(jobs[1::2], jobs[2::2], strict=True):
            if name == "result":
                continue
            self.assertRegex(
                body,
                r"(?m)^    timeout-minutes: (?:[6-9][0-9]|[1-9][0-9]{2,})$",
                f"{name}: 冷构建预算不足",
            )

    def assert_release_events(self, workflows: dict[str, str]) -> None:
        """检查各发布门禁的真实事件声明。"""
        for name in (
            "trust",
            "test",
            "frontend",
            "ruff",
            "system-tests",
            "service-integration",
            "dependency-audit",
            "codeql",
            "deploy",
        ):
            events = re.split(r"\n(?=[\w-]+:)", workflows[name].split("\non:\n", 1)[1], maxsplit=1)[0]
            push = re.search(r"(?ms)^  push:\n(.*?)(?=^  [\w-]+:|\Z)", events).group(1)
            self.assertRegex(push, r"(?m)^    tags: \['v\[0-9\]\*'\]$", f"{name}: 缺少版本 tag 触发")
            if name != "deploy":
                self.assertRegex(
                    push,
                    r"(?m)^(?:    branches: \[main, 'develop/\*\*'\]|      - 'develop/\*\*')$",
                    f"{name}: 缺少全部开发分支触发",
                )
        cli_events = workflows["publish-yuxi-cli"].split("\non:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(
            re.findall(r"^  (\w+):", cli_events, re.MULTILINE),
            ["workflow_dispatch"],
            "CLI 必须独立手动发布",
        )

    TOPOLOGY_JOBS = ("background-job-worker-path", "cooperation-e2e", "lifecycle-worker-e2e", "runtime-contracts")

    def assert_runtime_mount_ownership(self, workflow: str) -> None:
        """四个拓扑 job 在首次启动前为非 root 进程准备挂载目录。"""
        command = (
            "docker compose run --rm --no-deps --user 0:0 api "
            "install -d -o 1000 -g 1000 -m 0700 /app/user-data /app/skill-sources /app/skill-projections"
        )
        workflow = re.sub(r"\\\n\s*", "", workflow)
        jobs = re.split(r"(?m)^  ([\w-]+):\n", workflow.split("\njobs:\n", 1)[1])
        bodies = dict(zip(jobs[1::2], jobs[2::2], strict=True))
        for name in self.TOPOLOGY_JOBS:
            body = bodies[name]
            self.assertIn(command, body)
            self.assertLess(body.index("uses: ./.github/actions/prepare-topology"), body.index(command))
            self.assertLess(body.index(command), body.index("docker compose up -d"))

    def test_runtime_mounts_are_owned_before_service_startup(self) -> None:
        """首次启动的 API 和 worker 不依赖 Docker 创建目录的默认所有权。"""
        self.assert_runtime_mount_ownership((WORKFLOWS / "system-tests.yml").read_text())

    def test_runtime_mounts_reject_missing_ownership_preparation(self) -> None:
        """恢复 root 所有的挂载根目录或遗漏任一 job 时门禁失败。"""
        workflow = (WORKFLOWS / "system-tests.yml").read_text()
        for mount in ("/app/user-data", "/app/skill-sources", "/app/skill-projections"):
            with self.subTest(mount=mount), self.assertRaises(AssertionError):
                self.assert_runtime_mount_ownership(workflow.replace(" " + mount, ""))
        for job in self.TOPOLOGY_JOBS:
            with self.subTest(job=job), self.assertRaises(AssertionError):
                before, section = workflow.split("  " + job + ":\n", 1)
                section = section.replace("install -d -o 1000 -g 1000", "true", 1)
                self.assert_runtime_mount_ownership(before + "  " + job + ":\n" + section)

    def test_repository_release_events(self) -> None:
        """当前配置覆盖候选与正式 tag，CLI 仅手动触发。"""
        self.assert_release_events({path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")})

    def test_runtime_system_tests_have_cold_build_budget(self) -> None:
        """冷缓存构建不能因过短 job 超时而跳过运行链路。"""
        workflow = (WORKFLOWS / "system-tests.yml").read_text()
        self.assert_cold_build_budget(workflow)

    def test_runtime_system_tests_reject_short_build_budget(self) -> None:
        """恢复 35 分钟冷构建预算时 gate 必须失败。"""
        workflow = (WORKFLOWS / "system-tests.yml").read_text()
        for budget in re.findall(r"(?m)^    timeout-minutes: \d+\n", workflow):
            with self.subTest(budget=budget.strip()), self.assertRaises(AssertionError):
                self.assert_cold_build_budget(workflow.replace(budget, "    timeout-minutes: 35\n", 1))

    def test_runtime_system_tests_reject_missing_build_budget(self) -> None:
        """任一 job 遗漏预算时必须拒绝。"""
        workflow = (WORKFLOWS / "system-tests.yml").read_text()
        for budget in re.findall(r"(?m)^    timeout-minutes: \d+\n", workflow):
            with self.subTest(budget=budget.strip()), self.assertRaises(AssertionError):
                self.assert_cold_build_budget(workflow.replace(budget, "", 1))

    def test_missing_tag_trigger_is_rejected(self) -> None:
        """恢复仅监听分支的缺陷时对应门禁必须失败。"""
        workflows = {path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")}
        for name in (
            "trust",
            "test",
            "frontend",
            "ruff",
            "system-tests",
            "service-integration",
            "dependency-audit",
            "deploy",
        ):
            with (
                self.subTest(workflow=name),
                self.assertRaisesRegex(AssertionError, f"{name}: 缺少版本 tag 触发"),
            ):
                self.assert_release_events(workflows | {name: workflows[name].replace("    tags: ['v[0-9]*']\n", "")})

    def test_narrow_development_branch_trigger_is_rejected(self) -> None:
        """写死单个版本或漏掉嵌套开发分支时拒绝交付。"""
        workflows = {path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")}
        for name in ("trust", "test", "frontend", "ruff", "system-tests", "service-integration", "dependency-audit"):
            for replacement in ("develop/0-8-0", "develop/1.0", "develop/*", "feature/**"):
                with (
                    self.subTest(workflow=name, replacement=replacement),
                    self.assertRaisesRegex(AssertionError, f"{name}: 缺少全部开发分支触发"),
                ):
                    self.assert_release_events(workflows | {name: workflows[name].replace("develop/**", replacement)})

    def test_pull_requests_to_all_branches_are_checked(self) -> None:
        """汇总入口覆盖所有 PR，不能添加分支或路径过滤。"""
        workflow = (WORKFLOWS / "ci.yml").read_text()
        events = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(events.strip(), "pull_request:")

    def test_application_release_cannot_publish_cli(self) -> None:
        """恢复应用 Release 触发时禁止重复上传独立 CLI 包。"""
        workflows = {path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")}
        workflows["publish-yuxi-cli"] = workflows["publish-yuxi-cli"].replace("on:\n", "on:\n  release:\n    types: [published]\n", 1)
        with self.assertRaisesRegex(AssertionError, "CLI 必须独立手动发布"):
            self.assert_release_events(workflows)



class CodeQLWorkflowTests(unittest.TestCase):
    """持续扫描必须有真实触发、覆盖语言和结果上传入口。"""

    def test_codeql_runs_current_sources_with_scoped_permissions(self):
        """PR、主分支与周期扫描都能发布三种语言的结果。"""
        self.assert_codeql_configuration((WORKFLOWS / "codeql.yml").read_text())

    def test_missing_scan_trigger_language_or_upload_is_rejected(self):
        """移除任一必要扫描入口、语言或上传步骤时门禁拒绝。"""
        workflow = (WORKFLOWS / "codeql.yml").read_text()
        for required in (
            "  pull_request:", "  schedule:", "  workflow_dispatch:",
            "branches: [main, 'develop/**']", "security-events: write",
            "actions, javascript-typescript, python", "github/codeql-action/analyze@v4",
            "category: /language:${{ matrix.language }}",
        ):
            with self.subTest(required=required), self.assertRaises(AssertionError):
                self.assert_codeql_configuration(workflow.replace(required, "removed", 1))

    def assert_codeql_configuration(self, workflow: str):
        """验证扫描实际执行和历史 category 衔接，禁止提权执行 PR。"""
        for required in (
            "\n  pull_request:", "\n  schedule:", "\n  workflow_dispatch:",
            "branches: [main, 'develop/**']", "security-events: write",
            "language: [actions, javascript-typescript, python]",
            "uses: github/codeql-action/init@v4", "languages: ${{ matrix.language }}",
            "uses: github/codeql-action/analyze@v4", "category: /language:${{ matrix.language }}",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("contents: write", workflow)
        events = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("paths:", events, "安全扫描不能因路径过滤而继续使用历史快照")


if __name__ == "__main__":
    unittest.main()
