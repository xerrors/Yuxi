"""聊天模型加载、供应商协议适配与通用调用入口。"""

import base64
import hashlib
import hmac
import os
import time
from contextlib import aclosing, closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from uuid import uuid4

import httpx
from langchain.chat_models import BaseChatModel
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk, convert_to_messages
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr

from yuxi import get_version
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.models.providers.cache import USER_UID_SIGNATURE_SECRET_ENV, ModelInfo, model_cache
from yuxi.modules.models.utils import get_docker_safe_url

USER_UID_HEADER = "x-yuxi-uid"
USER_UID_TIMESTAMP_HEADER = "x-yuxi-uid-ts"
USER_UID_SIGNATURE_HEADER = "x-yuxi-uid-sig"
CHAT_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, write=30.0, read=120.0, pool=10.0)
CHAT_COMPLETIONS_TEXT_INDEX = "lc_text"
CHAT_COMPLETIONS_DIRECT_TEXT_INDEX = "lc_direct_text"
CHAT_COMPLETIONS_REASONING_INDEX = "lc_reasoning"


def load_chat_model(
    fully_specified_name: str | None,
    *,
    session_id: str | None = None,
    uid: str | None = None,
    **kwargs,
) -> BaseChatModel:
    """为 Agent runtime 返回完整 LangChain 模型，支持工具绑定、消息与流式协议。"""
    fully_specified_name = resolve_chat_model_spec(fully_specified_name)
    info = _require_chat_model_info(fully_specified_name)

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

    signing_uid = _resolve_user_uid(info, uid)

    kwargs["metadata"] = {
        **(kwargs.pop("metadata", None) or {}),
        "yuxi_provider_id": info.provider_id,
        "yuxi_provider_type": info.provider_type,
        "yuxi_model_id": info.model_id,
        "yuxi_model_spec": info.spec,
    }

    logger.debug(f"Loading model {fully_specified_name} with provider_type={info.provider_type}")

    if info.provider_type not in {"gemini", "anthropic"}:
        # 按网络阶段限制停顿，不为持续输出的长回答设置总时限。
        if "timeout" not in kwargs and "request_timeout" not in kwargs:
            kwargs["timeout"] = CHAT_HTTP_TIMEOUT

    if info.provider_type == "anthropic":
        return ChatAnthropicAdapter(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            user_uid=signing_uid,
            **kwargs,
        )
    if info.provider_type == "gemini":
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
        user_uid=signing_uid,
        replay_reasoning=info.provider_id
        in {
            "alibaba",
            "alibaba-cn",
            "deepseek",
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


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方显式 fallback；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


class ChatAnthropicAdapter(ChatAnthropic):
    """anthropic 家族在每次发送边界重签用户 UID 头，其余行为由上游负责。"""

    user_uid: str | None = Field(default=None, exclude=True)

    @cached_property
    def _client(self):
        """SDK 请求选项支持阶段超时，LangChain 的客户端缓存只接受标量。"""
        client = super()._client
        if "default_request_timeout" not in self.model_fields_set:
            client.timeout = CHAT_HTTP_TIMEOUT
        return client

    @cached_property
    def _async_client(self):
        """异步请求使用相同的阶段超时。"""
        client = super()._async_client
        if "default_request_timeout" not in self.model_fields_set:
            client.timeout = CHAT_HTTP_TIMEOUT
        return client

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """流式与非流式载荷都附带当次现算的签名。"""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        _attach_user_uid_headers(payload, self.user_uid)
        return payload


class ChatCompletionsAdapter(ChatOpenAI):
    """在解析边界保留扩展字段，HTTP、重试和工具绑定由上游负责。"""

    replay_reasoning: bool = Field(default=False, exclude=True)
    # 开启 UID 头时的用户 uid；签名在每次发送边界现算，不缓存时间戳。
    user_uid: str | None = Field(default=None, exclude=True)

    def _stream(self, *args, **kwargs):
        """按单次流式请求归并 Chat Completions 的思考来源。"""
        state = _ChatCompletionsStream()
        with closing(super()._stream(*args, **kwargs)) as chunks:
            while True:
                with _stream_content_scope(state):
                    chunk = next(chunks, None)
                if chunk is None:
                    return
                yield chunk

    async def _astream(self, *args, **kwargs):
        """并发调用各自持有思考与文本的合并状态。"""
        state = _ChatCompletionsStream()
        async with aclosing(super()._astream(*args, **kwargs)) as chunks:
            while True:
                with _stream_content_scope(state):
                    chunk = await anext(chunks, None)
                if chunk is None:
                    return
                yield chunk

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        """在上游丢弃扩展字段前读取推理，并归一化工具续片。"""
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation is None or not isinstance(generation.message, AIMessageChunk):
            return generation
        message = generation.message
        normalize_tool_call_chunks(message)
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        delta = choices[0]["delta"] if choices else {}
        self._standardize_content(message, _reasoning_content(delta), stream=_stream_content.get())
        return generation

    def _create_chat_result(self, response, generation_info=None):
        """非流式响应也保留同一供应商字段。"""
        result = super()._create_chat_result(response, generation_info)
        choices = response["choices"] if isinstance(response, dict) else response.choices
        for generation, choice in zip(result.generations, choices, strict=True):
            raw = choice["message"] if isinstance(choice, dict) else choice.message.model_dump(include={"reasoning_content", "reasoning"})
            reasoning = _reasoning_content(raw)
            if reasoning or self.replay_reasoning:
                self._standardize_content(generation.message, reasoning)
        return result

    def _standardize_content(self, message: AIMessage, reasoning: str, *, stream: "_ChatCompletionsStream | None" = None) -> None:
        """在模型边界生成标准块，文本不经过裁剪以便原样续答。"""
        blocks = message.content_blocks
        # 流式正文和思考可分帧到达，所有续片使用可合并的标准块。
        if isinstance(message.content, str) and message.content:
            blocks[0]["index"] = CHAT_COMPLETIONS_TEXT_INDEX if isinstance(message, AIMessageChunk) else CHAT_COMPLETIONS_DIRECT_TEXT_INDEX
        elif isinstance(message, AIMessageChunk) and isinstance(message.content, list):
            blocks = _structured_stream_blocks(blocks, message.tool_call_chunks)
        blocks = [
            block for block in blocks if not _is_reasoning_block(block) or isinstance(block.get("reasoning"), str) and block["reasoning"]
        ]

        if stream is not None and stream.uses_fallback is not None:
            uses_fallback = stream.uses_fallback
        else:
            has_standard = any(_is_reasoning_block(block) for block in blocks)
            uses_fallback = bool(reasoning) and not has_standard
            if stream is not None and (has_standard or reasoning):
                stream.uses_fallback = uses_fallback

        if uses_fallback:
            blocks = [block for block in blocks if not _is_reasoning_block(block)]
            if reasoning:
                blocks.insert(0, {"type": "reasoning", "reasoning": reasoning, "index": CHAT_COMPLETIONS_REASONING_INDEX})
        elif reasoning and self.replay_reasoning:
            # 供应商的回传字段可以与显示用的标准块不同。
            message.additional_kwargs["reasoning_content"] = reasoning

        message.content = stream.order_text(blocks) if stream is not None else blocks
        message.response_metadata["output_version"] = "v1"

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """在发送边界还原推理，并将纯文本工具结果序列化为字符串。"""
        originals = self._convert_input(input_).to_messages()
        messages = originals
        if self._use_responses_api({**self._default_params, **kwargs}):
            messages = [_responses_history_message(message) for message in originals]
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        _attach_user_uid_headers(payload, self.user_uid)
        if "messages" not in payload:
            if isinstance(items := payload.get("input"), list):
                # SDK 可把其它协议的思考转为缺少原生 ID 的 Responses item。
                payload["input"] = [
                    item for item in items if not isinstance(item, dict) or item.get("type") != "reasoning" or item.get("id")
                ]
            return payload
        self._restore_wire_assistant_messages(originals, payload["messages"])
        _sanitize_wire_invalid_tool_calls(payload["messages"], originals)
        # MCP 等工具返回文本块；部分 Chat Completions 网关只接受字符串工具消息。
        for message in payload["messages"]:
            content = message.get("content")
            if message.get("role") == "tool" and isinstance(content, list):
                if all(isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str) for block in content):
                    message["content"] = "\n".join(block["text"] for block in content)
        return payload

    def _restore_wire_assistant_messages(self, originals: list, wire_messages: list[dict]) -> None:
        """还原 Chat Completions 字符串正文，按供应商协议回填推理。"""
        for original, wire in zip(originals, wire_messages, strict=True):
            if not isinstance(original, AIMessage):
                continue
            content = wire.get("content")
            if isinstance(content, list):
                wire["content"] = self._restore_wire_assistant_content(original, content, has_tool_calls=bool(wire.get("tool_calls")))
            if not self.replay_reasoning:
                continue
            reasoning = _reasoning_content(original.additional_kwargs) or "".join(
                block["reasoning"]
                for block in original.content_blocks
                if _is_reasoning_block(block) and isinstance(block.get("reasoning"), str)
            )
            if reasoning:
                wire["reasoning_content"] = reasoning

    def _restore_wire_assistant_content(self, original: AIMessage, content: list, *, has_tool_calls: bool) -> str | list | None:
        """恢复正文的 wire 表示，工具片段由单独的 tool_calls 交付。"""
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_call_chunk":
                    continue
                if block.get("type") == "non_standard":
                    value = block.get("value")
                    if isinstance(value, dict) and value.get("type") == "refusal":
                        block = value
            text_parts.append(block)
        if self.replay_reasoning and all(isinstance(block, dict) and block.get("type") == "text" for block in text_parts):
            return "".join(block["text"] for block in text_parts) or None
        # v3 生成的 AIMessage 原本以文本块序列化，直接 SDK 调用的标量才恢复为字符串。
        scalar_index = CHAT_COMPLETIONS_TEXT_INDEX if isinstance(original, AIMessageChunk) else CHAT_COMPLETIONS_DIRECT_TEXT_INDEX
        if (
            len(text_parts) == 1
            and isinstance(text_parts[0], dict)
            and text_parts[0].keys() == {"type", "text"}
            and text_parts[0]["type"] == "text"
            and any(isinstance(block, dict) and block.get("index") == scalar_index for block in original.content)
        ):
            return text_parts[0]["text"]
        if not text_parts:
            if content and has_tool_calls:
                return None
            if (
                content
                or isinstance(original, AIMessageChunk)
                and not original.content
                or original.content
                and all(_is_reasoning_block(block) and block.get("index") == CHAT_COMPLETIONS_REASONING_INDEX for block in original.content)
            ):
                return ""
        return text_parts


