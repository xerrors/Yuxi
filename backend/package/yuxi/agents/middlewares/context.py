"""通用的 Context 相关中间件"""

from collections.abc import Callable

from langchain.agents.middleware import ModelRequest, ModelResponse, dynamic_prompt, wrap_model_call

from yuxi.agents import load_chat_model
from yuxi.utils import logger


@dynamic_prompt
def context_aware_prompt(request: ModelRequest) -> str:
    """从 runtime context 动态生成系统提示词"""
    return request.runtime.context.system_prompt


@wrap_model_call
async def context_based_model(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """从 runtime context 动态选择模型"""
    model_spec = request.runtime.context.model
    model = load_chat_model(model_spec)

    request = request.override(model=model)
    logger.debug(f"Using model {model_spec} for request {request.messages[-1].content[:200]}")
    return await handler(request)
