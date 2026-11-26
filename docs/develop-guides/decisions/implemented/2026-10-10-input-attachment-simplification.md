# 提交级附件与不可变接收回执

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/services/inputs.py

## 问题

读者为输入队列与附件维护者，前置知识为 Input 接收、Message 消费投影与 Run 执行。逐消息附件要求接收时记录位置、消费时逐消息绑定；Receipt 的消费回填重复保存执行归属。本决定保留多消息提交和多个 Input 同 Run 消费，简化附件归属与回执职责，取代[输入投影分离决定](2026-10-10-input-message-projection.md)中逐消息文件归属及回执消费回填的部分。

## 决策

### 实现方案

Input 保存有序原始 messages、提交级 attachment_file_ids 和冻结配置。HTTP 创建 Session 与消息事件统一在 yuxi.attachment_file_ids 接收最多 20 个文件，拒绝旧逐消息字段；同一幂等键的正文或附件列表变化返回冲突。附件只在接收时绑定 input_id，消费事务创建完整消息后一次绑定该 Input 最后一条用户 Message。保留 Message.input_position 的原始定位和唯一约束，附件复合外键保证最终消息来自同一 Input。

Receipt 固定记录命令意图及目标，提交、转换、取消输入只指向 Input；消费不修改回执。执行控制保留自己的 Turn/Run 目标，输入的执行归属由 Input 查询和 Run 生命周期事件读取。Web、CLI、Demo、协作和定时任务同步采用该读取方式。正式历史直接按 message_id 装配附件；pending/cancelled Input 返回原文、原始文件 ID 与现存附件，items 为空。

取消仅停止执行，保留绑定并允许文件继续准备；删除附件沿用删除行和字节的行为，不保留历史占位。原始 Input 保留文件 ID，不承诺已删文件可回放。保持现有 Session 锁、连续 ready steer 消费、follow-up FIFO、配置冻结和崩溃恢复边界。数据库初始化遵循[当前 Schema 基线](2026-10-19-schema-baseline-one.md)，由 [app-init](2026-10-18-app-initialization.md) 建立空库或校验当前版本，不自动迁移或清空现有数据库。

## 替代方案

- 一 Input 一 Message：删除多消息提交能力并不能简化执行合批，保留现有多消息协议。
- 附件只绑定 Input：历史与模型装配需要推导锚点，保留一次性的 message_id 以复用现有读取路径。
- 保留逐消息附件：保留额外位置及校验，选择明确的提交级语义。
- 文件软删除或隔离准备区：扩展存储生命周期与授权机制，不纳入本次简化。

## 后果

协议与 Schema 不兼容，每次提交的附件上限从逐消息计数收紧为总计 20。连续 steer 当前没有合批体积上限，文件准备仍持有 Session 锁。文件处于共享 Workdir，未消费只意味着未自动注入模型上下文，不是工具读取权限隔离。文件删除后原始引用不可回放；取消且准备持续失败时沿用现有重试与删除限制。本决定不扩大这些边界。

## 验证

旧能力不存在：删除逐消息附件协议、Attachment.input_position、消费时 Receipt 执行归属回填；不保留兼容路径。

重新引入条件：出现经过确认的一次提交内逐消息文件归属需求，或文件审计/隔离承诺变化时，另行决策。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 每个 Input 的文件绑定自己的末条 Message | 多 Input 同 Run 错绑 | input repository、附件 FK | PostgreSQL 附件测试、HTTP files、projection recovery E2E | 跨 Input FK、回滚、重复消费、四条消息完整顺序及逐消息图片 | Passed |
| 回执固定，执行归属从 Input 查询 | 重试/SSE/协作错绑 | inputs、events、cooperation | HTTP、worker、协作及定时任务回读 | 消费前后 Receipt 全字段一致 | Passed |
| 提交级协议一致 | 旧字段静默接受或附件遗漏 | Public schema、客户端 API | Web/CLI/Demo unit 与浏览器 | 旧字段、重复文件、越权、幂等冲突 | Passed |
| 取消与删除保持约定 | 未消费消息、草稿复用 | attachments、input repository | 文件/数据库/HTTP 回读及附件生命周期 E2E | 取消文件再次提交、删除后历史、原始 ID 保留 | Passed |
| 新结构隔离验证 | 误用或修改旧库 | schema-init、版本校验 | Schema integration 与新库启动 | Schema 7 runtime 校验拒绝，原版本和 Session 数量不变 | Passed |

### 验证结果

