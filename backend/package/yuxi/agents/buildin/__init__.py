import asyncio
import importlib
import inspect
from pathlib import Path

from server.utils.singleton import SingletonMeta
from yuxi.agents.base import BaseAgent
from yuxi.utils import logger


class AgentManager(metaclass=SingletonMeta):
    def __init__(self):
        self._classes = {}
        self._instances = {}  # 存储已创建的 agent 实例

    def register_agent(self, agent_class):
        self._classes[agent_class.__name__] = agent_class

    def init_all_agents(self):
        for agent_id in self._classes.keys():
            self.get_agent(agent_id)

    def get_agent(self, agent_id, reload=False, reload_graph=False, **kwargs):
        # 检查是否已经创建了该 agent 的实例
        if reload or agent_id not in self._instances:
            agent_class = self._classes[agent_id]
            self._instances[agent_id] = agent_class()

        # 如果仅需要重新加载 graph，则清空 graph 缓存
        if reload_graph and agent_id in self._instances:
            self._instances[agent_id].reload_graph()

        return self._instances[agent_id]

    def get_agents(self):
        return list(self._instances.values())

    async def reload_all(self):
        for agent_id in self._classes.keys():
            self.get_agent(agent_id, reload=True)

    async def get_agents_info(self, include_configurable_items: bool = True):
        agents = self.get_agents()
        return await asyncio.gather(
            *[a.get_info(include_configurable_items=include_configurable_items) for a in agents]
        )

    def auto_discover_agents(self):
        """自动发现并注册 yuxi/agents/buildin/ 下的所有智能体。

        遍历 yuxi/agents/buildin/ 目录下的所有子文件夹，如果子文件夹包含 __init__.py，
        则尝试从中导入 BaseAgent 的子类并注册。(使用自动导入的方式，支持私有agent)
        """
        # 获取 agents 目录的路径
        agents_dir = Path(__file__).parent

        # 遍历所有子目录
        for item in agents_dir.iterdir():
            # logger.info(f"尝试导入模块：{item}")
            # 跳过非目录、common 目录、__pycache__ 等
            if not item.is_dir() or item.name.startswith("_"):
                continue

            # 检查是否有 __init__.py 文件
            init_file = item / "__init__.py"
            if not init_file.exists():
                logger.warning(f"{item} 不是一个有效的模块")
                continue

            # 尝试导入模块
            try:
                module_name = f"yuxi.agents.buildin.{item.name}"
                module = importlib.import_module(module_name)

                # 查找模块中所有 BaseAgent 的子类
                for name, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, BaseAgent)
                        and obj is not BaseAgent
                        and obj.__module__.startswith(module_name)
                    ):
                        logger.info(f"自动发现智能体: {obj.__name__} 来自 {item.name}")
                        self.register_agent(obj)

            except Exception as e:
                logger.warning(f"无法从 {item.name} 加载智能体: {e}")


agent_manager = AgentManager()
# 自动发现并注册所有智能体
agent_manager.auto_discover_agents()
agent_manager.init_all_agents()

__all__ = ["agent_manager"]


if __name__ == "__main__":
    pass
