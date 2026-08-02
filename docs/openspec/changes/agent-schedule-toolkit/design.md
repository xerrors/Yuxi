## Context

Yuxi-Know 已经具备完整的定时任务子系统：模型 `ScheduleDefinition` / `ScheduleLog`（`backend/package/yuxi/storage/postgres/models_business.py:770-854`），仓储 `ScheduleRepository`（`backend/package/yuxi/repositories/schedule_repository.py:11-110`），服务 `ScheduleService`（`backend/package/yuxi/services/schedule_service.py:22-173`），HTTP 路由 `/api/schedules/*`（`backend/server/routers/schedule_router.py`），以及 `web/src/views/ScheduleView.vue` 独立管理页面。调度引擎使用 ARQ 周期任务，调度库为 `croniter`，并发控制走 Redis 锁 + Postgres `FOR UPDATE SKIP LOCKED`。

agent 运行时已具备 user 上下文注入链路：`chat_router.py:347-373` → `chat_service.py:779-820` → `agents/base.py:64-150`（把 `user_id` 写入 `BaseContext` 和 `config["configurable"]`）→ middleware 与 `@tool` 通过 `runtime.context.user_id` 读取。`@tool` 注册走 `agents/toolkits/registry.py:39-96` 的全局 `_all_tool_instances`，新工具只要出现在 `agent_config.context.tools` 列表中即被启用。

本次变更的目的是补齐"agent 运行时"这条入口的 schedule 管理能力，并将"按用户隔离"下沉到仓储层做兜底；不重做现有 HTTP API 与调度引擎。详见 `proposal.md` 中"Why"。

## Goals / Non-Goals

**Goals:**

- 在 `backend/package/yuxi/agents/toolkits/schedules/` 下交付一组 LangGraph `@tool`，让 LLM 能在对话中管理当前用户的 `ScheduleDefinition`。
- 通过 `runtime.context.user_id` 强制 owner 隔离；admin/superadmin 跳过。
- 在 `ScheduleRepository` 新增 owner-aware 方法并让 HTTP 路由和新 `@tool` 都改用这些方法。
- 修复 `create_schedule_route` / `update_schedule_route` 中跨用户绑定 agent 的越权漏洞。

**Non-Goals:**

- 不修改 `/api/schedules/*` 的 HTTP API 契约（请求/响应模型、URL 路径保持不变）。
- 不修改 `web/src/views/ScheduleView.vue` 页面或 `web/src/apis/schedule_api.js`。
- 不修改 ARQ 调度引擎、`ScheduleManager`、cron 解析库。
- 不修改 `ScheduleDefinition` / `ScheduleLog` 的列结构（无 schema 迁移）。
- 不为 `AgentConfig` 增加 `user_id` 字段（agent 仍按 department 共享；本次仅校验「创建/修改 schedule 时引用的 agent 属于当前用户」）。
- 不在前端 agent 配置页做 UI 改动（依靠现有 `get_all_tool_instances()` 自动出现新工具 + 勾选生效）。

## Decisions

### 1. 工具集实现位置：`backend/package/yuxi/agents/toolkits/schedules/`

参考 `kbs/tools.py` / `mysql/tools.py` 的目录范式。新建子包 `schedules/`，内含 `__init__.py` 和 `tools.py`，并在 `toolkits/__init__.py:3` 加上 `from . import schedules` 触发装饰器执行。

**Alternatives considered:**

- 放在 `toolkits/buildin/`：不推荐，`buildin` 当前只放计算器、搜索、ask_user_question 等通用工具，schedule 工具与 agent 数据强相关。
- 单文件平铺在 `toolkits/schedules_tools.py`：与现有按子包组织的风格不一致，不利于以后扩展（cron helper、validation 等）。

### 2. 用户上下文来源：`ToolRuntime.context.user_id`

完全沿用 `kbs/tools.py:23-72` 的范式。工具函数签名形如：

```python
@tool(args_schema=ListSchedulesInput)
async def list_my_schedules(runtime: ToolRuntime) -> str:
    user_id = getattr(runtime.context, "user_id", None)
    if not user_id:
        return "无法获取用户信息"
    ...
```

**Alternatives considered:**

- `InjectedToolArg(user_id)` 从 `config["configurable"]` 拿：可行但与项目现有范式不一致，且 `runtime.context` 已被 `base.py:64-150` 显式注入，可读性更好。
- 新增 `get_current_user()` 全局工具函数（依赖 request context）：**不可行**，因为 `@tool` 在 LLM 触发的 LangGraph 节点中执行，没有 FastAPI request context。

### 3. 数据访问：复用 `ScheduleRepository` 并新增 owner-aware 方法

不重写仓储层。新增以下方法（保留原方法以便现有 ARQ worker 内部使用，ARQ 在自己的 session 中调用不受用户上下文约束）：

