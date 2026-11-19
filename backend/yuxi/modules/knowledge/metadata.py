"""文件自定义属性及有界过滤条件的契约，不解释具体业务字段。"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

MAX_METADATA_FIELDS = 64
MAX_ATTRIBUTE_BYTES = 65536
MAX_FILTER_CONDITIONS = 50
MAX_FILTER_DEPTH = 5

FieldType = Literal["text", "number", "boolean", "date", "select", "multi_select"]
Comparison = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains_any", "contains_all", "exists", "not_exists"]

FIELD_OPERATORS = {
    "text": ["eq", "ne"],
    "number": ["eq", "ne", "gt", "gte", "lt", "lte"],
    "boolean": ["eq", "ne"],
    "date": ["eq", "ne", "gt", "gte", "lt", "lte"],
    "select": ["eq", "ne"],
    "multi_select": ["contains_any", "contains_all"],
}


class MetadataFieldCreate(BaseModel):
    """管理员定义字段；内部 key 由服务分配。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    type: FieldType
    description: str = Field(default="", max_length=500)
    options: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def check_options(self):
        """选项字段需要明确且不重复的非空选项。"""
        if any(ord(character) < 32 or ord(character) == 127 for character in self.name):
            raise ValueError("字段名称不能包含换行或控制字符")
        if "\x00" in self.description:
            raise ValueError("字段描述不能包含空字符")
        if self.type in {"select", "multi_select"}:
            if not self.options or any(not item or len(item) > 256 for item in self.options):
                raise ValueError("单选和多选字段需要 1 至 128 个非空选项，每项不超过 256 字符")
            if len(set(self.options)) != len(self.options):
                raise ValueError("字段选项不能重复")
            if any(ord(character) < 32 or ord(character) == 127 for option in self.options for character in option):
                raise ValueError("每个选项须为单行文本，不能包含控制字符")
        elif self.options:
            raise ValueError("只有单选和多选字段可以定义选项")
        return self


class MetadataField(MetadataFieldCreate):
    """持久字段定义，稳定 key 与显示名称分离。"""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class MetadataFieldUpdate(BaseModel):
    """只更新字段展示和选项，类型不支持原地修改。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    options: list[str] | None = Field(default=None, max_length=128)


class MetadataPatch(BaseModel):
    """属性部分更新；省略的字段保留，清空只通过 unset 表达。"""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    set_values: dict[str, Any] = Field(default_factory=dict, alias="set", max_length=MAX_METADATA_FIELDS)
    unset: list[str] = Field(default_factory=list, max_length=MAX_METADATA_FIELDS)

    @model_validator(mode="after")
    def check_changes(self):
        """拒绝同时设置与清除同一个字段的歧义请求。"""
        if set(self.set_values) & set(self.unset):
            raise ValueError("同一个字段不能同时设置和清除")
        if len(set(self.unset)) != len(self.unset):
            raise ValueError("清除字段不能重复")
        return self


class MetadataFilesPatch(MetadataPatch):
    """同一知识库内原子修改选中文件的属性。"""

    file_ids: list[str] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_file_ids(self):
        """文件 ID 必须明确且不重复。"""
        if any(not value.strip() for value in self.file_ids) or len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError("文件 ID 必须非空且不能重复")
        return self


class MetadataCondition(BaseModel):
    """根据字段类型执行一个确定性条件。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    field: str = Field(min_length=1, max_length=64, description="来自 get_kb_metadata 的字段 key")
    operator: Comparison
    value: Any = None


