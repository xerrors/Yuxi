"""Owner 派生工程信任 verifier 的负向测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_engineering_contracts import AGENTS_FILE_BUDGETS, verify


class EngineeringContractVerifierTest(unittest.TestCase):
    """证明 verifier 会从真实 Owner 检查接线与架构边界。"""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self._write("owner.md", "owner\n")
        self._write(
            "backend/server/routers/valid_router.py",
            "async def route():\n    return None\n",
        )
        self._write(
            "web/src/components/ValidComponent.vue",
            "<template><div>ok</div></template>\n",
        )
        for lifecycle in ("proposed", "implemented", "rejected", "archived"):
            (self.root / "docs/develop-guides/decisions" / lifecycle).mkdir(
                parents=True
            )
        self._write(
            "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md",
            """# 有效决策

状态：implemented
类型：process
Owner：owner.md

## 问题
工程事实需要稳定的语义 Owner。

## 决策
从真实 Owner 派生审计视图。

## 替代方案
拒绝手工维护第二份中央清单。

## 后果
Owner 与 gate 必须在同一变更中保持一致。

## 验证
运行 verifier 及其负向测试。
""",
        )
        self._write_valid_workflows()
        self._write_valid_agents_files()
        self._write_valid_postmortem_files()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_valid_workflows(self) -> None:
        self._write(
            ".github/workflows/trust.yml",
            """on:
  pull_request:
jobs:
  verify:
    steps:
      - run: python3 scripts/verify_engineering_contracts.py
      - run: python3 -m unittest scripts.test_verify_engineering_contracts
""",
        )
        self._write(
            ".github/workflows/test.yml",
            """on:
  pull_request:
    paths:
      - '.env.template'
      - 'backend/**'
      - 'docker/**'
      - 'scripts/init.sh'
      - 'scripts/init.ps1'
      - 'scripts/test_init_security.ps1'
      - '.github/workflows/test.yml'
jobs:
  unit:
    steps:
      - run: uv run pytest test/unit -m 'not slow' -q
  powershell:
    steps:
      - run: pwsh -NoProfile -File scripts/test_init_security.ps1
""",
        )
        self._write(
            ".github/workflows/web.yml",
            """on:
  pull_request:
    paths:
      - 'web/**'
      - '.github/workflows/web.yml'
jobs:
  web:
    steps:
      - run: pnpm run lint:check && pnpm run test:unit && pnpm run build
""",
        )
        self._write(
            ".github/workflows/system-tests.yml",
            """on:
  pull_request:
    paths:
      - 'backend/package/yuxi/**'
      - 'backend/server/**'
      - 'backend/test/integration/**'
      - 'backend/test/e2e/**'
      - 'backend/test/support/**'
      - 'docker/**'
      - 'scripts/ci_prepare_system_tests_env.sh'
      - 'scripts/ci_build_topology_images.sh'
      - 'scripts/migrate-storage.sh'
      - '.github/workflows/system-tests.yml'
jobs:
  system:
    steps:
      - run: docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/api/test_system_router_api.py::test_health_endpoint_is_public test/integration/api/test_system_router_api.py::test_readiness_endpoint_proves_core_runtime_dependencies test/integration/api/test_system_router_api.py::test_discovery_and_openapi_declare_full_knowledge_capabilities -q
      - run: docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/services/test_schema_migration_version.py -q
      - run: docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/services/test_agent_request_queue_concurrency.py -q
      - run: docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/services/test_agent_run_lease.py -q
      - run: docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest test/integration/api/test_agent_run_result_causality.py -q
      - run: docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest test/integration/api/test_chat_router.py::test_thread_message_audits_return_persisted_facts_without_leaking_into_history -q --setup-show -o faulthandler_timeout=60
      - run: docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest test/integration/api/test_chat_router.py::test_thread_artifact_uses_image_signature_for_content_type -q
      - run: docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api uv run --no-sync --no-dev pytest test/e2e/test_deterministic_agent_path_e2e.py -q -m e2e_smoke --durations=10
      - run: docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api uv run --no-sync --no-dev pytest test/e2e/test_deterministic_agent_path_e2e.py -q -m e2e_lifecycle --durations=10
      - run: docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api uv run --no-sync --no-dev pytest test/e2e/test_deterministic_agent_path_e2e.py -q -m e2e_boundaries --durations=10
      - run: docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest test/integration/services/test_identity_admin_service.py test/integration/services/test_api_key_schema_migration.py test/integration/services/test_api_key_user_lifecycle.py test/integration/api/test_apikey_router.py -q
      - run: |
          docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest \\
          test/integration/services/test_workdir_user_workspace.py \\
          test/integration/services/test_user_skill_projection.py \\
          test/integration/api/test_skill_artifact_authorization.py -q
      - run: docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/services/test_project_workdir_provisioner.py -q
