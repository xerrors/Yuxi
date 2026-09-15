"""文档限制配置的事务边界。"""

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ConfigOption


class DocumentLimitsConflict(ValueError):
    """设置版本发生并发变更。"""


async def read_limits() -> dict:
    """关键接收边界直接读取 PG，避免缓存失效失败继续接受旧限额。"""
    async with pg_manager.get_async_session_context() as db:
        row = await db.scalar(select(ConfigOption).where(ConfigOption.key == "document_limits"))
        if row is None:
            raise RuntimeError("Document limits configuration has not been initialized")
        return dict(row.value or {})


async def replace_limits(value: dict, revision: int, actor: str) -> None:
    """锁定配置行并比较版本，提交后由服务失效缓存。"""
    async with pg_manager.get_async_session_context() as db:
        row = await db.scalar(select(ConfigOption).where(ConfigOption.key == "document_limits").with_for_update())
        if row is None:
            raise RuntimeError("Document limits configuration has not been initialized")
        if int((row.value or {}).get("revision", 0)) != revision:
            raise DocumentLimitsConflict("设置已被修改，请刷新后重试")
        row.value = {**value, "revision": revision + 1}
        row.updated_by = actor
