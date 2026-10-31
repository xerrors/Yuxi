"""普通智能助手的能力组合。"""

from langchain.agents.middleware import AgentMiddleware

from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.runtime.agent_backends.chatbot.prompt import build_prompt_with_context
from yuxi.modules.agents.runtime.agent_backends.chatbot.state import ChatBotState
from yuxi.modules.agents.runtime.base import BaseAgent
from yuxi.modules.agents.runtime.graph import build_agent_graph
from yuxi.modules.agents.runtime.middlewares.cooperation import create_cooperation_middleware
from yuxi.modules.extensions.tools.builtin import ask_user_question, mark_for_improvement


class ChatbotAgent(BaseAgent):
    """普通对话支持用户选择的工具、Skills 和协作树。"""

    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    context_schema = ChatBotContext

    async def get_graph(self, *, context, checkpoint_only: bool = False, **kwargs):
        """声明普通助手的提示词、状态与协作树能力。"""
        basic_tools = AgentMiddleware()
        basic_tools.tools = [ask_user_question, mark_for_improvement]
        return await build_agent_graph(
            context,
            prompt_builder=build_prompt_with_context,
            middleware=(basic_tools, create_cooperation_middleware(context)),
            state_schema=ChatBotState,
            checkpoint_only=checkpoint_only,
        )
