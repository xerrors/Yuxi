"""Define the configurable parameters for the agent."""

import asyncio
import uuid
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, get_origin

from yuxi.agents.backends.sandbox.paths import sandbox_workspace_agent_context_file
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import WORKSPACE_AGENT_CONTEXT_FILES

WORKSPACE_AGENTS_PROMPT_MAX_BYTES = 64 * 1024
DEFAULT_SUMMARY_THRESHOLD_K = 100  # 100K tokens
DEFAULT_SUMMARY_KEEP_MESSAGES = 10
DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT = 300
DEFAULT_SUMMARY_L2_TRIGGER_RATIO = 0.4
DEFAULT_MAX_EXECUTION_STEPS = 300
DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS = 3
DEFAULT_YUXI_SUMMARY_PROMPT = """你是对话上下文压缩助手。
你的任务是把下面的对话历史压缩成后续智能体继续工作所需的高价值上下文。

请特别保留并清晰记录：

## SESSION INTENT
用户当前的主要目标、任务范围和最终交付物。

## USER REQUIREMENTS AND PREFERENCES
用户明确提出的要求、偏好、禁忌、输出格式、语言风格、技术约束、验收标准，以及对实现方式的取舍意见。只记录仍然可能影响后续回答或执行的内容。

## PROGRESS AND DECISIONS
已经完成的步骤、关键结论、已确认的方案、被否定的方案及原因。

## ARTIFACTS AND REFERENCES
已经创建、修改、读取或需要继续关注的文件、路径、工具输出路径、线程或运行标识。保留具体路径和关键标识符。

## NEXT STEPS
为了完成用户目标，后续最应该继续做的具体步骤。没有待办时写 None。

要求：
- 不要逐字复述冗长工具输出；保留结论、路径和必要证据。
- 不要编造没有出现在对话中的事实。
- 如果存在未解决的问题或风险，明确记录。
- 使用与用户主要对话一致的语言。

<messages>
{messages}
</messages>

只输出压缩后的上下文，不要添加额外说明。"""


def _role_can_access(auth: str | None, role: str | None) -> bool:
    if not auth:
        return True
    if auth == "admin":
        return role in {"admin", "superadmin"}
    if auth == "superadmin":
        return role == "superadmin"
    return False


def _load_workspace_agent_context(thread_id: str, uid: str) -> str:
    sections: list[str] = []
    for filename in WORKSPACE_AGENT_CONTEXT_FILES:
        context_file = sandbox_workspace_agent_context_file(thread_id, uid, filename)
        try:
            with context_file.open("rb") as buffer:
                content = buffer.read(WORKSPACE_AGENTS_PROMPT_MAX_BYTES + 1)
        except FileNotFoundError:
            continue
        except IsADirectoryError:
            logger.warning(f"读取工作区 {filename} 失败: 路径是目录")
            continue
        except OSError as exc:
            logger.warning(f"读取工作区 {filename} 失败: {exc}")
            continue

        prompt = content[:WORKSPACE_AGENTS_PROMPT_MAX_BYTES].decode("utf-8", errors="replace").strip()
        if not prompt:
            continue
        if len(content) > WORKSPACE_AGENTS_PROMPT_MAX_BYTES:
            prompt = f"{prompt}\n\n[{filename} 内容已截断]"
        sections.append(f"用户工作区 agents/{filename} 内容：\n{prompt}")
    return "\n\n".join(sections)


