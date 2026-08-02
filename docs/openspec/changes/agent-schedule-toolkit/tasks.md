## 1. 仓储层 owner-aware 方法

- [x] 1.1 在 `backend/package/yuxi/repositories/schedule_repository.py` 中新增 `get_by_id_for_user(schedule_id, user_id, *, is_admin=False)`，非 admin 时按 `(id, user_id)` 过滤返回；admin 退化为按 id。✅ commit 6202fbdc
- [x] 1.2 新增 `update_for_user(schedule_id, user_id, data, *, is_admin=False)`，内部先调 `get_by_id_for_user` 做 owner 校验，再 `setattr` 并 `commit`。✅ commit 6202fbdc
- [x] 1.3 新增 `delete_for_user(schedule_id, user_id, *, is_admin=False)`，同样先做 owner 校验。✅ commit 6202fbdc
- [x] 1.4 新增 `list_logs_for_user(schedule_id, user_id, *, limit, offset, is_admin=False)`，先校验 schedule 归属再返回 `ScheduleLog`。✅ commit 6202fbdc
- [x] 1.5 保留原 `get_by_id` / `update_schedule` / `delete_schedule` / `get_logs_by_schedule_id` 方法不动（ARQ worker 仍需使用）。✅ commit 6202fbdc（diff 仅 +299/-0）

## 2. 修复 HTTP 路由越权漏洞

- [ ] 2.1 在 `backend/server/routers/schedule_router.py` 的 `create_schedule_route` 中，当 `payload.agent_config_id` 不为空时调 `AgentConfigRepository.get_by_id`，校验 `config_item.user_id == str(current_user.id)`，admin 跳过；失败返回 403。
- [ ] 2.2 在 `update_schedule_route` 中做同样校验，并在替换 `agent_config_id` 之前完成。
- [ ] 2.3 将 `schedule_router.py` 中所有 schedule 读写调用改为 owner-aware 仓储方法（`get_by_id_for_user` / `update_for_user` / `delete_for_user` / `list_logs_for_user`），admin 路径显式传 `is_admin=True`。

## 3. 新增 @tool 工具集

- [ ] 3.1 新建 `backend/package/yuxi/agents/toolkits/schedules/__init__.py`（参考 `kbs/__init__.py` 写法）。
- [ ] 3.2 新建 `backend/package/yuxi/agents/toolkits/schedules/tools.py`，定义七个工具的 Pydantic args_schema（不包含 `user_id`）：`list_my_schedules` / `get_schedule` / `create_schedule` / `update_schedule` / `delete_schedule` / `trigger_schedule` / `list_schedule_logs`。每个函数签名形如 `async def xxx(..., runtime: ToolRuntime) -> str`，从 `runtime.context.user_id` 取当前用户。
- [ ] 3.3 七个工具内部使用 `async with pg_manager.get_async_session_context() as session: ScheduleRepository(session)` 模式调 owner-aware 仓储方法，admin 路径传 `is_admin=True`。
- [ ] 3.4 `create_schedule` / `update_schedule` 工具内部对 `agent_config_id` 做归属校验，失败返回 LLM 友好错误消息（"无权使用该 agent"）。
- [ ] 3.5 工具返回字符串结果（成功为 JSON 字符串、失败为可读中文错误消息），不抛未捕获异常。

## 4. 注册新工具

- [ ] 4.1 在 `backend/package/yuxi/agents/toolkits/__init__.py:3` 加上 `from . import schedules` 触发装饰器执行。
- [ ] 4.2 启动 api-dev 容器（`docker compose up -d api-dev`），调用 `get_all_tool_instances()` 验证七个新工具已注册；通过 `web/src/views/AgentView.vue` 或 `SubAgent` 配置页确认工具元数据可见。

## 5. 测试

- [ ] 5.1 在 `backend/test/agent_scheduled/` 下新增 `test_agent_schedule_tools.py`，覆盖：
  - 七个工具的 happy path
  - owner 隔离（非 admin 不能读/写/触发/取日志他人的 schedule）
  - admin 跳过隔离
  - `create_schedule` / `update_schedule` 中 `agent_config_id` 归属校验
  - `user_id` 试图被 LLM 传入时被忽略
  - `runtime.context.user_id` 缺失时返回友好错误
- [ ] 5.2 在 `backend/test/` 下新增或更新 `test_schedule_router.py`，覆盖 create/update 路由的 agent_config 归属校验。
- [ ] 5.3 在 `backend/test/` 下新增 `test_schedule_repository.py`，覆盖新 owner-aware 方法。
- [ ] 5.4 在 docker 环境下跑通 `pytest backend/test/agent_scheduled/ backend/test/test_schedule_router.py backend/test/test_schedule_repository.py`，确保全部通过。

## 6. 文档与验证

- [ ] 6.1 更新 `docs/develop-guides/roadmap.md`，记录本次新增 `agent-schedule-tools` 能力 + `scheduled-runs` 隔离契约加固。
- [ ] 6.2 在 `docs/agents/` 下新增（或追加到现有的）"agent-schedule-tools"说明文档，并在 `docs/.vitepress/config.mts` 的 `agents` 导航中补充入口（如 `docs/develop-guides/roadmap.md` 指引该分组维护）。
- [ ] 6.3 `make format` 格式化代码；按 `docs/develop-guides/testing-guidelines.md` 完成 lint 与端到端冒烟。
- [ ] 6.4 提交 PR（标题：`feat: 在 agent 运行时新增 schedule 管理工具集并加固按用户隔离`，正文按 `CONTRIBUTING.md` 模板）。
