from langchain_core.messages import convert_to_messages

from yuxi.agents.models import load_chat_model
from yuxi.models.providers.cache import model_cache
from yuxi.utils import logger


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


async def test_chat_model_status_by_spec(spec: str) -> dict:
    try:
        logger.debug(f"Testing model status by spec: {spec}")
        model = select_model(model_spec=spec)

        test_messages = [{"role": "user", "content": "Say 1"}]
        response = await model.call(test_messages, stream=False)

        if response and response.content:
            return {"spec": spec, "status": "available", "message": "连接正常"}
        return {"spec": spec, "status": "unavailable", "message": "响应无效"}

    except Exception as e:
        logger.error(f"测试模型状态失败 {spec}: {e}")
        return {"spec": spec, "status": "error", "message": str(e)}


if __name__ == "__main__":
    pass
