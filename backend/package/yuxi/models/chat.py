"""聊天模型加载、供应商协议适配与通用调用入口。"""

from typing import Any
from uuid import uuid4

from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage, convert_to_messages
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


def _sanitize_invalid_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """净化消息里的 invalid_tool_call，规避 DeepSeek 等接口的两类报错：

    1. unknown variant invalid_tool_call —— LLM 生成无效工具调用（JSON 参数截断/格式
       错误）时，LangChain 记录 invalid_tool_call，序列化后以该变体发往模型。
    2. role='tool' must be a response to a preceding message with 'tool_calls' ——
       孤儿 ToolMessage（tool_call_id 已无对应 tool_calls）。

    处理：invalid_tool_calls 属性清空、additional_kwargs["tool_calls"] 收敛到合法子集、
    content 数组里的 invalid_tool_call block 转 text、孤儿 ToolMessage 删除。

    返回新消息对象，不原地修改入参：这些消息来自共享的 graph state / checkpoint。
    """
    sanitized: list[BaseMessage] = []
    valid_ids: set[str] = set()

    for message in messages:
        if not isinstance(message, AIMessage):
            sanitized.append(message)
            continue

        valid_calls = list(message.tool_calls or [])
        message_valid_ids = {tc["id"] for tc in valid_calls if tc.get("id")}
        valid_ids |= message_valid_ids

        updates: dict[str, Any] = {}

        invalid_calls = getattr(message, "invalid_tool_calls", None)
        if invalid_calls:
            logger.warning(f"[invalid_tool_call 过滤] 清空 {len(invalid_calls)} 条 invalid_tool_calls")
            updates["invalid_tool_calls"] = []

        # additional_kwargs["tool_calls"] 是 OpenAI 响应的原始 wire 表示。langchain_openai
        # 的 _convert_message_to_dict 在 tool_calls 与 invalid_tool_calls 都为空时会回退用它
        # 序列化——若不同步收敛，清空解析字段反而会让畸形调用被原样重发。
        extra_tool_calls = message.additional_kwargs.get("tool_calls")
        if isinstance(extra_tool_calls, list):
            kept = [call for call in extra_tool_calls if isinstance(call, dict) and call.get("id") in message_valid_ids]
            if len(kept) != len(extra_tool_calls):
                logger.warning(
                    f"[invalid_tool_call 过滤] additional_kwargs 里移除 "
                    f"{len(extra_tool_calls) - len(kept)} 条原始 tool_call"
                )
                updates["additional_kwargs"] = {**message.additional_kwargs, "tool_calls": kept}

        if isinstance(message.content, list):
            new_content: list[Any] = []
            content_changed = False
            for block in message.content:
                if isinstance(block, dict) and block.get("type") == "invalid_tool_call":
                    name = block.get("name") or "unknown"
                    error = block.get("error") or "arguments malformed or truncated"
                    logger.warning(f"[invalid_tool_call 过滤] content 里的 block 转 text: {name}: {error}")
                    new_content.append({"type": "text", "text": f"[工具调用失败] {name}: {error}"})
                    content_changed = True
                else:
                    new_content.append(block)
            if content_changed:
                updates["content"] = new_content

        sanitized.append(message.model_copy(update=updates) if updates else message)

    filtered: list[BaseMessage] = []
    for message in sanitized:
        if isinstance(message, ToolMessage) and message.tool_call_id not in valid_ids:
            logger.warning(f"[invalid_tool_call 过滤] 删除孤儿 ToolMessage: {message.tool_call_id}")
            continue
        filtered.append(message)
    return filtered


class _InvalidToolCallFilterMixin:
    """在消息发送前清空 invalid_tool_calls，规避 DeepSeek 等接口不支持该变体。"""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return super()._generate(_sanitize_invalid_tool_calls(messages), stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await super()._agenerate(
            _sanitize_invalid_tool_calls(messages), stop=stop, run_manager=run_manager, **kwargs
        )

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        return super()._stream(_sanitize_invalid_tool_calls(messages), stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        async for chunk in super()._astream(
            _sanitize_invalid_tool_calls(messages), stop=stop, run_manager=run_manager, **kwargs
        ):
            yield chunk


def _wrap_model(cls: type) -> type:
    """动态创建带 invalid_tool_call 过滤的模型子类。"""
    return type(f"Filtered{cls.__name__}", (_InvalidToolCallFilterMixin, cls), {})


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

        return _wrap_model(ChatAnthropic)(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            **kwargs,
        )
    if info.provider_type == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return _wrap_model(ChatGoogleGenerativeAI)(
            model=info.model_id,
            google_api_key=SecretStr(api_key),
            **kwargs,
        )

    return _wrap_model(ChatCompletionsAdapter)(
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
        if self.preserve_reasoning and "messages" in payload:
            originals = self._convert_input(input_).to_messages()
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


if __name__ == "__main__":
    pass
