from yuxi.agents.base import BaseAgent
from yuxi.agents.buildin.chatbot.graph import ChatbotAgent
from yuxi.agents.buildin.subagent.graph import SubAgentBackend

BUILTIN_BACKENDS: dict[str, type[BaseAgent]] = {
    "ChatbotAgent": ChatbotAgent,
    "SubAgentBackend": SubAgentBackend,
}


class AgentBackendNotFoundError(ValueError):
    """配置引用了未注册的执行后端。"""


def get_agent_backend(backend_id: str) -> BaseAgent:
    """按稳定标识创建独立的轻量执行后端。"""
    try:
        backend_class = BUILTIN_BACKENDS[backend_id]
    except KeyError:
        raise AgentBackendNotFoundError(f"智能体后端 {backend_id} 不存在") from None
    return backend_class()


async def list_agent_backend_info() -> list[dict]:
    """查询已注册后端的基础信息，ID 由注册字典拥有。"""
    return [
        {**await get_agent_backend(backend_id).get_info(include_configurable_items=False), "backend_id": backend_id}
        for backend_id in BUILTIN_BACKENDS
    ]
