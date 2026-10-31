"""个人助理的显式用户沟通。"""

from typing import Annotated

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import StructuredTool
from pydantic import Field


class PersonalAssistantMiddleware(AgentMiddleware):
    """工具成功结果由现有消息记录和事件链保存，不创建平行消息表。"""

    def __init__(self):
        """只注册显式用户消息工具。"""
        super().__init__()

        async def send_to_user(content: Annotated[str, Field(min_length=1, max_length=32000)]) -> dict:
            """向用户发送消息；普通 assistant content 不会出现在主聊天区。"""
            if not content.strip():
                raise ValueError("用户消息不能为空")
            return {"content": content}

        self.tools = [StructuredTool.from_function(coroutine=send_to_user)]
