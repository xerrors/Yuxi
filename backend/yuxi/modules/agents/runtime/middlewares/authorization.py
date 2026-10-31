"""模型和工具执行边界重新读取身份与授权。"""

from copy import copy

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from sqlalchemy import select

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.runtime.context import BaseContext, refresh_agent_context_resources
from yuxi.modules.agents.runtime.middlewares.skills import injected_skill_identity
from yuxi.modules.agents.runtime.state import BaseState, RuntimeWarning
from yuxi.modules.extensions.mcp.service import available_mcp_servers, runtime_mcp_failure
from yuxi.modules.extensions.skills.runtime import connected_skills, expand_skill_closure, sync_agent_context_skills
from yuxi.modules.identity.models import User
from yuxi.shared.errors import PermissionDeniedError


class AgentExecutionRevoked(PermissionError):
    """账号或 Agent 使用权撤销，必须使执行形成失败终态。"""


class RuntimeAuthorizationMiddleware(AgentMiddleware):
    """每个新模型和工具调用使用当前调用者权限。"""

    state_schema = BaseState

    async def abefore_model(self, state, runtime):
        """刷新 Skill 输入，投影同步留给实际请求边界。"""
        await _refresh_execution_resources(runtime.context, state=state)
        return {"warnings": capability_warnings(runtime.context)}

    async def aafter_model(self, state, runtime):
        """模型返回后、审批或工具路由前复查账号与 Agent。"""
        async with pg_manager.get_async_session_context() as db:
            await _require_execution_actor(runtime.context, db=db)
        return {"warnings": capability_warnings(runtime.context)}

    async def awrap_model_call(self, request, handler):
        """复查身份与 MCP 策略并同步 Skill 投影，隐藏本轮已撤销的已注册工具。"""
        context = request.runtime.context
        mcp_policy = await refresh_execution_authorization(context, state=request.state)
        tools = [tool for tool in request.tools if tool_allowed(context, tool.name, request.state, mcp_policy)]
        return await handler(request.override(tools=tools))

    async def awrap_tool_call(self, request, handler):
        """按当前资源拒绝失权工具，不重建 Skill 文件投影。"""
        context = request.runtime.context
        mcp_policy = await _refresh_execution_resources(context, state=request.state)
        name = request.tool_call["name"]
        if not tool_allowed(context, name, request.state, mcp_policy):
            return ToolMessage(content="能力受限：当前调用者无权使用该工具。", tool_call_id=request.tool_call["id"], status="error")
        server = (getattr(context, "mcp_tool_servers", None) or {}).get(name)
        if not server:
            return await handler(request)
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            try:
                reason, permanent = runtime_mcp_failure(exc)
            except (PermissionError, PermissionDeniedError) as denied:
                raise AgentExecutionRevoked("MCP 工具调用权限被拒绝") from denied
            if permanent:
                context.mcp_unavailable[server] = reason
            logger.warning(
                "MCP tool unavailable: server={}, run_id={}, error_type={}, reason={}",
                server,
                context.run_id,
                type(exc).__name__,
                reason,
            )
            return ToolMessage(content=f"MCP {server} 不可用：{reason}", tool_call_id=request.tool_call["id"], status="error")


def capability_warnings(context) -> list[RuntimeWarning]:
    """生成当前能力警告，整体替换图状态中的列表。"""
    warnings: list[RuntimeWarning] = [
        {"code": "mcp_unavailable", "source": f"mcp:{slug}", "message": f"MCP {slug}：{reason}"}
        for slug, reason in getattr(context, "mcp_unavailable", {}).items()
    ]
    if getattr(context, "_capability_limited", False):
        warnings.append(
            {
                "code": "capability_limited",
                "source": "authorization",
                "message": "部分配置能力不可用，已按当前调用者权限过滤。",
            }
        )
    return warnings


def tool_allowed(context, name: str, state: dict, mcp_policy: dict[str, set[str]]) -> bool:
    """按当前资源和已授权 Skill 依赖检查已注册工具。"""
    servers = getattr(context, "mcp_tool_servers", None)
    registered = getattr(context, "registered_builtin_tool_names", None)
    snapshot = getattr(context, "skill_runtime_snapshot", None)
    if servers is None or registered is None or snapshot is None:
        return False
    allowed_mcps = set(context.mcps)
    allowed_tools = set(context.tools)
    active = set(state.get("activated_skills", [])) | set(snapshot["preloaded_skills"])
    for slug in connected_skills(
        list(active & set(snapshot["effective_skills"])),
        snapshot["runtime_skills"],
        set(mcp_policy),
    ):
        allowed_tools.update(snapshot["runtime_skills"][slug]["tools"])
        allowed_mcps.update(snapshot["runtime_skills"][slug]["mcps"])
    server = servers.get(name)
    if server:
        return server in allowed_mcps and server in mcp_policy and name not in mcp_policy[server]
    return name in getattr(context, "middleware_tool_names", set()) or (name in registered and name in allowed_tools)


