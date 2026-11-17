"""新 Agent 输入模型在真实 PostgreSQL 上的投递与约束证据。"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.services.agent_input_fixtures import (
    create_input_schema as _create_schema,
    drop_input_schema as _drop_schema,
)
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.input_messages import build_chat_input_message, serialize_input_message
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_follow_up_claim_fixes_order_and_turn_only_once() -> None:
    """接收不生成消息；消费原子生成有序投影，重投不会重复生成。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            item = await AgentInputRepository(db).create(
                input_id="input-one",
                thread_id="input-thread",
                uid="input-user",
                app_id=None,
                agent_slug="main",
                kind="follow_up",
                messages=[serialize_input_message(build_chat_input_message(text)) for text in ("first", "second")],
            )
            await AgentInputReceiptRepository(db).create(
                receipt_id="receipt-one",
                idempotency_key="key-one",
                uid="input-user",
                app_id=None,
                thread_id="input-thread",
                event_type="message",
                intent_hash="hash",
                input_id=item.id,
            )
            await db.commit()
        async with sessions() as db:
            assert await db.scalar(select(Message.id)) is None
            assert await db.scalar(select(AgentTurn.id)) is None
            repo = AgentInputRepository(db)
            batch = await repo.ready_batch(thread_id="input-thread", uid="input-user", app_id=None)
            assert [item.id for item in batch] == ["input-one"]
            turn = await AgentTurnRepository(db).create(turn_id="turn-one", thread_id="input-thread", uid="input-user", app_id=None)
            run = await AgentRunRepository(db).create_run(
                run_id="run-one",
                thread_id="input-thread",
                agent_slug="main",
                uid="input-user",
                turn_id=turn.id,
                input_payload={},
                session_record_id=await db.scalar(select(Session.id).where(Session.thread_id == "input-thread")),
            )
            await AgentTurnRepository(db).set_current(turn, run_id=run.id)
            projected = await repo.consume(inputs=batch, turn_id=turn.id, run_id=run.id)
            assert [message.content for message in projected] == ["first", "second"]
            await db.commit()
        async with sessions() as db:
            item = await db.get(AgentInput, "input-one")
            assert (item.status, item.turn_id, item.consumed_run_id) == ("consumed", "turn-one", "run-one")
            messages = await AgentInputRepository(db).list_messages(item.id)
            assert [(m.source_input_id, m.input_position) for m in messages] == [("input-one", 0), ("input-one", 1)]
            assert all(m.received_at == item.created_at and m.created_at >= m.received_at for m in messages)
            assert [raw["content"][0]["text"] for raw in item.messages] == ["first", "second"]
            receipt = await db.get(AgentInputReceipt, "receipt-one")
            assert receipt.turn_id is None and receipt.run_id is None
            with pytest.raises(IntegrityError, match="ck_agent_input_receipts_target"):
                async with db.begin_nested():
                    receipt.turn_id = "turn-one"
                    receipt.run_id = "run-one"
                    await db.flush()
            with pytest.raises(ValueError, match="已领取"):
                await AgentInputRepository(db).consume(inputs=[item], turn_id="turn-one", run_id="run-one")
            with pytest.raises(IntegrityError, match="uq_messages_input_position"):
                async with db.begin_nested():
                    db.add(
                        Message(
                            session_record_id=messages[0].session_record_id,
                            role="user",
                            content="duplicate",
                            source_input_id=item.id,
                            input_position=0,
                            received_at=item.created_at,
                            turn_id="turn-one",
                            run_id="run-one",
                        )
                    )
                    await db.flush()
            pending = await AgentInputRepository(db).create(
                input_id="pending-origin",
                thread_id="input-thread",
                uid="input-user",
                app_id=None,
                agent_slug="main",
                kind="follow_up",
                messages=[serialize_input_message(build_chat_input_message("later"))],
            )
            with pytest.raises(IntegrityError, match="fk_messages_consumed_input"):
                async with db.begin_nested():
                    db.add(
                        Message(
                            session_record_id=messages[0].session_record_id,
                            role="user",
                            content="wrong origin",
                            source_input_id=pending.id,
                            input_position=0,
                            received_at=pending.created_at,
                            turn_id="turn-one",
                            run_id="run-one",
                        )
                    )
                    await db.flush()
                    await db.execute(text("SET CONSTRAINTS fk_messages_consumed_input IMMEDIATE"))
    finally:
        await _drop_schema(schema, admin_engine, engine)