验证日期为 2026-10-10。Compose 独立项目 `yuxi-input-attachment` 使用新建 PostgreSQL、Redis、对象存储与 Workdir；business=8、knowledge=3，PostgreSQL fsync 保持开启。现有数据库未迁移、清空或重排。下表只记录本方案实际执行结果，不引用 Schema 7 阶段的旧方案证据。

后端命令在该隔离项目的 API 容器使用已安装测试环境执行 `python -m pytest`，前端在 frontend 容器执行 pnpm，CLI、Demo、文档在各自目录执行 npm。测试文件的命令选择由[系统测试工作流](https://github.com/xerrors/Yuxi/blob/main/.github/workflows/system-tests.yml)与[测试规范](../../testing-guidelines.md)维护。

| 实际执行命令 / 范围 | 结果与事实 |
|---|---|
| `python -m pytest test/unit -m "not slow"` | 2638 passed、55 skipped；包含附件锚点、整条 Input 遗漏、顺序与逐消息图片的 replay oracle 负控 |
| PostgreSQL：`test_thread_priority_inputs.py`、`test_input_attachments.py`、`test_agent_input_schema.py`、`test_schema_migration_version.py`；协作 3 项、定时任务 4 项 | 分组执行及失败项修正重跑，共 59 个不同用例通过；核对提交无 Message、原子消费、回滚、取消/转换竞争、附件约束和 Receipt 不变 |
| PostgreSQL：`test_database_relation_integrity.py`、`test_database_access_paths.py`、`test_schema_migration_version.py::test_schema_version_is_persisted_and_runtime_validation_fails_closed` | 首轮 4 项通过，初始化中断恢复超时；该项与后续 5 项重跑通过，共 10 项。版本校验与前一组重复，不重复累加；混合 Receipt 目标被约束拒绝，执行控制仍保留自己的 Turn/Run |
| `python -m pytest test/integration/services/test_agent_input_concurrency.py` | 11 passed；并发领取、投递恢复、取消幂等与过期/越权快照拒绝均回读持久结果 |
| HTTP：public files/auth、session items/resources、checkpoint/debug projection、chat router | 分组执行及旧夹具修正重跑，共 39 个不同用例通过 |
| `python -m pytest test/e2e/test_input_projection_recovery_e2e.py test/e2e/test_agent_lifecycle_e2e.py test/e2e/test_draft_attachments_e2e.py test/e2e/test_openai_events_e2e.py test/e2e/test_session_cooperation_e2e.py test/e2e/test_session_config_e2e.py test/e2e/test_agent_lifecycle_extended_e2e.py test/e2e/test_agent_lifecycle_key_scope_e2e.py` | 28 项通过后发现容量测试仍从接收回执读取 Turn；修正该测试消费者后，容量 2 项与后续 6 项重跑通过，累计 36 个不同用例通过。覆盖 A/B/C/D/E、审批、协作、配置、真实 worker 崩溃补投及 SSE 恢复 |
| frontend `pnpm lint:check`、`pnpm test:unit`、`pnpm build` | lint/build 通过，unit 533 passed |
| CLI `npm test`、`npm run typecheck`、`npm pack --dry-run` | 15 passed，类型检查及打包检查通过 |
| Demo `npm test`、`npm run lint`、`npm run build`、`npm run test:browser` | unit 13 passed、browser 18 passed，lint/build 通过 |
| 主前端浏览器脚本 `inputQueueLive.js`、`inputQueueProjection.js`、`sessionHistoryLoading.js` | 真实 API/worker 完成发送两条、转换一条、取消一条、消费及刷新无重复；route replay 覆盖逐消息图片直接引导、移动端布局与 950 条历史分页。分别留存真实与 replay 截图，不混同证据 |
| `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、docs `npm run build`、`git diff --check` | 契约通过，64 passed，文档构建及空白检查通过 |

独立 Reviewer 对完整需求、diff、决策和实际证据审查；附件位置旧文案、Receipt 测试消费者和整条 Input 遗漏的 oracle 负控均已修正。现有 CI 队列、附件 HTTP、worker 生命周期步骤包含新测试，工程契约检查同步约束这些入口。

未验证范围：未调用外部付费模型、真实 OCR/视觉服务或 Milvus 知识库链路；55 个后端单测跳过项不计为通过。进程崩溃测试证明补投与数据库归属，不证明断电耐久性；文件上下文过滤不等于 Workdir 文件隔离。首次在重 DDL 与 E2E 并行期间出现的超时不计为通过，串行重跑结果如表；初始化中断恢复测试首轮 30 秒等待超时，保持原阈值和 fsync 单独重跑通过。Python Ruff 的受影响生产文件检查通过，扩大检查的既有集成测试文件仍有未改动的长 SQL 行 E501，不声明全仓 Ruff 通过。