""",
        )
        self._write(
            ".github/workflows/real-provider-probe.yml",
            """on:
  workflow_dispatch:
jobs:
  probe:
    steps:
      - run: |
          if [ -z "$SILICONFLOW_API_KEY" ]; then
            echo "SILICONFLOW_API_KEY repository secret is required for this manual probe." >&2
            exit 1
          fi
      - run: docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api uv run --no-sync --no-dev pytest test/e2e/test_agent_async_e2e.py -q
""",
        )

    def _write_valid_agents_files(self) -> None:
        self._write(
            "AGENTS.md",
            "# 根约定\n\n见 [架构](ARCHITECTURE.md) 与 [决策](docs/develop-guides/decisions/README.md)。\n",
        )
        self._write("ARCHITECTURE.md", "# 架构\n")
        self._write("docs/develop-guides/decisions/README.md", "# 决策记录\n")
        self._write(
            "backend/AGENTS.md", "# Backend 约定\n见 [根约定](../AGENTS.md)。\n"
        )
        self._write("web/AGENTS.md", "# Web 约定\n见 [根约定](../AGENTS.md)。\n")
        self._write("docs/AGENTS.md", "# 文档约定\n见 [根约定](../AGENTS.md)。\n")

    def _write_valid_postmortem_files(self) -> None:
        self._write(
            "docs/develop-guides/postmortems/README.md",
            "# 工程事故复盘\n\n达到门槛的事故使用模板。\n",
        )
        self._write(
            "docs/develop-guides/postmortems/TEMPLATE.md",
            """# 事故标题

## 影响
影响。

## 事实时间线
时间线。

## 因果链
因果。

## 安全网为何漏过
漏过原因。

## 修正与验证
验证。

## 防复发措施
措施。

