# Brainstorm Summary

- Change: agent-schedule-toolkit
- Date: 2026-08-03

## 确认的技术方案

- **新能力**：`backend/package/yuxi/agents/toolkits/schedules/` 子包下提供 7 个 LangGraph `@tool`（list_my_schedules / get_schedule / create_schedule / update_schedule / delete_schedule / trigger_schedule / list_schedule_logs），全部通过 `runtime.context.user_id` 强制 owner 隔离。
- **数据层加固**：`ScheduleRepository` 新增 4 个 owner-aware 方法（`get_by_id_for_user` / `update_for_user` / `delete_for_user` / `list_logs_for_user`）；保留原方法给 ARQ worker 使用。HTTP 路由和新 `@tool` 改用 owner-aware 方法。
- **越权修复**：`create_schedule_route` / `update_schedule_route` 增加 `agent_config_id` 归属校验（admin 跳过）。
- **注册**：`toolkits/__init__.py:3` 加 `from . import schedules` 触发装饰器；新工具通过 `get_all_tool_instances()` 自动出现在 agent 配置 UI。
- **返回格式**：工具成功返 JSON 字符串，失败返可读中文错误消息。
- **分页**：`list_my_schedules` 和 `list_schedule_logs` 加 `limit`（默认 20、最大 100）和 `offset` 参数。
- **admin 路径**：不提供 `list_all_schedules` 专用工具，admin 通过同一工具内部 `is_admin=True` 分支跨用户操作（与 `_is_admin` 现有约定一致）。
- **agent_config_id 参数形式**：整数 PK（与 `ScheduleDefinition.agent_config_id` 字段类型一致）。

## 关键取舍与风险

1. **agent_config_id 整数 PK 对 LLM 不直观** → 不在本 change 内提供 `list_my_agents` 工具，记为 future work。LLM 现阶段需通过现有 `AgentView` / SubAgent 配置页知道 agent id。
2. **ARQ worker 仍用原仓储方法** → 保留 `get_by_id` / `update_schedule` / `delete_schedule` / `get_logs_by_schedule_id` 签名不动，并在 docstring 标注 "仅供系统内部使用"。
3. **测试需真实 Postgres** → 按 `docs/develop-guides/testing-guidelines.md` 用 docker compose 起的 `postgres-dev`；为 `@tool` 写单测时通过 chat router 链路注入 user_id。
4. **工具元数据自动出现但用户未勾选** → 在 `roadmap.md` 记录；不在默认 `tools` 列表里强加。

## 测试策略

- `backend/test/agent_scheduled/test_agent_schedule_tools.py`：7 工具 happy path（7 case）+ owner 隔离（5 case）+ admin 跳过（5 case）+ agent_config 归属校验（2 case）+ user_id 参数被忽略（1 case）+ user_id 缺失（1 case）。
- `backend/test/test_schedule_router.py`：create / update 路由 agent_config 归属校验（2 case）+ admin 跨用户兼容（1 case）。
- `backend/test/test_schedule_repository.py`：4 个 owner-aware 方法各 2 case（含 admin 路径）。
- 跑通 `pytest backend/test/agent_scheduled/ backend/test/test_schedule_router.py backend/test/test_schedule_repository.py`，按 testing-guidelines 在 docker 环境跑。

## Spec Patch

为 `specs/agent-schedule-tools/spec.md` 新增 3 条 Requirement（场景级补充，不动范围）：

1. **工具返回 JSON 字符串**：成功返可被 LLM 解析的 JSON；失败返可读中文消息，不抛异常。
2. **列表工具分页**：`list_my_schedules` / `list_schedule_logs` 必须有 `limit`（默认 20、最大 100）和 `offset` 参数。
3. **admin 走同工具内部判断**：admin 跳过隔离的语义在所有工具内部统一实现，不暴露 `list_all_schedules` 等专用工具。

记为 future work：新增 `list_my_agents` 工具供 LLM 查询可用 agent 列表（不在本次 scope）。
