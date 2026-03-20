from __future__ import annotations

import asyncio
import os
from abc import abstractmethod
from inspect import isawaitable
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver, aiosqlite
from langgraph.graph.state import CompiledStateGraph

from yuxi import config as sys_config
from yuxi.agents.context import BaseContext
from yuxi.utils import logger


class BaseAgent:
    """
    定义一个基础 Agent 供 各类 graph 继承
    """

    name = "base_agent"
    description = "base_agent"
    capabilities: list[str] = []  # 智能体能力列表，如 ["file_upload", "web_search"] 等
    context_schema: type[BaseContext] = BaseContext  # 智能体上下文 schema

    def __init__(self, **kwargs):
        self.graph = None  # will be covered by get_graph
        self.checkpointer = None
        self._checkpointer_cm = None
        self._async_conn = None
        self.workdir = Path(sys_config.save_dir) / "agents" / self.module_name
        self.workdir.mkdir(parents=True, exist_ok=True)

    @property
    def module_name(self) -> str:
        """Get the module name of the agent class."""
        return self.__class__.__module__.split(".")[-2]

    @property
    def id(self) -> str:
        """Get the agent's class name."""
        return self.__class__.__name__

    async def get_info(self, include_configurable_items: bool = True):
        # metadata 固定在代码中，由各 Agent 的类属性提供
        metadata = self.load_metadata()
        configurable_items = {}
        if include_configurable_items:
            configurable_items = self.context_schema.get_configurable_items()

        # Merge metadata with class attributes, metadata takes precedence
        return {
            "id": self.id,
            "name": getattr(self, "name", "Unknown"),
            "description": getattr(self, "description", "Unknown"),
            "metadata": metadata,
            # "examples": metadata.get("examples", []),
            "configurable_items": configurable_items,
            "capabilities": getattr(self, "capabilities", []),  # 智能体能力列表
        }

    async def get_config(self):
        return self.context_schema()

    async def stream_values(self, messages: list[str], input_context=None, **kwargs):
        context = self.context_schema()
        context.update_from_dict(input_context or {})
        graph = await self.get_graph(context=context)
        for event in graph.astream({"messages": messages}, stream_mode="values", context=context):
            yield event["messages"]

    async def stream_messages(self, messages: list[str], input_context=None, **kwargs):
        context = self.context_schema()
        context.update_from_dict(input_context or {})
        graph = await self.get_graph(context=context)
        logger.debug(f"stream_messages: {context=}")

        # 构建配置：LangGraph 会自动从 checkpointer 恢复 state
        input_config = {
            "configurable": {"thread_id": context.thread_id, "user_id": context.user_id},
            "recursion_limit": 300,
        }

        async for msg, metadata in graph.astream(
            {"messages": messages},
            stream_mode="messages",
            context=context,
            config=input_config,
        ):
            yield msg, metadata

    async def invoke_messages(self, messages: list[str], input_context=None, **kwargs):
        context = self.context_schema()
        context.update_from_dict(input_context or {})
        graph = await self.get_graph(context=context)
        logger.debug(f"invoke_messages: {context}")

        # 构建配置
        input_config = {
            "configurable": {"thread_id": context.thread_id, "user_id": context.user_id},
            "recursion_limit": 100,
        }

        msg = await graph.ainvoke(
            {"messages": messages},
            context=context,
            config=input_config,
        )
        return msg

    async def check_checkpointer(self):
        app = await self.get_graph()
        if not hasattr(app, "checkpointer") or app.checkpointer is None:
            return False
        return True

    async def get_history(self, user_id, thread_id) -> list[dict]:
        """获取历史消息"""
        try:
            app = await self.get_graph()

            if not await self.check_checkpointer():
                return []

            config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
            state = await app.aget_state(config)

            result = []
            if state:
                messages = state.values.get("messages", [])
                for msg in messages:
                    if hasattr(msg, "model_dump"):
                        msg_dict = msg.model_dump()  # 转换成字典
                    else:
                        msg_dict = dict(msg) if hasattr(msg, "__dict__") else {"content": str(msg)}
                    result.append(msg_dict)

            return result

        except Exception as e:
            logger.error(f"获取智能体 {self.name} 历史消息出错: {e}")
            return []

    def reload_graph(self):
        """重置 graph 缓存，强制下次调用 get_graph 时重新构建"""
        self.graph = None
        self.checkpointer = None
        if self._checkpointer_cm is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                loop.create_task(self._close_checkpointer_context())
        logger.info(f"{self.name} graph 缓存已清空，将在下次调用时重新构建")

    @abstractmethod
    async def get_graph(self, **kwargs) -> CompiledStateGraph:
        """
        获取并编译对话图实例。
        必须确保在编译时设置 checkpointer，否则将无法获取历史记录。
        例如: graph = workflow.compile(checkpointer=sqlite_checkpointer)
        """
        pass

    async def _get_checkpointer(self):
        if self.checkpointer is not None:
            return self.checkpointer

        checkpointer = None
        backend = os.getenv("LANGGRAPH_CHECKPOINTER_BACKEND", "sqlite").strip().lower()

        if backend == "postgres":
            checkpointer = await self._create_postgres_checkpointer()

        if checkpointer is None:
            try:
                checkpointer = AsyncSqliteSaver(await self.get_async_conn())
            except Exception as e:
                logger.error(f"构建 sqlite checkpointer 失败: {e}, 尝试使用内存存储")
                checkpointer = InMemorySaver()

        self.checkpointer = checkpointer
        return self.checkpointer

    async def _create_postgres_checkpointer(self):
        postgres_url = os.getenv("POSTGRES_URL")
        if not postgres_url:
            logger.warning("POSTGRES_URL 未配置，无法启用 postgres checkpointer，回退 sqlite")
            return None

        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
        except Exception as e:
            logger.warning(f"langgraph postgres checkpointer 不可用，回退 sqlite: {e}")
            return None

        conn_str = postgres_url.replace("+asyncpg", "")
        try:
            saver_factory = getattr(AsyncPostgresSaver, "from_conn_string", None)
            if callable(saver_factory):
                saver = saver_factory(conn_str)
            else:
                saver = AsyncPostgresSaver(conn_str)  # type: ignore[call-arg]

            if hasattr(saver, "__aenter__") and hasattr(saver, "__aexit__"):
                self._checkpointer_cm = saver
                saver = await saver.__aenter__()

            setup_fn = getattr(saver, "setup", None)
            if callable(setup_fn):
                result = setup_fn()
                if isawaitable(result):
                    await result
            logger.info(f"{self.name} 使用 postgres checkpointer")
            return saver
        except Exception as e:
            logger.warning(f"初始化 postgres checkpointer 失败，回退 sqlite: {e}")
            return None

    async def _close_checkpointer_context(self):
        if self._checkpointer_cm is None:
            return

        cm = self._checkpointer_cm
        self._checkpointer_cm = None
        try:
            await cm.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"关闭 postgres checkpointer 失败: {e}")

    async def get_async_conn(self) -> aiosqlite.Connection:
        """获取异步数据库连接"""
        if self._async_conn is not None:
            return self._async_conn

        conn = await aiosqlite.connect(os.path.join(self.workdir, "aio_history.db"))
        # Patch: langgraph's AsyncSqliteSaver expects is_alive() method which aiosqlite may not have
        if not hasattr(conn, "is_alive"):
            conn.is_alive = lambda: True
        self._async_conn = conn
        return self._async_conn

    async def get_aio_memory(self) -> AsyncSqliteSaver:
        """获取异步存储实例"""
        return AsyncSqliteSaver(await self.get_async_conn())

    def load_metadata(self) -> dict:
        """Load metadata from agent class attribute."""
        metadata = getattr(self, "metadata", {})
        if isinstance(metadata, dict):
            return metadata
        logger.warning(f"Agent {self.module_name} metadata is not a dict, fallback to empty metadata")
        return {}
