"""验证 BaseAgent 的构图契约在实例化期生效。"""

import pytest

from yuxi.modules.agents.runtime.base import BaseAgent


def test_incomplete_agent_backend_cannot_be_instantiated():
    """漏实现 get_graph 的子类必须在实例化时失败，而不是等到构图时返回 None。"""

    class IncompleteAgent(BaseAgent):
        """只声明继承关系，不实现构图入口。"""

    with pytest.raises(TypeError, match=r"abstract method 'get_graph'"):
        IncompleteAgent()
