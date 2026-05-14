"""Agent 会话中安装 Skill 的工具"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.repositories.agent_config_repository import AgentConfigRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger


ADMIN_ROLES = {"admin", "superadmin"}


class InstallSkillInput(BaseModel):
    source: str = Field(
        description="Skill 来源，支持两种格式:\n"
                    "1. Sandbox 路径: /home/gem/user-data/workspace/my-skill （/ 开头）\n"
                    "2. Git 仓库: owner/repo 或完整 GitHub URL"
    )
    skill_names: list[str] | None = Field(
        default=None,
        description="Git 安装时指定要安装的 skill slug 列表（至少一个）。Sandbox 路径安装时忽略此参数。"
    )


async def _assert_admin(user_id: str) -> None:
    """验证用户是管理员，否则抛出 ValueError。"""
    async with pg_manager.get_async_session_context() as db:
        repo = UserRepository(db)
        user = await repo.get_by_user_id(user_id)
        if user is None:
            raise ValueError("用户不存在")
        if user.role not in ADMIN_ROLES:
            raise ValueError("仅管理员可以安装 skill")


def _download_skill_dir(backend, remote_dir: str, local_dir: Path) -> None:
    """递归通过沙盒 API 下载 skill 目录到本地。"""
    entries = backend.ls_info(remote_dir)
    for entry in entries:
        path = entry["path"]
        if entry.get("is_dir"):
            sub = local_dir / PurePosixPath(path).name
            sub.mkdir(parents=True, exist_ok=True)
            _download_skill_dir(backend, path, sub)
        else:
            resp = backend.download_files([path])
            if resp and not resp[0].error:
                (local_dir / PurePosixPath(path).name).write_bytes(resp[0].content)


async def _install_skill_from_sandbox(sandbox_path: str, thread_id: str, user_id: str) -> str:
    """从 Sandbox 路径安装技能。返回最终安装的 slug（可能与传入的不同）。"""
    from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, resolve_virtual_path
    from yuxi.services.skill_service import import_skill_dir, is_valid_skill_slug

    slug = PurePosixPath(sandbox_path.rstrip("/")).name
    if not is_valid_skill_slug(slug):
        raise ValueError(f"slug '{slug}' 不合法（仅允许小写字母、数字和连字符）")

    if not sandbox_path.startswith("/home/gem/user-data/"):
        raise ValueError(
            f"不支持的沙盒路径: {sandbox_path}。"
            "请使用 /home/gem/user-data/workspace/...、/home/gem/user-data/uploads/... "
            "或 /home/gem/user-data/outputs/..."
        )

    with tempfile.TemporaryDirectory(prefix=".skill-install-") as tmp:
        staging = Path(tmp) / slug

        # 优先尝试共享卷路径（性能更好，无需走沙盒 API）
        try:
            local_path = resolve_virtual_path(thread_id, sandbox_path, user_id=user_id)
            if (local_path / "SKILL.md").exists():
                shutil.copytree(local_path, staging)
            else:
                raise FileNotFoundError(f"{local_path} 中未找到 SKILL.md")
        except (ValueError, FileNotFoundError):
            staging.mkdir(parents=True, exist_ok=True)
            backend = ProvisionerSandboxBackend(thread_id=thread_id, user_id=user_id)
            _download_skill_dir(backend, sandbox_path, staging)
            if not (staging / "SKILL.md").exists():
                raise ValueError(f"沙盒路径 {sandbox_path} 中未找到 SKILL.md")

        async with pg_manager.get_async_session_context() as db:
            result = await import_skill_dir(db, source_dir=staging, created_by=user_id)

    if isinstance(result, Path):
        return result.name
    if isinstance(result, str):
        return result
    return slug


async def _install_git_skills(source: str, skill_names: list[str], created_by: str) -> list[dict]:
    """从 Git 仓库安装多个 skill。返回结果列表。"""
    from yuxi.services.remote_skill_install_service import install_remote_skills_batch

    async with pg_manager.get_async_session_context() as db:
        return await install_remote_skills_batch(
            db, source=source, skills=skill_names, created_by=created_by
        )


async def _enable_skill_in_current_config(user_id: str, thread_id: str, slug: str) -> bool:
    """将 skill slug 原子追加到当前对话使用的 agent config 中。"""
    async with pg_manager.get_async_session_context() as db:
        conv_repo = ConversationRepository(db)
        conv = await conv_repo.get_conversation_by_thread_id(thread_id)
        if not conv:
            logger.warning(f"Conversation {thread_id} not found")
            return False

        agent_config_id = (conv.extra_metadata or {}).get("agent_config_id")
        if not agent_config_id:
            logger.warning(f"No agent_config_id found for thread {thread_id}")
            return False

        config_repo = AgentConfigRepository(db)
        result = await config_repo.add_skills_to_config_json(
            agent_config_id=agent_config_id,
            new_slugs=[slug],
        )
        if result:
            logger.info(f"Skill '{slug}' added to agent config {agent_config_id}")
        else:
            logger.warning(f"Failed to add skill '{slug}' to config {agent_config_id}")
        return result


@tool(
    category="buildin",
    tags=["skill", "安装"],
    display_name="安装技能",
    args_schema=InstallSkillInput,
)
def install_skill(
    source: str,
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    skill_names: list[str] | None = None,
) -> Command:
    """从 Sandbox 路径或 Git 仓库安装 skill 到平台。

    管理员安装后自动启用，新对话中立即生效。

    Sandbox 路径（以 / 开头）：
      指定沙盒中包含 SKILL.md 的目录，支持所有 /home/gem/... 路径。
      例如 /home/gem/user-data/workspace/my-skill

    Git 仓库（不以 / 开头）：
      支持 owner/repo 格式或完整 GitHub URL。
      必须指定 skill_names 选择要安装的 skill，至少一个。
    """
    import asyncio
    from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills
    from yuxi.services.skill_service import sync_thread_visible_skills

    thread_id = getattr(runtime.context, "thread_id", None)
    user_id = getattr(runtime.context, "user_id", None)
    if not thread_id or not user_id:
        return Command(update={
            "messages": [ToolMessage(content="错误：无法获取当前会话信息", tool_call_id=tool_call_id)]
        })

    try:
        asyncio.run(_assert_admin(user_id))

        installed_slugs: list[str] = []
        failed_items: list[dict] = []
        slug_warnings: list[str] = []

        if source.startswith("/"):
            # Sandbox 路径安装
            original_slug = PurePosixPath(source.strip().rstrip("/")).name
            actual_slug = asyncio.run(_install_skill_from_sandbox(source, thread_id, user_id))
            installed_slugs = [actual_slug]
            if actual_slug != original_slug:
                slug_warnings.append(f"⚠️ 技能 '{original_slug}' 已存在，已安装为 '{actual_slug}'")
        else:
            # Git 仓库安装
            _skill_names = skill_names or []
            if not _skill_names:
                return Command(update={
                    "messages": [ToolMessage(
                        content=f"❌ Git 安装需要指定 skill_names 参数。\n"
                                f"在沙盒中执行 'npx skills list --source {source}' "
                                f"查看可用的 skill 列表。",
                        tool_call_id=tool_call_id,
                    )]
                })
            results = asyncio.run(_install_git_skills(source, _skill_names, user_id))
            installed_slugs = [r["slug"] for r in results if r.get("success")]
            failed_items = [r for r in results if not r.get("success")]

        # 持久化：每个成功安装的 skill 单独写入 agent config
        config_success = True
        if installed_slugs:
            for slug in installed_slugs:
                ok = asyncio.run(_enable_skill_in_current_config(user_id, thread_id, slug))
                if not ok:
                    config_success = False

            # 文件同步（传递 current_skills + 新 skills，否则会删除已有的）
            current_skills = normalize_selected_skills(
                getattr(runtime.context, "skills", None)
            )
            sync_thread_visible_skills(thread_id, current_skills + installed_slugs)

        # 构建返回消息
        lines: list[str] = []
        if installed_slugs:
            lines.append(f"✅ Skill '{', '.join(installed_slugs)}' 安装成功！已启用，所有使用当前配置的对话中自动生效。")
        for w in slug_warnings:
            lines.append(w)
        for r in failed_items:
            lines.append(f"❌ {r['slug']}: {r.get('error', '未知错误')}")
        if not config_success:
            lines.append("⚠️ Skill 已安装但持久化到配置失败，请联系管理员。")

        return Command(update={
            "activated_skills": installed_slugs,
            "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
        })
    except ValueError as e:
        return Command(update={
            "messages": [ToolMessage(content=f"❌ 安装失败: {e}", tool_call_id=tool_call_id)]
        })
    except Exception as e:
        logger.exception("install_skill 异常")
        return Command(update={
            "messages": [ToolMessage(content=f"❌ 安装异常: {e}", tool_call_id=tool_call_id)]
        })
