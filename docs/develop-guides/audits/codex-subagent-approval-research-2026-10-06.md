# Codex 子会话审批调研

供 Yuxi 会话协作维护者设计人工审批入口使用。范围是子会话工具执行审批的提出、用户提示、决策归属及清理；不评估自动审批策略，也不引入通用任务暂停机制。阅读前提是了解 Thread、Turn、Run 与协作树。

## 证据范围

调研日期为 2026-10-06。已打开 OpenAI 官方 [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[App Server](https://learn.chatgpt.com/docs/app-server) 与 [Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review) 文档，并只读核对官方 `openai/codex` 的提交 [`822e58cc3d666166c7446c5b1ea2e52f5d09594c`](https://github.com/openai/codex/commit/822e58cc3d666166c7446c5b1ea2e52f5d09594c)，提交时间为 2026-10-06 03:58:55 UTC。源码链接固定在该提交，避免后续版本改变调研结论。

官方文档覆盖产品行为；公开源码核验范围是 Codex core、App Server 和 CLI TUI。未检查闭源桌面 App 的实际界面，不能把 CLI 的布局、快捷键或当前主分支实现当作本机桌面 App 的已验证事实。

## Codex 怎样让用户看到子会话审批

交互 CLI 即使停留在主会话，也会显示非当前智能体线程的审批浮层，并标明来源线程。用户可按 `o` 打开来源线程。无法提出新审批的非交互流程使相关动作失败，再向父工作流返回错误。[官方说明](https://learn.chatgpt.com/docs/agent-configuration/subagents#approvals-and-sandbox-controls)

App Server 为新建线程自动建立客户端监听。TUI 接收子线程请求后，将请求保存到该线程的待处理集合，同时在当前视图展示交互入口。未跟踪线程的请求须确认其父线程由该 TUI 管理，避免其他根会话的审批混入当前会话。已跟踪的递归后代沿同一机制处理。请求的显示位置取决于客户端当前视图。[客户端监听](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/app-server/src/lib.rs#L1283-L1302)、[根会话归属检查](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/app_server_events.rs#L718-L762)、[非当前线程请求展示](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/thread_routing.rs#L1439-L1487)

TUI 还在输入区显示哪些非当前线程存在待处理审批。多个请求进入浮层队列，切换线程后可从线程事件集合恢复未解决请求。[待审批线程摘要](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/thread_routing.rs#L1124-L1153)、[审批队列](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/bottom_pane/approval_overlay.rs#L172-L230)、[待处理请求重放](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/thread_routing.rs#L429-L473)

## 展示位置与执行归属如何分开

命令审批是服务端发起的 JSON-RPC 请求。外层 `requestId` 标识一次客户端交互，参数中的 `threadId`、`turnId`、`itemId` 标识实际工具动作。子命令另可使用 `approvalId` 区分同一命令 item 内的多次审批。[协议流程](https://learn.chatgpt.com/docs/app-server#command-execution-approvals)、[请求装配](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/app-server/src/bespoke_event_handling.rs#L785-L823)

用户在当前视图批准或拒绝时，浮层使用请求携带的来源 `threadId`，客户端再以该线程与审批 ID 查找原始 `requestId`。服务端收到决策后向原始 `CodexThread` 提交 `Op::ExecApproval`，其 `turn_id` 保留原值；文件修改审批同样回到原线程。原动作继续或被拒绝，最终通过 item 完成事件表示结果。[用户决策路由](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/bottom_pane/approval_overlay.rs#L375-L406)、[客户端请求匹配](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/app_server_requests.rs#L208-L280)、[原线程执行恢复](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/app-server/src/bespoke_event_handling.rs#L2079-L2090)

这条人工审批链路等待客户端用户决策，core 的原线程通过待审批回调接收结果；未发现它为审批自动创建父会话模型轮次。`approvals_reviewer = "auto_review"` 是另一条显式配置的链路，由独立 Reviewer 决策；父模型不因父子关系取得审批权限。[原线程等待回调](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/session/mod.rs#L2747-L2852)、[独立自动审批说明](https://learn.chatgpt.com/docs/sandboxing/auto-review#how-auto-review-works)

## 权限继承与不能照搬的例外

spawn 先根据父线程有效配置建立子配置、应用角色配置，再重新应用当前父 Turn 的 `approval_policy`、`approvals_reviewer`、工作目录与权限快照。因此自定义角色默认值不能覆盖这些 live runtime 选择；这不等同于子会话产生的每个审批请求都已被父会话授权。[官方权限说明](https://learn.chatgpt.com/docs/agent-configuration/subagents#approvals-and-sandbox-controls)、[配置应用顺序与继承](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/agent/child_config.rs#L51-L85)、[运行期权限复制](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/agent/child_config.rs#L166-L192)

模型主动调用的 `request_user_input` 在核验版本只允许 root thread。MCP 工具审批若启用 `ToolCallMcpElicitation`，使用原线程 elicitation；未启用时采用 `request_user_input` 的路径，非 root 会被明确拒绝，并要求向父智能体报告阻塞。在 `approval_policy = "never"` 下，需要审批的 MCP 动作也会被拒绝。[主动问答限制](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/tools/handlers/request_user_input.rs#L67-L71)、[MCP 审批分支](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/mcp_tool_call.rs#L1601-L1704)

上述例外说明不同人工交互入口存在能力差异；它不构成 Yuxi 禁止已有子会话问答能力的依据。Yuxi 设计应以自身已授权的人工交互契约为准。

## 请求解决、取消与视图关闭

App Server 用 `serverRequest/resolved {threadId, requestId}` 表示请求已回答或清理。中断 Turn 时，待审批请求被清理，core 回调销毁按 Abort 处理。TUI 根据解决事件与 Turn 终态移除待审批入口，避免切换视图后重现失效请求。[协议清理说明](https://learn.chatgpt.com/docs/app-server#toolrequestuserinput)、[请求回调清理](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/app-server/src/outgoing_message.rs#L213-L230)、[审批回调语义](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/session/mod.rs#L2747-L2753)

TUI 关闭审批浮层会提交 Cancel 或 Deny，并清空该浮层队列；这与普通切换线程不同。没有订阅者且空闲的线程卸载时，服务端取消该线程尚未解决的回调。桌面 App 标签页关闭是否触发执行取消未核验，Yuxi 不应据此推断“关闭子会话标签等于拒绝审批”。[浮层取消](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/bottom_pane/approval_overlay.rs#L503-L539)、[线程卸载清理](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/app-server/src/request_processors/thread_lifecycle.rs#L428-L444)

## 核验结果与边界

`Inspected`：源码及已有测试包含非当前线程审批弹入当前视图、保留命令/目录/理由、拒收其他根会话的请求、Turn 完成立即清除审批提示、中断 Turn 清理原请求、角色配置后重施父运行期权限、子线程主动问答被拒绝。对应测试为：

- [`inactive_thread_approval_bubbles_into_active_view`、`inactive_thread_exec_approval_preserves_context`、`inactive_thread_approval_badge_clears_after_turn_completion_notification`](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/tests.rs)
- [`subagent_approval_respects_root_ownership`](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/tui/src/app/tests/startup.rs#L1397-L1477)
- [`turn_interrupt_resolves_pending_command_approval_request`](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/app-server/tests/suite/v2/turn_interrupt.rs#L223-L328)
- [`spawn_agent_reapplies_runtime_sandbox_after_role_config`](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/tools/handlers/multi_agents_tests.rs#L2297-L2424)
- [`multi_agent_v2_request_user_input_rejects_subagent_threads`](https://github.com/openai/codex/blob/822e58cc3d666166c7446c5b1ea2e52f5d09594c/codex-rs/core/src/tools/handlers/request_user_input_tests.rs#L28-L83)

`Not run`：未构建或执行 Codex 测试，未在桌面 App 人工触发子会话审批；本记录提供实现证据和设计参考，不声称桌面 App 当前二进制或 Yuxi 新行为已通过验证。

## Yuxi 现状与建议

当前实现符合[统一 Session 协作决策](../decisions/implemented/2026-10-03-session-cooperation.md)的独立执行约束。以下为源码核对与拟议设计，尚未实现。

### 已有能力与缺口

`services/cooperation.py::create_session` 继承派发 Run 的 `tool_approval_mode`。`services/runs.py::settle_checkpoint` 将子 Turn 的等待点与协作通知写入同一事务。`notify_turn_state` 的收件人为自身与直接父会话。`runtime/middlewares/cooperation.py::abefore_model` 在下一次模型调用前消费本会话收件箱，因此运行中的直接父模型可以得知子会话等待审批；更深后代的通知不直接进入根会话收件箱。`wait_sessions` 可以显式观察全树目标，恢复只作用于已在等待协作更新的 Turn，不自动启动已结束的父 Turn。

浏览器的 `useSessionCooperation` 每 3 秒读取整树状态，但只向状态汇总和协作列表提供成员数据。`SessionWorkspace` 与 `useApproval` 的审批展示绑定当前会话；未打开的子会话没有对应工作区来显示其审批卡片。主会话 SSE 的 `stream_thread_events` 也只读取自己的 Input、Turn 与 Run。缺少常驻的树内待处理入口，会让只查看主会话的用户错过审批。

`tree_snapshot` 已返回成员 `turn_id`、`current_run_id` 和 `waiting_for`，审批详情可以通过现有 `get_turn_snapshot` 读取真实 `waitpoint`。`resume_turn` 已校验用户、APP、源会话、Turn、等待点及 interrupted Run，并一次性验证全部调用的 `call_id`、顺序与允许决策。相同幂等请求复用结果。集中展示可复用这条消费链路，不需要新建审批表、迁移 Schema 或改写审批归属。

### 推荐交互与执行约束

1. 在主工作区提供始终可发现的待处理数量与入口；分栏时属于主工作区，展开后属于外层工具栏。用户查看文件、子会话或关闭状态面板时仍能看到提示。状态面板继续只显示汇总。
2. 主工作区集中展示一张当前审批卡片与待处理请求清单，注明来源会话名称、树路径、具体动作及参数；提供批准、拒绝和打开源子会话。多个请求分别保留身份，切换处理对象不覆盖草稿或其他会话的审批。子会话自己的卡片与主入口消费同一等待点。
3. 从整树快照筛选 `turn_status=waiting` 且 `waiting_for=approval/answer` 的成员，按需读取其 Turn；仅展示等待点 `run_id` 与当前 Run 一致的请求。请求以源 Session、Turn、Run、waitpoint ID 识别，不用名称作为提交标识。分清会话数量与同一等待点中的工具调用数量。
4. 用户决定提交到来源子会话的现有 resume 入口，在该子会话原 Turn 创建下一 Run。子会话等待仅阻塞自己的执行，父会话和兄弟继续运行。子请求也不锁住主会话输入框。主会话自己的审批与子请求保持独立，沿用主请求已有的输入限制。
5. 刷新、标签切换或重新打开页面后从持久状态恢复待处理集合。普通关闭提示或子会话标签保留请求；拒绝工具与取消子 Turn 使用各自明确操作。已解决、已取消、等待点变更的卡片移除，提交遇到冲突重新读取来源 Turn。失败状态保留可重试反馈，不把本地移除卡片当作审批成功。
6. 根模型知晓与用户审批分开。如需根模型获知所有后代的人工等待，可将该状态通知额外投递给根会话并去重，在既有模型安全边界消费。通知不创建新的父 Input、不唤醒已结束父 Turn，也不赋予父模型批准工具的权限。现有子会话提问能力保留，采用同样的来源标识与用户交互入口。

更小的替代方案是在主工作区只显示待审批提示，点击打开来源子会话处理。它复用已有子会话卡片，改动更少；多个并发请求需要用户逐个进入不同标签。集中审批为建议方案，界面选择通过用户弹窗征询，尚未作为已实现行为记录。

### 后续实施的验收证据

- 浏览器只停留在根会话时，直接子会话与多级后代发起审批都出现来源明确的提示。父会话继续可输入，其他成员可继续完成任务。
- 多成员同时等待时，分别提交正确来源的批准/拒绝。回读数据库确认原子 Turn 接续、父 Turn 未被误恢复，工具文件或结果与用户决定一致。
- 根入口与源子页重复提交、另一浏览器先处理、取消后提交及延迟快照均不能复活旧审批。关闭标签与页面刷新保留尚待处理的请求，读取失败提供恢复入口。
- 不同用户、APP、协作树的请求不得混入或被跨作用域处理。同一等待点批次不能错配 `call_id` 或缺少部分决策。

Yuxi 链路为 `Inspected`；上述交互、通知扩展和验收均为 `Not run`。本轮只新增调研记录，保留已有源码改动，不执行真实用户审批或停止任务。
