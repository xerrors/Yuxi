"""统一会话协作的派发、消息、结果与持久等待。"""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from datetime import timedelta

from sqlalchemy import select

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.input_messages import build_chat_input_message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.receipts import control_intent_hash, require_idempotency_key, require_receipt_replay
from yuxi.modules.agents.repositories.cooperation import CooperationRepository
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
from yuxi.modules.agents.runtime_locks import cooperation_lock_key
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.directory import list_public_agents
from yuxi.modules.agents.services.input_config import resolve_agent_context_snapshot
from yuxi.modules.agents.services.inputs import accept_locked
from yuxi.modules.agents.services.queue_control import pause_pending_queue
from yuxi.modules.agents.services.run_creation import create_resume_run
from yuxi.modules.agents.services.run_lifecycle import settle_waiting_cancel
from yuxi.modules.agents.services.scheduler import Dispatch, deliver, dispatch_next_input
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.agents.services.turns import cancel_turn
from yuxi.modules.identity.models import User
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.services.bindings import resolve_session_workdir_binding
from yuxi.modules.workspace.services.projects import create_implicit_project
from yuxi.shared.datetime import format_utc_datetime, utc_now
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError


class SessionCooperationService:
    """工具调用复用会话输入主链路，授权以持久 Run 为准。"""

    def __init__(self, db, *, run_id: str, uid: str, worker_id: str, config_snapshot: dict | None = None):
        """绑定工具调用的来源身份。"""
        self.db = db
        self.run_id = run_id
        self.uid = uid
        self.worker_id = worker_id
        self.config_snapshot = config_snapshot
        self.repo = CooperationRepository(db)

    async def create_task(self, *, name: str, description: str, call_id: str, agent_id: str | None = None) -> dict:
        """委派独立任务：默认普通 Agent，创建自己的 Project，不继承调用方配置。"""
        if agent_id is None:
            agent, _ = await AgentRepository(self.db).ensure_default_agent()
            agent_id = agent.slug
        return await self.create_session(
            name=name,
            description=description,
            call_id=call_id,
            agent_id=agent_id,
            mode="root",
            new_project=True,
        )

    async def create_session(
        self,
        *,
        name: str,
        description: str,
        call_id: str,
        agent_id: str | None = None,
        mode: str = "child",
        new_project: bool = False,
    ) -> dict:
        """创建协作成员或独立会话，统一拥有授权、锁、幂等和输入派发。"""
        if mode not in {"child", "root"}:
            raise ValueError("创建模式必须是 child 或 root")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not description.strip():
            raise ValueError("name 只接受 1-64 位字母、数字、下划线或连字符，不接受路径；任务描述不能为空")
        run, caller = await self.caller()
        # 同一 Turn 可经多个恢复 Run 重放工具，副作用身份不能随执行段改变。
        child_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"yuxi-session:{run.turn_id}:{call_id}"))
        path = f"{caller.cooperation_path}/{name}" if mode == "child" else "/root"
        if len(path) > 1024:
            raise ValueError("Session 路径超过 1024 字符")
        # 共同锁序沿用 Agent → Project → Session；显式选择仍按持久执行用户授权。
        selected_agent_id = run.agent_slug if agent_id is None else agent_id
        user = await self.db.scalar(select(User).where(User.uid == run.uid, User.is_deleted == 0))
        if user is None:
            raise ValueError("执行账号已失效")
        agent = await AgentRepository(self.db).get_visible_by_slug(slug=selected_agent_id, user=user, for_key_share=True)
        if agent is None:
            raise ValueError("Agent 不存在或无权限运行")
        project = await ProjectRepository(self.db).lock_active_for_user(caller.project_id, self.uid)
        if project is None:
            raise ValueError("Project 已不可访问")
        caller = await SessionRepository(self.db).lock_session_by_thread_id(caller.thread_id)
        await self.caller()
        child = await self.db.scalar(select(Session).where(Session.thread_id == child_id))
        if child is None:
            maximum = (await system_options.get(self.db))["max_cooperation_depth"]
            depth = path.count("/") - 1
            if depth > maximum:
                raise InvalidOperationError(
                    f"协作深度 {depth} 超过最大协作深度 {maximum}，无法创建新的协作会话", code="cooperation_depth_exceeded"
                )
            if mode == "child" and await self.repo.resolve(caller, path) is not None:
                raise ValueError("同父 Session 下名称已存在")
            run_snapshot = run.input_payload["context_snapshot"]
            if agent_id is None:
                snapshot = self.config_snapshot if self.config_snapshot is not None else run_snapshot
            else:
                snapshot = await resolve_agent_context_snapshot(
                    None,
                    run_snapshot["tool_approval_mode"],
                    agent,
                    get_agent_backend(agent.backend_id),
                    self.db,
                )
            if new_project:
                project = await create_implicit_project(uid=run.uid, db=self.db)
            child = Session(
                thread_id=child_id,
                tree_root_thread_id=caller.tree_root_thread_id if mode == "child" else child_id,
                parent_thread_id=caller.thread_id if mode == "child" else None,
                cooperation_name=name,
                cooperation_path=path,
                created_by_run_id=run.id,
                agent_id=selected_agent_id,
                uid=run.uid,
                app_id=run.app_id,
                project_id=project.id,
                title=name,
                status="active",
                config_snapshot=copy.deepcopy(snapshot),
                extra_metadata={},
            )
            self.db.add(child)
            await self.db.flush()
        elif (
            child.cooperation_path != path
            or child.cooperation_name != name
            or child.parent_thread_id != (caller.thread_id if mode == "child" else None)
            or child.agent_id != selected_agent_id
        ):
            raise ValueError("幂等创建请求的名称、模式或 Agent 已变化")
        payload = await self._accept(target=child, description=description, call_id=call_id)
        return {**session_identity(child), "project_id": child.project_id, **payload}

    async def submit_input(self, *, target: str, description: str, call_id: str) -> dict:
        """提交授权会话的新工作，审批等待拒绝，普通忙碌状态持久排队。"""
        if not description.strip():
            raise ValueError("工作输入不能为空")
        _, caller = await self.caller()
        member = await self.require_target(caller, target)
        agent = await AgentRepository(self.db).get_by_slug(member.agent_id, for_key_share=True)
        if agent is None:
            raise ValueError("Agent 已删除")
        return await self._accept(target=member, description=description, call_id=call_id)

    async def send_message(self, *, target: str, content: str, call_id: str) -> dict:
        """持久投递信息，不创建工作或自动唤醒空闲会话。"""
        if not content.strip() or len(content) > 100000:
            raise ValueError("协作消息不能为空且不能超过 100000 字符")
        _, caller = await self.caller()
        member = await self.require_target(caller, target)
        run = await self._lock_source_run()
        event = await self.repo.append(
            sender=caller,
            recipient=member,
            key=f"message:{run.turn_id}:{call_id}",
            kind="message",
            content=content,
            run_id=run.id,
        )
        return {"status": "received", "message_id": event.id, "session_id": member.thread_id}

    async def list_sessions(self) -> dict:
        """返回当前用户和 APP 的全部会话及进度。"""
        _, caller = await self.caller()
        return await tree_snapshot(self.db, caller, all_sessions=True)

    async def list_agents(self) -> dict:
        """复用公开目录的可调用范围，只输出最小角色信息。"""
        run, _ = await self.caller()
        user = await self.db.scalar(select(User).where(User.uid == run.uid, User.is_deleted == 0))
        if user is None:
            raise ValueError("执行账号已失效")
        agents = await list_public_agents(user=user, db=self.db)
        return {"agents": [{"id": agent.slug, "name": agent.name, "description": agent.description} for agent in agents]}

    async def get_result(self, *, input_id: str | None = None, turn_id: str | None = None) -> dict:
        """读取精确提交或轮次的结果，不随下一轮工作改变。"""
        if bool(input_id) == bool(turn_id):
            raise ValueError("必须且只能提供 input_id 或 turn_id")
        _, caller = await self.caller()
        results = await read_task_results(self.db, caller, input_ids=[input_id] if input_id else None, turn_id=turn_id)
        return results[0]

    async def wait_inputs(self, *, input_ids: list[str], timeout_seconds: int) -> dict:
        """等待一组已提交输入全部结束，排队时也有稳定等待身份。"""
        if not input_ids or len(input_ids) > 100 or not 1 <= timeout_seconds <= 1800:
            raise ValueError("等待需要 1-100 个 input_id，时间必须为 1-1800 秒")
        _, caller = await self.caller()
        results = await read_task_results(self.db, caller, input_ids=input_ids)
        if any(result["session_id"] == caller.thread_id for result in results):
            raise ValueError("不能等待当前 Session 自身的输入")
        if all(result["terminal"] for result in results):
            return {"results": results, "wait_timed_out": False}
        return {
            "kind": "cooperation",
            "target_inputs": list(dict.fromkeys(input_ids)),
            "deadline": format_utc_datetime(utc_now() + timedelta(seconds=timeout_seconds)),
        }

    async def cancel_turn(self, *, target: str, turn_id: str, call_id: str) -> dict:
        """取消精确目标 Turn，控制权限覆盖当前用户和 APP 的会话。"""
        _, caller = await self.caller()
        member = await self.require_target(caller, target)

        member = await SessionRepository(self.db).lock_session_by_thread_id(member.thread_id)
        target_turn = await AgentTurnRepository(self.db).get_for_scope(
            turn_id=turn_id,
            thread_id=member.thread_id,
            uid=member.uid,
            app_id=member.app_id,
            for_update=True,
        )
        if target_turn is None:
            raise ResourceNotFoundError("Turn 不存在")
        # 双向取消按相同 Run 顺序领取，避免各持来源 Run 后等待对方。
        run = await self._lock_source_run(target_turn.current_run_id)
        key = f"cooperation:{run.turn_id}:{call_id}"
        await self.repo.lock_tool_receipt(run, member, key)
        return await cancel_turn(
            db=self.db,
            scope=ActorScope(uid=run.uid, app_id=run.app_id),
            thread_id=member.thread_id,
            turn_id=turn_id,
            idempotency_key=key,
            notify_parent=False,
        )

    async def wait_updates(self, *, targets: list[str], after_cursor: int, timeout_seconds: int) -> dict:
        """校验等待目标并返回已到达的更新或持久等待描述。"""
        if after_cursor < 0 or not 1 <= timeout_seconds <= 1800:
            raise ValueError("cursor 不能为负数，等待时间必须为 1-1800 秒")
        _, caller = await self.caller()
        if not targets:
            raise ValueError("等待目标不能为空")
        members = [await self.require_target(caller, target) for target in targets]
        if any(member.thread_id == caller.thread_id for member in members):
            raise ValueError("不能等待当前 Session 自身的状态更新")
        ids = [member.thread_id for member in members]
        result = await read_updates(self.db, caller, targets=ids, after=after_cursor)
        if result["updates"]:
            return result
        return {
            "kind": "cooperation",
            "target_sessions": ids,
            "after_cursor": after_cursor,
            "deadline": format_utc_datetime(utc_now() + timedelta(seconds=timeout_seconds)),
        }

    async def caller(self) -> tuple[AgentRun, Session]:
        """按来源 Run 校验执行身份，不能用模型传入的 UID 扩大授权。"""
        run = await AgentRunRepository(self.db).get_run_for_user(self.run_id, self.uid)
        if run is not None:
            await self.db.refresh(run)
        if (
            run is None
            or run.status != "running"
            or run.worker_id != self.worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= utc_now()
        ):
            raise ValueError("协作操作需要当前 Worker 持有有效租约的运行中 Run")
        caller = await self.db.get(Session, run.session_record_id)
        turn = await self.db.get(AgentTurn, run.turn_id)
        if (
            caller is None
            or caller.status != "active"
            or caller.uid != run.uid
            or caller.app_id != run.app_id
            or caller.thread_id != run.thread_id
            or caller.tree_root_thread_id != run.runtime_scope_id
            or turn is None
            or turn.current_run_id != run.id
            or turn.status != "running"
        ):
            raise ValueError("协作调用者的执行归属已失效")
        from yuxi.modules.agents.models.cooperation import CooperationRuntime

        tree_runtime = await self.db.get(CooperationRuntime, caller.tree_root_thread_id, populate_existing=True)
        if tree_runtime is not None and tree_runtime.stopped:
            # 停止意图先于逐成员取消提交；抢先进入模型边界的成员也按取消收敛。
            await cancel_turn(
                db=self.db,
                scope=ActorScope(uid=run.uid, app_id=run.app_id),
                thread_id=caller.thread_id,
                turn_id=turn.id,
                idempotency_key=f"tree-stop:{turn.id}",
                notify_parent=False,
            )
            raise asyncio.CancelledError("协作树已停止")
        return run, caller

    async def require_target(self, caller: Session, target: str) -> Session:
        """在产生副作用前收敛用户与 APP 可见范围。"""
        member = await self.repo.resolve(caller, target)
        if member is None:
            raise ValueError("目标 Session 不存在或不属于当前用户与 APP 授权范围")
        return member

    async def _accept(self, *, target: Session, description: str, call_id: str) -> dict:
        """复用 Input/Receipt 接收，不为排队输入提前创建 Turn。"""
        target = await SessionRepository(self.db).lock_session_by_thread_id(target.thread_id)

        # 接收与整树停止在同一 Session 行锁上串行化；入口检查后才提交的停止意图在此拒绝迟到的提交。
        from yuxi.modules.agents.models.cooperation import CooperationRuntime

        runtime = await self.db.get(CooperationRuntime, target.tree_root_thread_id, populate_existing=True)
        if runtime is not None and runtime.stopped:
            raise StateConflictError("协作树已停止，不能提交新工作")

        run = await self._lock_source_run()

        key = f"cooperation:{run.turn_id}:{call_id}"
        intent = str(uuid.uuid5(uuid.NAMESPACE_URL, description))
        existing = await self.repo.lock_tool_receipt(run, target, key)
        if existing is not None:
            require_receipt_replay(existing, "agent.session.input.message", intent)
            return await self._input_result(target, existing.input_id)
        frozen = {"runtime": {"tool_call_id": call_id}}
        receipt = await accept_locked(
            db=self.db,
            scope=ActorScope(uid=run.uid, app_id=run.app_id, api_key_id=run.api_key_id),
            agent_session=target,
            idempotency_key=key,
            event_type="agent.session.input.message",
            intent_hash=intent,
            mode="follow_up",
            messages=[build_chat_input_message(description)],
            model_spec=None,
            tool_approval_mode=None,
            source="cooperation",
            channel="internal",
            external_id=None,
            origin_metadata={"created_by_run_id": run.id},
            frozen_payload=frozen,
        )
        await self.db.commit()
        await dispatch_next_input(uid=run.uid, agent_slug=target.agent_id, thread_id=target.thread_id)
        return await self._input_result(target, receipt.input_id)

    async def _lock_source_run(self, target_run_id: str | None = None) -> AgentRun:
        """按稳定顺序锁定工具涉及的 Run，将来源身份保护到副作用事务提交。"""
        repo = AgentRunRepository(self.db)
        source = None
        for run_id in sorted({self.run_id, target_run_id} - {None}):
            run = await repo.lock_run_for_user(run_id, self.uid)
            if run_id == self.run_id:
                source = run
        if (
            source is None
            or source.status != "running"
            or source.worker_id != self.worker_id
            or source.lease_expires_at is None
            or source.lease_expires_at <= utc_now()
        ):
            raise StateConflictError("协作来源 Run 已失效，不能执行工具副作用")
        return source

    async def _input_result(self, target: Session, input_id: str) -> dict:
        """从持久 Input 读取派发归属，回执仅提供稳定目标。"""
        item = await AgentInputRepository(self.db).get_for_scope(
            input_id=input_id, thread_id=target.thread_id, uid=target.uid, app_id=target.app_id
        )
        if item is None:
            raise ValueError("协作回执缺少目标 Input")
        return {
            "session_id": target.thread_id,
            "input_id": item.id,
            "turn_id": item.turn_id,
            "run_id": item.consumed_run_id,
            "status": "started" if item.turn_id else "queued",
        }


