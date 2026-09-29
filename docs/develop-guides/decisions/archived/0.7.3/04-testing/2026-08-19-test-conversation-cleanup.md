# 测试对话与 Project Workdir 使用统一命名并闭合清理

状态：archived
类型：testing
Owner：backend/test/live_api_cleanup.py

## 问题

真实 HTTP 测试创建的 Conversation 使用分散的标题和标记，清理只覆盖部分 E2E 标记，并且把旧线程目录当作唯一文件存储。当前 Conversation 的 Project Workdir 已归属 UserWorkspace，测试结束后可能留下软删除会话、消息/run 历史和 `workspace/projects` 下的项目目录。

## 决策

所有真实 HTTP 测试创建的 Conversation 使用统一的 `YUXI_TEST_CONVERSATION_` 标题前缀。Agent Call 与 Agent Eval 测试先创建统一标记的 Conversation，再把 `thread_id` 传给调用入口。E2E 与 integration 的 teardown 共享测试清理流程：先从 PostgreSQL 读取测试 Conversation 的真实 `uid` 与 `workdir_path`，完成路径、Workdir 独占性和 Run 终态校验；随后取消 queued Request 并回读确认收敛，再通过当前用户 API 软删除活动会话。最终清理先在第一个 `SHARE` 表锁事务中重新校验 Owner、删除会话及其消息、请求、run、工具调用、反馈、子智能体关系和统计行，提交并回读确认数据库事实；再在第二个 `SHARE` 表锁事务中重新校验 Owner并删除 UserWorkspace Project Workdir。第二个锁阻止文件删除期间并发 INSERT/UPDATE 新增 Workdir Owner；文件清理失败会显式报告，但不会回滚已提交的数据库删除。旧 E2E 标记与现有历史测试标题格式继续作为一次性兼容识别规则。

清理只处理当前测试账号下可识别的测试会话。integration 临时标准用户在删除 User 前先以自身凭据执行同一清理。目标 Workdir 被非测试会话共享或路径嵌套、路径不是 `projects/` 下的子目录、Workdir 是符号链接、Run 仍处于非终态、queued Request 未收敛，或删除后回读仍有残留时，清理失败并报告，不静默跳过。数据库发现与全部前置 guard 完成前，不允许软删 Conversation、删除文件或删除临时 Agent。

## 替代方案

- 只统一标题，不改变 teardown：无法覆盖已软删除的历史、消息/run 以及新 UserWorkspace Project Workdir。
- 继续按线程 ID 删除目录：线程 ID 不再是 Project Workdir 的持久路径，且子智能体可能共享父会话的 Workdir。
- 修改生产 Conversation 删除 API 让所有删除都物理清理 Workdir：测试清理需求不应改变用户删除后的业务保留语义，也会扩大数据生命周期影响。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 真实 HTTP 测试会话标题统一以 `YUXI_TEST_CONVERSATION_` 开头 | 新增测试绕过 helper 使用旧标题 | 各测试的 Conversation 创建请求与 `test.live_api_cleanup` helper | 搜索全部 `/api/chat/thread` 创建入口；Agent Call/Eval router unit | `viewer-notes` 等普通标题不命中历史兼容格式 | Passed |
| teardown 删除测试会话的消息、run 关联与 Conversation 行 | API 软删除后历史仍留在 PostgreSQL | `backend/test/live_api_cleanup.py` 的测试资源清理 | cleanup unit + 真实 PostgreSQL cleanup integration（29 passed） | 非测试会话、普通 request_id 与相邻 run 保留；非终态 Run 拒绝 | Passed |
| teardown 删除对应 UserWorkspace Project Workdir | 仍按线程目录清理，或误删共享/越界路径 | `user_workdir_host_dir` 与 cleanup Owner | 临时 UserWorkspace 回读；PostgreSQL Workdir ownership、提交顺序与并发锁 integration | symlink、共享/嵌套 Workdir、越界路径拒绝；数据库提交后才删文件；锁持有期间并发绑定被 PostgreSQL 阻止；文件失败显式报告 | Passed |
| E2E 与 integration 都执行同一清理流程 | 只改一套 conftest，另一套继续遗留 | 两套 pytest session fixture | 真实 HTTP integration 选测；普通用户 fixture teardown | 数据库发现失败、guard 失败、queued Request 未收敛时不删除 Conversation/文件/Agent | Passed |

## 后果

- 清理直接删除测试数据库行，必须只由已识别的测试线程 ID 驱动，并在删除前阻止非终态 Run。
- 旧残留数据可能缺少统一前缀；兼容识别规则只覆盖当前仓库已有的 E2E 标记和旧测试标题，不按模糊的 `test` 文本匹配。
- pytest 进程被强制终止时 teardown 无法执行；统一标题、metadata 与 request_id 前缀为下一次或人工精确扫除提供稳定边界，不在并行测试期间跨用户自动扫库。
- LangGraph checkpoint 与 Redis 短期事件不纳入本次数据库/文件清理；其生命周期由 PostgreSQL checkpointer 与 Redis 事件 TTL/运行收敛规则拥有，若真实检查发现仍残留再单独补充 Owner-local 清理。
