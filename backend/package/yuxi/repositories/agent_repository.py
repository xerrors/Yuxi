from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Collection
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.context import AGENT_RUNTIME_RESOURCE_FIELDS
from yuxi.permissions import ResourcePermission, normalize_permission_config, resolve_agent_permission
from yuxi.storage.postgres.models_business import Agent, User
from yuxi.utils.datetime_utils import utc_now_naive

DEFAULT_AGENT_SLUG = "default-chatbot"
DEFAULT_AGENT_NAME = "智能助手"
DEFAULT_AGENT_BACKEND_ID = "ChatbotAgent"
SUB_AGENT_BACKEND_ID = "SubAgentBackend"
DEFAULT_AGENT_DESCRIPTION = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
DEFAULT_SHARE_CONFIG = {
    "version": 2,
    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
    "manage_scope": None,
}

GENERAL_PURPOSE_AGENT_SLUG = "general-purpose"
GENERAL_PURPOSE_AGENT_NAME = "通用任务"
GENERAL_PURPOSE_AGENT_DESCRIPTION = (
    "面向没有专用角色约束的一般任务，使用默认运行配置独立完成分析、整理、写作或文件处理。"
)

WEB_SEARCH_AGENT_SLUG = "web-search"
WEB_SEARCH_AGENT_NAME = "网页检索"
WEB_SEARCH_AGENT_DESCRIPTION = "围绕检索目标持续搜索网页，返回带引用来源的摘要资料。"
WEB_SEARCH_SYSTEM_PROMPT = """你是「网页检索」子智能体，专注于面向目标的网页信息检索。

你的职责：围绕调用方给定的检索目标，使用网页搜索工具持续检索，直到收集到足以回答目标的信息。

工作方式：
1. 拆解目标，确定需要检索的关键问题与检索词。
2. 多轮调用搜索工具：依据上一轮结果调整检索词、补充遗漏角度、交叉验证关键事实，直到信息充分或确认无法获取更多有效信息。
3. 优先采信权威、时效性强且彼此印证的来源；对存在冲突的信息要说明分歧。

输出要求：
- 返回一份结构化的摘要资料，按主题或要点组织。
- 每条关键结论后使用 <cite source="$URL" type="url">$INDEX</cite> 标注引用来源，$INDEX 从 1 开始递增。
- 引用不单独成行，直接跟在结论后面。
- 在结尾汇总「参考来源」列表，逐条列出标题与 URL。
- 不要编造来源或链接；无法验证的信息要明确标注。"""

DEEP_RESEARCH_AGENT_SLUG = "deep-research"
DEEP_RESEARCH_AGENT_NAME = "深度研究"
DEEP_RESEARCH_AGENT_DESCRIPTION = (
    "面向多来源、需事实核查的深度研究任务：规划拆解、并行调度调研子智能体、核验并综合成带引用的结构化报告。"
)
DEEP_RESEARCH_SYSTEM_PROMPT = """你是「深度研究」智能体，负责一项深度研究任务的整体把控与子智能体调度。

你的核心定位是编排者，而不是亲自完成所有检索：把繁重、可独立、可并行的调研与核验工作派发给子智能体，自己专注于规划、调度与最终综合。

工作方式：
1. 接到研究任务后，先读取 `deep-research` 技能（read_file 其 SKILL.md）获取完整方法论，并严格据此执行。
2. 问题不明确时先澄清范围，再用待办拆解出可独立调研的子问题。
3. 优先用 `subagent_start` 把子问题并行派发给调研子智能体，需要结果时用 `subagent_await` 等待。
   仅在澄清范围或补少量零散事实时自己直接检索。
4. 对关键结论与相互冲突的发现派发核查子智能体核验，未通过的结论不写入正文或明确降级标注。
5. 证据充分后由你统一综合为结构化、带引用的报告，不要简单拼接子智能体返回的原文。

始终全程跟踪进度，最终交付一份可直接使用、围绕论证组织、来源可追溯的报告。"""

