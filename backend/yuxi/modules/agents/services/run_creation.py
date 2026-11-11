"""Thread 锁内创建执行段、固定当前归属和恢复消息。"""

import json
import uuid
from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.services.input_config import merge_mcp_selections


async def create_turn_run(
    *,
    db: AsyncSession,
    agent_session: Session,
    turn: AgentTurn,
    source: AgentInput | AgentRun,
    api_key_id: int | None,
    previous: AgentRun | None = None,
    input_batch: list[AgentInput] | None = None,
) -> AgentRun:
    """在调用者的 Thread 锁和作用域内继承配置，固定当前 Run。"""
    snapshot = previous if previous is not None else source
    payload = deepcopy(snapshot.input_payload or {})
    # 连续 steer 共用执行段；只合并本批输入的连接器，不读取较晚接收的会话配置。
    if input_batch:
        selections = [item.input_payload["context_snapshot"].get("mcps", []) for item in input_batch]
        if any(selections):
            context = payload["context_snapshot"]
            context["mcps"] = merge_mcp_selections(context.get("mcps", []), *selections)
    run = await AgentRunRepository(db).create_run(
        run_id=str(uuid.uuid4()),
        thread_id=agent_session.thread_id,
        runtime_scope_id=previous.runtime_scope_id if previous is not None else agent_session.tree_root_thread_id,
        agent_slug=snapshot.agent_slug,
        uid=agent_session.uid,
        turn_id=turn.id,
        app_id=agent_session.app_id,
        api_key_id=api_key_id,
        input_payload=payload,
        source=source.source,
        channel=source.channel,
        external_id=source.external_id,
        origin_metadata=source.origin_metadata or {},
        session_record_id=agent_session.id,
        resume_from_run_id=previous.id if previous is not None else None,
        run_type="resume" if isinstance(source, AgentRun) else "chat",
        created_by_run_id=previous.created_by_run_id if previous is not None else (source.origin_metadata or {}).get("created_by_run_id"),
    )
    await AgentTurnRepository(db).set_current(turn, run_id=run.id)
    return run


async def create_resume_run(
    *,
    db: AsyncSession,
    agent_session: Session,
    turn: AgentTurn,
    previous: AgentRun,
    api_key_id: int | None,
    response: dict,
    metadata: dict,
) -> AgentRun:
    """交互与协作恢复共享同一 Run、Message、Turn 绑定事务。"""
    run = await create_turn_run(db=db, agent_session=agent_session, turn=turn, source=previous, previous=previous, api_key_id=api_key_id)
    message = Message(
        session_record_id=agent_session.id,
        role="user",
        content=json.dumps(response, ensure_ascii=False),
        message_type="resume",
        extra_metadata=metadata,
        delivery_status="dispatched",
        turn_id=turn.id,
        run_id=run.id,
    )
    db.add(message)
    await db.flush()
    await AgentRunRepository(db).set_input_message(run.id, message.id)
    return run
