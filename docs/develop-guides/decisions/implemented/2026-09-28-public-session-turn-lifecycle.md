# Public Session 与 Turn 生命周期

状态：implemented
类型：architecture
Owner：backend/package/yuxi/services/agents/public_api.py

## 问题

Session 原先是 Thread 的别名，Turn 等同 Request。运行中追加输入、steer 和审批续跑会产生不同 Request/Run；用最近一次执行推断一轮结果会错绑输入和输出。产品前端若继续使用旧创建与运行入口，也无法与外部调用方共享 Public 输入协议。

## 决策

`agent_turns` 持久化一轮的身份和资源作用域，`AgentRunRequest.turn_id` 与 `AgentRun.turn_id` 记录明确归属。Turn 状态从关联 Request/Run 投影，不保存第二套可漂移的执行状态。历史普通请求按现有 Request ID 回填 Turn；历史 resume 仅沿明确父 Run 链关联，不按时间猜测。队列仍由 `agent_request_service.py` 和 `agent_request_queue_service.py` 拥有，事务提交后才投递 worker。

Public Session 接受产品 JWT 和绑定 APP 的 API Key，两者保持独立资源命名空间。Session 可为空创建或带首条消息创建。消息事件的 `mode=follow_up` 保持 FIFO 并开启后续 Turn；`mode=steer` 将活跃输入绑定当前 Turn，省略模式按 steer。前端明确发送 follow-up。输入幂等意图包含模式、内容和参数；控制事件用持久 receipt 固定首次目标。同一 Key 改变事件返回 `409`。

空 Session、带首条消息的 Session 与原生 Thread 首条请求共用创建意图；同一 Key 改变标题、模型、审批模式或输入须拒绝。带首条输入的 Conversation、Message 和 Request 在同一事务内提交，失败不会留下占用幂等键的空会话；已持久化的旧创建键继续可重放。Public 多模态内容块按原顺序进入模型并从持久消息投影 Items，内联图片保留原始 MIME 类型。

空 Session 复用产品线程创建流程：数据库提交后再物化工作目录。若物化暂时失败，Public 返回可重试的 `503` 与已持久化的 Session ID；同一键恢复后重试，不另建会话。

运行中取消固定目标 Turn 并请求取消活跃 Run；产品携带目标 Run ID，在锁内发现交接变化时拒绝，避免误取消 FIFO 下一轮。空闲无目标取消返回空操作。等待审批/回答时取消返回 `409`，直到 checkpoint 清理具备可验证的执行 Owner。产品审批/回答以 `yuxi.session.input.resume` 扩展明确指定 Turn 与父 Run，续跑仍归同一 Turn。官方 `tool_result` 不等同于产品 checkpoint 输入，当前明确拒绝。

Session SSE 按持久 Turn 关系接续相关 Run 的临时事件，过程游标为 `<run_id>:<seq>`，终止游标为 `<run_id>:end`；Session、Turn 和 Items 查询提供断线后的持久结果。`server/routers/public_v1/agents.py` 拥有 HTTP 契约与响应，`services/agents/session_events.py` 拥有观察流程，`repositories/agents` 与 schema 拥有关联和幂等约束。前端的创建、消息、取消、恢复及运行订阅使用 Public HTTP；历史、队列管理和审计仍使用产品接口。Agent Call 在 OpenAPI 标记弃用，保留仓库外消费者的迁移窗口。

## 替代方案

- 继续把 Turn 视为 Request：无法表示引导和审批后的跨 Run 归属。
- 新建独立 Turn 执行状态机：会复制 Request/Run 状态及恢复责任。
- 前端按 JWT 走旧入口，外部 Key 走 Public：两套输入语义持续分叉。
- 把审批回答伪装为官方工具结果：缺少可信 call_id 与外部工具执行事实。

## 后果

Business Schema 从当前 main 的 v9 升至 v10，并对旧 Request/Run 做确定性回填。Public 仍是选定子集：单次只接受一个输入事件，Items 目前只展示用户和助手 Message，SSE 沿用 Yuxi envelope，等待中的取消与官方工具结果尚未实现。原生 Thread/Request、产品历史及队列管理仍有实际消费者，不能仅凭新路由上线删除。旧 Agent Call 的同步等待和 `choices` 结果也需外部调用方迁移后才能移除。

## 验证

`test_schema_migration_version.py` 在真实 PostgreSQL 重复升级并检查关联；`test_deterministic_agent_path_e2e.py` 通过真实 HTTP、worker、SSE 和持久结果检查输入、取消、断流与重放。Web 的 API 契约单测、lint、构建及真实页面网络请求验证产品入口。负向案例覆盖同键不同意图、跨 APP/用户、错误 Turn/Run、非支持事件与无 checkpoint 清理的等待取消。命令及未执行范围以 PR 记录为准。