async def test_product_key_active_turn_unique_and_steers_independent() -> None:
    """NULL APP 幂等和活跃 Turn 唯一，引导提交保持独立身份。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            await AgentInputReceiptRepository(db).create(
                receipt_id="receipt-product",
                idempotency_key="same-key",
                uid="input-user",
                app_id=None,
                thread_id="input-thread",
                event_type="message",
                intent_hash="one",
            )
            await AgentTurnRepository(db).create(turn_id="turn-active", thread_id="input-thread", uid="input-user", app_id=None)
            await AgentInputRepository(db).create(
                input_id="steer-one",
                thread_id="input-thread",
                uid="input-user",
                app_id=None,
                agent_slug="main",
                kind="steer",
                messages=[serialize_input_message(build_chat_input_message("steer"))],
            )
            await db.commit()

        async with sessions() as db:
            with pytest.raises(IntegrityError):
                await AgentInputReceiptRepository(db).create(
                    receipt_id="receipt-product-reuse",
                    idempotency_key="same-key",
                    uid="input-user",
                    app_id=None,
                    thread_id="input-thread",
                    event_type="cancel",
                    intent_hash="different",
                )
            await db.rollback()

            with pytest.raises(IntegrityError):
                await AgentTurnRepository(db).create(turn_id="turn-overlap", thread_id="input-thread", uid="input-user", app_id=None)
            await db.rollback()

            await AgentInputRepository(db).create(
                input_id="steer-overlap",
                thread_id="input-thread",
                uid="input-user",
                app_id=None,
                agent_slug="main",
                kind="steer",
                messages=[serialize_input_message(build_chat_input_message("steer"))],
            )
            await db.commit()

            with pytest.raises(IntegrityError, match="ck_agent_inputs_delivery"):
                await db.execute(text("UPDATE agent_inputs SET turn_id = 'turn-active' WHERE id = 'steer-one'"))
            await db.rollback()

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO sessions "
                    "(thread_id, tree_root_thread_id, uid, agent_id, project_id, is_pinned, status, config_snapshot) "
                    "VALUES ('other-thread', 'other-thread', 'input-user', 'main', 'input-project', false, 'active', "
                    '\'{"model":"test:chat","tool_approval_mode":"default"}\')'
                )
            )
        async with sessions() as db:
            turn = await AgentTurnRepository(db).create(turn_id="other-turn", thread_id="other-thread", uid="input-user", app_id=None)
            await AgentRunRepository(db).create_run(
                run_id="other-run",
                thread_id="other-thread",
                agent_slug="main",
                uid="input-user",
                turn_id=turn.id,
                input_payload={},
            )
            await db.commit()

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(text("UPDATE agent_turns SET current_run_id = 'other-run' WHERE id = 'turn-active'"))
                await connection.execute(text("SET CONSTRAINTS fk_agent_turns_current_run IMMEDIATE"))
    finally:
        await _drop_schema(schema, admin_engine, engine)


async def test_run_execution_sequence_orders_segments_across_a_turn() -> None:
    """持久执行序号可在断线后按 Thread 找回后续 Run。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            session_record_id = await db.scalar(select(text("id")).select_from(text("sessions")))
            turn = await AgentTurnRepository(db).create(turn_id="cursor-turn", thread_id="input-thread", uid="input-user", app_id=None)
            runs = AgentRunRepository(db)
            first = await runs.create_run(
                run_id="cursor-first",
                thread_id="input-thread",
                session_record_id=session_record_id,
                agent_slug="main",
                uid="input-user",
                turn_id=turn.id,
                input_payload={},
            )
            first_seq = first.execution_seq
            assert first_seq is not None
            first.status = "yielded"
            await db.flush()
            second = await runs.create_run(
                run_id="cursor-second",
                thread_id="input-thread",
                session_record_id=session_record_id,
                agent_slug="main",
                uid="input-user",
                turn_id=turn.id,
                resume_from_run_id=first.id,
                input_payload={},
            )
            assert second.execution_seq is not None and second.execution_seq > first_seq
            db.add_all(
                [
                    Message(
                        session_record_id=session_record_id,
                        role="assistant",
                        content="first",
                        message_type="model_audit",
                        turn_id=turn.id,
                        run_id=first.id,
                        operation_id="same-model-call",
                        execution_status="completed",
                        usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    ),
                    Message(
                        session_record_id=session_record_id,
                        role="assistant",
                        content="",
                        message_type="model_audit",
                        turn_id=turn.id,
                        run_id=second.id,
                        operation_id="same-model-call",
                        execution_status="abandoned",
                    ),
                ]
            )
            await db.flush()
            audits = await AgentTurnRepository(db).list_model_usage_audits(turn.id)
            assert [(audit.run_id, audit.execution_status) for audit in audits] == [
                (first.id, "completed"),
                (second.id, "abandoned"),
            ]
            await db.commit()

        async with sessions() as db:
            subsequent = await AgentRunRepository(db).list_thread_runs_after_sequence(
                thread_id="input-thread", uid="input-user", app_id=None, after_sequence=first_seq
            )
            assert [run.id for run in subsequent] == ["cursor-second"]
    finally:
        await _drop_schema(schema, admin_engine, engine)