@dataclass
class _ChatCompletionsStream:
    """按模型响应选择可实时输出的思考来源，并保留混合正文的顺序。"""

    uses_fallback: bool | None = None
    text_span: int = 0
    text_source: str | None = None

    def order_text(self, blocks: list[dict]) -> list[dict]:
        """正文切换表示时开启新片段，连续帧仍可按索引合并。"""
        unindexed_text_count = sum(
            1 for block in blocks if isinstance(block, dict) and block.get("type") == "text" and block.get("index") is None
        )
        for position, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            index = block.get("index")
            if index is None:
                source = "unindexed"
                index = "lc_unindexed_text"
            elif index == CHAT_COMPLETIONS_TEXT_INDEX:
                source = "scalar"
            else:
                source = "indexed"
            if (self.text_source is not None and self.text_source != source) or (source == "unindexed" and unindexed_text_count > 1):
                self.text_span += 1
            self.text_source = source
            index = f"{index}_{self.text_span}" if self.text_span else index
            if index != block.get("index"):
                blocks[position] = {**block, "index": index}
        return blocks


def _structured_stream_blocks(blocks: list[dict], tool_chunks: list[dict]) -> list[dict]:
    """为内容块分配独立索引，并补齐 SDK 在结构化帧中省略的工具片段。"""
    content = []
    tool_ids = set()
    tool_indices = set()
    for block in blocks:
        if isinstance(block, dict):
            kind = block.get("type")
            index = block.get("index")
            if kind in {"tool_call", "tool_call_chunk"}:
                if block.get("id"):
                    tool_ids.add(block["id"])
                if index is not None:
                    tool_indices.add(index)
            elif index is not None:
                block = {**block, "index": f"lc_block_{kind}_{index}"}
        content.append(block)
    content.extend(tool for tool in tool_chunks if tool.get("index") not in tool_indices and tool.get("id") not in tool_ids)
    return content


