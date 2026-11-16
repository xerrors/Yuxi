# 已创建智能体切换执行后端

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/definitions.py

## 问题

Agent 的执行后端决定运行图、Context Schema 和处理流程。管理员创建后需要能为自建 Agent 更换后端，同时保留目标后端认可的配置，并避免切换覆盖接收队列、执行中的 Run 或运行时清理责任。

## 决策

### 实现方案

有管理权限的用户可在 Agent 编辑页为非内置 Agent 切换已注册的执行后端。HTTP 更新仍使用 `PUT /api/agent/{agent_id}`，由 `AgentUpdate.backend_id` 承载选择；service 在一个事务内完成活动工作裁决、目标 Schema 配置准备及定义写入。Repository 锁定 Agent 后按 Thread 字典序排他锁定关联 Session，再检查活动 Turn、未终态或待清理 Run，以及 pending Input。Thread 锁使切换与既有 Thread 的输入接收串行化，Agent 行锁串行化定义管理与新 Thread 创建。存在未完成工作时返回 409，内置 Agent 的后端固定不变。

若既有 Thread 快照含目标后端无法解释的同名字段，返回 409。切换时，service 先按两个后端兼容的配置声明过滤既有配置，再校验并合并本次提交中目标 Schema 可写字段。兼容配置得以保留，不兼容字段被移除。前端编辑器读取目标后端自己的配置描述，加载期间禁用配置和保存，异步旧响应不能覆盖当前草稿。只读用户与内置 Agent 不提供可用的后端选择。

Agent 后端定义属于 `modules/agents/runtime/agent_backends`，定义更新和配置转换属于 Agent service，锁与持久工作查询属于 Agent repository，HTTP router 只适配字段和错误码，编辑交互属于 Agent 前端模块。后续新 Run 使用 Agent 当前后端；既有 Thread 的 `config_snapshot` 仍是其创建时快照，运行时按新后端 Schema 读取兼容字段。创建新 Thread 时则从当前 Agent 配置生成快照。

## 替代方案

- 删除并重建 Agent 会改变稳定 slug、现有 Thread 关联及共享授权，不能满足更新现有资源的需求。
- 只修改前端无法建立后端授权、活动工作检查和配置 Schema 转换。
- 允许活动工作期间切换会使运行准备读取的 Agent 后端与定义更新发生竞争。

## 后果

后端切换不修改历史 Thread 配置快照，切换后的后续 Run 以当前 Agent 后端装配执行图。目标 Schema 不声明的旧 Context 字段在 Agent 持久配置中删除，Thread 快照中的字段则由运行时 Schema 忽略。未来增加不兼容 LangGraph checkpoint Schema 的后端时，需要另行定义既有 Thread 的 checkpoint 兼容策略。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 |
|---|---|---|---|---|
| 切换自建智能体后端并按目标 Schema 保存配置 | 响应与数据库不一致、旧字段泄漏、旧 Thread 快照被覆盖 | 智能体定义服务与仓储 | `pytest test/integration/api/test_agent_backend_switch.py` | 切换时保留兼容提示词，移除测试后端字段，原 Thread 快照不变 |
| 未完成工作阻止切换 | 待处理 Input、活动执行或未完成清理被覆盖 | `AgentRepository.agent_has_unfinished_work` | 同一集成文件通过 PostgreSQL 回读并发锁与最终状态 | 检查等待 Thread 行锁，随后看到并发提交的 Input 并返回 409 |
| 编辑器按当前选择加载配置 | 较早的详情响应覆盖当前后端草稿 | `AgentEditModal.vue` | `node --test test/unit/agentPermissionRepair.test.js` | 详情响应逆序返回时，当前草稿及 Schema 保留 |

集成测试在隔离 PostgreSQL 中注册测试后端，生产注册表保持官方现有后端。生产 worker 上的多后端执行和真实浏览器截图未验证。
