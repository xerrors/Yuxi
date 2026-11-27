import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class BumpVersionScriptTests(unittest.TestCase):
    """验证版本脚本同步活动引用且保留历史记录。"""

    def _create_fixture(self, root: Path) -> Path:
        """创建包含重复版本入口和历史记录的最小仓库。"""
        script = root / "scripts/bump-version.sh"
        script.parent.mkdir(parents=True)
        shutil.copy2(Path(__file__).resolve().parents[1] / "bump-version.sh", script)

        fixtures = {
            ".env.template": "# SANDBOX_CONTAINER_IMAGE=yuxi-sandbox:0.7.2.beta1\n",
            "backend/pyproject.toml": 'version = "0.7.2.beta1"\n',
            "backend/uv.lock": ('name = "yuxi"\nversion = "0.7.2b1"\n'),
            "frontend/package.json": '{\n  "version": "0.7.2.beta1"\n}\n',
            "backend/yuxi/sandbox_runner/settings.py": 'image = "yuxi-sandbox:0.7.2.beta1"\n',
            "backend/test/integration/sandbox/test_container_sandbox.py": 'image = "yuxi-sandbox:0.7.2.beta1"\n' * 3,
            "docs/advanced/container-sandbox.md": 'docker build -t yuxi-sandbox:0.7.2.beta1 .\n' * 2,
            "backend/test/e2e/web-tools.compose.yml": "image: ${COMPOSE_PROJECT_NAME:-yuxi}-api:${YUXI_VERSION:-0.7.2.beta1}\n",
            "docker-compose.yml": "\n".join(
                f"image: ${{COMPOSE_PROJECT_NAME:-yuxi}}-{name}:${{YUXI_VERSION:-0.7.2.beta1}}"
                for name in (
                    "api",
                    "api",
                    "api",
                    "frontend",
                )
            )
            + "\n",
            "docker-compose.prod.yml": "\n".join(
                f"image: ${{COMPOSE_PROJECT_NAME:-yuxi}}-{name}:${{YUXI_VERSION:-0.7.2.beta1}}"
                for name in ("api", "api", "api", "frontend")
            )
            + "\n",
            "README.md": (
                "当前仓库默认配置对应 `v0.7.2.beta1`。\n"
                "当前候选源码版本为 `v0.7.2.beta1`。\n"
                "git clone --branch v0.7.2.beta1 --depth 1 https://github.com/xerrors/Yuxi.git\n"
            ),
            "README.en.md": (
                "The candidate source version is `v0.7.2.beta1`.\n"
                "git clone --branch v0.7.2.beta1 --depth 1 https://github.com/xerrors/Yuxi.git\n"
            ),
            "docs/intro/quick-start.md": (
                "仓库当前默认配置对应 `v0.7.2.beta1`。\n"
                "当前候选源码版本为 `v0.7.2.beta1`。\n"
                "git clone --branch v0.7.2.beta1 --depth 1 https://github.com/xerrors/Yuxi.git\n"
            ),
            "docs/advanced/deployment.md": (
                "从 v0.7.1 升级到当前 `v0.7.2.beta1`。\n当前候选为 `0.7.2.beta1`。\ngit checkout v0.7.2.beta1\n"
            ),
            "docs/.vitepress/theme/components/YuxiHome.vue": (
                "获取 0.7.2.beta1 候选源码\ngit clone --branch v0.7.2.beta1 --depth 1 https://github.com/xerrors/Yuxi.git\n"
            ),
            "docs/develop-guides/releases/0.7.2.md": "当前候选版本为 `0.7.2.beta1`。\n",
            "docs/develop-guides/changelog.md": "当前候选为 `0.7.2.beta1`。\n\n## v0.7.2.beta1 (历史记录)\n",
        }
        for relative_path, content in fixtures.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return script

    def test_release_bump_updates_current_references_only(self) -> None:
        """候选版本升级应更新候选引用并保留正式入口与历史记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = self._create_fixture(root)
            stable_checkout = "git clone --branch v0.7.1 --depth 1 https://github.com/xerrors/Yuxi.git\n"
            stable_paths = (
                "README.md",
                "README.en.md",
                "docs/intro/quick-start.md",
                "docs/.vitepress/theme/components/YuxiHome.vue",
            )
            for relative_path in stable_paths:
                path = root / relative_path
                path.write_text(stable_checkout + path.read_text())
            readme = root / "README.md"
            other_candidate = "历史候选：`v0.7.2.beta10`。\n"
            readme.write_text(readme.read_text() + other_candidate)
            sandbox_guide = root / "docs/advanced/container-sandbox.md"
            stable_sandbox = "docker build -t yuxi-sandbox:0.7.1 .\n"
            sandbox_guide.write_text(stable_sandbox + sandbox_guide.read_text())
            deployment = root / "docs/advanced/deployment.md"
            stable_deploy = "稳定版首次部署使用 `v0.7.1` 和 `yuxi-sandbox:0.7.1`。\n"
            deployment.write_text(stable_deploy + deployment.read_text())
            subprocess.run(
                ["bash", str(script), "0.7.2.beta2"],
                input="y\n",
                text=True,
                check=True,
                capture_output=True,
            )

            current_paths = [
                ".env.template",
                "backend/pyproject.toml",
                "frontend/package.json",
                "backend/yuxi/sandbox_runner/settings.py",
                "backend/test/integration/sandbox/test_container_sandbox.py",
                "docs/advanced/container-sandbox.md",
                "backend/test/e2e/web-tools.compose.yml",
                "docker-compose.yml",
                "docker-compose.prod.yml",
                "README.md",
                "README.en.md",
                "docs/intro/quick-start.md",
                "docs/advanced/deployment.md",
                "docs/.vitepress/theme/components/YuxiHome.vue",
                "docs/develop-guides/releases/0.7.2.md",
            ]
            for relative_path in current_paths:
                content = (root / relative_path).read_text()
                self.assertIn("0.7.2.beta2", content, relative_path)
                self.assertNotRegex(content, r"(?<![0-9.])0\.7\.2\.beta1(?![0-9A-Za-z])", relative_path)
                self.assertNotIn("0.7.2b1", content, relative_path)

            for relative_path in stable_paths:
                self.assertIn(stable_checkout, (root / relative_path).read_text(), relative_path)
            self.assertIn(other_candidate, readme.read_text())
            self.assertIn(stable_sandbox, sandbox_guide.read_text())
            self.assertIn(stable_deploy, deployment.read_text())
            self.assertEqual((root / "docker-compose.yml").read_text().count("0.7.2.beta2"), 4)
            self.assertEqual((root / "docker-compose.prod.yml").read_text().count("0.7.2.beta2"), 4)
            changelog = (root / "docs/develop-guides/changelog.md").read_text()
            self.assertIn('version = "0.7.2b2"', (root / "backend/uv.lock").read_text())
            self.assertIn("v0.7.2.beta1", changelog)
            self.assertIn("当前候选为 `0.7.2.beta2`", changelog)
            self.assertNotIn("## v0.7.2.beta2", changelog)

    def test_first_candidate_keeps_the_current_stable_version_in_docs(self) -> None:
        """从正式版开启首个候选时，现行正式版引用保持原值。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = self._create_fixture(root)
            (root / "backend/pyproject.toml").write_text('version = "0.7.1"\n')
            readme = root / "README.md"
            stable_docs = (
                "本文使用正式版 `v0.7.1`。\n"
                "git clone --branch v0.7.1 --depth 1 https://github.com/xerrors/Yuxi.git\n"
            )
            readme.write_text(stable_docs)

            subprocess.run(
                ["bash", str(script), "0.7.2.beta1"],
                input="y\n",
                text=True,
                check=True,
                capture_output=True,
            )

            self.assertEqual(readme.read_text(), stable_docs)

    def test_stable_bump_keeps_historical_upgrade_sources(self) -> None:
        """正式版升级应保留旧库迁移的发行标签和文档链接。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = self._create_fixture(root)
            (root / "backend/pyproject.toml").write_text('version = "0.7.1"\n')
            deployment = root / "docs/advanced/deployment.md"
            upgrade_source = (
                "原地升级以正式 `v0.7.1` 为起点，见[旧版说明](../develop-guides/releases/0.7.1.md)。\n"
                "git checkout v0.7.1\n"
            )
            deployment.write_text(upgrade_source + deployment.read_text())

            subprocess.run(
                ["bash", str(script), "0.7.2"],
                input="y\n",
                text=True,
                check=True,
                capture_output=True,
            )

            self.assertIn(upgrade_source, deployment.read_text())
            self.assertIn("git clone --branch v0.7.2 --depth 1", (root / "README.md").read_text())

    def test_dev_bump_keeps_release_document_references(self) -> None:
        """开发版本升级不得移动公开发布文档的稳定目标。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = self._create_fixture(root)
            subprocess.run(
                ["bash", str(script), "--dev", "0.7.2.dev2"],
                input="y\n",
                text=True,
                check=True,
                capture_output=True,
            )

            self.assertIn(
                'version = "0.7.2.dev2"',
                (root / "backend/pyproject.toml").read_text(),
            )
            for relative_path in (
                ".env.template",
                "backend/yuxi/sandbox_runner/settings.py",
                "backend/test/integration/sandbox/test_container_sandbox.py",
            ):
                self.assertIn("yuxi-sandbox:0.7.2.dev2", (root / relative_path).read_text())
                self.assertNotIn("0.7.2.beta1", (root / relative_path).read_text())
            for relative_path in (
                "README.md",
                "README.en.md",
                "docs/intro/quick-start.md",
                "docs/advanced/deployment.md",
                "docs/advanced/container-sandbox.md",
                "docs/.vitepress/theme/components/YuxiHome.vue",
                "docs/develop-guides/releases/0.7.2.md",
                "docs/develop-guides/changelog.md",
            ):
                self.assertIn("0.7.2.beta1", (root / relative_path).read_text())
                self.assertNotIn("0.7.2.dev2", (root / relative_path).read_text())

    def test_invalid_version_is_rejected_before_writes(self) -> None:
        """非法版本输入必须在读取或改写项目文件前失败。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "scripts/bump-version.sh"
            script.parent.mkdir(parents=True)
            shutil.copy2(Path(__file__).resolve().parents[1] / "bump-version.sh", script)

            result = subprocess.run(
                ["bash", str(script), "0.7.2;touch-pwned"],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("版本号格式无效", result.stdout)
            self.assertFalse((root / "touch-pwned").exists())


if __name__ == "__main__":
    unittest.main()
