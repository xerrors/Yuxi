---
comet_change: agent-schedule-toolkit
role: technical-design
canonical_spec: openspec
---

# agent-schedule-toolkit — Design Doc

## Context

Yuxi-Know 已具备完整的定时任务子系统（`ScheduleDefinition` / `ScheduleLog` / `ScheduleRepository` / `ScheduleService` / `/api/schedules` HTTP API / `ScheduleView.vue` 页面）。ARQ 周期任务作为调度引擎，`croniter` 解析 cron 表达式。agent 运行时已具备 user 上下文注入链路（`chat_router.py` → `chat_service.py` → `agents/base.py:64-150` → middleware 与 `@tool` 通过 `runtime.context.user_id` 读取）。

本次变更目的是补齐"agent 运行时"这条 schedule 管理入口，并将"按用户隔离"下沉到仓储层做兜底；同时修复 `create_schedule_route` / `update_schedule_route` 中跨用户绑定 agent 的越权漏洞。OpenSpec `proposal.md` "Why" 给出了动机。

## Goals / Non-Goals

**Goals:**

- 在 `backend/package/yuxi/agents/toolkits/schedules/` 下交付 7 个 LangGraph `@tool`，全部通过 `runtime.context.user_id` 强制 owner 隔离。
- `ScheduleRepository` 新增 4 个 owner-aware 方法（`get_by_id_for_user` / `update_for_user` / `delete_for_user` / `list_logs_for_user`），HTTP 路由和 `@tool` 改用这些方法。
- 修复 `create_schedule_route` / `update_schedule_route` 中 `agent_config_id` 归属校验缺失的越权漏洞。
- 在 `toolkits/__init__.py` 注册新子模块，使工具元数据通过 `get_all_tool_instances()` 自动出现在 agent 配置 UI。

**Non-Goals:**

- 不修改 `/api/schedules/*` 的 HTTP API 契约（请求/响应模型、URL 路径保持不变）。
- 不修改 `web/src/views/ScheduleView.vue` 页面或 `web/src/apis/schedule_api.js`。
- 不修改 ARQ 调度引擎、`ScheduleManager`、cron 解析库。
- 不修改 `ScheduleDefinition` / `ScheduleLog` 的列结构（无 schema 迁移）。
- 不为 `AgentConfig` 增加 `user_id` 字段（agent 仍按 department 共享；本次仅校验「创建/修改 schedule 时引用的 agent 属于当前用户」）。
- 不提供 `list_my_agents` 工具（记为 future work）。
- 不在前端 agent 配置页做 UI 改动。

## Decisions

### 1. 工具实现位置

新建 `backend/package/yuxi/agents/toolkits/schedules/` 子包（参考 `kbs/` / `mysql/` 范式），内含 `__init__.py` 和 `tools.py`。在 `toolkits/__init__.py:3` 加 `from . import schedules` 触发装饰器执行。

### 2. 用户上下文来源

完全沿用 `kbs/tools.py:23-72` 范式。函数签名：

```python
@tool(args_schema=XxxInput)
async def xxx(args: XxxInput, runtime: ToolRuntime) -> str:
    user_id = getattr(runtime.context, "user_id", None)
    if not user_id:
        return "无法获取用户信息"
    ...
```

`XxxInput` Pydantic 模型**不**包含 `user_id` 字段（即使 LLM 传入也忽略）。

### 3. 数据访问

`tool` 内部用 `async with pg_manager.get_async_session_context() as session:` 拿 session，构造 `ScheduleRepository(session)` 调 owner-aware 方法。`is_admin` 来自 `getattr(runtime.context, "is_admin", False)` 或对 user role 的等价判断（待 build 阶段确认 `BaseContext` 是否带 `is_admin`，否则工具内读 `user.role`）。

### 4. ScheduleRepository owner-aware 方法

新增方法（保留原方法给 ARQ worker）：

```python
async def get_by_id_for_user(self, schedule_id, user_id, *, is_admin=False) -> ScheduleDefinition | None
async def update_for_user(self, schedule_id, user_id, data, *, is_admin=False) -> ScheduleDefinition | None
async def delete_for_user(self, schedule_id, user_id, *, is_admin=False) -> bool
async def list_logs_for_user(self, schedule_id, user_id, *, limit, offset, is_admin=False) -> list[ScheduleLog]
```

非 admin 路径：先 `select ... where id = :id and user_id = :user_id`；admin 路径：仅按 id 查。`update_for_user` / `delete_for_user` 内部走 `get_by_id_for_user` 拿到对象后 `setattr` / `delete` + `commit`。

### 5. agent_config_id 归属校验

