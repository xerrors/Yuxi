"""配置记录持久化，提交由启动或管理员用例拥有。"""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.system.models import ConfigOption

# 与既有部署共用同一事务锁值，禁止仅为统一命名更换键。
OPTION_SYNC_LOCK_KEY = 94721801


async def sync_option_definitions(db: AsyncSession, definitions: dict) -> None:
    """幂等同步系统定义，保留管理员已经保存的值。"""

    if db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": OPTION_SYNC_LOCK_KEY})

    result = await db.execute(select(ConfigOption).where(ConfigOption.key.in_(definitions)))
    existing = {record.key: record for record in result.scalars().all()}
    for key, definition in definitions.items():
        record = existing.get(key)
        if record is None:
            record = ConfigOption(
                key=key,
                name=definition.name,
                description=definition.description,
                params=definition.params,
                value={},
                created_by="system",
                updated_by="system",
            )
            db.add(record)
        else:
            record.name = definition.name
            record.description = definition.description
            record.params = dict(definition.params)
    await db.flush()


async def list_options(db: AsyncSession) -> list[ConfigOption]:
    result = await db.execute(select(ConfigOption).where(ConfigOption.key != "system_options").order_by(ConfigOption.id))
    return list(result.scalars().all())


async def get_option(db: AsyncSession, key: str, *, for_update: bool = False) -> ConfigOption | None:
    statement = select(ConfigOption).where(ConfigOption.key == key).execution_options(populate_existing=True)
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def save_option_value(db: AsyncSession, record: ConfigOption, value: dict, updated_by: str) -> ConfigOption:
    """在调用方事务内写入已校验字段。"""
    record.value = value
    record.updated_by = updated_by
    await db.flush()
    return record
