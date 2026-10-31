"""真实 Cube、npm 和 Git 来源的个人技能 CLI 安装验收。"""

import pytest

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.redis import close_async_redis_client
from yuxi.modules.extensions.skills.personal import list_personal_skills, read_personal_skill_file
from yuxi.modules.workspace.paths import user_workspace_dir

pytestmark = [pytest.mark.integration, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest.mark.asyncio
async def test_cli_global_install_is_discovered_in_personal_workspace(resources):
    """回读个人列表与文件，确认标准全局入口直接写入个人持久目录。"""
    uid, _, _, backend = resources
    root = user_workspace_dir(uid)
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    try:
        result = backend.execute(
            "npx -y skills add mattpocock/skills --skill grill-me -g -y -a codex",
            timeout=300,
        )
        assert result.exit_code == 0, result.output
        assert "grill-me" in {item.slug for item in await list_personal_skills(uid)}
        content = (await read_personal_skill_file(uid, "grill-me", "SKILL.md"))["content"]
        installed = root / "agents/skills/grill-me/SKILL.md"
        assert installed.read_text(encoding="utf-8") == content
        assert "name: grill-me" in content
        assert (root / "keep.txt").read_text(encoding="utf-8") == "keep"
    finally:
        await pg_manager.close()
        await close_async_redis_client()
