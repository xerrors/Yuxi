"""显式修复 messages.extra_metadata 历史污染行中的不可读 NUL 转义。"""

import asyncio
import json

from sqlalchemy import text

from yuxi.infrastructure.postgres.base import normalize_json_nul
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.migrations.schema import schema_migration_lock


async def repair_messages_nul_escapes(manager: PostgresManager) -> None:
    """把历史污染期写入 messages.extra_metadata 的 `\\u0000` 转义归一化为 U+FFFD。

    裸 json 列按原文入库，读取端 json_each/`->` 会对这些行当场报错；写边界归一化不
    覆盖存量行，所以显式重放本脚本只重写真实含 NUL 字符的行。文本相等条件防止流量
    期间重放覆盖并发写入；等值行直接跳过，含字面 `\\u0000` 的健康行不会反复空写。
    """
    manager.require_initialized()
    async with schema_migration_lock(manager):
        async with manager.async_engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(r"SELECT id, extra_metadata::text FROM messages WHERE position(E'\\u0000' in extra_metadata::text) > 0")
                )
            ).all()
            for row_id, raw in rows:
                parsed = json.loads(raw)
                normalized = normalize_json_nul(parsed)
                if normalized == parsed:
                    continue
                await conn.execute(
                    text("UPDATE messages SET extra_metadata = CAST(:value AS json) WHERE id = :id AND extra_metadata::text = :raw"),
                    {
                        "value": json.dumps(normalized, ensure_ascii=False),
                        "id": row_id,
                        "raw": raw,
                    },
                )


async def main() -> None:
    """使用当前部署连接，幂等执行一次存量修复。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await repair_messages_nul_escapes(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
