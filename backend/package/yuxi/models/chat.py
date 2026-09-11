"""聊天模型加载、供应商协议适配与通用调用入口。"""

from uuid import uuid4

from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, convert_to_messages
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr

from yuxi import get_version
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url, logger


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def load_chat_model(fully_specified_name: str | None, *, session_id: str | None = None, **kwargs) -> BaseChatModel:
    """加载模型，为 OpenCode 请求绑定稳定会话路由。"""
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
    if info.provider_id in {"opencode", "opencode-go"}:
        kwargs["default_headers"] = {
            **(kwargs.get("default_headers") or {}),
            "User-Agent": f"yuxi/{get_version()}",
            "x-opencode-session": session_id or str(uuid4()),
        }
    if info.request_body_overrides:
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body.update(info.request_body_overrides)
        kwargs = {**kwargs, "extra_body": extra_body}

    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.update(
        {
            "yuxi_provider_id": info.provider_id,
            "yuxi_provider_type": info.provider_type,
            "yuxi_model_id": info.model_id,
            "yuxi_model_spec": info.spec,
        }
    )
    kwargs["metadata"] = metadata

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

    return ChatCompletionsAdapter(
        model=info.model_id,
        api_key=SecretStr(api_key),
        base_url=base_url,
        stream_usage=True,
        preserve_reasoning=info.provider_id
        in {
            "siliconflow",
            "siliconflow-cn",
            "opencode",
            "opencode-go",
            "zhipuai",
            "zhipuai-coding-plan",
            "zai",
            "zai-coding-plan",
        },
        **kwargs,
    )


def reasoning_content(message: dict) -> str:
    """读取供应商原始推理文本，保持空白与工具续答输入不变。"""
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def normalize_tool_call_chunks(message: AIMessageChunk) -> None:
    """将续片空串改为 None，防止 v3 累积覆盖工具首片的名称与 ID。"""
    for tool in message.tool_call_chunks:
        for key in ("name", "id"):
            if tool.get(key) == "":
                tool[key] = None


class ChatCompletionsAdapter(ChatOpenAI):
    """在解析边界保留扩展字段，HTTP、重试和工具绑定由上游负责。"""

    preserve_reasoning: bool = Field(default=False, exclude=True)

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        """在上游丢弃扩展字段前读取推理，并归一化工具续片。"""
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation is None or not isinstance(generation.message, AIMessageChunk):
            return generation
        message = generation.message
        normalize_tool_call_chunks(message)
        choices = chunk.get("choices") or []
        if self.preserve_reasoning:
            self._standardize_content(message, choices[0].get("delta") or {} if choices else {})
        return generation

    def _create_chat_result(self, response, generation_info=None):
        """非流式响应也保留同一供应商字段。"""
        result = super()._create_chat_result(response, generation_info)
        if self.preserve_reasoning:
            data = response if isinstance(response, dict) else response.model_dump()
            for generation, choice in zip(result.generations, data.get("choices", []), strict=True):
                self._standardize_content(generation.message, choice.get("message") or {})
        return result

    def _standardize_content(self, message: AIMessage, raw: dict) -> None:
        """在模型边界生成标准块，文本不经过裁剪以便原样续答。"""
        blocks = message.content_blocks
        for block in blocks:
            if block["type"] == "text":
                block["index"] = "lc_text"
        if reasoning := reasoning_content(raw):
            # lc_ 索引支持 v1 多片合并，并与整数工具索引隔离。
            blocks.insert(0, {"type": "reasoning", "reasoning": reasoning, "index": "lc_reasoning"})
        message.content = blocks
        message.response_metadata["output_version"] = "v1"

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """支持推理的供应商在工具续答时接收完整原文。"""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "messages" not in payload:
            return payload
        originals = self._convert_input(input_).to_messages()
        _sanitize_wire_invalid_tool_calls(payload["messages"], originals)
        if self.preserve_reasoning:
            for original, wire in zip(originals, payload["messages"], strict=True):
                if isinstance(original, AIMessage):
                    content = wire.get("content")
                    if isinstance(content, list) and all(block.get("type") == "text" for block in content):
                        wire["content"] = "".join(block["text"] for block in content) or None
                    reasoning = "".join(
                        block.get("reasoning", "") for block in original.content_blocks if block["type"] == "reasoning"
                    ) or reasoning_content(original.additional_kwargs)
                    if reasoning:
                        wire["reasoning_content"] = reasoning
        return payload


