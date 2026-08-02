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

- **WHEN** a non-admin user requests logs for a schedule they do not own
- **THEN** the data-access layer returns "not found"
- **AND** no log entries are returned

### Requirement: 数据层 owner 校验是兜底而非可选

The system MUST implement owner-aware methods on `ScheduleRepository` (e.g. `get_by_id_for_user`, `update_for_user`, `delete_for_user`, `list_logs_for_user`) and HTTP routes and `@tool` implementations MUST use them. Plain `get_by_id` / `update` / `delete` without a `user_id` argument MUST NOT be exposed to user-facing entry points.

#### Scenario: 路由改用 owner-aware 仓储方法

- **WHEN** the HTTP schedule routes are invoked
- **THEN** the routes call owner-aware `ScheduleRepository` methods that include the current user id in the data-access query

#### Scenario: 工具改用 owner-aware 仓储方法

- **WHEN** any new `@tool` reads or writes a `ScheduleDefinition`
- **THEN** it calls owner-aware `ScheduleRepository` methods with `runtime.context.user_id`

### Requirement: admin/superadmin 跳过用户隔离

The system MUST treat admin and superadmin users as allowed to read/write any schedule and to reference any `agent_config_id`. The skip is consistent with the existing `_is_admin` behavior in `schedule_router.py`.

#### Scenario: 管理员可操作任意用户的任务

- **WHEN** an admin or superadmin invokes a read, create, update, delete, trigger, or log operation on any schedule
- **THEN** the operation proceeds without the user-id ownership check failing
