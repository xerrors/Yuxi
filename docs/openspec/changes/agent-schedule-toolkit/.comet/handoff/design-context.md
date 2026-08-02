# Comet Design Handoff

- Change: agent-schedule-toolkit
- Phase: design
- Mode: compact
- Context hash: 4bc29435a694803fb41b2a379dde6827ff218119146a4153ba7eee08508b6a3f

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/agent-schedule-toolkit/proposal.md

- Source: openspec/changes/agent-schedule-toolkit/proposal.md
- Lines: 1-37
- SHA256: 42e96a53f90fdd14f5bb71690a8f1386042fa95c4630a14cdad252739c736f82

```md
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
```

## openspec/changes/agent-schedule-toolkit/design.md

- Source: openspec/changes/agent-schedule-toolkit/design.md
- Lines: 1-117
- SHA256: 1204594d881f782de01c6b666420b0d18b4d892fbd5883fd136a2c33337b5195

[TRUNCATED]

```md
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

```

Full source: openspec/changes/agent-schedule-toolkit/design.md

## openspec/changes/agent-schedule-toolkit/tasks.md

- Source: openspec/changes/agent-schedule-toolkit/tasks.md
- Lines: 1-46
- SHA256: 421fc5abb843b61a5ab58a66bdef07f22c30e511763bc6974ffc7b2533aabf19

```md
## 1. 仓储层 owner-aware 方法

- [ ] 1.1 在 `backend/package/yuxi/repositories/schedule_repository.py` 中新增 `get_by_id_for_user(schedule_id, user_id, *, is_admin=False)`，非 admin 时按 `(id, user_id)` 过滤返回；admin 退化为按 id。
- [ ] 1.2 新增 `update_for_user(schedule_id, user_id, data, *, is_admin=False)`，内部先调 `get_by_id_for_user` 做 owner 校验，再 `setattr` 并 `commit`。
- [ ] 1.3 新增 `delete_for_user(schedule_id, user_id, *, is_admin=False)`，同样先做 owner 校验。
- [ ] 1.4 新增 `list_logs_for_user(schedule_id, user_id, *, limit, offset, is_admin=False)`，先校验 schedule 归属再返回 `ScheduleLog`。
- [ ] 1.5 保留原 `get_by_id` / `update_schedule` / `delete_schedule` / `get_logs_by_schedule_id` 方法不动（ARQ worker 仍需使用）。

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
```

## openspec/changes/agent-schedule-toolkit/specs/agent-schedule-tools/spec.md

- Source: openspec/changes/agent-schedule-toolkit/specs/agent-schedule-tools/spec.md
- Lines: 1-201
- SHA256: f84da292b7237edafc26e3825566a504717f0644b0c48fcad02a433f5e76c4d7

[TRUNCATED]

```md
## Purpose

为 LangGraph agent 运行时提供一组 `@tool`，让 LLM 能在对话中管理当前用户名下的定时任务（`ScheduleDefinition`）。所有工具必须强制按当前用户隔离；admin/superadmin 跳过隔离。

## ADDED Requirements

### Requirement: 工具能列出当前用户的定时任务

The system SHALL provide a tool `list_my_schedules` that returns all `ScheduleDefinition` records whose `user_id` equals the calling user. For admin/superadmin the tool SHALL return all records (consistent with the existing `_is_admin` convention in the HTTP API).

#### Scenario: 普通用户只看到自己的任务

- **WHEN** a non-admin user invokes `list_my_schedules`
- **THEN** the result contains only schedules where `ScheduleDefinition.user_id == runtime.context.user_id`
- **AND** schedules owned by other users are excluded

#### Scenario: 管理员可看到全部任务

- **WHEN** an admin/superadmin invokes `list_my_schedules`
- **THEN** the result contains schedules owned by all users

### Requirement: 工具能按 id 获取单个定时任务

The system SHALL provide a tool `get_schedule` that takes `schedule_id` and returns the schedule when its `user_id` matches the calling user. For admin/superadmin the user-id check is skipped.

#### Scenario: 自己的任务可读取

- **WHEN** a non-admin user invokes `get_schedule` with a `schedule_id` they own
- **THEN** the tool returns the schedule's full details

#### Scenario: 他人的任务被拒绝

- **WHEN** a non-admin user invokes `get_schedule` with a `schedule_id` owned by another user
- **THEN** the tool returns an error indicating the schedule was not found or is not accessible
- **AND** no detail of the schedule is leaked in the error message

### Requirement: 工具能创建定时任务

The system SHALL provide a tool `create_schedule` that creates a new `ScheduleDefinition` whose `user_id` is forced to the calling user (any value the LLM attempts to pass for ownership is ignored). If `agent_config_id` is provided, the referenced agent MUST belong to the calling user; for admin/superadmin this check is skipped.

#### Scenario: 普通用户创建时 user_id 被强制为本人

- **WHEN** a non-admin user invokes `create_schedule` with `agent_config_id`, `cron_expr`, `query`, `timezone`, and any optional fields
- **THEN** the persisted `ScheduleDefinition.user_id` equals `runtime.context.user_id`
- **AND** any `user_id` value the LLM attempted to pass is ignored

#### Scenario: 普通用户不能用他人的 agent 创建

- **WHEN** a non-admin user invokes `create_schedule` with an `agent_config_id` that is not owned by them
- **THEN** the tool returns an error
- **AND** no `ScheduleDefinition` is persisted

#### Scenario: 管理员可以指定任意 agent_config_id

- **WHEN** an admin/superadmin invokes `create_schedule` with any `agent_config_id`
- **THEN** the schedule is created and `user_id` is taken from `runtime.context.user_id` (admin/superadmin themselves, not impersonated)

### Requirement: 工具能修改当前用户拥有的定时任务

The system SHALL provide a tool `update_schedule` that takes `schedule_id` and partial fields. The tool MUST only update schedules owned by the calling user (admin/superadmin may update any). If `agent_config_id` is being changed, the new `agent_config_id` MUST also belong to the calling user (admin skip).

#### Scenario: 修改自己的任务

- **WHEN** a non-admin user invokes `update_schedule` on a schedule they own
- **THEN** the persisted fields are updated with the provided values
- **AND** `user_id` cannot be changed

#### Scenario: 尝试修改他人任务被拒绝

- **WHEN** a non-admin user invokes `update_schedule` on a schedule owned by another user
- **THEN** the tool returns an error
- **AND** no fields are changed

#### Scenario: 把任务改绑到他人 agent 被拒绝

- **WHEN** a non-admin user invokes `update_schedule` with a new `agent_config_id` that is not owned by them
- **THEN** the tool returns an error
- **AND** the schedule's `agent_config_id` is not changed

### Requirement: 工具能删除当前用户拥有的定时任务
```