RESEARCH_EXPLORER_AGENT_SLUG = "research-explorer"
RESEARCH_EXPLORER_AGENT_NAME = "调研探索员"
RESEARCH_EXPLORER_AGENT_DESCRIPTION = "围绕单个子问题多轮检索网页与知识库，交叉验证后返回带引用的结构化发现。"
RESEARCH_EXPLORER_SYSTEM_PROMPT = """你是「调研探索员」子智能体。
专注于围绕调用方给定的**单个子问题**收集充分、可追溯的证据。

你的职责：围绕该子问题持续检索网页与知识库，直到收集到足以回答它的信息。

工作方式：
1. 拆解子问题，确定需要检索的关键点与检索词。
2. 多轮调用检索工具：依据上一轮结果调整检索词、补充遗漏角度、交叉验证关键事实，直到信息充分或确认无法获取更多有效信息。
3. 优先采信权威、时效性强且彼此印证的来源；对存在冲突的信息要说明分歧。

输出要求：
- 返回一份围绕该子问题、按要点组织的结构化发现，不要展开成完整报告。
- 每条关键结论后使用 <cite source="$URL" type="url">$INDEX</cite> 标注引用来源，$INDEX 从 1 开始递增。
- 引用紧跟结论后、不单独成行。
- 结尾汇总「参考来源」列表，逐条列出标题与 URL。
- 不要编造来源或链接；无法验证的信息要明确标注证据缺口。"""

FACT_VERIFIER_AGENT_SLUG = "fact-verifier"
FACT_VERIFIER_AGENT_NAME = "事实核查员"
FACT_VERIFIER_AGENT_DESCRIPTION = "对给定论断做对抗式核验，逐条给出支持/存疑/反驳判定、依据来源与置信度，并标注冲突。"
FACT_VERIFIER_SYSTEM_PROMPT = """你是「事实核查员」子智能体，专注于对调用方给定的论断做对抗式核验。

你的职责：对每一条论断独立查证，默认持怀疑态度——证据不足时倾向判定「存疑」，而不是默认相信。

工作方式：
1. 逐条拆出待核验的论断（事实、数字、因果、时间等）。
2. 主动检索权威、独立的来源交叉比对；优先寻找能反驳该论断的证据。
3. 对来源之间的冲突如实呈现，不强行调和。

输出要求：
- 对每条论断给出：判定（支持 / 存疑 / 反驳）+ 简要依据 + 依据来源 + 置信度（高/中/低）。
- 关键依据后使用 <cite source="$URL" type="url">$INDEX</cite> 标注来源，$INDEX 从 1 开始递增。
- 明确标注无法查证或来源相互冲突的论断。
- 不要编造来源或链接。"""

ADMIN_ROLES = {"admin", "superadmin"}
AGENT_RESOURCE_CONFIG_FIELDS = AGENT_RUNTIME_RESOURCE_FIELDS | {"preload_skills"}


def is_builtin_agent(agent: Agent) -> bool:
    return agent.slug == DEFAULT_AGENT_SLUG


def resolve_agent_is_subagent(backend_id: str, is_subagent: bool | None = None) -> bool:
    expected = backend_id == SUB_AGENT_BACKEND_ID
    if is_subagent is not None and bool(is_subagent) != expected:
        raise ValueError("SubAgentBackend 与 is_subagent 必须保持一致")
    return expected


def get_allowed_agent_access_levels(user: User) -> list[str]:
    if user.role in ADMIN_ROLES:
        return ["global", "department", "user"]
    return ["user"]


def normalize_agent_share_config(
    share_config: dict | None,
    *,
    allowed_access_levels: Collection[str] | None = None,
) -> dict:
    return normalize_permission_config(
        share_config or DEFAULT_SHARE_CONFIG,
        allowed_access_levels=allowed_access_levels,
        unauthorized_access_level_message="当前用户无权使用该智能体共享范围",
        strict=True,
    )


def user_can_access_agent(user: User, agent: Agent) -> bool:
    return resolve_agent_permission(user, agent) != ResourcePermission.NONE


