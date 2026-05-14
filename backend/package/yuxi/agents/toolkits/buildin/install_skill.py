import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg

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

async def _assert_admin(db, user_id: str) -> None:
    """验证用户是管理员，否则抛出 ValueError。"""
    repo = UserRepository()
    user = await repo.get_by_id_with_db(db, int(user_id))
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


async def _install_skill_from_sandbox(db, sandbox_path: str, thread_id: str, user_id: str) -> tuple[str, bool]:
    """从 Sandbox 路径安装 skill。返回 (slug, 是否因冲突被重命名)。"""
    from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, resolve_virtual_path
    from yuxi.services.skill_service import (
        _parse_skill_markdown,
        import_skill_dir,
        is_valid_skill_slug,
    )

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

        content = (staging / "SKILL.md").read_text(encoding="utf-8")
        parsed_name, _, _ = _parse_skill_markdown(content)
        result = await import_skill_dir(db, source_dir=staging, created_by=user_id)
    return result.slug, result.slug != parsed_name


async def _enable_skill_in_current_config(db, user_id: str, thread_id: str, skill_slug: str) -> bool:
    """在当前会话的配置中启用新安装的 skill"""
    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conv:
        return False
        
    agent_config_id = (conv.extra_metadata or {}).get("agent_config_id")
    if not agent_config_id:
        return False

    config_repo = AgentConfigRepository(db)
    result = await config_repo.add_skills_to_config_json(
        agent_config_id=agent_config_id, new_slugs=[skill_slug]
    )
    return result


async def _run_install_task(
    source: str,
    runtime: ToolRuntime,
    tool_call_id: str,
    skill_names: list[str] | None = None,
) -> Command:
    """执行异步安装任务的核心逻辑"""
    from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills
    from yuxi.services.skill_service import sync_thread_visible_skills
    from yuxi.services.remote_skill_install_service import install_remote_skills_batch

    user_id = getattr(runtime.context, "user_id", None)
    thread_id = getattr(runtime.context, "thread_id", None)
    
    logger.info(f"DEBUG: install_skill called with user_id={user_id}, thread_id={thread_id}, source={source}")
    
    if not user_id or not thread_id:
        return Command(update={
            "messages": [ToolMessage(content="错误：无法获取当前会话信息", tool_call_id=tool_call_id)]
        })

    try:
        async with pg_manager.get_async_session_context() as db:
            await _assert_admin(db, user_id)

            installed_slugs: list[str] = []
            failed_items: list[dict] = []
            slug_warnings: list[str] = []

            if source.startswith("/"):
                # Sandbox 路径安装
                actual_slug, was_renamed = await _install_skill_from_sandbox(db, source, thread_id, user_id)
                installed_slugs = [actual_slug]
                if was_renamed:
                    slug_warnings.append(f"⚠️ 技能 slug '{actual_slug}' 已存在，已自动重命名安装")
            else:
                # Git 安装
                _skill_names = skill_names or []
                if not _skill_names:
                    return Command(update={
                        "messages": [ToolMessage(
                            content="❌ 错误: 从 Git 安装时必须通过 skill_names 指定技能名称",
                            tool_call_id=tool_call_id,
                        )]
                    })
                results = await install_remote_skills_batch(db, source=source, skills=_skill_names, created_by=user_id)
                installed_slugs = [r["slug"] for r in results if r.get("success")]
                failed_items = [r for r in results if not r.get("success")]

            # 持久化
            config_success = True
            if installed_slugs:
                for slug in installed_slugs:
                    ok = await _enable_skill_in_current_config(db, user_id, thread_id, slug)
                    if not ok:
                        config_success = False

            # 文件同步
            current_skills = normalize_selected_skills(
                getattr(runtime.context, "skills", None)
            )
            sync_thread_visible_skills(thread_id, current_skills + installed_slugs)

            # 响应
            lines = []
            if installed_slugs:
                lines.append(f"✅ 成功安装并激活技能: {', '.join(installed_slugs)}")
            for w in slug_warnings:
                lines.append(w)
            if failed_items:
                for item in failed_items:
                    lines.append(f"❌ 安装失败 ({item['slug']}): {item.get('error', '未知错误')}")
            if not config_success:
                lines.append("⚠️ 技能已安装到系统，但在当前会话配置中激活失败")
            if not installed_slugs and not failed_items:
                lines.append("ℹ️ 未发现需要安装的技能")

            return Command(update={
                "activated_skills": installed_slugs,
                "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
            })

    except Exception as e:
        logger.exception("install_skill 异常")
        return Command(update={
            "messages": [ToolMessage(
                content=f"❌ 安装异常: {str(e)}",
                tool_call_id=tool_call_id,
            )]
        })


@tool(
    category="buildin",
    tags=["skill", "安装"],
    display_name="安装技能",
    args_schema=InstallSkillInput,
)
async def install_skill(
    source: str,
    skill_names: list[str] | None = None,
    runtime: ToolRuntime = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """安装新的技能 (Skill) 到系统中。

    参数说明:
    - source: 必填。支持两种格式:
      1. Sandbox 路径: 例如 "/tmp/my-skill"
      2. Git 仓库: 例如 "owner/repo" 或 "https://github.com/owner/repo"
    - skill_names: 从 Git 仓库安装时必填，指定要安装的技能列表。

    注意:
    - 仅超级管理员 (superadmin) 有权执行此操作。
    - 安装成功后，该技能会自动在当前会话 (thread) 中激活。
    """
    return await _run_install_task(source, runtime, tool_call_id, skill_names)
