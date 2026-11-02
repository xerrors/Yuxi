"""会话标题生成的真实持久化契约。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

import pytest

from test.integration.services.agent_input_fixtures import SCOPE
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.services import threads

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


@pytest.mark.parametrize(
    "outcome",
    ["generated", "timeout", "quota", "manual", "placeholder", "thinking", "dangling_think", "unclosed_think", "truncated_reference"],
)
async def test_auto_title_persists_once_and_preserves_manual_rename(sessions, monkeypatch, outcome):
    """回读真实数据库验证生成、失败回退及手动改名优先。"""
    from yuxi.modules.agents.services import titles

    text = "请帮我分析 PostgreSQL 的慢查询并给出索引优化建议"
    fallback = text[:30]
    async with sessions() as db:
        await SessionRepository(db).add_session(
            uid=SCOPE.uid,
            agent_id="main",
            project_id="input-project",
            thread_id="title-thread",
            config_snapshot={"model": "test:chat", "tool_approval_mode": "default"},
        )
        await db.commit()

    async def invoke(_messages):
        async with sessions() as db:
            persisted = await SessionRepository(db).get_session_by_thread_id("title-thread")
            assert persisted.title == fallback
            if outcome == "manual":
                await threads.update_thread(db=db, scope=SCOPE, thread_id="title-thread", title=fallback)
        if outcome == "timeout":
            raise TimeoutError
        if outcome == "quota":
            request = httpx.Request("POST", "https://models.example/chat/completions")
            response = httpx.Response(429, request=request, json={"error": "PRIVATE_UPSTREAM_QUOTA"})
            raise httpx.HTTPStatusError("quota exhausted", request=request, response=response)
        return SimpleNamespace(
            text={
                "placeholder": "标题",
                "thinking": "<think>PRIVATE_REASONING</think>PostgreSQL 慢查询优化",
                "dangling_think": "PRIVATE_REASONING</think>PostgreSQL 慢查询优化",
                "unclosed_think": "<think>PRIVATE_REASONING",
                "truncated_reference": "PostgreSQL 慢查询优化" + "建议" * 6 + "@session:private-thread",
            }.get(outcome, "PostgreSQL 慢查询优化")
        )

    monkeypatch.setattr(titles, "system_options", SimpleNamespace(get=AsyncMock(return_value={"fast_model": "test:fast"})))
    monkeypatch.setattr(titles, "load_chat_model", lambda *args, **kwargs: SimpleNamespace(ainvoke=invoke))
    await titles.generate_session_title(thread_id="title-thread", uid=SCOPE.uid, app_id=None, content=text)

    async def unexpected(_messages):
        pytest.fail("同一会话重复生成标题")

    monkeypatch.setattr(titles, "load_chat_model", lambda *args, **kwargs: SimpleNamespace(ainvoke=unexpected))
    await titles.generate_session_title(thread_id="title-thread", uid=SCOPE.uid, app_id=None, content="第二条消息")
    async with sessions() as db:
        persisted = await SessionRepository(db).get_session_by_thread_id("title-thread")
        assert persisted.title == ("PostgreSQL 慢查询优化" if outcome in {"generated", "thinking", "dangling_think"} else fallback)


@pytest.mark.parametrize(
    ("message", "candidate", "expected"),
    [
        ("你好", "日常问候", "日常问候"),
        ("谢谢", "表达感谢", "表达感谢"),
        ("如何提升速度", "性能优化建议", "性能优化建议"),
        ("回复字母 A", "浏览器UA", "浏览器UA"),
        ("Hello", "Greetings", "Greetings"),
        ("你好", "こんにちは", "こんにちは"),
        ("回复字母 A", "A", "A"),
        ("给我一个数字", "123", "123"),
        ("如何提升速度", ' 标题："性能优化建议" ', "性能优化建议"),
        ("如何提升速度", "## **标题：性能优化建议**", "性能优化建议"),
        ("如何提升速度", "“Title: **性能优化建议**”", "性能优化建议"),
        ("如何提升速度", "性能\n\t优化建议", "性能 优化建议"),
        ("如何提升速度", "性能优化建议" * 10, ("性能优化建议" * 10)[:30]),
    ],
)
async def test_generated_titles_are_normalized_without_source_word_matching(sessions, monkeypatch, message, candidate, expected):
    """规范化标题持久化成功，不要求复用用户消息中的字词。"""
    from yuxi.modules.agents.services import titles

    async with sessions() as db:
        await SessionRepository(db).add_session(uid=SCOPE.uid, agent_id="main", project_id="input-project", thread_id="letter-title")
        await db.commit()
    monkeypatch.setattr(titles, "system_options", SimpleNamespace(get=AsyncMock(return_value={"fast_model": "test:fast"})))
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(text=candidate)))
    monkeypatch.setattr(titles, "load_chat_model", lambda *args, **kwargs: model)
    await titles.generate_session_title(thread_id="letter-title", uid=SCOPE.uid, app_id=None, content=message)
    async with sessions() as db:
        session = await SessionRepository(db).get_session_by_thread_id("letter-title")
        assert session.title == expected
        assert "auto_title" not in session.extra_metadata


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('@session:"referenced-thread 数据库优化" 请继续分析', "数据库优化 请继续分析"),
        ('@session:"referenced-thread 数据库优化"', "数据库优化"),
        ("@session:referenced-thread", "引用会话"),
        ('@session:"referenced-thread" 请继续', "引用会话 请继续"),
        ('@session:"first-thread 数据库" 与 @session:"second-thread 索引" 对比', "数据库 与 索引 对比"),
        ('@session:"referenced-thread 分析\\"索引\\"策略"', '分析"索引"策略'),
        ('@session:"outer-thread @session:inner-thread PostgreSQL优化" 请继续', "引用会话 PostgreSQL优化 请继续"),
        ('@session:"outer-thread @session:\\"inner-thread PostgreSQL优化\\"" 请继续', "PostgreSQL优化 请继续"),
    ],
)
@pytest.mark.parametrize("outcome", ["generated", "timeout", "protocol"])
async def test_session_reference_titles_keep_readable_labels(sessions, monkeypatch, content, expected, outcome):
    """会话引用在命名输入与持久回退中均不泄露协议和线程标识。"""
    from yuxi.modules.agents.services import titles

    observed = {}
    generated_title = f"{expected}讨论"[:30]
    async with sessions() as db:
        await SessionRepository(db).add_session(uid=SCOPE.uid, agent_id="main", project_id="input-project", thread_id="reference-title")
        await db.commit()

    async def invoke(messages):
        """记录模型边界事实，由调用外断言避免被附属任务吞掉。"""
        observed["messages"] = messages
        async with sessions() as db:
            session = await SessionRepository(db).get_session_by_thread_id("reference-title")
            observed["fallback"] = session.title
        if outcome == "timeout":
            raise TimeoutError
        return SimpleNamespace(text=f"@session:private-thread {expected}" if outcome == "protocol" else generated_title)

    monkeypatch.setattr(titles, "system_options", SimpleNamespace(get=AsyncMock(return_value={"fast_model": "test:fast"})))
    monkeypatch.setattr(titles, "load_chat_model", lambda *args, **kwargs: SimpleNamespace(ainvoke=invoke))
    await titles.generate_session_title(thread_id="reference-title", uid=SCOPE.uid, app_id=None, content=content)
    assert observed["messages"][-1] == ("human", expected)
    assert observed["fallback"] == expected[:30]
    async with sessions() as db:
        session = await SessionRepository(db).get_session_by_thread_id("reference-title")
        assert session.title == (generated_title if outcome == "generated" else expected[:30])
