import os
import traceback

from langchain.chat_models import BaseChatModel, init_chat_model
from pydantic import SecretStr

from yuxi import config
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger


def load_chat_model(fully_specified_name: str, **kwargs) -> BaseChatModel:
    """
    Load a chat model from a fully specified name.
    """
    provider, model = fully_specified_name.split("/", maxsplit=1)

    assert provider != "custom", "[弃用] 自定义模型已移除，请在 yuxi/config/static/models.py 中配置"

    model_info = config.model_names.get(provider)
    if not model_info:
        raise ValueError(f"Unknown model provider: {provider}")

    env_var = model_info.env

    api_key = os.getenv(env_var) or env_var

    base_url = get_docker_safe_url(model_info.base_url)

    if provider in ["openai", "deepseek"]:
        model_spec = f"{provider}:{model}"
        logger.debug(f"[offical] Loading model {model_spec} with kwargs {kwargs}")
        return init_chat_model(model_spec, **kwargs)

    elif provider in ["dashscope"]:
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(
            model=model,
            api_key=SecretStr(api_key),
            base_url=base_url,
            api_base=base_url,
            stream_usage=True,
        )

    else:
        try:  # 其他模型，默认使用OpenAIBase, like openai, zhipuai
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model,
                api_key=SecretStr(api_key),
                base_url=base_url,
                stream_usage=True,
            )
        except Exception as e:
            raise ValueError(f"Model provider {provider} load failed, {e} \n {traceback.format_exc()}")