def session_identity(member: Session) -> dict:
    """公开身份使用稳定 ID，名称路径仅用于可读寻址。"""
    return {
        "session_id": member.thread_id,
        "thread_id": member.thread_id,
        "name": member.cooperation_name,
        "path": member.cooperation_path,
        "parent_session_id": member.parent_thread_id,
        "tree_root_session_id": member.tree_root_thread_id,
    }


async def tree_snapshot(db, caller: Session, *, all_sessions: bool = False, created_by_thread_id: str | None = None) -> dict:
    """返回完整协作摘要；结果正文由精确任务查询拥有。"""
    rows = await CooperationRepository(db).summary(caller, all_sessions=all_sessions, created_by_thread_id=created_by_thread_id)
    return {
        "tree_root_session_id": caller.tree_root_thread_id,
        "sessions": [
            {
                **session_identity(member),
                **({"project_id": member.project_id} if all_sessions else {}),
                "title": member.title,
                "status": member.status,
                "queue_paused": member.queue_paused,
                "has_pending_input": pending,
                "turn_id": turn.id if turn else None,
                "turn_status": turn.status if turn else None,
                "run_status": run_status,
                "waiting_for": (turn.waitpoint or {}).get("kind") if turn else None,
                "current_run_id": turn.current_run_id if turn else None,
                "result_run_id": turn.result_run_id if turn else None,
            }
            for member, turn, run_status, pending in rows
        ],
    }