## 未解决风险
风险。
""",
        )

    def _errors(self) -> list[str]:
        return verify(self.root)[0]

    def test_valid_repository_passes(self) -> None:
        self.assertEqual(self._errors(), [])

    def test_central_claim_inventory_is_rejected(self) -> None:
        self._write("docs/develop-guides/engineering-claims.json", "{}\n")

        self.assertTrue(
            any("禁止手工中央主张清单" in error for error in self._errors())
        )

    def test_decision_missing_heading_is_rejected(self) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace("## 问题", "## 背景"),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少标题：## 问题" in error for error in self._errors()))

    def test_decision_missing_owner_is_rejected(self) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Owner：owner.md", "Owner：missing.md"
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("Owner 引用不存在" in error for error in self._errors()))

    def test_decision_unknown_type_is_rejected(self) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace("类型：process", "类型：refactor"),
            encoding="utf-8",
        )

        self.assertTrue(any("类型必须是" in error for error in self._errors()))

    def test_decision_owner_symlink_cannot_escape_repository(self) -> None:
        (self.root / "outside-owner").symlink_to("/etc/hosts")
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Owner：owner.md", "Owner：outside-owner"
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("符号链接逃逸仓库" in error for error in self._errors()))

    def test_implemented_progress_heading_is_rejected(self) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8") + "\n## 进度\n已完成。\n", encoding="utf-8"
        )

        self.assertTrue(
            any("不能保留提案或进度标题" in error for error in self._errors())
        )

    def test_heading_in_code_fence_does_not_count(self) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        text = path.read_text(encoding="utf-8").replace(
            "## 验证\n运行 verifier 及其负向测试。",
            "```markdown\n## 验证\n伪造内容。\n```",
        )
        path.write_text(text, encoding="utf-8")

        self.assertTrue(any("缺少标题：## 验证" in error for error in self._errors()))

    def test_workflow_command_drift_is_rejected(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "python3 scripts/verify_engineering_contracts.py",
                "python3 scripts/other.py",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少实际 run step" in error for error in self._errors()))

    def test_comment_cannot_impersonate_workflow_run_step(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "- run: python3 scripts/verify_engineering_contracts.py",
                "# run: python3 scripts/verify_engineering_contracts.py",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少实际 run step" in error for error in self._errors()))

    def test_conditionally_skipped_step_is_rejected(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "- run: python3 scripts/verify_engineering_contracts.py",
                "- if: ${{ false }}\n        run: python3 scripts/verify_engineering_contracts.py",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("被跳过或吞错" in error for error in self._errors()))

    def test_continue_on_error_step_is_rejected(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "- run: python3 scripts/verify_engineering_contracts.py",
                "- continue-on-error: true\n        run: python3 scripts/verify_engineering_contracts.py",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("被跳过或吞错" in error for error in self._errors()))

    def test_conditionally_skipped_job_is_rejected(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "  verify:\n    steps:",
                "  verify:\n    if: github.actor == 'trusted-only'\n    steps:",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("被跳过或吞错" in error for error in self._errors()))

    def test_gate_command_cannot_swallow_failure(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "python3 scripts/verify_engineering_contracts.py",
                "python3 scripts/verify_engineering_contracts.py || true",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("吞掉失败" in error for error in self._errors()))

    def test_trust_workflow_cannot_narrow_pull_request_paths(self) -> None:
        path = self.root / ".github/workflows/trust.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "pull_request:\n", "pull_request:\n    paths: ['scripts/**']\n"
            ),
            encoding="utf-8",
        )

        self.assertTrue(
            any(
                "全仓信任 workflow 不得使用 path filter" in error
                for error in self._errors()
            )
        )

    def test_backend_unit_selector_cannot_drift_to_marker(self) -> None:
        path = self.root / ".github/workflows/test.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "test/unit -m 'not slow'", "test -m unit"
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少实际 run step" in error for error in self._errors()))

    def test_backend_workflow_cannot_ignore_initialization_contract_changes(
        self,
    ) -> None:
        path = self.root / ".github/workflows/test.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("      - 'scripts/init.sh'\n", ""),
            encoding="utf-8",
        )

        self.assertTrue(
            any(
                "workflow PR paths 缺少 owning scope" in error
                for error in self._errors()
            )
        )

    def test_powershell_security_gate_cannot_be_removed(self) -> None:
        path = self.root / ".github/workflows/test.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "pwsh -NoProfile -File scripts/test_init_security.ps1",
                "pwsh -NoProfile -File scripts/other.ps1",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少实际 run step" in error for error in self._errors()))

    def test_system_workflow_cannot_narrow_owning_paths(self) -> None:
        path = self.root / ".github/workflows/system-tests.yml"
        original = path.read_text(encoding="utf-8")
        for owning_path in (
            "backend/package/yuxi/**",
            "backend/test/e2e/**",
            "backend/test/support/**",
            "docker/**",
        ):
            with self.subTest(owning_path=owning_path):
                path.write_text(
                    original.replace(f"      - '{owning_path}'\n", ""),
                    encoding="utf-8",
                )
                self.assertTrue(
                    any(
                        "workflow PR paths 缺少 owning scope" in error
                        and owning_path in error
                        for error in self._errors()
                    )
                )

    def test_system_workflow_required_command_cannot_be_removed(self) -> None:
        path = self.root / ".github/workflows/system-tests.yml"
        original = path.read_text(encoding="utf-8")
        for test_path in (
            "test/integration/services/test_project_workdir_provisioner.py",
            "test/e2e/test_deterministic_agent_path_e2e.py",
            'docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest test/integration/api/test_chat_router.py::test_thread_message_audits_return_persisted_facts_without_leaking_into_history -q --setup-show -o faulthandler_timeout=60',
            'docker compose exec -T -e TEST_USERNAME="$E2E_USERNAME" -e TEST_PASSWORD="$E2E_PASSWORD" api uv run --no-sync --no-dev pytest test/integration/api/test_chat_router.py::test_thread_artifact_uses_image_signature_for_content_type -q',
        ):
            with self.subTest(test_path=test_path):
                path.write_text(
                    original.replace(
                        test_path,
                        "test/integration/services/test_removed_contract.py",
                    ),
                    encoding="utf-8",
                )
                self.assertTrue(
                    any("缺少实际 run step" in error for error in self._errors())
                )

    def test_authenticated_system_steps_cannot_drop_credentials(self) -> None:
        """恢复 HTTP 测试缺少账号的接线时 gate 必须拒绝。"""
        path = self.root / ".github/workflows/system-tests.yml"
        original = path.read_text(encoding="utf-8")
        for credential in (
            '-e TEST_USERNAME="$E2E_USERNAME" ',
            '-e TEST_PASSWORD="$E2E_PASSWORD" ',
        ):
            with self.subTest(credential=credential):
                path.write_text(original.replace(credential, ""), encoding="utf-8")
                errors = self._errors()
                for test_file in (
                    "test_agent_run_result_causality.py",
                    "test_apikey_router.py",
                    "test_skill_artifact_authorization.py",
                ):
                    self.assertTrue(
                        any(
                            "缺少实际 run step" in error and test_file in error
                            for error in errors
                        )
                    )

    def test_real_provider_probe_requires_manual_trigger(self) -> None:
        path = self.root / ".github/workflows/real-provider-probe.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("workflow_dispatch:", "push:"),
            encoding="utf-8",
        )

        self.assertTrue(
            any(
                "workflow 不监听 workflow_dispatch" in error for error in self._errors()
            )
        )

    def test_real_provider_probe_command_cannot_be_removed(self) -> None:
        path = self.root / ".github/workflows/real-provider-probe.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "test/e2e/test_agent_async_e2e.py",
                "test/e2e/test_other_real_provider.py",
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少实际 run step" in error for error in self._errors()))

    def test_real_provider_probe_cannot_drop_credential_preflight(self) -> None:
        path = self.root / ".github/workflows/real-provider-probe.yml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'if [ -z "$SILICONFLOW_API_KEY" ]; then',
                'if [ -n "$SILICONFLOW_API_KEY" ]; then',
            ),
            encoding="utf-8",
        )

        self.assertTrue(any("缺少实际 run step" in error for error in self._errors()))

    def test_router_sqlalchemy_query_builder_is_rejected(self) -> None:
        self._write(
            "backend/server/routers/invalid_router.py",
            "from sqlalchemy import select\n",
        )

        self.assertTrue(
            any(
                "router 不得拥有 SQLAlchemy query builder" in error
                for error in self._errors()
            )
        )

    def test_router_execute_and_delete_are_rejected(self) -> None:
        self._write(
            "backend/server/routers/invalid_router.py",
            """async def route(db, user):
    await db.execute('query')
    await db.delete(user)
