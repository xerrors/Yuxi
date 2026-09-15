"""MCP 展示名的窄持久化写入，不经过连接配置更新。"""

from sqlalchemy import select

from yuxi.storage.postgres.models_business import MCPServer


async def lock_server(db, slug):
    """锁定稳定 slug 所属记录，写入事务由服务持有。"""
    return await db.scalar(select(MCPServer).where(MCPServer.slug == slug).with_for_update())
