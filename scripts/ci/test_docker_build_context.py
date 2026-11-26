"""在宿主通过真实 BuildKit 验证私有输入的构建隔离。"""

import subprocess
import tempfile
import unittest
from pathlib import Path


class DockerBuildContextTests(unittest.TestCase):
    """合成凭据只存在于临时上下文，不读取部署数据。"""

    def test_private_inputs_are_excluded_and_missing_guard_admits_them(self):
        """实际 COPY 必须拒绝私有文件，移除排除规则后负控成功。"""
        ignore_rules = (
            Path(__file__).resolve().parents[2] / ".dockerignore"
        ).read_bytes()
        with tempfile.TemporaryDirectory(prefix="yuxi-build-context-") as directory:
            context = Path(directory)
            ignore_file = context / ".dockerignore"
            ignore_file.write_bytes(ignore_rules)
            for filename in (
                ".env",
                ".env.prod",
                "backend/test/.env.test",
                ".yuxi/runtime-secret",
                ".yuxi-identity/test-signing-key.pem",
                "settings/.yuxi-identity/test-signing-key.pem",
                ".playwright-cli/credentials.json",
            ):
                with self.subTest(filename=filename):
                    private_file = context / filename
                    private_file.parent.mkdir(parents=True, exist_ok=True)
                    private_file.write_text("synthetic-private-sentinel\n")
                    result = self._copy_file(context, filename)
                    self.assertNotEqual(
                        result.returncode, 0, f"{filename} entered the build context"
                    )
                    self.assertIn("not found", result.stderr)

            ignore_file.write_text("")
            result = self._copy_file(context, ".env")
            self.assertEqual(result.returncode, 0, result.stderr)

    def _copy_file(self, context: Path, filename: str) -> subprocess.CompletedProcess:
        """用无基础镜像、无网络的最小构建探测 COPY 边界。"""
        return subprocess.run(
            ["docker", "build", "--progress", "plain", "-f", "-", str(context)],
            input=f"FROM scratch\nCOPY {filename} /sentinel\n",
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