""",
        )

        errors = self._errors()
        self.assertTrue(any("db.execute" in error for error in errors))
        self.assertTrue(any("db.delete" in error for error in errors))

    def test_web_api_literal_outside_api_owner_is_rejected(self) -> None:
        self._write(
            "web/src/components/InvalidComponent.vue",
            "<script>fetch('/api/users')</script>\n",
        )

        self.assertTrue(
            any(
                "web/src/apis 外不得拥有 /api 路径" in error for error in self._errors()
            )
        )

    def test_service_workspace_host_path_bypasses_are_rejected(self) -> None:
        cases = (
            (
                "from yuxi.workspace.paths import user_workdir_host_dir\n",
                "普通 Service/Repository 不得取得 UserWorkspace 宿主 Path",
            ),
            (
                "import yuxi.workspace.paths as workspace_paths\n",
                "普通 Service/Repository 不得取得 UserWorkspace 宿主 Path",
            ),
            (
                "from yuxi.config import get_user_data_dir\n"
                "def scan():\n"
                "    return list((get_user_data_dir() / 'shared').iterdir())\n",
                "普通 Service/Repository 不得取得 UserWorkspace 宿主 Path",
            ),
            (
                "import os\nfrom pathlib import Path\n"
                "def scan():\n"
                "    return list(Path(os.environ['YUXI_USER_DATA_DIR']).iterdir())\n",
                "不得读取 UserWorkspace 宿主根环境变量",
            ),
        )
        path = "backend/package/yuxi/services/invalid_service.py"
        for source, expected_error in cases:
            with self.subTest(source=source):
                self._write(path, source)
                self.assertTrue(
                    any(expected_error in error for error in self._errors())
                )

    def test_agents_instruction_file_missing_is_rejected(self) -> None:
        (self.root / "backend/AGENTS.md").unlink()

        self.assertTrue(
            any("缺少 AGENTS 指令文件" in error for error in self._errors())
        )

    def test_agents_instruction_broken_link_is_rejected(self) -> None:
        path = self.root / "AGENTS.md"
        path.write_text("# 根约定\n\n见 [断链](missing-guide.md)。\n", encoding="utf-8")

        self.assertTrue(any("AGENTS 指令引用失效" in error for error in self._errors()))

    def test_agents_instruction_external_link_is_allowed(self) -> None:
        path = self.root / "AGENTS.md"
        path.write_text(
            "# 根约定\n\n参考 [规范](https://example.com/spec) 与 [锚点](#任务)。\n",
            encoding="utf-8",
        )

        self.assertEqual(self._errors(), [])

    def test_agents_instruction_multiple_h1_is_rejected(self) -> None:
        path = self.root / "web/AGENTS.md"
        path.write_text(
            "# Web 约定\n\n# 重复标题\n见 [根约定](../AGENTS.md)。\n",
            encoding="utf-8",
        )

        self.assertTrue(any("必须有且只有一个 H1" in error for error in self._errors()))

    def test_agents_instruction_budget_overflow_is_rejected(self) -> None:
        for relative in AGENTS_FILE_BUDGETS:
            with self.subTest(relative=relative):
                path = self.root / relative
                path.write_text("# 标题\n\n" + "规则" * 3000 + "\n", encoding="utf-8")

                self.assertTrue(
                    any(
                        "超出字符预算" in error and relative in error
                        for error in self._errors()
                    )
                )

    def test_document_contrastive_negation_is_rejected(self) -> None:
        examples = (
            "系统不是缓存层，而是最终事实源。",
            "质量不是取决于篇幅，而在于是否回答问题。",
            "质量并非由篇幅决定，而在于是否回答问题。",
            "问题不在于缓存大小，而是缓存失效没有边界。",
        )
        for prose in examples:
            with self.subTest(prose=prose):
                self._write(
                    "docs/advanced/invalid-prose.md",
                    f"# 无效文案\n\n{prose}\n",
                )

                self.assertTrue(
                    any("禁止对举式否定" in error for error in self._errors())
                )

    def test_document_contrastive_negation_in_code_fence_is_allowed(self) -> None:
        self._write(
            "docs/advanced/quoted-prose.md",
            "# 引用示例\n\n```text\n系统不是缓存层，而是最终事实源。\n```\n",
        )

        self.assertEqual(self._errors(), [])

    def test_document_contrastive_negation_in_list_fence_is_allowed(self) -> None:
        self._write(
            "docs/advanced/list-fence.md",
            "# 列表示例\n\n- 示例\n\n    ```text\n    系统不是缓存层，而是最终事实源。\n    ```\n",
        )

        self.assertEqual(self._errors(), [])

    def test_document_contrastive_negation_in_blockquote_fence_is_allowed(
        self,
    ) -> None:
        self._write(
            "docs/advanced/blockquote-fence.md",
            "# 引用示例\n\n> ```text\n> 系统不是缓存层，而是最终事实源。\n> ```\n",
        )

        self.assertEqual(self._errors(), [])

    def test_deeper_blockquote_marker_does_not_close_outer_fence(self) -> None:
        self._write(
            "docs/advanced/nested-blockquote-fence.md",
            "# 嵌套引用示例\n\n> ```text\n>> ```\n> 系统不是缓存层，而是最终事实源。\n> ```\n",
        )

        self.assertEqual(self._errors(), [])

    def test_document_precise_exclusion_is_allowed(self) -> None:
        self._write(
            "docs/advanced/security-boundary.md",
            "# 安全边界\n\n客户端发送 JWT，而不是账户密码。\n",
        )

        self.assertEqual(self._errors(), [])

    def test_document_historical_transition_is_allowed(self) -> None:
        self._write(
            "docs/develop-guides/changelog.md",
            "# 变更记录\n\n从 2.0 起，接口不再返回旧字段，而是返回新结构。\n",
        )

        self.assertEqual(self._errors(), [])

    def test_four_space_indented_fence_cannot_hide_following_prose(self) -> None:
        self._write(
            "docs/advanced/indented-fence.md",
            "# 缩进示例\n\n    ```text\n\n系统不是缓存层，而是最终事实源。\n",
        )

        self.assertTrue(any("禁止对举式否定" in error for error in self._errors()))

    def test_shorter_fence_cannot_close_longer_fence(self) -> None:
        self._write(
            "docs/advanced/long-fence.md",
            "# 长围栏\n\n````text\n```\n系统不是缓存层，而是最终事实源。\n````\n",
        )

        self.assertEqual(self._errors(), [])

    def test_unclosed_blockquote_fence_cannot_hide_following_prose(self) -> None:
        self._write(
            "docs/advanced/unclosed-blockquote-fence.md",
            "# 引用示例\n\n> ```text\n> 示例\n系统不是缓存层，而是最终事实源。\n",
        )

        self.assertTrue(any("禁止对举式否定" in error for error in self._errors()))

    def test_unclosed_list_fence_cannot_hide_following_prose(self) -> None:
        self._write(
            "docs/advanced/unclosed-list-fence.md",
            "# 列表示例\n\n- 示例\n\n    ```text\n    示例\n\n系统不是缓存层，而是最终事实源。\n",
        )

        self.assertTrue(any("禁止对举式否定" in error for error in self._errors()))

    def test_nested_list_fence_cannot_hide_outer_item_prose(self) -> None:
        self._write(
            "docs/advanced/nested-list-fence.md",
            "# 列表示例\n\n- 外层\n  - 内层\n"
            "    ```text\n    fence 内容\n"
            "  系统不是缓存层，而是最终事实源。\n",
        )

        self.assertTrue(any("禁止对举式否定" in error for error in self._errors()))

    def test_vibe_drafts_are_excluded_from_prose_check(self) -> None:
        self._write(
            "docs/vibe/2026-08-17-personal-note.md",
            "# 临时计划\n\n系统不是缓存层，而是最终事实源。\n",
        )

        self.assertEqual(self._errors(), [])

    def test_node_modules_markdown_is_excluded_from_prose_check(self) -> None:
        self._write(
            "docs/node_modules/example/README.md",
            "# README\n\n系统不是缓存层，而是最终事实源。\n",
        )

        self.assertEqual(self._errors(), [])

    def test_proposed_decision_missing_acceptance_heading_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/proposed/2026-08-16-flawed-proposal.md",
            "# 有缺陷的提案\n\n状态：proposed\nOwner：owner.md\n\n## 问题\n缺验收标准。\n\n## 提案\n占位。\n\n## 替代方案\n无。\n\n## 风险\n无。\n",
        )

        self.assertTrue(
            any("缺少标题：## 验收标准" in error for error in self._errors())
        )

    def test_proposed_decision_without_evidence_matrix_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/proposed/2026-08-16-no-evidence.md",
            """# 缺证据矩阵的提案