`create_schedule_route`（`schedule_router.py:54-92`）和 `update_schedule_route`（`schedule_router.py:132-172`）以及两个 `@tool` 中，若 `payload.agent_config_id` 不为空，调 `AgentConfigRepository.get_by_id`，校验 `config_item.user_id == current_user.id`（admin 跳过）。失败返 403 / 友好错误。

### 6. 工具返回格式与分页

- 成功：`json.dumps(..., ensure_ascii=False, default=str)`，便于 LLM 解析。
- 失败：可读中文（如 "无权访问该任务"），不抛异常。
- 列表工具：`list_my_schedules` / `list_schedule_logs` 加 `limit`（默认 20，最大 100，build 阶段定义为常量）和 `offset`（默认 0）参数。

### 7. admin 路径

不提供 `list_all_schedules` 等专用工具；admin 跨用户操作在所有工具内部统一通过 `is_admin=True` 分支处理（与 `_is_admin` 现有约定一致）。

### 8. agent_config_id 参数形式

整数 PK（与 `ScheduleDefinition.agent_config_id` 字段类型一致）。`create_schedule` / `update_schedule` 的 Pydantic args_schema 中 `agent_config_id: int`。

### 9. 时区与 cron 校验

`create_schedule` / `update_schedule` 不在工具内做 cron 语法预校验；落到 `ScheduleService.create_scheduled_run` 时由 `croniter` 触发异常，工具捕获后返回中文错误（如 "cron 表达式无效"）。

### 10. 触发工具的实现

`trigger_schedule` 调用 `ScheduleService.manual_trigger_schedule`（`schedule_service.py:97-173`）；该方法已经接受 `schedule` + `db` 参数，不依赖 request context，可以直接复用。

## Components

### A. `backend/package/yuxi/agents/toolkits/schedules/__init__.py`

空包，触发 `tools.py` 装饰器执行。

### B. `backend/package/yuxi/agents/toolkits/schedules/tools.py`

- 7 个 `@tool` 函数，签名模式：`async def xxx(args: XxxInput, runtime: ToolRuntime) -> str`
- 7 个 Pydantic `*Input` 类（不包含 `user_id`）
- 内部 helper：`_resolve_user(runtime)`、`_is_admin(user)`、`_json_or_error(obj, err)`、`_check_agent_ownership(session, agent_config_id, user_id, is_admin)`

### C. `backend/package/yuxi/repositories/schedule_repository.py`（扩展）

新增 4 个 owner-aware 方法；保留原方法不动；新方法 docstring 标注"HTTP 入口和 @tool 入口请用此方法"。

### D. `backend/server/routers/schedule_router.py`（修改）

- `create_schedule_route` / `update_schedule_route` 加 agent_config 归属校验
- 所有 schedule 读写调用改为 owner-aware 仓储方法

### E. `backend/package/yuxi/agents/toolkits/__init__.py`（修改）

加 `from . import schedules` 触发注册。

### F. `backend/test/agent_scheduled/test_agent_schedule_tools.py`（新增）

7 工具 happy + 隔离 + admin + agent 归属 + 参数忽略 + 缺 user_id 测试用例。

### G. `backend/test/test_schedule_router.py`（新增或扩展）

路由层 agent_config 归属校验回归。

### H. `backend/test/test_schedule_repository.py`（新增或扩展）

4 个 owner-aware 仓储方法测试。

## Data Flow

### 工具调用 list_my_schedules

```
LLM 调 list_my_schedules(limit=20, offset=0)
  └─> runtime.context.user_id = "u_123"
  └─> async with pg_manager.get_async_session_context() as session:
        repo = ScheduleRepository(session)
        rows = await repo.list_schedules(user_id="u_123", limit=20, offset=0)
  └─> json.dumps([...], ensure_ascii=False)  → 返回 LLM
```

### 工具调用 create_schedule (普通用户，agent_config 归属校验)

```
LLM 调 create_schedule(agent_config_id=42, cron_expr="0 * * * *", query="...", timezone="Asia/Shanghai")
  └─> runtime.context.user_id = "u_123"; is_admin = False
  └─> async with get_async_session_context() as session:
        agent = await AgentConfigRepository(session).get_by_id(42)
        if not agent or agent.user_id != "u_123":
            return "无权使用该 agent"  → 返回 LLM
        schedule = await ScheduleService.create_scheduled_run(
            schedule=ScheduleDefinition(user_id="u_123", agent_config_id=42, ...),
            db=session)
  └─> json.dumps({"id": schedule.id, ...})  → 返回 LLM
```

### 工具调用 get_schedule (非 admin，访问他人任务)

```
LLM 调 get_schedule(schedule_id=99)
  └─> user_id = "u_123", is_admin = False
  └─> repo.get_by_id_for_user(99, "u_123", is_admin=False) → None
  └─> return "未找到该任务"  → 返回 LLM
（错误消息不区分"未找到"和"无权访问"，避免泄露存在性）
```

