# 反馈列表用未登记名称展示并筛选已删除智能体

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/repositories/feedback.py

## 问题

仪表盘在智能体行不存在时显示「未登记智能体（slug）」，统计仍计入这些会话。反馈列表把外连接得到的 `Agent.name` 原样返回，行被删除时名称是空的。筛选下拉只请求当前仍登记的 `GET /api/agent`，已删除智能体的反馈无法按它筛选。

## 决策

反馈列表在名称为空时使用与仪表盘相同的 `unregistered_agent_name`。筛选选项来自当前可见反馈记录中的 `agent_slug`，而不是活着的智能体目录。反馈条数、分页和计数口径不变。

### 实现方案

`AgentFeedbackRepository.list_page` 仍 outer join `Agent`。`list_agent_options` 在同一可见范围上取不重复的 slug 和名称。平台接口是 `GET /api/system/agent-feedback/agents`。反馈页的下拉改读这个接口。仪表盘统计查询没有改动。

## 替代方案

- 继续使用活智能体目录：已删除智能体的反馈无法被选中。
- 只在前端把空名称换成 slug：筛选列表仍然缺少这条记录，展示文案也和仪表盘不一致。
- 删除智能体时改写历史反馈上的 slug：会改变已经发生的执行归属。

## 后果

已删除智能体在反馈列表和筛选项中显示「未登记智能体（slug）」。没有反馈记录的活智能体不再出现在这个下拉框里。同名展示依赖仪表盘已有的命名函数，不另写一套文案。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 删除智能体行后列表仍有这一条，名称是未登记文案 | 名称为空或记录消失 | list_page | test_deleted_agent_stays_named_in_feedback_list_and_filter | 直接返回 Agent.name | Passed |
| 筛选项仍包含该 slug | 选项只来自活目录 | list_feedback_agent_options | 同上 | 调用 GET /api/agent | Passed |
| 前端下拉使用反馈选项中的已删除名称 | 下拉没有 gone | AgentFeedbackList | turnTraceNavigation.test.js 中「反馈筛选使用记录中的智能体」 | loadAgents 改回 getAgents | Passed |
