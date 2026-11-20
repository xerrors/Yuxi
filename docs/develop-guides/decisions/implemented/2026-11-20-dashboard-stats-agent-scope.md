# 看板统计不再按 agents 行过滤会话

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/system/repositories/dashboard.py

## 问题

`sessions.agent_id` 是无外键、无重指派的字符串列：删除智能体（非内置）只删 `agents` 行，历史会话保留悬空 slug。看板读模型有 26 处 `JOIN agents ON sessions.agent_id = agents.slug`——其中 25 处 Agent 列根本不出现在结果里，仅作过滤；1 处（会话分布）inner join 后直接取 `Agent.name`。效果是这些会话从活跃用户 24h/30d/日活曲线、会话/消息/工具计数、调用与会话时序、token 时序、多维分析中整体消失：用户"明明用过"却不活跃、总数对不上明细，且随删除的智能体越多偏差越大。会话与用户存在即为事实，Agent 行缺失不改变"谁在什么时候用了多少次"，因此这属于数据正确性缺陷。

## 决策

统计口径以会话与用户为事实主体；`agents` 只用于命名富化，缺失时以显式"未登记智能体（slug）"系列呈现，不丢弃也不裸显 slug。

### 实现方案

- 数量与时序类 25 处删除 filter-only join（用户活跃三项、基础计数三项、工具调用统计、调用时序四处、会话多维九处、会话时序两处、token 时序一处等）。
- 分组类不再丢弃：`get_agent_session_distribution` 改 `outerjoin`，名称经 `unregistered_agent_name(slug)` 回退；`get_agent_analytics` 先按登记智能体出系列，再对"会话/工具计数中出现但 `agents` 表没有"的 slug 补独立系列，`total_agents` 与 `agent_names` 含未登记项。
- 展示名回退同口径的四处既有位置（会话审计、筛选选项、线程详情、调用时序 agents 指标的 `agent_names`）经同一 helper 兜底，前端不再拿到裸 slug 作回退显示。
- 已删除智能体的会话只证明"用过"，不证明智能体存活：`agent_deleted`/`is_deleted` 标记保持按 `agents` 行缺失判定。
- 已知限制：`agent_id` 为空串的脏行（历史遗留形态）计入调用时序总数但不成系列（时序按空 category 跳过），与分布/分析显示"未登记智能体（unknown）"存在口径差；该形态仅脏数据可达，收敛需要单独的写入校验决策。

## 替代方案

- 保持 inner join 并在删除智能体时级联清理或重指派会话：删除语义被统计绑架，且历史归属会被伪造。
- 只隐藏未登记系列：用户活跃与总数仍漏数，问题主体不在展示列表而在过滤。
- 把 slug 原样作为名称显示：无法区分"真实智能体叫这个名字"与"悬空引用"，审计误导。

## 后果

- 活跃用户、会话/消息总数、各时序与分析数字相对旧版本阶跃上调（旧值偏小是缺陷而非语义）；跨版本同比须注明口径修正。
- `total_agents` 自此包含未登记系列；按 agent 分组的图表可能出现"未登记智能体（slug）"条目，属预期。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 悬空 slug 会话照常计入 24h/30d 活跃、日活曲线与基础计数；仅删除态会话不计 | 用户活跃与总数漏数 | DashboardRepository.get_user_activity_stats / get_basic_stats | `pytest test/unit/services/test_dashboard_user_activity_agents.py`（sqlite 真实 SQL 5 例） | 把 24h 的 join 加回 → 两条用例红；恢复 basic_stats 的 join → `total_sessions` 红 | Passed |
| 分布与分析不再丢弃未登记系列，名称为"未登记智能体（slug）" | 分组整组消失或裸显 slug | get_agent_session_distribution / get_agent_analytics | 同文件分布标签与分析系列用例 | 分布改回 inner join → KeyError 红；停掉未登记补入循环 → `agent_names` 断言红 | Passed |
| 既有看板期望与新口径一致 | 期望漂移 | test/unit/services/test_dashboard_service.py | 16 passed（期望按修正口径上调并注明来源） | — | Passed |
| 看板 API 端到端不受影响 | 响应形状回归 | api/routers/dashboard | `pytest test/integration/api/test_dashboard_router.py`（真实 HTTP）13 passed；`test_dashboard_vm_stats_match_live_control_plane` 依赖真实 CUBE 控制面环境变量，本地未执行 | 不适用 | Passed |
| 调用时序 agents 指标对悬空 slug 输出未登记展示名 | 前端按 slug 裸显回退，与本 PR 不变量冲突 | get_call_timeseries | `pytest test/unit/services/test_dashboard_user_activity_agents.py::test_call_timeseries_agents_series_uses_unregistered_name`（sqlite 注册 timezone/to_char 逻辑钉桩） | 删去 agent_names 补全行 → 断言 KeyError/裸显红 | Passed |
