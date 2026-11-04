"""AgentRun 运行清单与执行指纹。

在 worker 取得执行所有权后准备唯一的执行 Context，从其实际配置
生成只含稳定标识与非敏感摘要的 manifest，
并以规范化 JSON 的 SHA-256 作为指纹。manifest 由 AgentRun 行拥有，
write-once 固化后不得改写；历史 Run 保持 NULL 表示 unknown。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, fields
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
from yuxi.modules.agents.runtime.context import BaseContext, prepare_agent_runtime_context
from yuxi.modules.extensions.skills.personal import PERSONAL_SKILL_SOURCE_TYPE
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.runtime_paths import runtime_workdir_path
from yuxi.modules.workspace.services.bindings import AuthorizedWorkdir

MANIFEST_SCHEMA_VERSION = 2
# 直接进入 manifest 的关键 limit 字段；未列出的 context 字段只以 config_digest 形式存在。
MANIFEST_LIMIT_FIELDS = (
    "max_execution_steps",
    "model_retry_times",
    "summary_threshold",
    "summary_keep_messages",
    "summary_tool_result_token_limit",
)


@dataclass(frozen=True)
class PreparedRunExecution:
    """返回同一次准备产生的执行 Context 与持久化清单。"""

    manifest: dict
    context: BaseContext
    backend_id: str


async def prepare_and_record_run_execution(
    *,
    run: AgentRun,
    user: User,
    worker_id: str,
    workdir_binding: AuthorizedWorkdir,
) -> PreparedRunExecution:
    """在构图执行前固化运行清单与指纹；准备和固化失败由调用方收尾。"""
    async with pg_manager.get_async_session_context() as db:
        result = await prepare_run_execution(
            run=run,
            user=user,
            db=db,
            worker_id=worker_id,
            workdir_binding=workdir_binding,
        )
        fingerprint = compute_manifest_fingerprint(result.manifest)
        persisted_run, recorded = await AgentRunRepository(db).record_run_manifest(
            run.id,
            manifest=result.manifest,
            fingerprint=fingerprint,
            worker_id=worker_id,
        )
        _require_persisted_manifest_match(persisted_run, recorded=recorded, fingerprint=fingerprint)
        return result


def canonical_json(payload: Any) -> str:
    """键排序 + 紧凑分隔符的确定性序列化，保证字段顺序不影响指纹。"""
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def compute_manifest_fingerprint(manifest: dict) -> str:
    """计算运行清单的 SHA-256 指纹。"""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def compute_config_digest(normalized_context: dict) -> str:
    """对完整规范化 context 计算 SHA-256 摘要；prompt 等内容只以摘要形式进入 manifest。"""
    return hashlib.sha256(canonical_json(normalized_context or {}).encode("utf-8")).hexdigest()


def _resource_keys(value: Any) -> list[str]:
    """提取资源字段中的字符串键；非列表或非字符串项忽略，避免不可序列化值进入 manifest。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def build_manifest_payload(
    *,
    run_type: str,
    agent_slug: str,
    backend_id: str | None,
    model_spec: str | None,
    tool_approval_mode: str | None,
    normalized_context: dict,
    skill_entries: list[dict],
    code_revision: str | None,
    limits: dict,
) -> dict:
    """从已解析的执行资产组装 manifest；直接字段仅限稳定标识、摘要与关键 limit。

    limits 由调用方传入实际生效值（含 schema 默认值），不在此处解析。
    """
    return {
        "manifest_version": MANIFEST_SCHEMA_VERSION,
        "run_type": run_type,
        "agent": {
            "slug": agent_slug,
            "backend_id": backend_id,
        },
        "model": {
            "spec": model_spec if isinstance(model_spec, str) and model_spec else None,
        },
        "tool_approval_mode": tool_approval_mode,
        "resources": {
            "tools": _resource_keys(normalized_context.get("tools")),
            "mcps": _resource_keys(normalized_context.get("mcps")),
            "skills": skill_entries,
        },
        "limits": limits,
        "config_digest": compute_config_digest(normalized_context),
        "code_revision": code_revision or "unresolved",
    }


