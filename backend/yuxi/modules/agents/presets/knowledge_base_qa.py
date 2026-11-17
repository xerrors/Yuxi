from yuxi.modules.agents.presets import AgentPreset

PRESET = AgentPreset(
    slug="knowledge-base-qa",
    name="知识库问答",
    description="按需加载知识库插件，基于可访问的知识库检索资料、核验原文并回答问题。",
    backend_id="ChatbotAgent",
    context={
        "skills": [],
        "plugins": ["knowledge-base"],
    },
)