async def get_cooperation_summary(*, db, scope: ActorScope, thread_id: str) -> dict:
    """HTTP 观察只读取授权范围内的持久摘要。"""

    caller = await require_thread(db=db, scope=scope, thread_id=thread_id)
    return await tree_snapshot(db, caller)


async def read_task_results(db, caller: Session, *, input_ids: list[str] | None = None, turn_id: str | None = None) -> list[dict]:
    """从 Input 到精确 Turn/Run 回读任务结果及可观察终态。"""
    rows = await CooperationRepository(db).task_rows(caller, input_ids=input_ids, turn_id=turn_id)
    if not rows or (input_ids is not None and {row[1].id for row in rows} != set(input_ids)):
        raise ValueError("任务不存在或不属于当前用户与 APP 授权范围")
    results = []
    for member, input_item, turn, run, message in rows:
        if message and (message.turn_id != turn.id or message.run_id != run.id or message.session_record_id != member.id):
            raise ValueError("协作结果消息的 Turn/Run 归属不一致")
        status = turn.status if turn else input_item.status
        results.append(
            {
                "session_id": member.thread_id,
                "input_id": input_item.id if input_item else None,
                "input_status": input_item.status if input_item else None,
                "turn_id": turn.id if turn else None,
                "status": status,
                "terminal": status in {"completed", "failed", "cancelled"},
                "current_run_id": turn.current_run_id if turn else None,
                "result_run_id": turn.result_run_id if turn else None,
                "output": message.content if message else None,
                "error": {"type": run.error_type, "message": run.error_message} if run and run.error_type else None,
            }
        )
    if input_ids is not None:
        by_input = {result["input_id"]: result for result in results}
        return [by_input[input_id] for input_id in dict.fromkeys(input_ids)]
    return results


