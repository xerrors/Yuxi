"""部署时的可选业务能力装配。"""

import os


def knowledge_enabled() -> bool:
    """读取知识库部署开关；非法值明确阻止启动。"""
    value = os.getenv("YUXI_KNOWLEDGE_ENABLED", "true").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("YUXI_KNOWLEDGE_ENABLED 必须为 true 或 false")
    return value == "true"


def personal_assistant_enabled() -> bool:
    """读取个人助理部署开关，默认关闭。"""
    value = os.getenv("YUXI_PERSONAL_ASSISTANT_ENABLED", "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("YUXI_PERSONAL_ASSISTANT_ENABLED 必须为 true 或 false")
    return value == "true"
