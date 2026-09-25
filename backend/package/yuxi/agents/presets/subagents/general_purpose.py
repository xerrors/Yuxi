from yuxi.agents.presets import AgentPreset

PRESET = AgentPreset(
    slug="general-purpose",
    name="通用任务",
    description="面向没有专用角色约束的一般任务，使用默认运行配置独立完成分析、整理、写作或文件处理。",
    backend_id="SubAgentBackend",
)