def _is_reasoning_block(block: object) -> bool:
    """识别标准思考块。"""
    return isinstance(block, dict) and block.get("type") == "reasoning"


def _responses_history_message(message):
    """保留原生 Responses 项目，移除其它协议历史中的思考与正文 ID。"""
    if not isinstance(message, AIMessage):
        return message
    metadata = message.response_metadata
    response_id = metadata.get("id")
    has_response_id = isinstance(response_id, str) and response_id.startswith("resp_")
    # 旧版 Responses 使用配对的 message/response ID；Chat Completions SDK 会记录 finish_reason。
    legacy_response = has_response_id and isinstance(message.id, str) and message.id.startswith("msg_") and "finish_reason" not in metadata
    if metadata.get("object") == "response" or metadata.get("output_version") == "responses/v1" or legacy_response:
        return message

    updates = {}
    if has_response_id:
        updates["response_metadata"] = {key: value for key, value in metadata.items() if key != "id"}
    if isinstance(message.content, list):
        blocks = _responses_foreign_blocks(message.content)
        if blocks != message.content:
            updates["content"] = blocks

    # 旧 LangChain 会从这些字段重建 Responses 项。
    extras = dict(message.additional_kwargs)
    extras.pop("reasoning", None)
    if isinstance(extras.get("tool_outputs"), list):
        extras["tool_outputs"] = _responses_foreign_blocks(extras["tool_outputs"])
    if extras != message.additional_kwargs:
        updates["additional_kwargs"] = extras
    if isinstance(message.id, str) and message.id.startswith("msg_"):
        updates["id"] = None
    return message.model_copy(update=updates) if updates else message


