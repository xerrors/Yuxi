# Agent 生命周期与服务边界

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/services/inputs.py

## 问题

普通消息曾先进入 AgentRunRequest，等待恢复直接创建 Message 和 Run；两条路径共用 request_id 却没有同一个接收实体。Thread、Session、队列、Run 和结果各自推断工作状态，steer 还会改写排队请求的 Turn 归属。旧聊天、Invocation 和 Public 入口重复管理幂等、事务和权限，使跨执行段的状态与结果难以从持久关系直接验证。

## 决策

### 实现方案

Thread 是长期对话和调度隔离范围，Public Session 只在 HTTP 边界将名称映射到同一 Thread。每批消息规范化为有序的持久 Input；InputReceipt 保存接收序号、命令类型、意图摘要和作用域幂等键。排队 follow-up 尚无 Turn。调度器按 Thread 锁领取 FIFO 队头时，在同一事务创建 Turn、首段 pending Run，并固定 Input、Message 与 Run 归属。一个 Turn 可有多段 Run；工具循环不自行分段。

[输入用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/inputs.py)负责校验、配置冻结、接收与回执；[调度器](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/scheduler.py)负责领取、steer 批次和提交后投递；[Turn 用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/turns.py)负责等待点、恢复、取消与最终结果；[Run 用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/runs.py)负责执行 owner、lease、终态和 checkpoint 收敛。PostgreSQL Schema 约束 Turn 的当前 Run、结果 Run 及 Input 的消费归属；查询从这些关系投影，不沿相邻 Run 猜测结果。顶层用例拥有提交；ARQ 投递和取消信号只在 owning transaction 提交后发生。持久 pending Run（含子 Run）按原 Run ID 补投，子 Run 在自身 Turn 复核执行 ownership。初始会话的目录创建时机见[目录删除与会话继续执行](2026-11-27-agent-task-workdir.md)。

输入优先级、Thread 级 steer 合批与暂停保留规则由[优先输入队列决策](2026-10-01-thread-priority-input-queue.md)接续。worker 在工具批次结果和 PostgreSQL checkpoint 已保存的安全边界将旧 Run 标记 yielded，再以同一 Turn 建立下一 Run；无 steer 的普通工具调用继续原 Run。等待点绑定 interrupted Run，普通消息在 waiting 期间被拒绝；结构化回答或审批一次性消费等待点并创建恢复 Run。取消暂停待消费队列；执行树与等待 checkpoint 清理完成前 Turn 保持 cancelling，checkpoint 清理中断后可沿已保存的取消标记重入。失败也暂停后续队列，用户显式继续才领取下一输入。

[Public Thread 路由](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/api/routers/public_v1/agents/threads.py)是 Agent 对话主协议，Web、CLI、定时任务和评估调用方使用它背后的同一领域用例。认证边界把 JWT 和未绑定 APP 的 full Key 映射到所属用户的产品作用域，绑定 APP 的 Key 则解析终端用户；repository 与产生文件副作用的边界再次核对作用域。Thread 只能归档，Project 删除先检查在途 Turn/Input，再归档其所有普通与子 Thread；删除 Agent 则拒绝仍有活跃 Turn、待处理 Input 或未清理 Run 的情况。Session 路由只调整路径、字段与事件名，不建立独立实体。

[事件用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/subscriptions.py)以 Turn 为订阅范围，跨 Run 结合 Redis 短期事件和 PostgreSQL 快照恢复；最终状态、消息、输出和用量仍以持久事实为准。Langfuse 的根观察按 Turn 关联多段 Run，前端以 Turn 展示同一轮工作。独立的 Agent eval/Invocation 对话入口和 Request 业务状态机不再注册或存储；CLI 评估从 Dataset 读取样例后通过 Public Thread 执行。

## 替代方案

| 方案 | 取舍 |
|---|---|
| 继续保留 Request 状态机 | 普通消息与恢复仍需同步两套生命周期和结果指针。 |
| 排队时预建 Turn | 未开始的输入占用工作身份，取消与队列展示产生多余状态。 |
| 每次工具结果或 steer 都创建 Run | 工具循环与连续 steer 产生无意义分段；安全边界只需要一个待消费批次。 |
| 增加通用命令总线、事件溯源或兼容层 | 当前接收、调度和执行可由 PostgreSQL 事务及现有 ARQ 表达，额外机制无独立 consumer。 |

## 后果

新 Schema 只适用于独立的新数据环境；旧 Request 数据与客户端行为不做迁移或读取降级。每个接收事件有持久回执，但只有领取后的 Turn 才拥有工作状态；同一 Turn 的多段 Run 共用最终结果和观察身份。Redis 断线或事件过期不改变 PostgreSQL 业务事实。子 Agent 使用自身 Thread 的 FIFO、独立 Turn、等待点与恢复，created_by_run_id 保存委派创建者；子生命周期和公开事件由[原生事件与 Agents API 决策](2026-09-30-langgraph-agents-events.md)接续。

旧能力不存在：AgentRunRequest 表与状态机、旧聊天和 Invocation/eval 专属对话接口、按 request_id 推断结果、排队输入升级为 steer、Thread 删除与旧客户端兼容写入。重新引入条件：明确的新 consumer、数据迁移方案和独立决策。

## 验证

真实 PostgreSQL Schema/迁移与并发测试核对 Input/Receipt 约束、Turn/Run 外键、FIFO、作用域、owner/lease 和清理；真实 HTTP 测试核对幂等冲突、Thread/Session 别名、归档、权限与结果错绑拒绝；完整 API→PostgreSQL→Redis→worker E2E 回读 follow-up、steer、等待恢复、取消、跨 Run SSE 和最终产物。Web 浏览器网络、CLI 真实调用和 lint/unit/build 核对消费方只使用 Public 对话协议。实际命令、结果与环境限制记录在交付 PR。

内部执行链的模块收拢与尚未补齐的验收范围见[执行链收敛方案](../proposed/2026-09-29-agent-lifecycle-framework.md)。