- `get_by_id_for_user(schedule_id, user_id, *, is_admin=False) -> ScheduleDefinition | None`：当 `is_admin=True` 时退化为按 id 查询。
- `update_for_user(schedule_id, user_id, data, *, is_admin=False) -> ScheduleDefinition | None`：内部调用 `get_by_id_for_user` 做 owner 校验后 `setattr`。
- `delete_for_user(schedule_id, user_id, *, is_admin=False) -> bool`：同上。
- `list_logs_for_user(schedule_id, user_id, *, limit, offset, is_admin=False)`：列表 `ScheduleLog` 前先做 owner 校验。

`list_schedules` 已有可选 `user_id` 过滤参数，保持原签名，HTTP 路由和 `@tool` 调用时强制传值。

**Alternatives considered:**

- 在仓储方法内部抛 `PermissionError`：可行但与"按 user_id 过滤 → 返回 None"风格不一致，调用方需要写 try/except 嵌套。
- 把所有现有方法都改成强制要求 `user_id`：会破坏 ARQ worker 的内部调用（worker 以 system 身份跑，不属于任何 user），改动面过大。

### 4. AgentConfig 归属校验：放在路由 / 工具入口

在 `create_schedule_route`（`schedule_router.py:54-92`）和 `update_schedule_route`（`schedule_router.py:132-172`）中，如果 `payload.agent_config_id` 不为空，调 `AgentConfigRepository.get_by_id`，校验 `config_item.user_id == current_user.id`；admin 跳过。`@tool` 实现里同样校验（`runtime.context.user_id`）。

**Alternatives considered:**

- 让仓储层在创建/更新时自动改写/校验 `agent_config_id`：模型层不持有"agent 属于谁"的信息，需要 join，破坏仓储单表职责。
- 把 `AgentConfig` 改造成按 user 私有：影响范围过大（agent 目前是按 department 共享的，且被多处依赖），不属于本次需求。

### 5. 工具返回格式：字符串（LLM 友好）

工具返回字符串或结构化 JSON 的字符串化结果，便于 LLM 解析和复述。错误情况下返回可读中文消息（如 "无权访问该任务" / "未找到该任务"），不抛异常。

**Alternatives considered:**

- 返回 Pydantic 对象：LangGraph 工具节点会自动 JSON 序列化但会丢失中文错误信息结构。
- 抛异常让 `ToolNode` 转成 `ToolMessage`：可行但会污染对话历史，错误信息传递不友好。

### 6. 测试策略

按 `docs/develop-guides/testing-guidelines.md` 规范在 `backend/test/` 下新增：

- `test_agent_schedule_tools.py`：覆盖七个工具的 owner 隔离、admin 跳过、agent_config 归属校验、user_id 强制覆盖。
- `test_schedule_router.py`（如不存在则新建）：回归 create/update 路由的 agent_config 归属校验。
- 复用现有 `test_schedule_repository.py`（如不存在则新建）：覆盖新 owner-aware 方法。

跑通 docker 环境下的 `pytest backend/test/agent_scheduled/ test_schedule_*.py`，详见 `docs/develop-guides/testing-guidelines.md`。

## Risks / Trade-offs

- **[Risk] agent_config 归属校验依赖 `AgentConfig.user_id` 字段** → Mitigation：在 `models_business.py:134-186` 已确认该字段存在；如未来 AgentConfig 改造为非 user 归属，校验会失效，需同步修改。
- **[Risk] 工具的 `args_schema` 中允许 LLM 传 `user_id` 字段** → Mitigation：所有 `*Input` Pydantic 模型显式不包含 `user_id`；并在工具函数内断言 runtime context 优先。
- **[Risk] `ScheduleRepository` 新增 owner-aware 方法后，原 `get_by_id` / `update` / `delete` 仍被 ARQ worker 等内部调用使用** → Mitigation：保留原方法签名（只是新增 owner-aware 版本），并在新方法 docstring 中标注 owner-aware 适用场景。
- **[Risk] 工具元数据自动出现在 agent 配置 UI，但用户未勾选** → Mitigation：在 `docs/develop-guides/roadmap.md` 记录；如需更主动引导，build 阶段可考虑在 `agent_config` 默认 `tools` 列表里加入 schedule 工具（由 LLM 调用者按需在配置页启用）。
- **[Risk] 测试需要 postgres 真实数据库** → Mitigation：按 `docs/develop-guides/testing-guidelines.md` 用 docker compose 起的 `postgres-dev` 实例。

## Migration Plan

- 不涉及数据库迁移（无 schema 变更）。
- 部署步骤：合并 PR → CI 通过 → docker compose 自动重载 api-dev 容器（热重载已配）→ 无须人工重启。
- 回滚：直接 revert PR；新工具没被勾选就不生效；新仓储方法是新增的，回滚后路由调用旧方法退化为现状。
- 灰度：无需灰度（无破坏性 API 变更）。

## Open Questions

- 工具的 `args_schema` 中 `agent_config_id` 是用整数 PK 还是 `agent_id` 字符串（`models_business.py:142` 定义的语义 ID）？倾向于整数 PK（与现有 `ScheduleDefinition.agent_config_id` 字段类型一致），让 LLM 通过"先 list agents → 选 id → 创建 schedule"的两步调用完成。**可推迟到 build 阶段实现时再定**，不影响 spec。
