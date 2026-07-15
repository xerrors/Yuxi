from langchain.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from yuxi import config as sys_config
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback, sys_config.default_model):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def load_chat_model(fully_specified_name: str | None, **kwargs) -> BaseChatModel:
    fully_specified_name = resolve_chat_model_spec(fully_specified_name)

    info = model_cache.get_model_info(fully_specified_name)
    if not info:
        available_specs = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available_specs[:10]]
        raise ValueError(
            f"Unknown model spec: '{fully_specified_name}'. "
            f"Available chat models ({len(available_specs)}): {available_ids}"
        )

    if info.model_type != "chat":
        raise ValueError(f"Model {fully_specified_name} is not a chat model (type={info.model_type})")

    api_key = info.api_key
    base_url = get_docker_safe_url(info.base_url)

    logger.debug(f"Loading model {fully_specified_name} with provider_type={info.provider_type}")

    if info.provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            **kwargs,
        )
    if info.provider_type == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=info.model_id,
            google_api_key=SecretStr(api_key),
            **kwargs,
        )

    return _ToolCallChunkFixChatOpenAI(
        model=info.model_id,
        api_key=SecretStr(api_key),
        base_url=base_url,
        stream_usage=True,
        **kwargs,
    )


class _ToolCallChunkFixChatOpenAI(ChatOpenAI):
    """归一化流式 tool_call 续片中的空串 name/id，规避 v3 流式累积缺陷。"""

    async def _astream(self, *args, **kwargs):
        async for chunk in super()._astream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk

    def _stream(self, *args, **kwargs):
        for chunk in super()._stream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk


def _normalize_tool_call_chunks(message) -> None:
    """把工具调用续片里空字符串的 name/id 归一化为 None。

    LangGraph v3 流式累积对 tool_call 字段是“后值覆盖”：部分 OpenAI 兼容提供商
    （siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片
    的真实值（siliconflow 丢 name、百炼丢 id），导致工具结果无法按 tool_call_id
    关联、工具状态停留在“进行中”。OpenAI 官方在续片里发 None 不会触发覆盖，这里
    把空串归一化为 None 对齐该行为。待上游修复 v3 协议后可移除。
    """
    for chunk in message.tool_call_chunks:
        if chunk.get("name") == "":
            chunk["name"] = None
        if chunk.get("id") == "":
            chunk["id"] = None