async def read_updates(db, caller: Session, *, targets: list[str], after: int) -> dict:
    """等待可观察目标自身状态及发送给调用者的消息。"""
    events = await CooperationRepository(db).updates(caller, targets=targets, after=after)
    return {
        "updates": [
            {
                "event_id": event.id,
                "sender_session_id": event.sender_thread_id,
                "kind": event.kind,
                "content": event.content,
                "turn_id": event.turn_id,
                "run_id": event.source_run_id,
                **event.payload,
            }
            for event in events
        ],
        "cursor": events[-1].id if events else after,
        "wait_timed_out": False,
    }


async def recover_cooperation_waits() -> None:
    """分页恢复等待；单项失败不阻止后续记录，失败仍使本轮健康失效。"""
    after = ""
    failures = 0
    while True:
        async with pg_manager.get_async_session_context() as db:
            ids = list(
                (
                    await db.scalars(
                        select(AgentTurn.id)
                        .where(
                            AgentTurn.status == "waiting",
                            AgentTurn.waitpoint["kind"].as_string() == "cooperation",
                            AgentTurn.id > after,
                        )
                        .order_by(AgentTurn.id)
                        .limit(100)
                    )
                ).all()
            )
        if not ids:
            break
        for turn_id in ids:
            try:
                await _recover_cooperation_wait(turn_id)
            except Exception:
                failures += 1
                logger.exception("Cooperation recovery failed: turn_id=%s", turn_id)
        after = ids[-1]
    if failures:
        raise RuntimeError(f"协作等待恢复失败：{failures} 条")


