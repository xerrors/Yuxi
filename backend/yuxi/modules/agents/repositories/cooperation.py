"""协作树、消息与执行容量的 PostgreSQL 查询。"""

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import defer

from yuxi.modules.agents.models.cooperation import CooperationEvent, CooperationRuntime
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES, AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.sessions import exclude_personal_sessions
from yuxi.modules.agents.runtime_locks import cooperation_lock_key, user_environment_lock_key
from yuxi.modules.system.options import system_options


class CooperationRepository:
    """在 owning transaction 中维护协作事实。"""

    def __init__(self, db):
        """绑定调用方事务。"""
        self.db = db

    async def resolve(self, caller: Session, target: str) -> Session | None:
        """稳定 ID 在用户与 APP 内寻址，路径只在当前树内解析。"""
        return await self.db.scalar(
            select(Session).where(
                Session.uid == caller.uid,
                Session.app_id == caller.app_id,
                Session.status == "active",
                or_(
                    Session.thread_id == target,
                    and_(Session.tree_root_thread_id == caller.tree_root_thread_id, Session.cooperation_path == target),
                ),
            )
        )

    async def updates(self, caller: Session, *, targets: list[str], after: int) -> list[CooperationEvent]:
        """复核等待目标归属后读取可见更新，归档不删除历史更新。"""
        members = list(
            await self.db.scalars(
                select(Session).where(
                    Session.thread_id.in_(targets),
                    Session.uid == caller.uid,
                    Session.app_id == caller.app_id,
                    Session.status != "deleted",
                )
            )
        )
        if {member.thread_id for member in members} != set(targets):
            raise ValueError("目标 Session 不存在或不属于当前用户与 APP 授权范围")
        query = select(CooperationEvent).where(
            CooperationEvent.id > after,
            or_(
                and_(
                    CooperationEvent.kind == "turn_status",
                    CooperationEvent.recipient_thread_id == CooperationEvent.sender_thread_id,
                ),
                and_(CooperationEvent.kind != "turn_status", CooperationEvent.recipient_thread_id == caller.thread_id),
            ),
            CooperationEvent.sender_thread_id.in_([member.thread_id for member in members]),
        )
        return list((await self.db.scalars(query.order_by(CooperationEvent.id).limit(100))).all())

    async def has_unfinished_execution(self, caller: Session) -> bool:
        """检查授权整树的执行与清理责任，待消费输入不阻止用户恢复。"""
        members = select(Session.thread_id).where(
            Session.tree_root_thread_id == caller.tree_root_thread_id,
            Session.uid == caller.uid,
            Session.app_id == caller.app_id,
        )
        active_turn = (
            select(AgentTurn.id)
            .where(AgentTurn.thread_id.in_(members), AgentTurn.status.in_(("running", "waiting", "cancelling")))
            .exists()
        )
        active_run = (
            select(AgentRun.id)
            .where(
                AgentRun.thread_id.in_(members),
                or_(AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES), AgentRun.runtime_cleanup_pending.is_(True)),
            )
            .exists()
        )
        return bool(await self.db.scalar(select(or_(active_turn, active_run))))

    async def summary(self, caller: Session, *, all_sessions: bool = False, created_by_thread_id: str | None = None) -> list:
        """批量读取整树协作状态，不加载 checkpoint 或结果正文。"""
        members = list(
            (
                await self.db.scalars(
                    select(Session)
                    .options(defer(Session.config_snapshot), defer(Session.extra_metadata))
                    .where(
                        *([] if all_sessions else [Session.tree_root_thread_id == caller.tree_root_thread_id]),
                        *([exclude_personal_sessions()] if all_sessions else []),
                        *(
                            []
                            if created_by_thread_id is None
                            else [Session.created_by_run_id.in_(select(AgentRun.id).where(AgentRun.thread_id == created_by_thread_id))]
                        ),
                        Session.uid == caller.uid,
                        Session.app_id == caller.app_id,
                        Session.status != "deleted",
                    )
                )
            ).all()
        )
        if not members:
            return []
        if all_sessions:
            trees = {}
            for member in members:
                trees.setdefault(member.tree_root_thread_id, []).append(member)
            members = [member for tree in trees.values() for member in order_tree_members(tree, allow_hidden_parents=True)]
        else:
            members = order_tree_members(members)
        ids = [member.thread_id for member in members]
        turns = {
            turn.thread_id: turn
            for turn in (
                await self.db.scalars(
                    select(AgentTurn)
                    .where(AgentTurn.thread_id.in_(ids))
                    .distinct(AgentTurn.thread_id)
                    .order_by(AgentTurn.thread_id, AgentTurn.created_at.desc(), AgentTurn.id.desc())
                )
            ).all()
        }
        run_ids = [turn.current_run_id for turn in turns.values() if turn.current_run_id]
        runs = dict((await self.db.execute(select(AgentRun.id, AgentRun.status).where(AgentRun.id.in_(run_ids)))).all()) if run_ids else {}
        pending = set(
            (
                await self.db.scalars(
                    select(AgentInput.thread_id).where(AgentInput.thread_id.in_(ids), AgentInput.status == "pending").distinct()
                )
            ).all()
        )
        rows = []
        for member in members:
            turn = turns.get(member.thread_id)
            run_status = runs.get(turn.current_run_id) if turn else None
            rows.append((member, turn, run_status, member.thread_id in pending))
        return rows

    async def task_rows(self, caller: Session, *, input_ids: list[str] | None = None, turn_id: str | None = None):
        """按已提交身份读取授权用户与 APP 的结果，归档成员仍可读取历史。"""
        query = select(Session, AgentInput, AgentTurn, AgentRun, Message).select_from(Session)
        if input_ids is not None:
            query = (
                query.join(AgentInput, AgentInput.thread_id == Session.thread_id)
                .outerjoin(AgentTurn, AgentTurn.id == AgentInput.turn_id)
                .where(AgentInput.id.in_(input_ids))
            )
        else:
            query = (
                query.join(AgentTurn, AgentTurn.thread_id == Session.thread_id)
                .outerjoin(AgentInput, (AgentInput.turn_id == AgentTurn.id) & (AgentInput.kind == "follow_up"))
                .where(AgentTurn.id == turn_id)
            )
        query = (
            query.outerjoin(AgentRun, AgentRun.id == func.coalesce(AgentTurn.result_run_id, AgentTurn.current_run_id))
            .outerjoin(Message, (Message.id == AgentRun.output_message_id) & (AgentRun.id == AgentTurn.result_run_id))
            .where(
                Session.uid == caller.uid,
                Session.app_id == caller.app_id,
                Session.status != "deleted",
            )
        )
        return list((await self.db.execute(query)).all())

    async def append(
        self,
        *,
        sender: Session,
        recipient: Session,
        key: str,
        kind: str,
        content: str = "",
        run_id: str | None = None,
        turn_id: str | None = None,
        payload: dict | None = None,
    ) -> CooperationEvent:
        """按用户与 APP 串行发布，跨树 cursor 不越过未提交事件。"""
        if (sender.uid, sender.app_id) != (
            recipient.uid,
            recipient.app_id,
        ):
            raise ValueError("协作消息必须处于同一用户与 APP 授权范围")
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"cooperation-events:{sender.uid}:{sender.app_id or ''}"},
        )
        query = select(CooperationEvent).where(
            CooperationEvent.sender_thread_id == sender.thread_id,
            CooperationEvent.idempotency_key == key,
        )
        if kind != "message":
            query = query.where(CooperationEvent.recipient_thread_id == recipient.thread_id)
        existing = await self.db.scalar(query)
        if existing is not None:
            if (
                existing.sender_thread_id,
                existing.recipient_thread_id,
                existing.kind,
                existing.content,
                existing.payload,
            ) != (
                sender.thread_id,
                recipient.thread_id,
                kind,
                content,
                payload or {},
            ):
                raise ValueError("协作幂等键已用于不同内容")
            return existing
        event = CooperationEvent(
            tree_root_thread_id=recipient.tree_root_thread_id,
            sender_thread_id=sender.thread_id,
            recipient_thread_id=recipient.thread_id,
            source_run_id=run_id,
            turn_id=turn_id,
            kind=kind,
            content=content,
            payload=payload or {},
            idempotency_key=key,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def lock_tool_receipt(self, run: AgentRun, target: Session, key: str) -> AgentInputReceipt | None:
        """按来源工具调用串行重放，不能更换目标制造第二次副作用。"""
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"cooperation-call:{key}"},
        )
        receipt = await self.db.scalar(
            select(AgentInputReceipt).where(
                AgentInputReceipt.uid == run.uid,
                AgentInputReceipt.app_id == run.app_id,
                AgentInputReceipt.idempotency_key == key,
            )
        )
        if receipt is not None and receipt.thread_id != target.thread_id:
            raise ValueError("协作幂等键的目标 Session 已变化")
        return receipt

    async def mailbox(
        self, caller: Session, *, after: int = 0, targets: list[str] | None = None, limit: int = 100
    ) -> list[CooperationEvent]:
        """读取调用者收件箱，cursor 只推进到实际返回的最后一项。"""
        query = select(CooperationEvent).where(
            CooperationEvent.tree_root_thread_id == caller.tree_root_thread_id,
            CooperationEvent.recipient_thread_id == caller.thread_id,
            CooperationEvent.id > after,
        )
        if targets:
            query = query.where(CooperationEvent.sender_thread_id.in_(targets))
        return list((await self.db.scalars(query.order_by(CooperationEvent.id).limit(limit))).all())

    async def claim_execution_slot(self, run: AgentRun) -> bool:
        """在树级锁内检查真实执行租约；取消收尾同样占容量。"""
        # 回收仅持有专用生命周期锁；执行暂缓领取，不堵塞消息树锁。
        if not await self.db.scalar(
            text("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(:key, 0))"),
            {"key": user_environment_lock_key(run.uid)},
        ):
            return False
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": cooperation_lock_key(run.runtime_scope_id)},
        )
        runtime = await self.db.get(CooperationRuntime, run.runtime_scope_id)
        if runtime is not None and runtime.stopped:
            return False
        active = await self.db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.runtime_scope_id == run.runtime_scope_id,
                AgentRun.uid == run.uid,
                AgentRun.app_id == run.app_id,
                AgentRun.status.in_(("running", "cancel_requested")),
                AgentRun.worker_id.is_not(None),
            )
        )
        if active >= (await system_options.get(self.db))["max_cooperation_concurrency"]:
            return False
        from sqlalchemy.dialects.postgresql import insert

        await self.db.execute(
            insert(CooperationRuntime)
            .values(tree_root_thread_id=run.runtime_scope_id, stopped=False)
            .on_conflict_do_nothing(index_elements=[CooperationRuntime.tree_root_thread_id])
        )
        return True


def order_tree_members(members: list[Session], *, allow_hidden_parents: bool = False) -> list[Session]:
    """先序遍历成员；列表过滤后的父节点缺席不阻止子节点展示。"""
    children: dict[str | None, list[Session]] = {}
    for member in members:
        children.setdefault(member.parent_thread_id, []).append(member)
    for siblings in children.values():
        siblings.sort(key=lambda member: member.cooperation_name)
    ordered = []
    member_ids = {member.thread_id for member in members}
    pending = [
        member
        for member in members
        if member.parent_thread_id is None or (allow_hidden_parents and member.parent_thread_id not in member_ids)
    ]
    while pending:
        member = pending.pop()
        ordered.append(member)
        pending.extend(reversed(children.get(member.thread_id, [])))
    if len(ordered) != len(members):
        raise ValueError("协作树成员无法从根会话完整遍历")
    return ordered
