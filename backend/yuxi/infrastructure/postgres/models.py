"""显式注册业务和知识 ORM 映射，供 Schema 初始化与测试独立装配。"""

from importlib import import_module

from yuxi.infrastructure.postgres.base import BusinessBase, KnowledgeBase

MODEL_MODULES = (
    "yuxi.modules.agents.models.definitions",
    "yuxi.modules.agents.models.personal_assistants",
    "yuxi.modules.agents.models.sessions",
    "yuxi.modules.agents.models.cooperation",
    "yuxi.modules.agents.models.inputs",
    "yuxi.modules.agents.models.attachments",
    "yuxi.modules.agents.models.turns",
    "yuxi.modules.agents.models.runs",
    "yuxi.modules.agents.models.sandbox",
    "yuxi.modules.agents.models.messages",
    "yuxi.modules.agents.models.feedback",
    "yuxi.modules.workspace.models",
    "yuxi.modules.identity.models",
    "yuxi.modules.extensions.skills.models",
    "yuxi.modules.extensions.mcp.models",
    "yuxi.modules.models.tables",
    "yuxi.modules.system.models",
    "yuxi.modules.background_jobs.models",
    "yuxi.modules.schedules.models",
    "yuxi.modules.knowledge.models",
)


def load_models() -> tuple:
    """加载全部映射并返回两个独立 metadata。"""
    for module in MODEL_MODULES:
        import_module(module)
    return BusinessBase.metadata, KnowledgeBase.metadata