async def _recover_cooperation_wait(turn_id: str) -> None:
    """在独立事务内领取等待点，提交后投递恢复 Run。"""
    async with pg_manager.get_async_session_context() as db:
        turn = await db.get(AgentTurn, turn_id)
        if turn is None:
            return
        member = await SessionRepository(db).lock_session_by_thread_id(turn.thread_id)
        if member is None:
            return
        turn = await AgentTurnRepository(db).get_for_scope(
            turn_id=turn_id,
            thread_id=member.thread_id,
            uid=member.uid,
            app_id=member.app_id,
            for_update=True,
        )
        if turn is None:
            return
        wait = turn.waitpoint or {}
        if turn.status != "waiting" or wait.get("kind") != "cooperation":
            return
        # 按 ID 恢复已就绪或超时项；未就绪项继续留在 checkpoint，不能推迟其他项的截止时间。
        response = {}
        for entry in wait["waits"]:
            if "target_inputs" in entry:
                value = {
                    "results": await read_task_results(db, member, input_ids=entry["target_inputs"]),
                    "wait_timed_out": False,
                }
                ready = all(item["terminal"] for item in value["results"])
            else:
                value = await read_updates(db, member, targets=entry["target_sessions"], after=entry["after_cursor"])
                ready = bool(value["updates"])
            if not ready:
                from datetime import datetime

                if utc_now() < datetime.fromisoformat(entry["deadline"]):
                    continue
                value["wait_timed_out"] = True
            response[entry["interrupt_id"]] = value
        if not response:
            return
        previous = await db.get(AgentRun, turn.current_run_id)
        binding = await resolve_session_workdir_binding(agent_session=member, uid=member.uid, db=db)
        run = await create_resume_run(
            db=db,
            agent_session=member,
            turn=turn,
            previous=previous,
            api_key_id=previous.api_key_id,
            response=response,
            metadata={"resume": response},
        )
        run_id = run.id

    await deliver(Dispatch(run_id=run_id, binding=binding))