class GeneralResponse:
    def __init__(self, content):
        self.content = content
        self.is_full = False


class LangChainChatAdapter:
    def __init__(self, model, *, model_name: str, base_url: str | None = None, info: dict | None = None):
        self.model = model
        self.model_name = model_name
        self.base_url = base_url
        self.info = info or {}

    @staticmethod
    def _normalize_messages(message):
        if isinstance(message, str):
            return message
        return convert_to_messages(message)

    async def call(self, message, stream=False):
        messages = self._normalize_messages(message)
        try:
            if stream:
                return self._stream_response(messages)
            response = await self.model.ainvoke(messages)
            return GeneralResponse(response.text)
        except Exception as e:
            err = f"Error calling model: {e}, URL: {self.base_url}, Model: {self.model_name}"
            logger.error(err)
            raise Exception(err)

    async def _stream_response(self, messages):
        async for chunk in self.model.astream(messages):
            if chunk.text:
                yield GeneralResponse(chunk.text)


def _langchain_kwargs(provider_type: str, kwargs: dict) -> dict:
    langchain_kwargs = dict(kwargs.pop("model_params", {}) or {})
    langchain_kwargs.update(kwargs)
    if provider_type == "anthropic" and "max_completion_tokens" in langchain_kwargs:
        langchain_kwargs.setdefault("max_tokens", langchain_kwargs.pop("max_completion_tokens"))
    return langchain_kwargs


def select_model(model_spec: str, **kwargs) -> LangChainChatAdapter:
    if not model_spec:
        raise ValueError("model_spec 不能为空")

    info = model_cache.get_model_info(model_spec)
    if not info:
        available = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available[:10]]
        raise ValueError(f"未找到模型: '{model_spec}'。可用聊天模型 ({len(available)}): {available_ids}")

    if info.model_type != "chat":
        raise ValueError(f"Model {model_spec} is not a chat model (type={info.model_type})")

    logger.info(f"Selecting model: {model_spec} (provider_type={info.provider_type})")

    model = load_chat_model(
        model_spec,
        **_langchain_kwargs(info.provider_type, kwargs),
    )
    return LangChainChatAdapter(
        model,
        model_name=info.model_id,
        base_url=info.base_url,
        info={"provider_type": info.provider_type, "provider_id": info.provider_id},
    )


def _sanitize_wire_invalid_tool_calls(messages: list[dict], originals: list) -> None:
    """把 wire 消息里来自 invalid_tool_calls 的截断 function 转成失败反馈。

    langchain_openai 把 AIMessage.invalid_tool_calls 序列化成 type:"function"、
    arguments 为截断 JSON 的 tool_call，DeepSeek 等接口因此报参数解析失败。这里在
    发送边界按 tool_call id 移除这些截断调用，并在 content 里给模型明确的 text 反馈，
    而不是发出畸形参数。原始 checkpoint（LangChain 消息对象）不动。
    """
    for wire, original in zip(messages, originals, strict=True):
        if not isinstance(original, AIMessage):
            continue
        invalid = list(original.invalid_tool_calls or [])
        if not invalid:
            continue
        invalid_ids = {call.get("id") for call in invalid if call.get("id")}
        tool_calls = wire.get("tool_calls")
        if isinstance(tool_calls, list):
            kept = [call for call in tool_calls if call.get("id") not in invalid_ids]
            if kept:
                wire["tool_calls"] = kept
            else:
                wire.pop("tool_calls", None)
        feedback = "；".join(
            f"[工具调用失败] {call.get('name') or 'unknown'}: {call.get('error') or 'arguments malformed or truncated'}"
            for call in invalid
        )
        content = wire.get("content")
        if isinstance(content, list):
            content.append({"type": "text", "text": feedback})
        elif content:
            wire["content"] = [{"type": "text", "text": content}, {"type": "text", "text": feedback}]
        else:
            wire["content"] = [{"type": "text", "text": feedback}]


if __name__ == "__main__":
    pass
