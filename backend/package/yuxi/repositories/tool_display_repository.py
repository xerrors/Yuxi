"""工具展示覆盖的持久化边界，不改变工具注册表。"""

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ConfigOption

KEY = "tool_display_names"


async def read_names(*, key=KEY):
    """每次读取 PostgreSQL，避免多进程展示缓存不一致。"""
    async with pg_manager.get_async_session_context() as db:
        row = await db.scalar(select(ConfigOption).where(ConfigOption.key == key))
        return dict(row.value or {}) if row else {}


async def save_name(slug, name, actor, *, key=KEY):
    """锁住同一展示映射并保存，空字符串清除覆盖。"""
    async with pg_manager.get_async_session_context() as db:
        await db.execute(select(func.pg_advisory_xact_lock(94721805, 0)))
        row = await db.scalar(select(ConfigOption).where(ConfigOption.key == key).with_for_update())
        if row is None:
            row = ConfigOption(key=key, name="资源显示名称", params={"internal": True}, value={})
            db.add(row)
        names = dict(row.value or {})
        if name:
            names[slug] = name
        else:
            names.pop(slug, None)
        row.value = names
        row.updated_by = actor
