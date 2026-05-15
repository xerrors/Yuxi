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
SANDBOX_PATH_HINT = (
    "请使用 /home/gem/user-data/workspace/...、/home/gem/user-data/uploads/... 或 /home/gem/user-data/outputs/..."
)


class InstallSkillInput(BaseModel):
    source: str = Field(
        description="Skill 来源，支持两种格式:\n"
        "1. Sandbox 路径: /home/gem/user-data/workspace/...、"
        "/home/gem/user-data/uploads/... 或 /home/gem/user-data/outputs/...（/ 开头）\n"
        "2. Git 仓库: owner/repo 或完整 GitHub URL"
    )
    skill_names: list[str] | None = Field(
        default=None, description="Git 安装时指定要安装的 skill slug 列表（至少一个）。Sandbox 路径安装时忽略此参数。"
    )


async def _assert_admin(db, user_id: str) -> None:
    """验证用户是管理员，否则抛出 ValueError。"""
    repo = UserRepository()
    user = await repo.get_by_id_with_db(db, int(user_id))
    if user is None:
        raise ValueError("用户不存在")
    if user.role not in ADMIN_ROLES:
        raise ValueError("仅管理员可以安装 skill")


def _collect_sandbox_file_paths(backend, remote_dir: str) -> list[str]:
    entries = backend.ls_info(remote_dir)
    file_paths: list[str] = []
    for entry in entries:
        path = entry["path"]
        if entry.get("is_dir"):
            file_paths.extend(_collect_sandbox_file_paths(backend, path))
        else:
            file_paths.append(path)
    return file_paths


def _download_skill_dir(backend, remote_dir: str, local_dir: Path) -> None:
    """递归通过沙盒 API 下载 skill 目录到本地。"""
    remote_root = PurePosixPath(remote_dir.rstrip("/"))
    file_paths = _collect_sandbox_file_paths(backend, remote_dir)
    if not file_paths:
        raise ValueError(f"沙盒路径 {remote_dir} 中未发现可下载文件")

    responses = backend.download_files(file_paths)
    if len(responses) != len(file_paths):
        raise ValueError("沙盒文件下载结果数量不匹配")

    for expected_path, response in zip(file_paths, responses):
        error = getattr(response, "error", None)
        content = getattr(response, "content", None)
        if error or content is None:
            raise ValueError(f"下载沙盒文件失败: {expected_path} ({error or 'empty_content'})")

        pure_path = PurePosixPath(expected_path)
        try:
            relative_path = pure_path.relative_to(remote_root)
        except ValueError:
            relative_path = PurePosixPath(pure_path.name)

        target_path = local_dir / Path(relative_path.as_posix())
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)


def _prepare_skill_from_sandbox(
    sandbox_path: str, thread_id: str, user_id: str, staging_root: Path
) -> tuple[Path, str]:
    """从 Sandbox 路径准备 skill 目录。返回 (本地目录, 原始 skill name)。"""
    from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, resolve_virtual_path
    from yuxi.services.skill_service import (
        _parse_skill_markdown,
        is_valid_skill_slug,
    )

    slug = PurePosixPath(sandbox_path.rstrip("/")).name
    if not is_valid_skill_slug(slug):
        raise ValueError(f"slug '{slug}' 不合法（仅允许小写字母、数字和连字符）")

    if not sandbox_path.startswith("/home/gem/user-data/"):
        raise ValueError(f"不支持的沙盒路径: {sandbox_path}。{SANDBOX_PATH_HINT}")

    staging = staging_root / slug

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
    return staging, parsed_name


async def _enable_skills_in_current_config(db, thread_id: str, skill_slugs: list[str]) -> bool:
    """在当前会话的配置中启用新安装的 skill"""
    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conv:
        return False

    agent_config_id = (conv.extra_metadata or {}).get("agent_config_id")
    if not agent_config_id:
        return False

    config_repo = AgentConfigRepository(db)
    result = await config_repo.add_skills_to_config_json(agent_config_id=agent_config_id, new_slugs=skill_slugs)
    return result


