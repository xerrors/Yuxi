"""Subagent run orchestration service.

This module owns parent/child agent-thread relationships. It decides whether a
task starts a new child thread or continues an existing one, records the
``SubagentThread`` relation and builds the subagent-only runtime payload.

It deliberately delegates durable run mechanics to ``agent_run_service``:
request id idempotency, active-run conflict checks, input message persistence,
AgentRun row creation and queue enqueueing all stay in the shared AgentRun
lifecycle boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yuxi.services.agent_run_service as agent_run_service
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.tool_approval import DEFAULT_TOOL_APPROVAL_MODE
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.repositories.subagent_thread_repository import SubagentThreadRepository
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.storage.postgres.models_business import Agent, AgentRun, SubagentThread
from yuxi.utils.datetime_utils import format_utc_datetime
from yuxi.utils.hash_utils import hash_id, subagent_child_thread_id


@dataclass(frozen=True)
class SubagentStartResult:
    run: AgentRun
    created: bool
    continuing: bool
    relation: SubagentThread


@dataclass(frozen=True)
class SubagentRunBusy(Exception):
    thread_id: str
    active_run_id: str | None
    active_run_status: str | None
    message: str | None

    def to_payload(self) -> dict:
        return {
            "status": "busy",
            "thread_id": self.thread_id,
            "active_run_id": self.active_run_id,
            "active_run_status": self.active_run_status,
            "message": self.message,
        }


def subagent_run_urls(run_id: str) -> dict[str, str]:
    """生成子智能体 run 对外暴露的事件流和结果查询 URL。"""
    return {
        "events_url": f"/api/agent/runs/{run_id}/events",
        "result_url": f"/api/agent/runs/{run_id}/result",
    }


def serialize_subagent_run_state(run: AgentRun) -> dict:
    """序列化给父智能体状态使用的子智能体 run 摘要。

    任务描述不在此冗余存储：其唯一来源是父对话里 `task` 工具调用的入参，
    前端面板按 tool_call_id 回填展示。
    """
    payload = run.input_payload
    if not isinstance(payload, dict):
        raise ValueError("subagent run 缺少 input_payload")
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("subagent run 缺少 runtime")
    tool_call_id = str(runtime.get("tool_call_id") or "").strip()
    if not tool_call_id:
        raise ValueError("subagent run 缺少 tool_call_id")

    state = {
        "id": tool_call_id,
        "run_id": run.id,
        "subagent_slug": run.agent_slug,
        "subagent_name": runtime.get("subagent_name"),
        "child_thread_id": run.conversation_thread_id,
        "status": run.status,
        "created_at": format_utc_datetime(run.created_at),
        "completed_at": format_utc_datetime(run.finished_at),
        "error": run.error_message,
        **subagent_run_urls(run.id),
    }
    return {key: value for key, value in state.items() if value is not None}


class SubagentRunService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.run_repo = AgentRunRepository(db)
        self.conv_repo = ConversationRepository(db)
        self.project_repo = ProjectRepository(db)
        self.thread_repo = SubagentThreadRepository(db)

    async def start(
        self,
        *,
        uid: str,
        created_by_run_id: str,
        agent_item: Agent,
        input_message: AgentRunInputMessage,
        tool_call_id: str,
        requested_thread_id: str | None = None,
        model_spec: str | None = None,
    ) -> SubagentStartResult:
        """启动或继续一个后台子智能体 run，并在新建时入队 worker。"""

        creator_run = await self.run_repo.lock_run_for_user(created_by_run_id, uid)
        if not creator_run:
            raise ValueError("父运行任务不存在")
        if getattr(creator_run, "status", "running") != "running":
            raise ValueError("父运行已结束，不能再创建子智能体")
        if getattr(creator_run, "run_type", None) == "subagent":
            raise ValueError("子智能体不能创建子智能体")

        child_thread_id = str(requested_thread_id or "").strip()
        continuing = bool(child_thread_id)  # 表示是继续运行已有子智能体线程，而非新建子线程
        if not child_thread_id:
            child_thread_id = subagent_child_thread_id(
                creator_run.conversation_thread_id,
                agent_item.slug,
                tool_call_id,
            )

        # 1. 确保子线程有对应 conversation，必要时创建 subagent 对话
        # 2. 确保父子线程关系存在，必要时创建 SubagentThread 记录；relation 是后台子 run 的线程归属来源
        relation = await self._ensure_thread_relation(
            child_thread_id=child_thread_id,
            uid=uid,
            agent_item=agent_item,
            creator_run=creator_run,
            continuing=continuing,
        )

        # 创建数据库记录
        request_id = hash_id("req:", f"{creator_run.id}:{child_thread_id}:{tool_call_id}")
        try:
            run, created = await self._create_run_record(
                input_message=input_message,
                request_id=request_id,
                current_uid=uid,
                model_spec=model_spec,
                creator_run=creator_run,
                relation=relation,
                tool_call_id=tool_call_id,
            )
        except HTTPException as exc:
            detail = exc.detail
            if exc.status_code == 409 and isinstance(detail, dict) and detail.get("code") == "run_busy":
                raise SubagentRunBusy(
                    thread_id=str(detail.get("thread_id") or child_thread_id),
                    active_run_id=detail.get("active_run_id"),
                    active_run_status=detail.get("active_run_status"),
                    message=detail.get("message"),
                ) from exc
            raise ValueError(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)) from exc

        # 创建成功后入队 worker 执行；幂等命中已有 run 时不重复入队。
        if created:
            await self.db.commit()
            await agent_run_service.enqueue_agent_run(run.id)
            # 创建即推送：父面板立刻出现该子智能体条目，不等 worker 领取(running)后的推送。
            try:
                from yuxi.services.run_queue_service import append_run_stream_event

                # 事件必须挂到父 Run 的线程上：订阅方按父线程归属消费该增量；
                # run.conversation_thread_id 是子会话线程 ID，不是父线程锚点。
                await append_run_stream_event(
                    created_by_run_id,
                    "subagent_run_update",
                    {"subagent_run": serialize_subagent_run_state(run)},
                    thread_id=creator_run.conversation_thread_id,
                )
            except Exception:
                pass  # 推送失败不影响 run 创建；后续 mark_run_running 仍会推送

        return SubagentStartResult(
            run=run,
            created=created,
            continuing=continuing,
            relation=relation,
        )

    async def get_run_for_creator(self, *, uid: str, created_by_run_id: str, run_id: str) -> AgentRun:
        """在父 run 作用域内读取子智能体 run，防止工具访问其它对话的子任务。"""
        execution_pair = await self.run_repo.get_subagent_run_with_creator(
            uid=uid,
            created_by_run_id=created_by_run_id,
            run_id=run_id,
        )
        if not execution_pair:
            raise ValueError("子智能体运行不存在或不属于当前父运行")
        _creator_run, run = execution_pair
        return run

    async def _create_run_record(
        self,
        *,
        input_message: AgentRunInputMessage,
        request_id: str,
        current_uid: str,
        model_spec: str | None,
        creator_run: AgentRun,
        relation: SubagentThread,
        tool_call_id: str,
    ) -> tuple[Any, bool]:
        """创建后台子智能体 run，并把规范化输入消息保存为该 run 的输入。"""
        if not input_message.content:
            raise HTTPException(status_code=422, detail="input_message 不能为空")

        scope = await agent_run_service.prepare_agent_run_creation_scope(
            agent_slug=relation.subagent_slug,
            conversation_thread_id=relation.child_thread_id,
            request_id=request_id,
            current_uid=current_uid,
            db=self.db,
            run_type="subagent",
            agent_kind="subagent",
            created_by_run_id=creator_run.id,
            subagent_thread_relation_id=relation.id,
        )
        if relation.child_conversation_id != scope.conversation.id:
            raise HTTPException(status_code=409, detail="subagent thread relation 与本次运行不匹配")
        if scope.existing_run:
            return scope.existing_run, False

        if creator_run.conversation_id != relation.parent_conversation_id:
            raise HTTPException(status_code=409, detail="subagent thread relation 与本次运行不匹配")

        context = agent_run_service.load_agent_run_context(scope.agent_item, scope.agent_backend)
        resolved_model_spec = await agent_run_service.resolve_agent_run_model_spec(
            model_spec,
            getattr(context, "model", None),
            self.db,
        )
        runtime_payload = {
            "tool_call_id": tool_call_id,
            "subagent_name": scope.agent_item.name,
            "parent_thread_id": creator_run.conversation_thread_id,
        }
        input_payload = {
            "model_spec": resolved_model_spec,
            "tool_approval_mode": creator_run.input_payload.get("tool_approval_mode", DEFAULT_TOOL_APPROVAL_MODE),
            "runtime": {key: value for key, value in runtime_payload.items() if value is not None},
        }
        subagent_input_message = input_message.with_metadata(
            {
                "request_id": request_id,
                "source": "subagent",
                "raw_message": input_message.raw_message(),
            }
        )
        persisted_input_message = await agent_run_service.create_agent_run_input_message(
            db=self.db,
            conversation_id=scope.conversation.id,
            request_id=request_id,
            input_message=subagent_input_message,
        )
        return await agent_run_service.persist_agent_run_record(
            agent_slug=relation.subagent_slug,
            conversation_thread_id=relation.child_thread_id,
            runtime_scope_id=getattr(creator_run, "runtime_scope_id", None) or creator_run.conversation_thread_id,
            current_uid=current_uid,
            db=self.db,
            request_id=request_id,
            conversation_id=scope.conversation.id,
            run_type="subagent",
            input_payload=input_payload,
            persisted_input_message=persisted_input_message,
            created_by_run_id=creator_run.id,
            subagent_thread_relation_id=relation.id,
            source="subagent",
            channel="internal",
        )

    async def _ensure_child_conversation(
        self,
        *,
        child_thread_id: str,
        uid: str,
        agent_item: Agent,
        creator_run: AgentRun,
        parent_project_id: str,
    ):
        """确保子线程有对应 conversation；新线程会创建标记为 subagent 的对话。"""
        conversation = await self.conv_repo.get_conversation_by_thread_id(child_thread_id)
        if conversation:
            if conversation.uid != str(uid) or conversation.status == "deleted":
                raise ValueError("子智能体线程不存在")
            if conversation.status != "subagent":
                raise ValueError(f"子智能体线程 {child_thread_id} 已被普通对话占用")
            if conversation.agent_id != agent_item.slug:
                raise ValueError(f"子智能体线程 {child_thread_id} 属于智能体 {conversation.agent_id}")
            if conversation.project_id != parent_project_id:
                raise ValueError("子智能体线程与父对话的 Workdir 不一致")
            return conversation

        conversation = await self.conv_repo.add_conversation(
            uid=uid,
            agent_id=agent_item.slug,
            title=f"SubAgent: {agent_item.name}",
            thread_id=child_thread_id,
            metadata={
                "source": "subagent",
                "parent_thread_id": creator_run.conversation_thread_id,
                "created_by_run_id": creator_run.id,
                "parent_conversation_id": creator_run.conversation_id,
                "subagent_slug": agent_item.slug,
            },
            project_id=parent_project_id,
        )
        conversation.status = "subagent"
        await self.db.flush()
        return conversation

    def _validate_thread_relation(
        self,
        relation: SubagentThread,
        *,
        child_thread_id: str,
        agent_item: Agent,
        creator_run: AgentRun,
    ) -> None:
        """校验已有子线程关系仍属于当前父对话和子智能体。"""
        if relation.parent_conversation_id != creator_run.conversation_id:
            raise ValueError(f"子智能体线程 {child_thread_id}：线程不属于当前对话")
        if relation.subagent_slug != agent_item.slug:
            raise ValueError(f"子智能体线程 {child_thread_id} 属于子智能体 {relation.subagent_slug or '未知'}")

    async def _ensure_thread_relation(
        self,
        *,
        child_thread_id: str,
        uid: str,
        agent_item: Agent,
        creator_run: AgentRun,
        continuing: bool,
    ) -> SubagentThread:
        """读取或创建父子线程关系；relation 是后台子 run 的线程归属来源。"""
        if creator_run.conversation_id is None:
            raise ValueError("父运行任务缺少 conversation_id，无法创建子智能体线程关系")
        parent_conversation = await self.conv_repo.get_conversation_by_id(creator_run.conversation_id)
        if parent_conversation is None or parent_conversation.uid != str(uid):
            raise ValueError("父运行任务的 Conversation 不存在")
        parent_project = await self.project_repo.lock_active_for_user(
            parent_conversation.project_id,
            str(uid),
        )
        if parent_project is None:
            raise ValueError("父运行任务的 Project 不存在")
        parent_conversation = await self.conv_repo.lock_conversation_by_thread_id(creator_run.conversation_thread_id)
        if (
            parent_conversation is None
            or parent_conversation.id != creator_run.conversation_id
            or parent_conversation.uid != str(uid)
            or parent_conversation.status == "deleted"
            or parent_conversation.project_id != parent_project.id
        ):
            raise ValueError("父运行任务的 Conversation 不存在")
        parent_project_id = parent_project.id

        existing = await self.thread_repo.get_by_child_thread_for_user(child_thread_id, uid)
        if existing:
            self._validate_thread_relation(
                existing,
                child_thread_id=child_thread_id,
                agent_item=agent_item,
                creator_run=creator_run,
            )
            child_conversation = await self.conv_repo.get_conversation_by_id(existing.child_conversation_id)
            if (
                child_conversation is None
                or child_conversation.uid != str(uid)
                or child_conversation.status == "deleted"
            ):
                raise ValueError("子智能体线程不存在")
            if child_conversation.project_id != parent_project_id:
                raise ValueError("子智能体线程与父对话的 Workdir 不一致")
            return existing
        if continuing:
            raise ValueError(f"无法继续子智能体线程 {child_thread_id}：当前对话中没有找到对应的运行记录")
        child_conversation = await self._ensure_child_conversation(
            child_thread_id=child_thread_id,
            uid=uid,
            agent_item=agent_item,
            creator_run=creator_run,
            parent_project_id=parent_project_id,
        )
        return await self.thread_repo.create(
            uid=uid,
            parent_conversation_id=creator_run.conversation_id,
            child_conversation_id=child_conversation.id,
            child_thread_id=child_thread_id,
            subagent_slug=agent_item.slug,
            created_by_run_id=creator_run.id,
        )