def _responses_foreign_blocks(blocks: list) -> list:
    """检查 SDK 会展开的内容，保留普通文本和工具关联。"""
    parts = []
    for block in blocks:
        wrapper = isinstance(block, dict) and block.get("type") == "non_standard"
        part = block.get("value") if wrapper else block
        if _is_reasoning_block(part):
            continue
        if isinstance(part, dict) and part.get("type") in {"text", "output_text", "refusal"} and "id" in part:
            part = {key: value for key, value in part.items() if key != "id"}
            block = {**block, "value": part} if wrapper else part
        parts.append(block)
    return parts


_stream_content = ContextVar[_ChatCompletionsStream | None]("chat_completions_stream_content", default=None)


@contextmanager
def _stream_content_scope(state: _ChatCompletionsStream):
    """仅在 SDK 解析当前帧时设置状态，在向调用者产出前还原。"""
    token = _stream_content.set(state)
    try:
        yield
    finally:
        _stream_content.reset(token)


def normalize_tool_call_chunks(message: AIMessageChunk) -> None:
    """将续片空串改为 None，防止 v3 累积覆盖工具首片的名称与 ID。"""
    for tool in message.tool_call_chunks:
        for key in ("name", "id"):
            if tool.get(key) == "":
                tool[key] = None


class GeneralResponse:
    def __init__(self, content):
        self.content = content


class LangChainChatAdapter:
    def __init__(self, model, *, model_name: str, base_url: str | None = None, info: dict | None = None):
        self.model = model
        self.model_name = model_name
        self.base_url = base_url
        self.info = info or {}

    async def call(self, message, stream=False):
        """文本任务入口；保留供应商异常类型供调用方判断与重试。"""
        messages = self._normalize_messages(message)
        try:
            if stream:
                return self._stream_response(messages)
            response = await self.model.ainvoke(messages)
            return GeneralResponse(response.text)
        except Exception as e:
            err = f"Error calling model: {e}, URL: {self.base_url}, Model: {self.model_name}"
            logger.error(err)
            raise

    @staticmethod
    def _normalize_messages(message):
        if isinstance(message, str):
            return message
        return convert_to_messages(message)

    async def _stream_response(self, messages):
        async for chunk in self.model.astream(messages):
            if chunk.text:
                yield GeneralResponse(chunk.text)


def select_model(model_spec: str, **kwargs) -> LangChainChatAdapter:
    """为图抽取、评估等文本任务装配只消费 content 的轻量适配器。"""
    if not model_spec:
        raise ValueError("model_spec 不能为空")

    info = _require_chat_model_info(model_spec)

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


def _require_chat_model_info(spec: str) -> ModelInfo:
    """读取聊天模型信息；spec 未配置或类型不是 chat 时显式失败。"""
    info = model_cache.get_model_info(spec)
    if not info:
        all_specs = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in all_specs[:10]]
        raise ValueError(f"未找到模型: '{spec}'。可用聊天模型 ({len(all_specs)}): {available_ids}")
    if info.model_type != "chat":
        raise ValueError(f"Model {spec} is not a chat model (type={info.model_type})")
    return info


def _resolve_user_uid(info, uid: str | None) -> str | None:
    """解析需要签名的 uid：开关未开启、未提供 uid 或 uid 为空白时返回 None。

    uid 含 HTTP 头非法字符、或签名密钥读不到时在构图期就显式失败，不等到发送请求才暴露。
    """
    if not info.include_user_uid:
        return None
    if info.provider_type == "gemini":
        raise ValueError("Gemini 供应商暂不支持请求携带用户 ID")
    header_uid = (uid or "").strip()
    if not header_uid:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in header_uid):
        raise ValueError(f"uid 含 HTTP 头非法字符，不能注入 {USER_UID_HEADER}")
    _require_user_uid_secret()
    return header_uid