async def build_agent_input_context(
    agent_config: dict | None,
    *,
    thread_id: str,
    uid: str,
    run_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    input_context = dict(agent_config or {})
    agent_context = await asyncio.to_thread(_load_workspace_agent_context, thread_id, uid)

    if agent_context:
        base_prompt = str(input_context.get("system_prompt") or "").rstrip()
        input_context["system_prompt"] = f"{base_prompt}\n\n{agent_context}" if base_prompt else agent_context

    input_context.update({"uid": uid, "thread_id": thread_id, "run_id": run_id, "request_id": request_id})
    return input_context


def filter_config_by_role(
    config_json: dict,
    role: str | None,
    context_schema: type["BaseContext"] | None = None,
) -> dict:
    """按 Context 字段 metadata.auth 过滤 config_json.context。"""
    if not isinstance(config_json, dict):
        return {}

    schema = context_schema or BaseContext
    restricted_fields = {
        f.name
        for f in fields(schema)
        if f.metadata.get("auth") and not _role_can_access(str(f.metadata.get("auth")), role)
    }
    if not restricted_fields:
        return dict(config_json)

    filtered = dict(config_json)
    context = filtered.get("context")
    if isinstance(context, dict):
        filtered["context"] = {key: value for key, value in context.items() if key not in restricted_fields}
    return filtered


@dataclass(kw_only=True)
class BaseContext:
    """
    定义一个基础 Context 供 各类 graph 继承

    配置优先级:
    1. 运行时配置(RunnableConfig)：最高优先级，直接从函数参数传入
    2. 类默认配置：最低优先级，类中定义的默认值
    """

    def update(self, data: dict):
        """更新配置字段"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    thread_id: str = field(
        default_factory=lambda: str(uuid.uuid4()),
        metadata={"name": "线程ID", "configurable": False, "description": "用来唯一标识一个对话线程"},
    )

    uid: str = field(
        default_factory=lambda: str(uuid.uuid4()),
        metadata={"name": "UID", "configurable": False, "description": "用来唯一标识一个用户"},
    )

    run_id: str | None = field(
        default=None,
        metadata={"name": "运行 ID", "configurable": False, "hide": True},
    )

    request_id: str | None = field(
        default=None,
        metadata={"name": "请求 ID", "configurable": False, "hide": True},
    )

    system_prompt: str = field(
        default="You are a helpful assistant.",
        metadata={"name": "系统提示词", "description": "用来描述智能体的角色和行为", "kind": "prompt"},
    )

    model: str = field(
        default="",
        metadata={
            "name": "智能体模型",
            "options": [],
            "description": "智能体的驱动模型，留空时使用系统默认模型。",
            "kind": "llm",
        },
    )

    tools: list[str] | None = field(
        default=None,
        metadata={
            "name": "工具",
            "description": "内置的工具。默认选择当前用户可用的全部工具。",
            "type": "list",
            "kind": "tools",
        },
    )

    knowledges: list[str] | None = field(
        default=None,
        metadata={
            "name": "知识库",
            "description": "知识库列表，可以在左侧知识库页面中创建知识库。默认选择当前用户可访问的全部知识库。",
            "type": "list",
            "kind": "knowledges",
        },
    )

    mcps: list[str] | None = field(
        default=None,
        metadata={
            "name": "MCP服务器",
            "options": [],
            "description": (
                "MCP服务器列表，默认选择当前用户可用的全部 MCP 服务器。建议使用支持 SSE 的 MCP 服务器，"
                "如果需要使用 uvx 或 npx 运行的服务器，也请在项目外部启动 MCP 服务器，并在项目中配置 MCP 服务器。"
            ),
            "type": "list",
            "kind": "mcps",
        },
    )

    skills: list[str] | None = field(
        default=None,
        metadata={
            "name": "Skills",
            "options": [],
            "description": "可选 Skill 拓展列表，默认选择当前用户可用的全部 Skill 拓展。"
            "Skill 拓展依赖的工具和 MCP 服务器也会被自动挂载。",
            "type": "list",
            "kind": "skills",
        },
    )

    summary_threshold: int = field(
        default=DEFAULT_SUMMARY_THRESHOLD_K,
        metadata={
            "name": "上下文摘要触发阈值 (K)",
            "description": (
                f"当上下文大小超过该值时，启用摘要功能以优化上下文使用。单位为 K，默认值为 "
                f"{DEFAULT_SUMMARY_THRESHOLD_K}K。"
            ),
            "type": "number",
            "auth": "admin",
        },
    )

    summary_keep_messages: int = field(
        default=DEFAULT_SUMMARY_KEEP_MESSAGES,
        metadata={
            "name": "摘要后保留消息数",
            "description": (
                f"上下文摘要触发后，除摘要消息外保留最近的消息数量，默认 {DEFAULT_SUMMARY_KEEP_MESSAGES} 条。"
            ),
            "type": "number",
            "auth": "admin",
        },
    )

    summary_prompt: str = field(
        default=DEFAULT_YUXI_SUMMARY_PROMPT,
        metadata={
            "name": "上下文摘要提示词",
            "description": "触发上下文摘要时使用的提示词，必须能接收 {messages} 作为待摘要消息占位符。",
            "type": "string",
            "kind": "prompt",
            "auth": "admin",
        },
    )

    summary_tool_result_token_limit: int = field(
        default=DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
        metadata={
            "name": "摘要工具结果 token 上限",
            "description": (
                "上下文摘要 L1 清洗历史工具结果时，超过该 token 数的 ToolMessage 会写入 outputs，"
                "并在上下文中保留不超过该 token 数的预览；未超过则保持原样。默认 "
                f"{DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT}。"
            ),
            "type": "number",
            "auth": "admin",
        },
    )

    summary_l2_trigger_ratio: float = field(
        default=DEFAULT_SUMMARY_L2_TRIGGER_RATIO,
        metadata={
            "name": "L2 摘要触发比例",
            "description": (
                "L1 结构精简后，剩余上下文超过 摘要触发阈值 * 该比例 时才进入 L2 summary。"
                "建议范围 0.1 到 1.0，值越小越容易触发 L2，默认 "
                f"{DEFAULT_SUMMARY_L2_TRIGGER_RATIO}。"
            ),
            "type": "number",
            "auth": "admin",
        },
    )

    max_execution_steps: int = field(
        default=DEFAULT_MAX_EXECUTION_STEPS,
        metadata={
            "name": "最大执行步数",
            "description": (
                "单次 Agent 运行允许的最大 LangGraph 执行步数，对应 recursion_limit，默认 "
                f"{DEFAULT_MAX_EXECUTION_STEPS}。"
            ),
            "type": "number",
            "auth": "admin",
        },
    )

    model_retry_times: int = field(
        default=2,
        metadata={
            "name": "模型重试次数",
            "description": "模型调用失败时的最大重试次数，默认值为 2。",
            "type": "number",
            "auth": "admin",
        },
    )

    @classmethod
    def get_configurable_items(cls, user_role: str | None = None):
        """实现一个可配置的参数列表，在 UI 上配置时使用"""
        configurable_items = {}
        for f in fields(cls):
            if f.init and not f.metadata.get("hide", False):
                if user_role is not None and not _role_can_access(f.metadata.get("auth"), user_role):
                    continue
                if f.metadata.get("configurable", True):
                    type_name = cls._get_type_name(f.type)

                    options = f.metadata.get("options", [])
                    if callable(options):
                        options = options()

                    configurable_items[f.name] = {
                        "type": f.metadata.get("type", type_name),
                        "name": f.metadata.get("name", f.name),
                        "options": options,
                        "default": f.default
                        if f.default is not MISSING
                        else f.default_factory()
                        if f.default_factory is not MISSING
                        else None,
                        "description": f.metadata.get("description", ""),
                        "kind": f.metadata.get("kind", ""),
                    }

        return configurable_items

    @classmethod
    def _get_type_name(cls, field_type) -> str:
        """获取类型名称"""
        origin = get_origin(field_type)
        if origin is not None:
            if hasattr(origin, "__name__"):
                return origin.__name__
            return str(origin)
        elif hasattr(field_type, "__name__"):
            return field_type.__name__
        else:
            return str(field_type)

    def update_from_dict(self, data: dict):
        """从字典更新配置字段"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)


_DEFAULT_ALL_CONTEXT_FIELDS = frozenset({"tools", "knowledges", "mcps", "skills"})
_EMPTY_ALL_CONTEXT_FIELDS = frozenset({"subagents"})
_AGENT_RESOURCE_FIELDS = _DEFAULT_ALL_CONTEXT_FIELDS | _EMPTY_ALL_CONTEXT_FIELDS


def _normalize_selected_resource_keys(value: Any, available: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []

    allowed = set(available)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        key = item.strip()
        if not key or key in seen or key not in allowed:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def _resource_fields_requiring_available_keys(normalized: dict, resource_fields: set[str]) -> set[str]:
    fields_to_load: set[str] = set()
    for field_name in resource_fields:
        current = normalized.get(field_name)
        if current is None:
            if field_name in _DEFAULT_ALL_CONTEXT_FIELDS | _EMPTY_ALL_CONTEXT_FIELDS:
                fields_to_load.add(field_name)
            else:
                normalized[field_name] = []
        elif field_name in _EMPTY_ALL_CONTEXT_FIELDS and current == []:
            normalized[field_name] = None
            fields_to_load.add(field_name)
        elif isinstance(current, list) and current:
            fields_to_load.add(field_name)
        else:
            normalized[field_name] = []
    return fields_to_load


def _resource_option(key: Any, name: Any = None, description: Any = None) -> dict[str, str]:
    key_value = str(key)
    return {
        "key": key_value,
        "name": str(name or key_value),
        "description": str(description or ""),
    }


async def resolve_agent_resource_options(
    resource_fields: set[str] | None = None,
    *,
    db,
    user,
) -> dict[str, list[dict[str, str]]]:
    fields_to_load = _AGENT_RESOURCE_FIELDS if resource_fields is None else resource_fields
    if not fields_to_load:
        return {}

    options: dict[str, list[dict[str, str]]] = {}

    if "tools" in fields_to_load:
        from yuxi.agents.toolkits.service import get_tool_metadata

        options["tools"] = [
            _resource_option(tool["slug"], tool.get("name"), tool.get("description"))
            for tool in get_tool_metadata(category="buildin")
            if tool.get("slug")
        ]
    if "knowledges" in fields_to_load:
        from yuxi.knowledge.runtime import knowledge_base

        databases = (await knowledge_base.get_databases_by_user(user)).get("databases", [])
        options["knowledges"] = [
            _resource_option(item.get("kb_id"), item.get("name"), item.get("description"))
            for item in databases
            if isinstance(item, dict) and item.get("kb_id")
        ]
    if "mcps" in fields_to_load:
        from yuxi.agents.mcp.service import get_all_mcp_servers

        servers = await get_all_mcp_servers(db)
        options["mcps"] = [
            _resource_option(server.slug, server.name, server.description)
            for server in servers
            if server.enabled and server.slug
        ]
    if "skills" in fields_to_load:
        from yuxi.agents.skills.service import list_accessible_skills

        skills = await list_accessible_skills(db, user)
        options["skills"] = [
            _resource_option(skill.slug, skill.name, skill.description) for skill in skills if skill.slug
        ]
    if "subagents" in fields_to_load:
        from yuxi.repositories.agent_repository import AgentRepository

        subagents = await AgentRepository(db).list_visible_subagents(user=user)
        options["subagents"] = [
            _resource_option(agent.slug, agent.name, agent.description) for agent in subagents if agent.slug
        ]

    return options


async def normalize_agent_context_config(
    context: dict | None,
    *,
    db,
    user,
    context_schema: type[BaseContext] | None = None,
) -> dict:
    schema = context_schema or BaseContext
    raw_context = dict(context) if isinstance(context, dict) else {}
    filtered = filter_config_by_role({"context": raw_context}, getattr(user, "role", None), schema)
    normalized = dict(filtered.get("context") or {})
    field_names = {item.name for item in fields(schema)}
    resource_fields = _AGENT_RESOURCE_FIELDS & field_names
    if not resource_fields:
        return normalized

    fields_to_load = _resource_fields_requiring_available_keys(normalized, resource_fields)
    if not fields_to_load:
        return normalized

    resource_options = await resolve_agent_resource_options(fields_to_load, db=db, user=user)
    available = {
        field_name: [option["key"] for option in field_options]
        for field_name, field_options in resource_options.items()
    }

    for field_name, available_keys in available.items():
        current = normalized.get(field_name)
        if current is None:
            normalized[field_name] = available_keys
        else:
            normalized[field_name] = _normalize_selected_resource_keys(current, available_keys)

    return normalized


async def prepare_agent_runtime_context(
    context: BaseContext,
    *,
    context_schema: type[BaseContext] | None = None,
) -> BaseContext:
    """准备 Agent 运行时上下文，主要是根据 context 中的 uid 加载用户可访问的资源列表，并进行规范化处理。"""
    schema = context_schema or type(context)
    uid = str(getattr(context, "uid", "") or "").strip()
    if not uid:
        return context

    from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context
    from yuxi.agents.middlewares.skills import resolve_runtime_skills_for_context
    from yuxi.repositories.user_repository import UserRepository
    from yuxi.storage.postgres.manager import pg_manager

    resource_fields = _AGENT_RESOURCE_FIELDS
    async with pg_manager.get_async_session_context() as db:
        user = await UserRepository().get_by_uid_with_db(db, uid)
        if user is None:
            for field_name in resource_fields:
                if hasattr(context, field_name):
                    setattr(context, field_name, [])
            setattr(context, "_visible_knowledge_bases", [])
            setattr(context, "_prompt_skills", [])
            setattr(context, "_readable_skills", [])
            setattr(context, "_runtime_skill_metadata", {})
            setattr(context, "_runtime_skill_dependency_map", {})
            return context

        raw_resources = {
            field_name: getattr(context, field_name, None)
            for field_name in resource_fields
            if hasattr(context, field_name)
        }
        normalized = await normalize_agent_context_config(
            raw_resources,
            db=db,
            user=user,
            context_schema=schema,
        )
        for field_name in resource_fields:
            if hasattr(context, field_name):
                setattr(context, field_name, normalized.get(field_name, []))

        await resolve_visible_knowledge_bases_for_context(context)
        skill_scope = await resolve_runtime_skills_for_context(context, db=db, user=user)
        context.skills = skill_scope["context_skills"]
        setattr(context, "_prompt_skills", skill_scope["prompt_skills"])
        setattr(context, "_readable_skills", skill_scope["readable_skills"])
        setattr(context, "_runtime_skill_metadata", skill_scope["runtime_skill_metadata"])
        setattr(context, "_runtime_skill_dependency_map", skill_scope["runtime_skill_dependency_map"])

    return context