状态：proposed
类型：feature
Owner：owner.md

## 问题
问题。

## 提案
方案。

## 替代方案
替代。

## 验收标准
只有散文验收。

## 风险
风险。
""",
        )

        self.assertTrue(
            any(
                "proposed 验收标准缺少证据矩阵表头" in error for error in self._errors()
            )
        )

    def test_proposed_decision_empty_evidence_matrix_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/proposed/2026-08-16-empty-evidence.md",
            """# 空证据矩阵

状态：proposed
类型：feature
Owner：owner.md

## 问题
问题。

## 提案
方案。

## 替代方案
替代。

## 验收标准
| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|

## 风险
风险。
""",
        )

        self.assertTrue(
            any(
                "proposed 验收标准缺少证据矩阵数据行" in error
                for error in self._errors()
            )
        )

    def test_proposed_evidence_matrix_empty_cell_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/proposed/2026-08-16-empty-cell.md",
            """# 证据矩阵空列

状态：proposed
类型：feature
Owner：owner.md

## 问题
问题。

## 提案
方案。

## 替代方案
替代。

## 验收标准
| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 主张 |  | owner.md | command | negative | Not run |

## 风险
风险。
""",
        )

        self.assertTrue(
            any(
                "proposed 证据矩阵必须填写全部六列" in error for error in self._errors()
            )
        )

    def test_proposed_evidence_matrix_unknown_result_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/proposed/2026-08-16-bad-result.md",
            """# 证据矩阵非法结果

