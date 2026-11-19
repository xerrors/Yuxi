"""业务和知识模型使用独立的 SQLAlchemy metadata。"""

from typing import Any

from sqlalchemy import JSON, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

BusinessBase = declarative_base()
KnowledgeBase = declarative_base()
Base = BusinessBase
JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def normalize_json_nul(value: Any) -> Any:
    """递归把 JSON 字符串与键中的 U+0000 替换为 U+FFFD。

    PostgreSQL 无法把 `json` 文本值里的 `\\u0000` 转义转换回 text：裸 json 列按原文
    存储不校验，但 json_each/`->` 等读取会当场报错，导致该行所有按 JSON 展开的查询
    永久失败。PG 的文本域本就无法表示 NUL，落库前替换是唯一可恢复语义。
    """
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, dict):
        return {normalize_json_nul(key): normalize_json_nul(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json_nul(item) for item in value]
    return value


class NormalizedJSON(TypeDecorator):
    """写入边界归一化 NUL 的裸 json 列，供承载模型/工具 JSON 的字段使用。

    process_bind_param 在序列化前覆盖所有 INSERT 与 flush 时的整字典替换，
    工具审计、模型完整 dump 和 public_items 快照共用这一个收敛点。
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        """返回归一化后的结构副本，序列化仍由 impl 完成。"""
        return None if value is None else normalize_json_nul(value)