class MetadataGroup(BaseModel):
    """支持 AND/OR 分组，不支持代码或原始数据库表达式。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    operator: Literal["and", "or"]
    conditions: list[MetadataFilter] = Field(min_length=1, max_length=MAX_FILTER_CONDITIONS)

    @model_validator(mode="before")
    @classmethod
    def check_budget(cls, value):
        """在构造递归模型前限制整棵表达式。"""
        require_filter_budget(value)
        return value


MetadataFilter = Annotated[MetadataCondition | MetadataGroup, Field(discriminator="operator")]
MetadataGroup.model_rebuild()
FILTER_ADAPTER = TypeAdapter(MetadataFilter)


def validate_attributes(values: dict[str, Any], fields: list[MetadataField]) -> dict[str, Any]:
    """在写入边界按当前字段定义校验属性，并限制序列化大小。"""
    by_key = {field.key: field for field in fields}
    result = {}
    for key, value in values.items():
        field = by_key.get(key)
        if field is None:
            raise ValueError(f"属性字段不存在：{key}，请刷新字段定义")
        result[key] = validate_attribute_value(field, value)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_ATTRIBUTE_BYTES:
        raise ValueError("文件属性超过 64 KiB，请缩短属性内容")
    return result


def validate_filter(value: Any, fields: list[MetadataField]) -> MetadataFilter | None:
    """把模型或 HTTP 条件校验为当前知识库可执行的有界表达式。"""
    if value is None or value == []:
        return None
    require_filter_budget(value)
    expression = FILTER_ADAPTER.validate_python(value)
    by_key = {field.key: field for field in fields}
    pending = [expression]
    while pending:
        node = pending.pop()
        if isinstance(node, MetadataGroup):
            pending.extend(node.conditions)
            continue
        field = by_key.get(node.field)
        if field is None:
            raise ValueError(f"筛选字段不存在：{node.field}，请通过 get_kb_metadata 刷新字段定义")
        if node.operator in {"exists", "not_exists"}:
            if node.value is not None:
                raise ValueError("是否设置条件不接受比较值")
            continue
        if node.operator not in FIELD_OPERATORS[field.type]:
            raise ValueError(f"字段“{field.name}”不支持操作符 {node.operator}")
        node.value = validate_attribute_value(field, node.value)
    return expression


def require_filter_budget(value) -> None:
    """在 wire JSON 与条件规范化两个入口共用线性预算检查。"""
    pending = [(value, 1)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > MAX_FILTER_DEPTH or count > MAX_FILTER_CONDITIONS:
            raise ValueError("属性筛选最多 5 层、50 个条件或分组")
        if isinstance(node, MetadataGroup):
            children = node.conditions
        elif isinstance(node, dict) and node.get("operator") in ("and", "or"):
            children = node.get("conditions", [])
        else:
            continue
        if isinstance(children, list):
            if len(children) + count > MAX_FILTER_CONDITIONS:
                raise ValueError("属性筛选最多 5 层、50 个条件或分组")
            pending.extend((child, depth + 1) for child in children)


def validate_attribute_value(field: MetadataField, value: Any) -> Any:
    """值类型严格匹配，不将字符串数字、布尔值或缺失值猜测为其他类型。"""
    kind = field.type
    if kind == "number":
        try:
            finite = type(value) in {int, float} and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"字段“{field.name}”需要有限数字")
        return value
    if kind == "boolean":
        if type(value) is not bool:
            raise ValueError(f"字段“{field.name}”需要布尔值")
        return value
    if kind == "multi_select":
        if (
            not isinstance(value, list)
            or not value
            or len(value) > 128
            or any(not isinstance(item, str) or item not in field.options for item in value)
        ):
            raise ValueError(f"字段“{field.name}”需要非空的已定义选项列表")
        return sorted(set(value))
    if not isinstance(value, str) or len(value) > 4000 or not value.strip():
        raise ValueError(f"字段“{field.name}”需要 1 至 4000 字符；清空请使用 unset")
    value = value.strip()
    if "\x00" in value:
        raise ValueError(f"字段“{field.name}”的值不能包含空字符")
    if kind == "select" and value not in field.options:
        raise ValueError(f"字段“{field.name}”的值不在选项中")
    if kind == "date":
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as error:
            raise ValueError(f"字段“{field.name}”需要 YYYY-MM-DD 格式日期") from error
    return value