def _require_user_uid_secret() -> str:
    """读取固定 UID 签名密钥；缺失即显式失败，不发出未签名请求。"""
    secret = (os.getenv(USER_UID_SIGNATURE_SECRET_ENV) or "").strip()
    if not secret:
        raise ValueError(f"UID 签名密钥环境变量 {USER_UID_SIGNATURE_SECRET_ENV} 未设置或为空，已拒绝发送未签名的 {USER_UID_HEADER}")
    return secret


def _attach_user_uid_headers(payload: dict, uid: str | None) -> None:
    """把每次现算的 UID 签名头写进出站载荷，调用方自带的请求头保留。"""
    if not uid:
        return
    payload["extra_headers"] = {**(payload.get("extra_headers") or {}), **_build_user_uid_headers(uid)}


def _build_user_uid_headers(uid: str) -> dict[str, str]:
    """为单次出站请求生成新鲜的 UID 签名头。

    模型实例由主 Agent 和摘要长期复用，而网关按 ±300 秒窗口验签，因此时间戳与签名
    只能在每次发送边界现算，不能在构图时固定。密钥读不到时同样显式失败。
    """
    secret = _require_user_uid_secret()
    timestamp = str(int(time.time()))
    return {
        USER_UID_HEADER: uid,
        USER_UID_TIMESTAMP_HEADER: timestamp,
        USER_UID_SIGNATURE_HEADER: _sign_user_uid(secret, uid, timestamp),
    }


def _sign_user_uid(secret: str, uid: str, timestamp: str) -> str:
    """对 uid 与时间戳计算 HMAC-SHA256 签名；规范化消息即外部网关的验签契约。"""
    message = f"uid={uid}\nts={timestamp}"
    return base64.b64encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()).decode()


def _reasoning_content(message: dict) -> str:
    """读取供应商原始推理文本，保持空白与工具续答输入不变。"""
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _langchain_kwargs(provider_type: str, kwargs: dict) -> dict:
    langchain_kwargs = dict(kwargs.pop("model_params", {}) or {})
    langchain_kwargs.update(kwargs)
    if provider_type == "anthropic" and "max_completion_tokens" in langchain_kwargs:
        langchain_kwargs.setdefault("max_tokens", langchain_kwargs.pop("max_completion_tokens"))
    return langchain_kwargs


def _sanitize_wire_invalid_tool_calls(messages: list[dict], originals: list) -> None:
    """把 wire 消息里来自 invalid_tool_calls 的截断 function 转成失败反馈。

    langchain_openai 把 AIMessage.invalid_tool_calls 序列化成 type:"function"、
    arguments 为截断 JSON 的 tool_call，DeepSeek 等接口因此报参数解析失败。这里在
    发送边界移除截断调用及同批补齐结果，在 content 中保留失败反馈。
    合法调用、其它孤儿结果和原始 checkpoint（LangChain 消息对象）不动。
    """
    filtered = []
    invalid_ids: set[str] = set()
    for wire, original in zip(messages, originals, strict=True):
        # 只过滤同批已删除调用的补齐结果，后续 AI 复用相同 ID 仍是独立调用。
        if wire.get("role") == "tool" and wire.get("tool_call_id") in invalid_ids:
            continue
        filtered.append(wire)
        if not isinstance(original, AIMessage):
            if wire.get("role") != "tool":
                invalid_ids = set()
            continue
        invalid = list(original.invalid_tool_calls or [])
        invalid_ids = {call.get("id") for call in invalid if call.get("id")}
        if not invalid:
            continue
        tool_calls = wire.get("tool_calls")
        if isinstance(tool_calls, list):
            kept = [call for call in tool_calls if call.get("id") not in invalid_ids]
            if kept:
                wire["tool_calls"] = kept
            else:
                wire.pop("tool_calls", None)
        feedback = "；".join(
            f"[工具调用失败] {call.get('name') or 'unknown'}: {call.get('error') or 'arguments malformed or truncated'}" for call in invalid
        )
        content = wire.get("content")
        if isinstance(content, list):
            content.append({"type": "text", "text": feedback})
        elif content:
            wire["content"] = [{"type": "text", "text": content}, {"type": "text", "text": feedback}]
        else:
            wire["content"] = [{"type": "text", "text": feedback}]
    messages[:] = filtered