状态：proposed
类型：feature
Owner：owner.md

## 问题
问题。

## 提案
方案。

## 替代方案
替代。

## 验收标准
| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 主张 | 失败 | owner.md | command | negative | Maybe |

## 风险
风险。
""",
        )

        self.assertTrue(
            any("proposed 证据结果必须是" in error for error in self._errors())
        )

    def test_simplification_without_deletion_contract_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/proposed/2026-08-16-weak-simplification.md",
            """# 不完整的简化提案

状态：proposed
类型：simplification
Owner：owner.md

## 问题
问题。

## 提案
方案。

## 替代方案
替代。

## 验收标准
| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 简化 | 旧实现残留 | owner.md | 搜索 | 恢复旧入口 | Not run |

## 风险
风险。
""",
        )

        errors = self._errors()
        self.assertTrue(any("旧能力不存在：" in error for error in errors))
        self.assertTrue(any("重新引入条件：" in error for error in errors))

    def test_implemented_simplification_without_deletion_contract_is_rejected(
        self,
    ) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "类型：process", "类型：simplification"
            ),
            encoding="utf-8",
        )

        errors = self._errors()
        self.assertTrue(
            any(
                "simplification ## 验证 缺少：旧能力不存在：" in error
                for error in errors
            )
        )
        self.assertTrue(
            any(
                "simplification ## 验证 缺少：重新引入条件：" in error
                for error in errors
            )
        )

    def test_postmortem_readme_missing_is_rejected(self) -> None:
        (self.root / "docs/develop-guides/postmortems/README.md").unlink()

        self.assertTrue(
            any("缺少 postmortem 入口或模板" in error for error in self._errors())
        )

    def test_postmortem_template_missing_is_rejected(self) -> None:
        (self.root / "docs/develop-guides/postmortems/TEMPLATE.md").unlink()

        self.assertTrue(
            any("缺少 postmortem 入口或模板" in error for error in self._errors())
        )

    def test_postmortem_template_missing_heading_is_rejected(self) -> None:
        path = self.root / "docs/develop-guides/postmortems/TEMPLATE.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("## 防复发措施", "## 后续"),
            encoding="utf-8",
        )

        self.assertTrue(
            any(
                "postmortem 模板缺少标题：## 防复发措施" in error
                for error in self._errors()
            )
        )

    def test_postmortem_template_empty_heading_is_rejected(self) -> None:
        path = self.root / "docs/develop-guides/postmortems/TEMPLATE.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "## 防复发措施\n措施。", "## 防复发措施\n"
            ),
            encoding="utf-8",
        )

        self.assertTrue(
            any(
                "postmortem 模板标题下没有内容：## 防复发措施" in error
                for error in self._errors()
            )
        )

    def test_rejected_decision_missing_rejection_reason_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/rejected/2026-08-16-flawed-rejection.md",
            "# 缺拒绝理由\n\n状态：rejected\nOwner：owner.md\n\n## 问题\n占位。\n\n## 提案\n占位。\n\n## 替代方案\n无。\n",
        )

        self.assertTrue(
            any("缺少标题：## 拒绝理由" in error for error in self._errors())
        )

    def test_archived_decision_with_progress_heading_is_rejected(self) -> None:
        self._write(
            "docs/develop-guides/decisions/archived/2026-08-16-flawed-archive.md",
            "# 带进度章节的归档\n\n状态：archived\nOwner：owner.md\n\n## 问题\n占位。\n\n## 决策\n占位。\n\n## 替代方案\n无。\n\n## 后果\n无。\n\n## 验证\n占位。\n\n## Checklist\n遗留清单。\n",
        )

        self.assertTrue(
            any("不能保留提案或进度标题" in error for error in self._errors())
        )

    def test_decision_status_must_match_lifecycle_directory(self) -> None:
        path = (
            self.root
            / "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "状态：implemented", "状态：proposed"
            ),
            encoding="utf-8",
        )

        self.assertTrue(
            any("状态必须是 implemented" in error for error in self._errors())
        )

    def test_projection_is_derived_from_current_owners(self) -> None:
        errors, projection = verify(self.root)

        self.assertEqual(errors, [])
        self.assertTrue(projection["derived"])
        self.assertEqual(
            projection["decisions"],
            [
                {
                    "path": "docs/develop-guides/decisions/implemented/2026-08-15-valid-decision.md",
                    "status": "implemented",
                    "type": "process",
                    "owner": "owner.md",
                }
            ],
        )
        self.assertEqual(
            projection["postmortems"],
            [
                "docs/develop-guides/postmortems/README.md",
                "docs/develop-guides/postmortems/TEMPLATE.md",
            ],
        )
        self.assertEqual(
            {workflow["path"] for workflow in projection["workflows"]},
            {
                ".github/workflows/trust.yml",
                ".github/workflows/test.yml",
                ".github/workflows/web.yml",
                ".github/workflows/system-tests.yml",
                ".github/workflows/real-provider-probe.yml",
            },
        )


if __name__ == "__main__":
    unittest.main()