async def control_tree(*, db, scope: ActorScope, thread_id: str, idempotency_key: str, stopped: bool) -> dict:
    """持久接受用户的整树停止或继续意图，重试不改变新一轮状态。"""

    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert

    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
    from yuxi.modules.agents.services.threads import control_accepted

    require_idempotency_key(idempotency_key)
    member = await require_thread(db=db, scope=scope, thread_id=thread_id)
    event_type = "yuxi.session.tree.stop" if stopped else "yuxi.session.tree.continue"
    intent = control_intent_hash(event_type)
    # Session → 树锁；停止意图提交时固定全树队列，避免恢复早于逐成员取消。
    members = list(
        (
            await db.scalars(
                select(Session)
                .where(
                    Session.tree_root_thread_id == member.tree_root_thread_id,
                    Session.uid == scope.uid,
                    Session.app_id == scope.app_id,
                    Session.status == "active",
                )
                .order_by(Session.thread_id)
                .with_for_update(key_share=True)
            )
        ).all()
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": cooperation_lock_key(member.tree_root_thread_id)},
    )
    repo = AgentInputReceiptRepository(db)
    receipt = await repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if receipt is not None:
        require_receipt_replay(receipt, event_type, intent)
        return control_accepted(receipt)
    if not stopped:
        await _require_tree_resumable(db=db, member=member)
    await db.execute(
        insert(CooperationRuntime)
        .values(tree_root_thread_id=member.tree_root_thread_id, stopped=stopped)
        .on_conflict_do_update(index_elements=[CooperationRuntime.tree_root_thread_id], set_={"stopped": stopped})
    )
    receipt = await repo.create(
        receipt_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=thread_id,
        event_type=event_type,
        intent_hash=intent,
    )
    for target in members:
        if stopped:
            await pause_pending_queue(db, target)
        else:
            target.queue_paused = False
    await db.commit()
    if stopped:
        await reconcile_stopped_trees(tree_root_thread_id=member.tree_root_thread_id)
    else:
        from yuxi.modules.agents.services.scheduler import dispatch_next_input

        for target in members:
            await dispatch_next_input(uid=scope.uid, agent_slug=target.agent_id, thread_id=target.thread_id)
    return control_accepted(receipt)


