## Why

Yuxi-Know 已经具备完整的定时任务能力（`ScheduleDefinition` 数据模型、`/api/schedules` HTTP API、`/schedules` 前端页面、ARQ 周期执行），但所有入口都只服务 Web 交互，用户在 LangGraph agent 对话中无法让 LLM 自主查看、创建、修改或删除自己的定时任务。本次变更补齐"agent 运行时"这条入口，让用户在对话中就能以自然语言让 agent 帮自己管理定时任务。同时经过隔离审查，发现两处越权漏洞一并修复：HTTP `create_schedule`/`update_schedule` 路由未校验 `agent_config_id` 是否归属当前用户，仓储层 `get_by_id/update/delete/get_logs_by_schedule_id` 全部不强制 user_id 过滤、依赖路由层把关。

## What Changes

- **新增 `agent-schedule-tools` 能力**：在 `backend/package/yuxi/agents/toolkits/schedules/` 下提供一组 LangGraph `@tool`，覆盖 `list_my_schedules` / `get_schedule` / `create_schedule` / `update_schedule` / `delete_schedule` / `trigger_schedule` / `list_schedule_logs`。所有工具通过 `runtime.context.user_id` 拿到当前用户，对 `ScheduleRepository` 的调用强制 `user_id` 过滤；admin/superadmin 跳过隔离（与现有 `_is_admin` 行为一致）。
- **修复 `create_schedule_route` / `update_schedule_route` 越权漏洞**：当 `payload.agent_config_id` 不为空时，调用 `AgentConfigRepository` 校验其归属是否等于 `current_user.id`（admin 跳过），否则返回 403。
- **仓储层加 owner-aware 兜底**：在 `ScheduleRepository` 中新增 `get_by_id_for_user(schedule_id, user_id, *, is_admin=False)`、`update_for_user`、`delete_for_user`、`list_logs_for_user` 等方法；HTTP 路由和新的 @tool 改用这些方法，让"按 user_id 隔离"在数据层有强约束，未来扩展不会再依赖调用方自觉。
- **注册新工具**：在 `backend/package/yuxi/agents/toolkits/__init__.py:3` 增加 `from . import schedules` 触发装饰器执行；工具元数据通过现有 `get_all_tool_instances()` 自动出现在 agent 配置 UI，用户在 `agent_config.context.tools` 列表勾选后即可生效。

## Capabilities

### New Capabilities

- `agent-schedule-tools`: LangGraph agent 运行时提供给 LLM 的定时任务管理工具集，按当前用户隔离，支持 list / get / create / update / delete / trigger / list_logs 七种操作。

### New Capabilities

- `agent-schedule-tools`: LangGraph agent 运行时提供给 LLM 的定时任务管理工具集，按当前用户隔离，支持 list / get / create / update / delete / trigger / list_logs 七种操作。
- `scheduled-runs`: `ScheduleDefinition` 读写隔离契约（HTTP 路由、@tool 入口、仓储层均按 `user_id` 强制过滤；创建/修改时若指定 `agent_config_id`，该 agent 必须归属当前用户；admin/superadmin 跳过隔离）。这是项目首次用 OpenSpec 落地此能力，故归为 New。

### Modified Capabilities

（无 — `docs/openspec/specs/` 当前为空，不存在既有 capability 的需求级修改。）

## Impact

- **后端代码**
  - 新增：`backend/package/yuxi/agents/toolkits/schedules/__init__.py`、`tools.py`
  - 修改：`backend/package/yuxi/agents/toolkits/__init__.py`（注册新子模块）
  - 修改：`backend/server/routers/schedule_router.py`（`create_schedule_route` / `update_schedule_route` 增 agent_config 归属校验 + 改用 owner-aware 仓储方法）
  - 修改：`backend/package/yuxi/repositories/schedule_repository.py`（新增 owner-aware 方法，不破坏现有签名）
- **测试**：按 [docs/develop-guides/testing-guidelines.md](../develop-guides/testing-guidelines.md)，在 `backend/test/` 新增 tools 单测 + 路由层越权回归测试
- **配置 / 数据 / 调度引擎**：均无改动
- **前端 / HTTP API 契约**：均无破坏性改动
- **AGENTS.md / docs**：更新 [roadmap.md](../develop-guides/roadmap.md) 记录本次新增能力
