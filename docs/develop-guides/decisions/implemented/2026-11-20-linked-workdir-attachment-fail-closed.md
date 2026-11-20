# linked Workdir 永久缺失对附件 fail-closed，不进入无限重试

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/attachments.py

## 问题

`directory_mode='linked'` 的 Project 绑定的是用户拥有的盘上目录：系统不创建它，用户随时可能删除或改权限。DB 绑定行仍在、目录永久不存在时，携带附件的消息进入该线程：接收事务把附件落成 `preparing`，worker 每 30 秒（`RUN_RECONCILIATION_SECONDS`）的 `recover_pending_dispatches → dispatch_next_input → prepare_input_attachments` 在 `ensure_session_workdir_available → Workdir.open_existing` 抛 `FileNotFoundError`，被泛化的 `except Exception` 当作瞬时失败保留来源，下一轮再命中同一缺失目录——traceback 每周期刷屏、补投周期任务本身持续判失败，同线程后续输入被未就绪队头挡住。该失败是永久性状态：不存在任何一轮会成功的重试语义。

## 决策

把"linked Workdir 缺失"识别为附件生命周期的终止性失败，在两个真实边界分别闭合，运行时自愈，不交付现网 SQL 或运维脚本：

- 接收边界（fail-fast，主案）：`accept_locked` 在创建 Input/Receipt 之前，对携带附件的输入校验 linked Workdir 是否存在（`ensure_session_workdir_acceptable`，缺失抛 `StateConflictError` → 409，提示恢复目录后重试；语义是"当前资源状态不允许、修复后可重试"，与 Public API 既有错误文档一致）。managed 目录在提交后才物化，不受影响；纯文本提交不经该门禁。用户当场得到可行动的错误，不再产生进入重试循环的 `preparing` 行。
- 准备边界（竞态兜底 + 存量收敛）：`prepare_input_attachments` 中仅包住 Workdir 解析与打开的窄捕获分两类。
  `FileNotFoundError/NotADirectoryError/ValueError`（目录缺失、symlink 边界拒绝、中间组件非目录）属
  "不存在会成功的后续轮次"的永久形态，立即终止；`PermissionError` 可能是宿主卷瞬态故障（remount、
  权限修复中），按 Input `created_at` 计时保留来源重试，`WORKDIR_ACCESS_RETRY_BUDGET_SECONDS`（1h）内
  回落既有瞬时保留语义，超预算才终止。managed 目录由系统创建，其物化异常在此显式回落瞬时保留、不终止。
  终止时：附件退回 `draft`（置空 `input_id`，保留 `object_name` 供既有到期草稿回收删除 MinIO 来源）、
  `pending` Input 一律取消（含 steer 与 resume，比 TTL 收割更严，因永久失败下收割豁免名单里的输入同样
  不可能成功），并把原因写入 `origin_metadata.terminal_error`——`GET /sessions/{id}/inputs/{input_id}`
  （`InputResponse.error`）随快照返回给调用方，不再只有日志可见；异常消息可能含宿主路径，只记异常类型。
  回收扫描的 scope 谓词（`status='preparing'` 或按 input 连接的 `object_name IS NOT NULL`）与
  `list_preparing_inputs` 从此不再命中该线程，刷屏即时终止。

### 关键取舍：复用 draft 终态而非新增 failed 状态

`agent_attachments.status` 的 CHECK 约束是既有对象契约，新增 `failed` 需要改约束并同步恢复扫描、状态聚合与前端。回退到 `draft` 天然满足既有约束（`input_id IS NULL`、`object_name IS NOT NULL`），且各消费方语义正确：会话附件列表经 `input_id` join 不再展示（该文件确实不属于会话）、到期草稿回收本来就是"无人认领来源"的处置者。

## 替代方案

- 对所有失败统一给重试次数上限：权限类故障按预算有界重试是本轮采纳的形态，但对确认缺失的目录计数只是把黑洞多留 N 个周期；类型二分比计数更快收敛且语义诚实。
- 在接收边界返回 400：409 才是"资源状态修复后可重试"的既有语义；400 会暗示请求本身不可重试。
- 把缺失目录的 Input 标 failed 留队头：仍需人工清账；永久失败下保留可恢复假象没有任何消费者会再恢复它。

## 后果

- 附件上传后、提交前目录被删：提交直接 409，草稿文件留在 MinIO 由既有到期回收清理。
- 接收后目录才消失（竞态/运行期删除）：该 Input 取消、附件回草稿；线程恢复可继续新输入，无需运维介入。
- 409 文案不含宿主路径；准备侧日志只记异常类型。
- 无 Schema 变更。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| linked 目录缺失时接收即 409：零 Input/Receipt、附件保持 draft、纯文本提交不受门禁 | 生成必然失败的 preparing 行并进入重试循环 | bindings.ensure_session_workdir_acceptable + inputs.accept_locked | `pytest test/integration/services/test_input_attachments.py::test_accept_rejects_attachments_when_linked_workdir_missing`（真实 PG+MinIO） | 移除接收门禁 → Input/Receipt 计数断言红 | Passed |
| 接收后目录消失的存量 Input 一轮收敛：附件回 draft 保留 object_name、Input 取消、恢复扫描不再领取、不建 Run | 每 30s traceback 刷屏并挡住同线程队头 | attachments.prepare_input_attachments 窄捕获分支 | `pytest test/integration/services/test_input_attachments.py::test_missing_linked_workdir_terminates_stuck_preparation`；`test_input_attachments.py` 全文件回归 | 去掉窄分支（回落泛化 except）→ status/队头断言红；去掉 managed 豁免（终止分支吃进 PermissionError）→ `test_managed_transient_workdir_failure_keeps_source_for_retry` 的 pending/preparing 断言红 | Passed |
| 权限类失败预算内保留重试、超预算终止且快照返回原因 | 可恢复故障被过早取消，或永久不可访问伪装成瞬时刷屏 | attachments.prepare_input_attachments 双分支 + inputs.get_input_snapshot | `pytest test/integration/services/test_input_attachments.py::test_permission_denial_retries_within_budget_then_terminates`（真实 PG+MinIO：首轮 pending/preparing；`created_at` 老化 2h 后 cancelled/draft 且 `snapshot["error"]` 含终止原因） | 把 PermissionError 并入立即终止 → 首轮断言红；删除预算判定 → 老化轮断言红 | Passed |
| 既有失败保留与取消路径语义不变 | 瞬时失败被误终止、丢来源 | 同上 | `test_input_attachments.py` 其余用例（含 `test_failed_preparation_blocks_dispatch_and_cancel_keeps_submission`、崩溃重放）全绿 | — | Passed |