def build_skill_manifest_entries(config: dict, skill_scope: dict) -> list[dict]:
    """只从首次授权解析结果投影 Skill 身份与实际预加载内容摘要。"""
    slugs = list(dict.fromkeys([*_resource_keys(config.get("skills")), *skill_scope["preloaded_skills"]]))
    entries = []
    for slug in slugs:
        metadata = skill_scope["skill_metadata"][slug]
        personal = metadata["source_scope"] == PERSONAL_SKILL_SOURCE_TYPE
        entry = {
            "slug": slug,
            "version": None if personal else metadata["version"],
            "content_hash": None if personal else metadata["content_hash"],
        }
        if slug in skill_scope["preloaded_skill_contents"]:
            content = skill_scope["preloaded_skill_contents"][slug]
            entry["preload_content_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        entries.append(entry)
    return entries


def resolve_code_revision() -> str | None:
    """读取部署环境提供的代码 revision；缺失时由 build_manifest_payload 显式记为 unresolved。"""
    revision = os.getenv("YUXI_CODE_REVISION", "").strip()
    return revision or None


async def prepare_run_execution(
    *,
    run: AgentRun,
    user: User,
    db: AsyncSession,
    workdir_binding: AuthorizedWorkdir,
    worker_id: str,
) -> PreparedRunExecution:
    """准备唯一执行 Context，并从实际配置派生持久化摘要。"""
    if user is None:
        raise ValueError("执行用户不存在")
    agent_item = await AgentRepository(db).get_visible_by_slug(
        slug=run.agent_slug,
        user=user,
    )
    if agent_item is None:
        raise ValueError("智能体不存在或无权限访问")
    backend = get_agent_backend(agent_item.backend_id)

    context = backend.context_schema()
    configured = run.input_payload.get("context_snapshot")
    if not isinstance(configured, dict) or not {"model", "tool_approval_mode"} <= configured.keys():
        raise ValueError("Run 缺少完整配置快照")
    configurable_fields = {item.name for item in fields(context) if item.metadata.get("configurable", True)}
    context.update_config(configured)
    context.update(
        {
            "tool_approval_mode": configured["tool_approval_mode"],
            "agent_slug": run.agent_slug,
            "thread_id": run.thread_id,
            "uid": str(user.uid),
            "run_id": run.id,
            "worker_id": worker_id,
            "runtime_scope_id": run.runtime_scope_id or run.thread_id,
            "workdir_relative_path": workdir_binding.workdir_path,
            "workdir_path": runtime_workdir_path(workdir_binding.workdir_path),
        }
    )
    configured_prompt = context.system_prompt
    context = await prepare_agent_runtime_context(context)
    if not getattr(context, "_runtime_prepared", False):
        raise ValueError("执行用户不存在，无法准备 Context")

    # 会话审批独立于智能体配置，仍参与协作继承与 manifest 摘要。
    effective_config = {name: getattr(context, name) for name in configurable_fields}
    effective_config["tool_approval_mode"] = context.tool_approval_mode
    # 工作区提示由每个成员准备时加载，配置继承不能重复拼接派发方的展开文本。
    context._cooperation_config_snapshot = {**effective_config, "system_prompt": configured_prompt}
    manifest = build_manifest_payload(
        run_type=run.run_type,
        agent_slug=run.agent_slug,
        backend_id=agent_item.backend_id,
        model_spec=context.model,
        tool_approval_mode=context.tool_approval_mode,
        normalized_context=effective_config,
        limits={name: getattr(context, name, None) for name in MANIFEST_LIMIT_FIELDS},
        skill_entries=build_skill_manifest_entries(effective_config, context.skill_runtime_snapshot),
        code_revision=resolve_code_revision(),
    )
    return PreparedRunExecution(manifest=manifest, context=context, backend_id=agent_item.backend_id)


def _require_persisted_manifest_match(persisted_run: AgentRun | None, *, recorded: bool, fingerprint: str) -> None:
    """重试只能复用与 write-once manifest 完全一致的运行资产。"""
    if recorded:
        return
    if persisted_run is None or persisted_run.manifest_fingerprint != fingerprint:
        raise RuntimeError("运行资产已在重试前变化，与已固化 manifest 不一致")
