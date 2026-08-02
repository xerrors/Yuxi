# agent-schedule-tools

Yuxi-Know 的 agent 在对话中可以直接调用以下 7 个 schedule 管理工具，无需离开对话流。

## 可用工具

| 工具名 | 说明 |
|---|---|
| `list_my_schedules` | 列出当前用户的定时任务；admin 看到全部 |
| `get_schedule` | 查看单条定时任务详情 |
| `create_schedule` | 创建新的定时任务；agent_config_id 必须归属当前用户 |
| `update_schedule` | 更新定时任务字段；agent_config_id 归属校验同 create |
| `delete_schedule` | 删除定时任务 |
| `trigger_schedule` | 立即触发一次（不影响原 cron 周期） |
| `list_schedule_logs` | 查看历史执行日志 |

## 用户隔离

所有工具按 `runtime.context.user_id` 强制 owner 隔离。

- 普通用户只能看到 / 操作自己创建的 schedule
- 绑定 `agent_config_id` 时必须归属当前用户（admin 跳过）
- 失败统一返回中文错误消息，不区分"未找到"与"无权访问"，避免泄露存在性

## 在哪里开启

`SubAgent` / `Agent` 配置页 → 工具列表 → 勾选上述 7 个工具。

`BaseContext.tools` 默认列表中**未**包含这些工具（避免自动启用带来预期外行为）。
