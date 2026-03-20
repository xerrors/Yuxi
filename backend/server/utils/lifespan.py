from contextlib import asynccontextmanager

from fastapi import FastAPI

from yuxi.services.task_service import tasker
from yuxi.services.mcp_service import init_mcp_servers
from yuxi.services.subagent_service import init_builtin_subagents
from yuxi.services.run_queue_service import close_queue_clients, get_redis_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.knowledge import knowledge_base
from yuxi.utils import logger
from yuxi import get_version


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan事件管理器"""
    # 初始化数据库连接
    try:
        pg_manager.initialize()
        await pg_manager.create_business_tables()
        await pg_manager.ensure_business_schema()
        await pg_manager.ensure_knowledge_schema()
    except Exception as e:
        logger.error(f"Failed to initialize database during startup: {e}")

    # 初始化 MCP 服务器配置
    try:
        await init_mcp_servers()
    except Exception as e:
        logger.error(f"Failed to initialize MCP servers during startup: {e}")

    # 初始化内置 SubAgent
    try:
        await init_builtin_subagents()
    except Exception as e:
        logger.error(f"Failed to initialize builtin subagents during startup: {e}")
        raise

    # 初始化知识库管理器
    import os
    if os.environ.get("LITE_MODE", "").lower() in ("true", "1"):
        logger.info("LITE_MODE enabled, skipping knowledge base initialization")
    else:
        try:
            await knowledge_base.initialize()
        except Exception as e:
            logger.error(f"Failed to initialize knowledge base manager: {e}")

    # 预热 Redis（run 队列）
    try:
        redis = await get_redis_client()
        await redis.ping()
    except Exception as e:
        logger.warning(f"Run queue redis unavailable on startup: {e}")

    await tasker.start()
    logger.info(f"""

░██     ░██                       ░██
 ░██   ░██
  ░██ ░██   ░██    ░██ ░██    ░██ ░██
   ░████    ░██    ░██  ░██  ░██  ░██
    ░██     ░██    ░██   ░█████   ░██
    ░██     ░██   ░███  ░██  ░██  ░██
    ░██      ░█████░██ ░██    ░██ ░██  v{get_version()}

    """)
    logger.info("Yuxi backend startup complete")
    yield
    await tasker.shutdown()
    await close_queue_clients()
    await pg_manager.close()
