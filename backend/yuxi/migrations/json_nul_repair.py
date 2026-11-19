"""显式修复 messages.extra_metadata 历史污染行中的不可读 NUL 转义。"""

import asyncio
import json

from sqlalchemy import text

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.base import normalize_json_nul
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.migrations.schema import schema_migration_lock


async def repair_messages_nul_escapes(manager: PostgresManager) -> dict:
    """把历史污染期写入 messages.extra_metadata 的 `\\u0000` 转义归一化为 U+FFFD。

    裸 json 列按原文入库，读取端 json_each/`->` 会对这些行当场报错；写边界归一化不
    覆盖存量行，所以显式重放本脚本只重写真实含 NUL 字符的行。文本相等条件防止流量
    期间重放覆盖并发写入；等值行直接跳过，含字面 `\\u0000` 的健康行不会反复空写。

    逐行隔离：解析失败或结果无法编码 UTF-8（如未配对代理项经 json.loads 再 dumps）
    的行只计数并汇报 id，不中断其余行；UPDATE 走独立 SAVEPOINT，单行数据库错误也不
    连坐整个事务。返回 scanned/repaired/failed_ids 供运维核对，未修复行仍需人工处置。
    """
    manager.require_initialized()
    repaired = 0
    failed: list[int] = []
    async with schema_migration_lock(manager):
        async with manager.async_engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(r"SELECT id, extra_metadata::text FROM messages WHERE position(E'\\u0000' in extra_metadata::text) > 0")
                )
            ).all()
            for row_id, raw in rows:
                try:
                    parsed = json.loads(raw)
                    normalized = normalize_json_nul(parsed)
                    if normalized == parsed:
                        continue
                    payload = json.dumps(normalized, ensure_ascii=False)
                    # 未配对代理项要在触达连接前失败：语句级报错会中止 PG 事务，连坐后续行。
                    payload.encode("utf-8")
                    async with conn.begin_nested():
                        await conn.execute(
                            text(
                                "UPDATE messages SET extra_metadata = CAST(:value AS json) WHERE id = :id AND extra_metadata::text = :raw"
                            ),
                            {"value": payload, "id": row_id, "raw": raw},
                        )
                    repaired += 1
                except Exception:
                    failed.append(row_id)
                    logger.opt(exception=True).warning(f"messages extra_metadata NUL 修复跳过该行，需人工处置: id={row_id}")
    return {"scanned": len(rows), "repaired": repaired, "failed_ids": failed}


async def main() -> None:
    """使用当前部署连接，幂等执行一次存量修复并汇报逐行结果。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        summary = await repair_messages_nul_escapes(manager)
        logger.info(
            f"JSON NUL 存量修复完成: scanned={summary['scanned']} repaired={summary['repaired']} failed_ids={summary['failed_ids']}"
        )
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