## Error Handling

| 失败场景 | 工具返回 |
|----------|----------|
| `runtime.context.user_id` 缺失 | "无法获取用户信息" |
| schedule 不存在或不属于当前用户 | "未找到该任务"（不区分两种情况） |
| `agent_config_id` 归属校验失败 | "无权使用该 agent" |
| cron 表达式无效 | "cron 表达式无效：{异常消息}" |
| schedule 已禁用 + trigger | 仍允许触发（与现有 `manual_trigger_schedule` 行为一致） |
| 数据库连接失败 | 异常向上抛，由 `ToolNode` 转 `ToolMessage` 错误（最后兜底） |

不暴露堆栈；不暴露他人 schedule 存在性。

## Testing Strategy

### 单元 / 集成测试（`backend/test/`）

按 `docs/develop-guides/testing-guidelines.md` 规范：

1. **工具测试** `test_agent_scheduled/test_agent_schedule_tools.py`
   - 7 happy path（每个工具 1 case）
   - 5 owner 隔离 case（get / update / delete / trigger / list_logs 越权）
   - 5 admin 跳过 case（list / get / update / delete / trigger 跨用户）
   - 2 agent_config 归属校验 case（create / update）
   - 1 user_id 参数忽略 case
   - 1 user_id 缺失 case
   - 1 分页 case（list_my_schedules / list_schedule_logs 各 1）
2. **路由测试** `test_schedule_router.py`
   - 2 agent_config 归属校验 case（create / update 路由）
   - 1 admin 跨用户兼容 case
3. **仓储测试** `test_schedule_repository.py`
   - 4 个 owner-aware 方法各 2 case（普通用户 + admin）

### 端到端冒烟

- 启动 `docker compose up -d api-dev web-dev`；进入 `web/src/views/AgentView.vue` 选择一个有 7 个新工具的 agent，配置页勾选所有 7 个工具。
- 在对话中让 LLM 调 `list_my_schedules` → 确认只看到本人 schedule。
- 让 LLM 调 `create_schedule(agent_config_id=<他人 agent id>, ...)` → 确认失败。
- 创建一个 schedule → 调 `list_schedule_logs` 看到 0 条（未触发）；调 `trigger_schedule` → 调 `list_schedule_logs` 看到 1 条。

### Lint / Format

- `make format` 跑通。
- 按 testing-guidelines 跑 `flake8` / `mypy`（如项目配置）。

## Risks / Trade-offs

- **[Risk] `BaseContext` 当前可能未提供 `is_admin` 字段** → Mitigation：build 阶段先检查 `agents/context.py:10-40`，若没有则在工具内通过 `user_id` 查 `users.role` 二次判断（一次额外 DB 查询，admin 操作低频可接受）。或扩展 `BaseContext` 加 `is_admin` 字段（侵入 base.py，build 阶段决定）。
- **[Risk] agent_config_id 整数 PK 对 LLM 不直观** → Mitigation：本次不提供 `list_my_agents` 工具，记为 future work；用户通过现有 UI 查 agent id。
- **[Risk] 工具元数据自动出现但用户未勾选** → Mitigation：在 `roadmap.md` 记录；不在 `BaseContext.tools` 默认列表里强加。
- **[Risk] 工具调用链路过长**（`ToolNode` → repository → DB → 序列化）：单次调用通常 < 100ms，依赖 ARQ 的 `pg_manager` 池化，可接受。
- **[Risk] admin 跨用户操作的审计**：当前无审计日志，admin 误操作不可追溯 → Mitigation：本 change 不引入审计（超范围），记为 future work。

## Open Questions

- `BaseContext` 是否带 `is_admin` 字段？build 阶段如发现没有，按 Risks 第 1 条处理。
- 工具中如何处理"schedule 已禁用 + trigger"的语义？倾向于沿用现有 `manual_trigger_schedule` 行为（仍可触发），不在 spec 上加限制。

## Migration Plan

- 无数据库迁移（schema 不变）。
- 部署：合并 PR → CI 通过 → docker compose 自动热重载 → 无须人工重启。
- 回滚：直接 revert PR；新工具未勾选就不生效；新仓储方法是新增的，回滚后路由调用旧方法退化为现状。
- 灰度：不需要（无破坏性 API 变更）。

## Future Work

- `list_my_agents` 工具：让 LLM 在对话中查询当前用户可用的 agent 列表，便于精确选择 `agent_config_id`。
- 工具调用审计日志：admin 跨用户操作、用户删除/触发关键动作的审计。
- `BaseContext` 扩展 `is_admin` 字段：避免工具内重复查 `users.role`。