def user_can_manage_agent(user: User, agent: Agent) -> bool:
    if is_builtin_agent(agent):
        return user.role in ADMIN_ROLES
    return resolve_agent_permission(user, agent) == ResourcePermission.MANAGE


def _slugify(value: str | None) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip().lower()).strip("-")
    return base[:56] or f"agent-{uuid.uuid4().hex[:12]}"


class AgentRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def ensure_default_agent(self, *, created_by: str | None = None) -> Agent:
        agent = await self.get_by_slug(DEFAULT_AGENT_SLUG)
        if agent:
            needs_update = False
            if agent.share_config != DEFAULT_SHARE_CONFIG:
                agent.share_config = DEFAULT_SHARE_CONFIG.copy()
                needs_update = True
            if not agent.description:
                agent.description = DEFAULT_AGENT_DESCRIPTION
                needs_update = True
            if getattr(agent, "is_subagent", False):
                agent.is_subagent = False
                needs_update = True
            if not agent.is_default:
                return await self.set_default(agent=agent, updated_by=created_by)
            if needs_update:
                agent.updated_by = created_by
                agent.updated_at = utc_now_naive()
                await self.db.commit()
                await self.db.refresh(agent)
            return agent

        agent = Agent(
            slug=DEFAULT_AGENT_SLUG,
            backend_id=DEFAULT_AGENT_BACKEND_ID,
            name=DEFAULT_AGENT_NAME,
            description=DEFAULT_AGENT_DESCRIPTION,
            icon=None,
            pics=[],
            config_json={"context": {}},
            share_config=DEFAULT_SHARE_CONFIG.copy(),
            is_default=True,
            is_subagent=False,
            created_by=created_by,
            updated_by=created_by,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def ensure_web_search_subagent(self, *, created_by: str | None = None) -> Agent:
        agent = await self.get_by_slug(WEB_SEARCH_AGENT_SLUG)
        if agent:
            return agent

        agent = Agent(
            slug=WEB_SEARCH_AGENT_SLUG,
            backend_id=SUB_AGENT_BACKEND_ID,
            name=WEB_SEARCH_AGENT_NAME,
            description=WEB_SEARCH_AGENT_DESCRIPTION,
            icon=None,
            pics=[],
            config_json={"context": {"system_prompt": WEB_SEARCH_SYSTEM_PROMPT}},
            share_config=DEFAULT_SHARE_CONFIG.copy(),
            is_default=False,
            is_subagent=True,
            created_by=created_by,
            updated_by=created_by,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def ensure_general_purpose_subagent(self, *, created_by: str | None = None) -> Agent:
        return await self._ensure_builtin_agent(
            slug=GENERAL_PURPOSE_AGENT_SLUG,
            backend_id=SUB_AGENT_BACKEND_ID,
            name=GENERAL_PURPOSE_AGENT_NAME,
            description=GENERAL_PURPOSE_AGENT_DESCRIPTION,
            config_context={},
            is_subagent=True,
            created_by=created_by,
        )

    async def _ensure_builtin_agent(
        self,
        *,
        slug: str,
        backend_id: str,
        name: str,
        description: str,
        config_context: dict,
        is_subagent: bool,
        created_by: str | None = None,
    ) -> Agent:
        """落库一个内置 Agent；已存在则原样返回，避免覆盖管理员后续修改。"""
        agent = await self.get_by_slug(slug)
        if agent:
            return agent

        agent = Agent(
            slug=slug,
            backend_id=backend_id,
            name=name,
            description=description,
            icon=None,
            pics=[],
            config_json={"context": config_context},
            share_config=DEFAULT_SHARE_CONFIG.copy(),
            is_default=False,
            is_subagent=is_subagent,
            created_by=created_by,
            updated_by=created_by,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def ensure_deep_research_agents(self, *, created_by: str | None = None) -> None:
        """落库内置「深度研究」编排器及其配套调研、核查子智能体。"""
        await self._ensure_builtin_agent(
            slug=RESEARCH_EXPLORER_AGENT_SLUG,
            backend_id=SUB_AGENT_BACKEND_ID,
            name=RESEARCH_EXPLORER_AGENT_NAME,
            description=RESEARCH_EXPLORER_AGENT_DESCRIPTION,
            config_context={"system_prompt": RESEARCH_EXPLORER_SYSTEM_PROMPT},
            is_subagent=True,
            created_by=created_by,
        )
        await self._ensure_builtin_agent(
            slug=FACT_VERIFIER_AGENT_SLUG,
            backend_id=SUB_AGENT_BACKEND_ID,
            name=FACT_VERIFIER_AGENT_NAME,
            description=FACT_VERIFIER_AGENT_DESCRIPTION,
            config_context={"system_prompt": FACT_VERIFIER_SYSTEM_PROMPT},
            is_subagent=True,
            created_by=created_by,
        )
        await self._ensure_builtin_agent(
            slug=DEEP_RESEARCH_AGENT_SLUG,
            backend_id=DEFAULT_AGENT_BACKEND_ID,
            name=DEEP_RESEARCH_AGENT_NAME,
            description=DEEP_RESEARCH_AGENT_DESCRIPTION,
            config_context={
                "system_prompt": DEEP_RESEARCH_SYSTEM_PROMPT,
                "subagents": [RESEARCH_EXPLORER_AGENT_SLUG, FACT_VERIFIER_AGENT_SLUG],
                "skills": [DEEP_RESEARCH_AGENT_SLUG],
            },
            is_subagent=False,
            created_by=created_by,
        )

    async def list_visible(self, *, user: User, include_subagent_definitions: bool = False) -> list[Agent]:
        """列出用户可见的主智能体，只有显式请求时才包含子智能体定义。"""
        stmt = select(Agent)
        if not include_subagent_definitions:
            stmt = stmt.where(Agent.is_subagent.is_(False))
        result = await self.db.execute(stmt.order_by(Agent.is_default.desc(), Agent.id.asc()))
        agents = list(result.scalars().all())
        if user.role == "superadmin":
            return agents
        return [agent for agent in agents if user_can_access_agent(user, agent)]

    async def list_visible_subagents(self, *, user: User) -> list[Agent]:
        result = await self.db.execute(
            select(Agent).where(Agent.is_subagent.is_(True)).order_by(Agent.name.asc(), Agent.id.asc())
        )
        agents = list(result.scalars().all())
        if user.role == "superadmin":
            return agents
        return [agent for agent in agents if user_can_access_agent(user, agent)]

    async def get_by_slug(self, slug: str) -> Agent | None:
        result = await self.db.execute(select(Agent).where(Agent.slug == slug))
        return result.scalar_one_or_none()

    async def list_by_slugs(self, slugs: list[str]) -> list[Agent]:
        result = await self.db.execute(select(Agent).where(Agent.slug.in_(slugs)))
        return list(result.scalars().all())

    async def get_visible_by_slug(
        self, *, slug: str, user: User, kind: Literal["main", "subagent", "any"] = "main"
    ) -> Agent | None:
        """按 slug 读取用户可见智能体，并按入口语义过滤主/子智能体。"""
        agent = await self.get_by_slug(slug)
        if not agent:
            return None
        if not user_can_access_agent(user, agent):
            return None
        if kind == "any":
            return agent
        if kind == "main":
            return None if agent.is_subagent else agent
        if kind == "subagent":
            return agent if agent.is_subagent else None
        raise ValueError(f"未知智能体入口类型: {kind}")

    async def get_default(self) -> Agent | None:
        result = await self.db.execute(select(Agent).where(Agent.is_default.is_(True)))
        return result.scalar_one_or_none()

    async def set_default(self, *, agent: Agent, updated_by: str | None = None) -> Agent:
        if agent.is_subagent:
            raise ValueError("子智能体不能设为默认智能体")
        if not is_builtin_agent(agent):
            raise ValueError("默认智能体已固定为内置智能助手")
        share_config = agent.share_config or DEFAULT_SHARE_CONFIG.copy()
        read_scope = share_config.get("read_scope") or {}
        if read_scope.get("access_level") != "global":
            raise ValueError("内置智能体必须全局共享")

        now = utc_now_naive()
        await self.db.execute(update(Agent).where(Agent.is_default.is_(True)).values(is_default=False, updated_at=now))
        agent.is_default = True
        agent.updated_by = updated_by
        agent.updated_at = now
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def _slug_exists(self, slug: str) -> bool:
        result = await self.db.execute(select(Agent.id).where(Agent.slug == slug))
        return result.scalar_one_or_none() is not None

    async def _unique_slug(self, desired: str | None, name: str) -> str:
        base = _slugify(desired or name)
        candidate = base
        idx = 2
        while await self._slug_exists(candidate):
            suffix = f"-{idx}"
            candidate = f"{base[: 80 - len(suffix)]}{suffix}"
            idx += 1
        return candidate

    async def create(
        self,
        *,
        name: str,
        backend_id: str,
        slug: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        pics: list[str] | None = None,
        config_json: dict | None = None,
        config_resource_access: dict[str, Collection[str]] | None = None,
        share_config: dict | None = None,
        is_default: bool = False,
        is_subagent: bool | None = None,
        created_by: str | None = None,
        creator: User | None = None,
    ) -> Agent:
        resolved_is_subagent = resolve_agent_is_subagent(backend_id, is_subagent)
        if resolved_is_subagent and is_default:
            raise ValueError("子智能体不能设为默认智能体")
        owner_uid = str(created_by or "")
        default_share_config = {
            "version": 2,
            "read_scope": {
                "access_level": "user",
                "department_ids": [],
                "user_uids": [owner_uid],
            },
            "manage_scope": None,
        }
        allowed_access_levels = get_allowed_agent_access_levels(creator) if creator else None
        normalized_share_config = normalize_agent_share_config(
            share_config or default_share_config,
            allowed_access_levels=allowed_access_levels,
        )
        if is_default and (normalized_share_config.get("read_scope") or {}).get("access_level") != "global":
            raise ValueError("默认智能体必须全局共享")

        agent = Agent(
            slug=await self._unique_slug(slug, name),
            backend_id=backend_id,
            name=name.strip() or "未命名智能体",
            description=description,
            icon=icon,
            pics=pics or [],
            config_json=merge_agent_config_json(
                {"context": {}},
                config_json or {},
                resource_access=config_resource_access or {},
            ),
            share_config=normalized_share_config,
            is_default=False,
            is_subagent=resolved_is_subagent,
            created_by=created_by,
            updated_by=created_by,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        if is_default:
            return await self.set_default(agent=agent, updated_by=created_by)
        return agent

    async def update(
        self,
        agent: Agent,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        pics: list[str] | None = None,
        config_json: dict | None = None,
        config_resource_access: dict[str, Collection[str]] | None = None,
        share_config: dict | None = None,
        is_subagent: bool | None = None,
        updated_by: str | None = None,
        updater: User | None = None,
    ) -> Agent:
        if is_subagent is not None:
            agent.is_subagent = resolve_agent_is_subagent(agent.backend_id, is_subagent)
        if name is not None:
            agent.name = name.strip() or "未命名智能体"
        if description is not None:
            agent.description = description
        if icon is not None:
            agent.icon = icon
        if pics is not None:
            agent.pics = pics
        if config_json is not None:
            result = await self.db.execute(select(Agent.config_json).where(Agent.id == agent.id).with_for_update())
            row = result.one_or_none()
            if row is None:
                raise ValueError("智能体不存在")
            agent.config_json = merge_agent_config_json(
                row[0],
                config_json,
                resource_access=config_resource_access or {},
            )
        if share_config is not None:
            if is_builtin_agent(agent):
                agent.share_config = DEFAULT_SHARE_CONFIG.copy()
            else:
                allowed_access_levels = get_allowed_agent_access_levels(updater) if updater else None
                agent.share_config = normalize_agent_share_config(
                    share_config,
                    allowed_access_levels=allowed_access_levels,
                )

        agent.updated_by = updated_by
        agent.updated_at = utc_now_naive()
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def delete(self, *, agent: Agent) -> None:
        await self.db.delete(agent)
        await self.db.commit()

    async def serialize(
        self,
        agent: Agent,
        *,
        user: User,
        include_configurable_items: bool = False,
        backend_info_cache: dict[tuple[str, bool, str], dict] | None = None,
    ) -> dict[str, Any]:
        data = agent.to_dict()
        data["share_config"] = normalize_permission_config(
            agent.share_config,
        )
        permission = resolve_agent_permission(user, agent)
        is_builtin = is_builtin_agent(agent)
        data["can_manage"] = user_can_manage_agent(user, agent)
        data["effective_permission"] = permission.value
        data["is_builtin"] = is_builtin
        data["permission_locked"] = is_builtin

        from yuxi.agents.buildin import agent_manager

        backend = agent_manager.get_agent(agent.backend_id)
        if backend:
            cache_key = (agent.backend_id, include_configurable_items, user.role)
            backend_info = backend_info_cache.get(cache_key) if backend_info_cache is not None else None
            if backend_info is None:
                backend_info = await backend.get_info(
                    include_configurable_items=include_configurable_items,
                    user_role=user.role,
                    db=self.db if include_configurable_items else None,
                    user=user if include_configurable_items else None,
                )
                if backend_info_cache is not None:
                    backend_info_cache[cache_key] = backend_info
            data["capabilities"] = backend_info.get("capabilities", [])
            data["metadata"] = backend_info.get("metadata", {})
            if include_configurable_items:
                data["configurable_items"] = backend_info.get("configurable_items", {})
        else:
            data["capabilities"] = []
            data["metadata"] = {}
            if include_configurable_items:
                data["configurable_items"] = {}
        return data


def merge_agent_config_json(
    existing: dict | None,
    patch: dict,
    *,
    resource_access: dict[str, Collection[str]],
) -> dict:
    """合并 Agent 配置补丁，并在资源字段上保留旧的不可见引用。"""
    if not isinstance(patch, dict):
        raise ValueError("智能体配置必须是对象")

    current = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    patch_copy = copy.deepcopy(patch)
    merged = {**current, **patch_copy}
    if "context" not in patch:
        return merged

    patch_context = patch_copy["context"]
    if not isinstance(patch_context, dict):
        raise ValueError("智能体 context 配置必须是对象")

    current_context = current.get("context")
    if not isinstance(current_context, dict):
        current_context = {}
    merged_context = {**current_context, **patch_context}

    for field_name in AGENT_RESOURCE_CONFIG_FIELDS & patch_context.keys():
        requested = _normalize_resource_references(field_name, patch_context[field_name])
        if not requested:
            merged_context[field_name] = requested
            continue

        if field_name not in resource_access:
            raise ValueError(f"智能体资源字段 {field_name} 未经过权限校验")
        accessible = {str(item) for item in resource_access[field_name]}
        previous = _normalize_resource_references(field_name, current_context.get(field_name)) or []
        previous_set = set(previous)
        unauthorized_new = [item for item in requested if item not in accessible and item not in previous_set]
        if unauthorized_new:
            raise ValueError(f"无权新增智能体资源 {field_name}: {', '.join(unauthorized_new)}")

        requested_set = set(requested)
        kept_existing = [item for item in previous if item not in accessible or item in requested_set]
        new_visible = [item for item in requested if item in accessible and item not in previous_set]
        merged_context[field_name] = [*kept_existing, *new_visible]

    merged["context"] = merged_context
    return merged


def _normalize_resource_references(field_name: str, value: Any) -> list[str] | None:
    """规范化一个资源引用字段，并拒绝非字符串列表。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"智能体资源字段 {field_name} 必须是字符串列表或 null")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"智能体资源字段 {field_name} 必须是字符串列表或 null")
        key = item.strip()
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    return normalized
