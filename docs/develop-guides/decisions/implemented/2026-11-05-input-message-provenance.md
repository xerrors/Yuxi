# 正式消息的附件、来源与可见性边界

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/repositories/input.py

## 问题

一次 Input 可以包含多条消息。附件必须属于该 Input 自己的正式 Message，模型输入不能把其他历史输入的附件补到当前消息。消息来源必须由持久 Input 决定，Memory 需要保留该来源；Input 快照还必须遵守 Thread 的用户、APP 和删除边界。

## 决策

### 实现方案

Input repository 在消费时以持久 Input 的 source、channel、external_id 覆盖正式 Message 的 input_source。原始 Input 正文保持不变，不迁移历史 Message。Memory repository 只额外选择该 JSON 子字段，service 在非空时透传，继续执行现有可见性和字节预算。

执行 service 按当前已消费 Message ID 读取附件，逐条追加模型上下文，不重新扫描历史附件，也不修改原始消息。Input 快照复用 require_thread，只允许所属用户和 APP 的 active、archived 会话。附件 replay 的禁入 oracle 检查所有上下文片段，不把没有附件视为违反禁入条件。

## 替代方案

- 保留 Thread 附件扫描：会把附件归属和模型消息位置混为一谈，不能满足每次提交独立的输入事实。
- 完整读取 Message metadata：会搬运 raw_message 等大载荷，并扩大模型可见面。
- 回填历史来源：需要额外迁移与无法完整重建的旧来源证据，本修复只保证新消费。

## 后果

正式 Message 拥有附件位置，执行层不再从相邻 Input 推断归属。附件上下文只追加到模型输入副本，持久 Input、正式 Message 正文与图片内容保持原始顺序。模型仍能读取已有 checkpoint 中的历史消息和附件；本决定只限制新输入的附件追加。

来源真实性只覆盖新消费的 Message。历史 input_source 没有回填，Memory 对历史来源的透传不证明其真实性。Memory 查询不搬运整个 metadata 或其中的 raw_message。Input 快照对所属用户和 APP 的 active、archived 会话可见，deleted 会话返回不存在。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 附件只追加到其正式 Message | 多消息与历史附件串用 | execution、attachments repository | `test_draft_attachments_e2e.py::test_draft_initial_send_queue_cancel_and_model_attachment_isolation` 的真实 HTTP→worker→模型 oracle→PostgreSQL 回读；执行 unit | 历史附件不进入当前消息，首图和末条附件分别锚定，steer 不携带旧附件 | Passed |
| 禁入 oracle 检查所有附件片段 | 无附件误拒或早段漏检 | openai replay server | `test_openai_replay_contract.py` | 无附件可通过，任一片段含禁用文件则拒绝 | Passed |
| 新消费的来源来自持久 Input | 用户 metadata 伪造来源 | input repository | `test_thread_priority_inputs.py::test_consumed_message_source_comes_from_persistent_input`，独立 PostgreSQL 事务回读 | 伪造 input_source 被真实来源覆盖，原始 Input 保留原文 | Passed |
| Memory 保留来源且只读必要字段 | 丢来源或读取整个 metadata | memory_history repository/service | `test_memory_service.py::test_memory_history_reads_only_source_metadata`，PostgreSQL 历史搜索、锚点读取及实际 SQL | 大 raw_message 不进入投影，空来源不新增字段 | Passed |
| Memory 开关控制历史能力 | 关闭后仍能搜索或读取 | memory service | unit `test_memory_service.py` 的历史开关案例 | 关闭后当前和其他线程均拒绝读取；开启后保留跨线程能力 | Passed |
| 已删除 Thread 不提供 Input 快照 | 删除后的越界读取 | require_thread、inputs service | `test_thread_priority_inputs.py::test_http_input_snapshot_respects_thread_status`，ASGITransport 请求正式路由并连接真实 PostgreSQL | deleted 返回 404；active、archived 可回读 | Passed |

执行与协议 unit 的目标缺陷，以及来源、历史投影和删除边界的 PostgreSQL/ASGITransport 案例，均验证缺陷恢复时失败、修复后通过。Input 快照证据属于进程内 ASGI 路由与真实数据库集成，不代表运行中部署的 TCP HTTP 验证。
