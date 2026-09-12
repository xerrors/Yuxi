"""通过真实 HTTP 与 PostgreSQL 验证反馈目标及持久化结果。"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.models_business import Conversation, Message, MessageFeedback, Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_feedback_target_contract(test_client, admin_headers):
    """非法角色和审计行零落库，普通及旧版助手消息保持可反馈。"""
    current_user = await test_client.get("/api/auth/me", headers=admin_headers)
    assert current_user.status_code == 200, current_user.text
    uid = str(current_user.json()["uid"])
    project_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    message_ids = []
    conversation_id = None
    cases = [
        ("user", "text", 422),
        ("system", "text", 422),
        ("tool", "text", 422),
        ("assistant", "model_audit", 422),
        ("assistant", "tool_audit", 422),
        ("assistant", "text", 200),
        ("assistant", None, 200),
    ]
    try:
        async with factory() as db:
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    selection_status="implicit",
                    workdir_path=f"pytest-feedback/{project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            conversation = Conversation(
                thread_id=thread_id,
                uid=uid,
                agent_id="pytest-feedback",
                project_id=project_id,
                title="pytest feedback target contract",
            )
            db.add(conversation)
            await db.flush()
            conversation_id = conversation.id
            for role, message_type, _ in cases:
                message = Message(
                    conversation_id=conversation_id,
                    role=role,
                    message_type=message_type,
                    content="feedback target fixture",
                    extra_metadata={},
                )
                db.add(message)
                await db.flush()
                message_ids.append(message.id)
            # ORM 插入时的默认值会覆盖 None；显式 SQL NULL 才证明旧数据兼容。
            await db.execute(update(Message).where(Message.id == message_ids[-1]).values(message_type=None))
            await db.commit()

        for (_, _, expected), message_id in zip(cases, message_ids, strict=True):
            response = await test_client.post(
                f"/api/chat/message/{message_id}/feedback",
                headers=admin_headers,
                json={"rating": "like"},
            )
            assert response.status_code == expected, response.text
            async with factory() as db:
                rows = (await db.scalars(select(MessageFeedback).where(MessageFeedback.message_id == message_id))).all()
            assert len(rows) == int(expected == 200)
            if expected == 200:
                assert rows[0].id == response.json()["id"]
                assert rows[0].uid == uid
                assert rows[0].rating == "like"
            else:
                assert response.json()["detail"] == "Feedback is only supported for non-audit assistant messages"

        duplicate = await test_client.post(
            f"/api/chat/message/{message_ids[-1]}/feedback",
            headers=admin_headers,
            json={"rating": "dislike"},
        )
        assert duplicate.status_code == 409, duplicate.text
        async with factory() as db:
            rows = (await db.scalars(select(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids)))).all()
        assert len(rows) == 2
        assert all(row.rating == "like" for row in rows)
    finally:
        async with factory() as db:
            if message_ids:
                await db.execute(delete(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids)))
                await db.execute(delete(Message).where(Message.id.in_(message_ids)))
            if conversation_id is not None:
                await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await engine.dispose()