async def test_parent_and_child_usage_stays_in_its_own_turn() -> None:
    """父子 Turn 分别计数，错 Run 或错 Turn 的文本不得混入。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            parent_session = await db.scalar(select(Session).where(Session.thread_id == "input-thread"))
            turn = await AgentTurnRepository(db).create(turn_id="usage-turn", thread_id="input-thread", uid="input-user", app_id=None)
            runs = AgentRunRepository(db)
            parent_run = await runs.create_run(
                run_id="usage-parent",
                thread_id="input-thread",
                agent_slug="main",
                uid="input-user",
                turn_id=turn.id,
                session_record_id=parent_session.id,
                input_payload={},
            )
            child_session = Session(
                thread_id="usage-child-thread",
                project_id="input-project",
                uid="input-user",
                agent_id="helper",
                tree_root_thread_id=parent_session.thread_id,
                parent_thread_id=parent_session.thread_id,
                cooperation_name="helper",
                cooperation_path="/root/helper",
                status="active",
            )
            db.add(child_session)
            await db.flush()
            child_turn = await AgentTurnRepository(db).create(
                turn_id="child-usage-turn", thread_id=child_session.thread_id, uid="input-user", app_id=None
            )
            child_run = await runs.create_run(
                run_id="usage-child",
                thread_id=child_session.thread_id,
                runtime_scope_id=parent_session.thread_id,
                agent_slug="helper",
                uid="input-user",
                turn_id=child_turn.id,
                session_record_id=child_session.id,
                run_type="chat",
                created_by_run_id=parent_run.id,
                input_payload={},
            )
            parent_run.status = "completed"
            child_run.status = "completed"
            await db.flush()
            other_turn = AgentTurn(id="other-turn", thread_id="input-thread", uid="input-user", status="completed")
            db.add(other_turn)
            await db.flush()
            other_run = await runs.create_run(
                run_id="other-run",
                thread_id="input-thread",
                agent_slug="main",
                uid="input-user",
                turn_id=other_turn.id,
                session_record_id=parent_session.id,
                input_payload={},
            )
            parent_audit = Message(
                session_record_id=parent_session.id,
                run_id=parent_run.id,
                turn_id=turn.id,
                role="assistant",
                content="delegate",
                message_type="model_audit",
                operation_id="parent-model",
                execution_status="completed",
                usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            )
            child_audit = Message(
                session_record_id=child_session.id,
                run_id=child_run.id,
                turn_id=child_turn.id,
                role="assistant",
                content="tool call",
                message_type="model_audit",
                operation_id="child-tool-model",
                execution_status="completed",
                usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            )
            wrong_run_text = Message(
                session_record_id=child_session.id,
                run_id=child_run.id,
                turn_id=turn.id,
                role="assistant",
                content="wrong run pointer",
                message_type="text",
                operation_id="wrong-run-model",
                execution_status="completed",
                usage={"input_tokens": 900, "output_tokens": 900, "total_tokens": 1800},
            )
            wrong_turn_text = Message(
                session_record_id=parent_session.id,
                run_id=other_run.id,
                turn_id=turn.id,
                role="assistant",
                content="wrong turn pointer",
                message_type="text",
                operation_id="wrong-turn-model",
                execution_status="completed",
                usage={"input_tokens": 900, "output_tokens": 900, "total_tokens": 1800},
            )
            child_final = Message(
                session_record_id=child_session.id,
                run_id=child_run.id,
                turn_id=child_turn.id,
                role="assistant",
                content="child result",
                message_type="text",
                operation_id="child-final-model",
                execution_status="completed",
                usage={"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
            )
            other_final = Message(
                session_record_id=parent_session.id,
                run_id=other_run.id,
                turn_id=other_turn.id,
                role="assistant",
                content="other turn result",
                message_type="text",
                operation_id="other-final-model",
                execution_status="completed",
                usage={"input_tokens": 900, "output_tokens": 900, "total_tokens": 1800},
            )
            db.add_all([parent_audit, child_audit, child_final, other_final])
            await db.flush()
            for invalid_message in (wrong_run_text, wrong_turn_text):
                with pytest.raises(IntegrityError, match="fk_messages_run_turn_session"):
                    async with db.begin_nested():
                        db.add(invalid_message)
                        await db.flush()
                        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            for run, foreign_message in ((parent_run, child_final), (other_run, parent_audit)):
                with pytest.raises(IntegrityError, match="fk_agent_runs_output_message_scope"):
                    async with db.begin_nested():
                        run.output_message_id = foreign_message.id
                        await db.flush()
                        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                await db.refresh(run)
            other_run.output_message_id = other_final.id
            child_run.output_message_id = child_final.id
            await db.commit()

        async with sessions() as db:
            audits = await AgentTurnRepository(db).list_model_usage_audits(turn.id)
            assert [message.operation_id for message in audits] == ["parent-model"]
            assert {key: sum(message.usage[key] for message in audits) for key in ("input_tokens", "output_tokens", "total_tokens")} == {
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            }
            child_audits = await AgentTurnRepository(db).list_model_usage_audits(child_turn.id)
            assert [message.operation_id for message in child_audits] == ["child-tool-model", "child-final-model"]
            assert sum(message.usage["total_tokens"] for message in child_audits) == 10
            other_audits = await AgentTurnRepository(db).list_model_usage_audits(other_turn.id)
            assert [(message.operation_id, message.usage["total_tokens"]) for message in other_audits] == [("other-final-model", 1800)]
    finally:
        await _drop_schema(schema, admin_engine, engine)