Full source: openspec/changes/agent-schedule-toolkit/specs/agent-schedule-tools/spec.md

## openspec/changes/agent-schedule-toolkit/specs/scheduled-runs/spec.md

- Source: openspec/changes/agent-schedule-toolkit/specs/scheduled-runs/spec.md
- Lines: 1-106
- SHA256: f21901d692d80fe10450d5ea6eb81fcb9fc28000c792efb7086b61edd3a06e34

[TRUNCATED]

```md
## Purpose

定义 `ScheduleDefinition` 及其相关 `ScheduleLog` 的读写隔离契约：所有读写入口（HTTP 路由、agent `@tool`、仓储层方法）都必须按当前 `user_id` 强制过滤；创建/修改时若指定 `agent_config_id`，该 agent 必须归属当前用户；admin/superadmin 跳过隔离。

## ADDED Requirements

### Requirement: 所有读操作按当前用户过滤

The system MUST filter `ScheduleDefinition` reads by the current user's id at the data-access layer. Non-admin users SHALL only see schedules where `ScheduleDefinition.user_id` equals their own id. Admin/superadmin MAY read all schedules.

#### Scenario: 列表接口返回本人数据

- **WHEN** a non-admin user lists schedules via HTTP or via `list_my_schedules` tool
- **THEN** the response contains only schedules with `user_id == current_user.id`

#### Scenario: 管理员列表接口返回全量

- **WHEN** an admin/superadmin lists schedules
- **THEN** the response contains schedules for all users

#### Scenario: 按 id 读取受 owner 校验保护

- **WHEN** a non-admin user fetches a schedule by id (HTTP or `@tool`)
- **THEN** the data-access layer returns "not found" for any schedule where `user_id != current_user.id`
- **AND** no other user's schedule data is returned

### Requirement: 所有写操作按当前用户校验

The system MUST reject any create, update, delete, or trigger operation that targets a `ScheduleDefinition` not owned by the current user (admin/superadmin may operate on any). The owner check MUST happen at the data-access layer, not only in the HTTP route, so that any caller (HTTP, `@tool`, future internal service) is covered.

#### Scenario: 创建时 user_id 强制来自当前用户

- **WHEN** any caller creates a schedule and attempts to set `user_id`
- **THEN** the persisted `user_id` is the current user's id, regardless of what value was passed

#### Scenario: 更新他人任务被拒绝

- **WHEN** any caller invokes update on a schedule with `user_id != current_user.id` (and is not admin)
- **THEN** the data-access layer reports the schedule as not found
- **AND** no fields are changed

#### Scenario: 删除他人任务被拒绝

- **WHEN** any caller invokes delete on a schedule with `user_id != current_user.id` (and is not admin)
- **THEN** the data-access layer reports the schedule as not found
- **AND** no row is removed

#### Scenario: 触发他人任务被拒绝

- **WHEN** any caller invokes trigger on a schedule with `user_id != current_user.id` (and is not admin)
- **THEN** the data-access layer reports the schedule as not found
- **AND** no execution is enqueued

### Requirement: 创建和修改时必须校验 agent_config 归属

When `agent_config_id` is set (on create or update), the system MUST verify the referenced `AgentConfig` is owned by the current user. Admin/superadmin MAY reference any agent. When the check fails, the operation MUST be rejected with an authorization error and no data is persisted.

#### Scenario: 非 admin 用户不能用他人 agent 创建

- **WHEN** a non-admin user creates a schedule with `agent_config_id` referring to an agent owned by another user
- **THEN** the system rejects the operation
- **AND** no `ScheduleDefinition` is persisted

#### Scenario: 非 admin 用户不能把自己的任务改绑到他人 agent

- **WHEN** a non-admin user updates their own schedule and sets `agent_config_id` to an agent owned by another user
- **THEN** the system rejects the operation
- **AND** the schedule's `agent_config_id` is unchanged

#### Scenario: 管理员可以指定任意 agent_config_id

- **WHEN** an admin/superadmin sets `agent_config_id` to any value
- **THEN** the schedule is created or updated

### Requirement: 执行日志读取受 owner 校验保护

The system MUST reject any read of `ScheduleLog` whose parent `ScheduleDefinition` is not owned by the current user (admin skip). The check MUST happen at the data-access layer.

#### Scenario: 非 owner 读取日志被拒绝

```

Full source: openspec/changes/agent-schedule-toolkit/specs/scheduled-runs/spec.md