async def resume_tree_for_user_input(*, db, agent_session: Session) -> bool:
    """整树结束后仅恢复用户目标会话，保留其他成员的旧待办暂停。"""
    from sqlalchemy import text

    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.runtime_locks import cooperation_lock_key

    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": cooperation_lock_key(agent_session.tree_root_thread_id)},
    )
    runtime = await db.get(CooperationRuntime, agent_session.tree_root_thread_id, populate_existing=True)
    if runtime is None or not runtime.stopped:
        return False
    await _require_tree_resumable(db=db, member=agent_session)
    runtime.stopped = False
    return True


async def reconcile_stopped_trees(*, tree_root_thread_id: str | None = None) -> None:
    """补做已持久停止意图，禁止父轮次结束默认影响后代。"""
    from yuxi.modules.agents.models.cooperation import CooperationRuntime

    async with pg_manager.get_async_session_context() as db:
        members = list(
            (
                await db.scalars(
                    select(Session)
                    .join(CooperationRuntime, CooperationRuntime.tree_root_thread_id == Session.tree_root_thread_id)
                    .where(
                        CooperationRuntime.stopped.is_(True),
                        Session.status == "active",
                        *([Session.tree_root_thread_id == tree_root_thread_id] if tree_root_thread_id else []),
                    )
                )
            ).all()
        )
    failures = 0
    waiting = []
    for member in members:
        try:
            async with pg_manager.get_async_session_context() as db:
                target = await SessionRepository(db).lock_session_by_thread_id(member.thread_id)
                if target is None:
                    continue
                # continue 同样先锁所有成员；持有 Session 锁后重读，丢弃旧停止快照。
                stopped = await db.scalar(
                    select(CooperationRuntime.stopped).where(CooperationRuntime.tree_root_thread_id == target.tree_root_thread_id)
                )
                if not stopped:
                    continue
                await pause_pending_queue(db, target)
                turn = await AgentTurnRepository(db).lock_active_for_thread(
                    thread_id=target.thread_id,
                    uid=target.uid,
                    app_id=target.app_id,
                )
                if turn:
                    await cancel_turn(
                        db=db,
                        scope=ActorScope(uid=target.uid, app_id=target.app_id),
                        thread_id=target.thread_id,
                        turn_id=turn.id,
                        idempotency_key=f"tree-stop:{turn.id}",
                        notify_parent=False,
                        cleanup_waiting=False,
                    )
                    if turn.waitpoint:
                        waiting.append((target, turn.id))
        except Exception:
            failures += 1
            logger.exception("Stopped tree recovery failed: thread_id=%s", member.thread_id)
    for target, turn_id in waiting:
        try:
            await settle_waiting_cancel(thread_id=target.thread_id, turn_id=turn_id, uid=target.uid, app_id=target.app_id)
        except Exception:
            failures += 1
            logger.exception("Stopped waiting cleanup failed: turn_id=%s", turn_id)
    if failures:
        raise RuntimeError(f"停止会话恢复失败：{failures} 条")


async def _require_tree_resumable(*, db, member: Session) -> None:
    """持有 Session → 树锁时，统一检查解除整树停止意图的前置条件。"""
    if await CooperationRepository(db).has_unfinished_execution(member):
        raise StateConflictError("正在停止全部任务，请等待清理完成")