async def _run_install_task(
    source: str,
    runtime: ToolRuntime,
    tool_call_id: str,
    skill_names: list[str] | None = None,
) -> Command:
    """执行异步安装任务的核心逻辑"""
    from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills
    from yuxi.services.remote_skill_install_service import prepare_remote_skills_batch
    from yuxi.services.skill_service import import_skill_dir, sync_thread_visible_skills

    user_id = getattr(runtime.context, "user_id", None)
    thread_id = getattr(runtime.context, "thread_id", None)

    logger.info(f"DEBUG: install_skill called with user_id={user_id}, thread_id={thread_id}, source={source}")

    if not user_id or not thread_id:
        return Command(
            update={"messages": [ToolMessage(content="错误：无法获取当前会话信息", tool_call_id=tool_call_id)]}
        )

    try:
        async with pg_manager.get_async_session_context() as db:
            await _assert_admin(db, user_id)

        installed_slugs: list[str] = []
        failed_items: list[dict] = []
        slug_warnings: list[str] = []
        config_success = True

        if source.startswith("/"):
            with tempfile.TemporaryDirectory(prefix=".skill-install-") as tmp:
                source_dir, parsed_name = _prepare_skill_from_sandbox(source, thread_id, user_id, Path(tmp))
                async with pg_manager.get_async_session_context() as db:
                    await _assert_admin(db, user_id)
                    item = await import_skill_dir(db, source_dir=source_dir, created_by=user_id)
                    installed_slugs = [item.slug]
                    if item.slug != parsed_name:
                        slug_warnings.append(f"⚠️ 技能 slug '{item.slug}' 已存在，已自动重命名安装")
                    config_success = await _enable_skills_in_current_config(db, thread_id, installed_slugs)
        else:
            _skill_names = skill_names or []
            if not _skill_names:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="❌ 错误: 从 Git 安装时必须通过 skill_names 指定技能名称",
                                tool_call_id=tool_call_id,
                            )
                        ]
                    }
                )

            preparation = await prepare_remote_skills_batch(source=source, skills=_skill_names)
            try:
                async with pg_manager.get_async_session_context() as db:
                    await _assert_admin(db, user_id)
                    for result in preparation.results:
                        if not result.get("success"):
                            failed_items.append(result)
                            continue

                        try:
                            item = await import_skill_dir(db, source_dir=result["source_dir"], created_by=user_id)
                            installed_slugs.append(item.slug)
                        except Exception as e:
                            await db.rollback()
                            failed_items.append({"slug": result["slug"], "success": False, "error": str(e)})

                    config_success = True
                    if installed_slugs:
                        config_success = await _enable_skills_in_current_config(db, thread_id, installed_slugs)
            finally:
                preparation.cleanup()

        # 文件同步
        current_skills = normalize_selected_skills(getattr(runtime.context, "skills", None))
        sync_thread_visible_skills(thread_id, normalize_selected_skills(current_skills + installed_slugs))

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

        return Command(
            update={
                "activated_skills": installed_slugs,
                "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
            }
        )

    except Exception as e:
        logger.exception("install_skill 异常")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"❌ 安装异常: {str(e)}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )


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
      1. Sandbox 路径: /home/gem/user-data/workspace/...、/home/gem/user-data/uploads/...
         或 /home/gem/user-data/outputs/...
      2. Git 仓库: 例如 "owner/repo" 或 "https://github.com/owner/repo"
    - skill_names: 从 Git 仓库安装时必填，指定要安装的技能列表。

    注意:
    - 仅管理员 (admin/superadmin) 有权执行此操作。
    - 安装成功后，该技能会自动在当前会话 (thread) 中激活。
    """
    return await _run_install_task(source, runtime, tool_call_id, skill_names)
