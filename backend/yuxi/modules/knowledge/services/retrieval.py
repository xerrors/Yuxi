"""单次检索覆盖的校验。知识库类型声明查询参数，这里只接受声明内的值。"""

from typing import Any

from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.repositories.files import MAX_FILTER_FILE_IDS
from yuxi.shared.errors import InvalidInputError

MAX_FILE_NAME_LENGTH = 200
MAX_OPTION_STRING_LENGTH = 500
MAX_FILE_ID_LENGTH = 64


def normalize_query_overrides(kb_class: type, overrides: dict) -> dict[str, Any]:
    """按知识库类型的查询参数声明接受单次覆盖。

    未知键和超出声明范围的值直接拒绝。file_name 与 file_ids 是文档过滤，
    不属于类型声明，仍在这一处收口。
    """

    if not isinstance(overrides, dict):
        raise InvalidInputError("检索参数必须是对象")

    options = {
        option["key"]: option
        for option in kb_class.get_query_params_config().get("options", [])
        if isinstance(option, dict) and isinstance(option.get("key"), str)
    }
    allowed = set(options) | {"file_name", "file_ids"}
    guarded: dict[str, Any] = {}
    for key, value in overrides.items():
        if not isinstance(key, str) or key not in allowed:
            raise InvalidInputError(f"不支持的检索参数: {key}")
        if key == "file_name":
            file_name = _file_name(value)
            if file_name:
                guarded["file_name"] = file_name
            continue
        if key == "file_ids":
            guarded["file_ids"] = _file_ids(value)
            continue
        guarded[key] = _option_value(options[key], value)
    return guarded


async def query_with_overrides(kb_id: str, query: str, overrides: dict) -> list:
    """校验单次覆盖并复用生产检索，保留管理接口的片段列表格式。"""

    if not isinstance(overrides, dict):
        raise InvalidInputError("检索参数必须是对象")

    from yuxi.modules.knowledge.runtime import knowledge_base

    guarded: dict[str, Any] = {}
    if overrides:
        config = await knowledge_base.get_kb_config(kb_id)
        kb_class = KnowledgeBaseFactory.get_kb_class(config.kb_type)
        guarded = normalize_query_overrides(kb_class, overrides)
        if "file_ids" in guarded:
            await require_file_ids_in_kb(kb_id, guarded["file_ids"])
    output = await knowledge_base.retrieve(kb_id, query, **guarded)
    results = output["results"]
    for item in results:
        metadata = item.get("metadata") or {}
        for field in ("score", "score_type", "distance", "rerank_score", "highlights"):
            if field in metadata:
                item[field] = metadata[field]
    return results


async def require_file_ids_in_kb(kb_id: str, file_ids: list[str]) -> None:
    """file_ids 只能引用目标知识库中未删除的文件。"""

    if not file_ids:
        return
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    found = set(await KnowledgeFileRepository().list_live_file_ids(kb_id=kb_id, file_ids=file_ids))
    if found != set(file_ids):
        raise InvalidInputError("file_ids 含有不属于该知识库的文件")


def _file_name(value: Any) -> str:
    if not isinstance(value, str) or len(value) > MAX_FILE_NAME_LENGTH:
        raise InvalidInputError("file_name 必须是长度不超过 200 的字符串")
    return value.strip()


def _file_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_FILTER_FILE_IDS:
        raise InvalidInputError(f"file_ids 必须是不超过 {MAX_FILTER_FILE_IDS} 个文件标识的列表")
    unique: list[str] = []
    seen: set[str] = set()
    for file_id in value:
        if not isinstance(file_id, str) or not file_id or len(file_id) > MAX_FILE_ID_LENGTH or any(char.isspace() for char in file_id):
            raise InvalidInputError("file_ids 含有无效的文件标识")
        if file_id in seen:
            continue
        seen.add(file_id)
        unique.append(file_id)
    return unique


def _option_value(option: dict, value: Any) -> Any:
    kind = option.get("type")
    key = option["key"]
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidInputError(f"{key} 必须是数字")
        minimum = option.get("min")
        maximum = option.get("max")
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise InvalidInputError(f"{key} 超出允许范围")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise InvalidInputError(f"{key} 必须是布尔值")
        return value
    if kind == "select":
        choices = _select_values(option)
        if choices and value not in choices:
            raise InvalidInputError(f"{key} 不是允许的取值")
        if not isinstance(value, str) or len(value) > MAX_OPTION_STRING_LENGTH:
            raise InvalidInputError(f"{key} 必须是字符串")
        return value
    if kind == "string":
        if not isinstance(value, str) or len(value) > MAX_OPTION_STRING_LENGTH:
            raise InvalidInputError(f"{key} 必须是长度不超过 {MAX_OPTION_STRING_LENGTH} 的字符串")
        return value
    raise InvalidInputError(f"不支持的检索参数: {key}")


def _select_values(option: dict) -> list[Any]:
    values = []
    for item in option.get("options") or []:
        if isinstance(item, dict) and "value" in item:
            values.append(item["value"])
        elif isinstance(item, str):
            values.append(item)
    return values