async def refresh_execution_authorization(context, *, state=None) -> dict[str, set[str]]:
    """在实际模型与摘要请求前复查身份、解析当前权限并同步 Skill 投影。"""
    injected = injected_skill_identity.get()
    if injected is None:
        mcp_policy = await _execution_mcp_policy(context, state=state)
    else:
        mcp_policy = await _refresh_execution_resources(context, state=state)
        current = context.skill_runtime_snapshot["skill_metadata"]
        if any(current.get(slug) != metadata for slug, metadata in injected.items()):
            raise AgentExecutionRevoked("已注入的 Skill 权限或来源已改变")
    await sync_agent_context_skills(context)
    return mcp_policy


async def _refresh_execution_resources(context: BaseContext, *, state=None) -> dict[str, set[str]]:
    """模型与工具边界刷新资源和 Skill 快照，并解析当前 MCP 策略。"""
    refreshed = copy(context)
    async with pg_manager.get_async_session_context() as db:
        user, agent, _ = await _require_execution_actor(refreshed, db=db)
        await refresh_agent_context_resources(refreshed, db=db, user=user, agent=agent)
        policy = await _resolve_mcp_policy(refreshed, db=db, user=user, state=state)
    resource_fields = context.get_resource_fields()
    for name in (*resource_fields, "skill_runtime_snapshot", "_resource_selections", "_capability_limited", "agent_slug"):
        setattr(context, name, getattr(refreshed, name))
    return policy


async def _execution_mcp_policy(context, *, state=None) -> dict[str, set[str]]:
    """实际模型与摘要请求复查身份并解析 MCP 策略。"""
    async with pg_manager.get_async_session_context() as db:
        user, _, _ = await _require_execution_actor(context, db=db)
        return await _resolve_mcp_policy(context, db=db, user=user, state=state)


async def _resolve_mcp_policy(context, *, db, user, state=None) -> dict[str, set[str]]:
    """按当前快照与激活 Skill 求候选 MCP，返回各服务的禁用工具集合。"""
    snapshot = context.skill_runtime_snapshot
    candidates = set(context.mcps)
    if snapshot:
        active = [*snapshot["preloaded_skills"], *(state or {}).get("activated_skills", [])]
        for slug in expand_skill_closure(active, snapshot["runtime_skills"]):
            if slug in snapshot["effective_skills"]:
                candidates.update(snapshot["runtime_skills"][slug]["mcps"])
    servers = await available_mcp_servers(db, candidates, user_id=user.id)
    unavailable = getattr(context, "mcp_unavailable", {})
    servers = {slug: server for slug, server in servers.items() if slug not in unavailable}
    return {slug: set(server.disabled_tools or []) for slug, server in servers.items()}


async def _require_execution_actor(context, *, db):
    """复查持久执行身份与 Agent 可见性，返回阶段不扫描资源。"""
    user = await db.scalar(select(User).where(User.uid == context.uid, User.is_deleted == 0))
    run = await db.get(AgentRun, context.run_id) if context.run_id else None
    if context.run_id and run is None:
        raise AgentExecutionRevoked("执行记录已失效")
    if user is None:
        raise AgentExecutionRevoked("执行账号已失效")
    if run is not None:
        agent = await AgentRepository(db).get_visible_by_slug(
            slug=run.agent_slug,
            user=user,
        )
        if agent is None:
            raise AgentExecutionRevoked("Agent 使用权限已撤销")
    else:
        # 主动压缩没有 Run，使用已绑定调用者的 Thread 找到当前 Agent。
        thread = await db.scalar(
            select(Session).where(
                Session.thread_id == context.thread_id,
                Session.uid == user.uid,
                Session.status == "active",
            )
        )
        if thread is None:
            raise AgentExecutionRevoked("执行对话已失效")
        agent = await AgentRepository(db).get_visible_by_slug(slug=thread.agent_id, user=user)
        if agent is None:
            raise AgentExecutionRevoked("Agent 使用权限已撤销")
    context.agent_slug = agent.slug
    return user, agent, run
