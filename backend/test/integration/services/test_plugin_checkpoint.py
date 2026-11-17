"""隔离 PostgreSQL 验证插件激活经过断开连接后仍能恢复。"""

import os
import uuid
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row

from test.unit.middlewares.test_plugins_middleware import ExamplePlugin, RecordingModel
from yuxi.modules.agents.runtime.middlewares import plugins
from yuxi.modules.agents.runtime.middlewares.plugins import PluginLoaderMiddleware

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_plugin_activation_survives_postgres_reconnect(monkeypatch):
    """真实持久化加载结果，新连接和新图读取同一激活事实。"""
    monkeypatch.setattr(plugins, "get_plugins", lambda: (ExamplePlugin,))
    monkeypatch.setattr(plugins, "get_plugin", lambda plugin_id: ExamplePlugin)
    schema = f"pytest_plugin_{uuid.uuid4().hex}"
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")
    config = {"configurable": {"thread_id": "plugin-recovery"}}
    context = SimpleNamespace(
        plugins=["example"],
        mcps=[],
        mcp_tools={},
        mcp_tool_servers={},
        mcp_unavailable={},
        registered_builtin_tool_names=set(),
        middleware_tool_names={"load_plugin"},
        skill_runtime_snapshot={
            "plugin_entry_contents": {"example": "Plugin instructions from authorized source"},
            "runtime_skills": {
                "example-guide": {"path": "/skills/example-guide/SKILL.md"},
                "chat-widgets": {"path": "/skills/chat-widgets/SKILL.md"},
            },
        },
    )
    async with await AsyncConnection.connect(dsn, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with await AsyncConnection.connect(
                dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row, options=f"-csearch_path={schema}"
            ) as connection:
                saver = AsyncPostgresSaver(connection)
                await saver.setup()
                model = RecordingModel(
                    responses=[
                        AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
                        AIMessage(content="done"),
                    ]
                )
                graph = create_agent(model, middleware=[PluginLoaderMiddleware()], checkpointer=saver)
                await graph.ainvoke({"messages": [{"role": "user", "content": "draw"}]}, config, context=context)
            async with await AsyncConnection.connect(
                dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row, options=f"-csearch_path={schema}"
            ) as connection:
                graph = create_agent(
                    RecordingModel(responses=[]), middleware=[PluginLoaderMiddleware()], checkpointer=AsyncPostgresSaver(connection)
                )
                saved = await graph.aget_state(config)
                assert saved.values["activated_plugins"] == ["example"]
                assert any("Plugin instructions from authorized source" in message.content for message in saved.values["messages"])
                assert "activated_skills" not in saved.values
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
