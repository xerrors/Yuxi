# agent-schedule-toolkit

为 LangGraph agent 运行时新增一组 @tool，实现当前用户名下 ScheduleDefinition 的增删改查，强制 user_id 隔离，并修复 create/update 路由中跨用户绑定 agent_config 的越权漏洞
