from yuxi.agents.presets import AgentPreset

PRESET = AgentPreset(
    slug="default-chatbot",
    name="智能助手",
    description="基础的对话机器人，可以回答问题，可在配置中启用需要的工具。",
    backend_id="ChatbotAgent",
)
