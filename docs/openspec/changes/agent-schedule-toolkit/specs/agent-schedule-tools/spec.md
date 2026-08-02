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

The system SHALL provide a tool `delete_schedule` that deletes a schedule only if it is owned by the calling user (admin/superadmin may delete any).

#### Scenario: 删除自己的任务

- **WHEN** a non-admin user invokes `delete_schedule` on a schedule they own
- **THEN** the schedule is removed

#### Scenario: 删除他人任务被拒绝

- **WHEN** a non-admin user invokes `delete_schedule` on a schedule owned by another user
- **THEN** the tool returns an error
- **AND** the schedule is not removed

### Requirement: 工具能手动触发当前用户拥有的定时任务

The system SHALL provide a tool `trigger_schedule` that runs the schedule's `query` against the bound agent only when the schedule is owned by the calling user (admin skip).

#### Scenario: 触发自己的任务

- **WHEN** a non-admin user invokes `trigger_schedule` on a schedule they own
- **THEN** the schedule is enqueued for execution with the schedule owner as the executing user

#### Scenario: 触发他人任务被拒绝

- **WHEN** a non-admin user invokes `trigger_schedule` on a schedule owned by another user
- **THEN** the tool returns an error
- **AND** no execution is enqueued

### Requirement: 工具能查看当前用户拥有的定时任务执行日志

The system SHALL provide a tool `list_schedule_logs` that returns `ScheduleLog` records for a schedule owned by the calling user (admin skip).

#### Scenario: 查看自己的执行日志

- **WHEN** a non-admin user invokes `list_schedule_logs` on a schedule they own
- **THEN** the tool returns the schedule's log entries

#### Scenario: 查看他人任务日志被拒绝

- **WHEN** a non-admin user invokes `list_schedule_logs` on a schedule owned by another user
- **THEN** the tool returns an error
- **AND** no log entries are returned

### Requirement: 工具的用户上下文来源

The system SHALL obtain the calling user id exclusively from `runtime.context.user_id`. The system MUST NOT accept `user_id` as a tool argument from the LLM.

#### Scenario: 工具不接受 user_id 参数

- **WHEN** the LLM passes a `user_id` argument to any schedule tool
- **THEN** the tool ignores the argument
- **AND** the tool uses `runtime.context.user_id` for ownership decisions

### Requirement: 工具能优雅处理未配置用户上下文的调用

The system SHALL return a clear, LLM-friendly error message when `runtime.context.user_id` is missing or empty, without raising an unhandled exception.

#### Scenario: 缺少 user_id 时返回明确错误

- **WHEN** any schedule tool is invoked and `runtime.context.user_id` is missing
- **THEN** the tool returns a message explaining that the user context is unavailable
- **AND** no database write or read is performed

### Requirement: 工具自动注册到 agent 工具列表

The system SHALL make all new schedule tools discoverable through the existing `get_all_tool_instances()` registry, so they appear in the agent configuration UI and can be enabled by adding their names to `agent_config.context.tools`.

#### Scenario: 工具在 agent 配置页可见

- **WHEN** the application starts
- **THEN** the new schedule tools appear in the list returned by `get_all_tool_instances()`
- **AND** they can be added to an agent's `tools` configuration

### Requirement: 工具成功时返回 JSON 字符串

The system SHALL return successful tool results as a JSON-encoded string (UTF-8, `ensure_ascii=False`) so that the LLM can parse and reason about the data. Failures SHALL return a human-readable Chinese error message instead of a JSON error payload.

#### Scenario: 成功响应可被 LLM 解析

- **WHEN** a tool completes successfully
- **THEN** the tool returns a `str` that parses as valid JSON

#### Scenario: 失败响应是可读文本

- **WHEN** a tool fails (ownership violation, not found, invalid input)
- **THEN** the tool returns a readable Chinese message
- **AND** the message is not wrapped in a JSON error envelope

### Requirement: 列表工具支持分页

`list_my_schedules` and `list_schedule_logs` SHALL accept `limit` (default 20, max 100) and `offset` (default 0) parameters. The system SHALL reject `limit` values outside the allowed range.

#### Scenario: 默认分页生效

- **WHEN** the LLM calls `list_my_schedules` without `limit` and `offset`
- **THEN** the tool returns at most 20 records starting from offset 0

#### Scenario: 翻页

- **WHEN** the LLM calls `list_my_schedules` with `limit=20, offset=20`
- **THEN** the tool returns the second page of records

#### Scenario: 越界 limit 被拒绝

- **WHEN** the LLM passes `limit=500` or `limit=0`
- **THEN** the tool returns a clear error message and no data

### Requirement: admin 走同工具内部判断

The system SHALL NOT expose admin-only schedule tools (such as `list_all_schedules`). Admin and superadmin users SHALL skip ownership checks inside the same tools used by regular users; this behavior is the same convention as the existing `_is_admin` flag in `schedule_router.py`.

#### Scenario: admin 调 list_my_schedules 看到全量

- **WHEN** an admin/superadmin invokes `list_my_schedules`
- **THEN** the result contains schedules owned by all users

#### Scenario: 没有 admin 专用工具暴露

- **WHEN** `get_all_tool_instances()` is queried
- **THEN** no `list_all_schedules` or other admin-only schedule tool is registered
