# 模型/工具 JSON 在持久化边界归一化 NUL 字符

状态：implemented
类型：bug-fix
Owner：backend/yuxi/infrastructure/postgres/base.py

## 问题

线上 0.8 实例上，某个 Turn 的公开 items 分页（`GET /api/v1/agents/sessions/{session_id}/items`）持续 500：`asyncpg.exceptions.UntranslatableCharacterError: \u0000 cannot be converted to text`。`messages.extra_metadata` 是裸 `json` 列，PostgreSQL 按原文存储、不校验内容；当模型回复或工具输出携带 U+0000 字符时，SQLAlchemy 默认序列化把它写成 `\u0000` 转义直接入库。该转义无法被 PostgreSQL 任何 JSON→text 转换读回：`json_each`、`->`、`->>` 都会当场报错，导致该行参与的所有分页/展开查询（`PublicItemRepository.list_page` 等）在数据存活期间永久失败。写该行的 Run 早已成功，故障只在读取端显现且无法自愈。

## 决策

在持久化这一事实信任边界闭合，不逐调用者修补：

- `backend/yuxi/infrastructure/postgres/base.py` 新增 `normalize_json_nul` 与 `NormalizedJSON`（TypeDecorator，impl 仍为裸 `JSON`）：`process_bind_param` 在序列化前递归把字符串值和键中的 U+0000 替换为 U+FFFD。PG 的文本域本就不可表示 NUL，替换字符是唯一能保留内容可读性的归一化选择。替换不得静默丢条目：本来不含 NUL 的键保留原名先落位，替换后与既有键重名的键追加 U+FFFD 后缀直到唯一，任意输入键值在输出中都有归属。
- `backend/yuxi/modules/agents/models/messages.py` 将 `Message.extra_metadata`（承载模型完整 dump、工具审计 output 与 public_items 快照的列）挂到 `NormalizedJSON`。该列的写入分散在 tool_audit、checkpoint_messages、public_items 等约十处 `extra_metadata = {...}` 赋值，列级绑定是唯一能同时覆盖 INSERT 与 flush 时整字典替换的单点。
- `backend/yuxi/migrations/json_nul_repair.py` 新增显式一次性修复脚本（与 `mcp_personal_credentials.py` 同通道：独立模块、`python -m` 执行、`schema_migration_lock` 串行化）：用 Python `json.loads` 读取历史污染行（PG 侧已无法解析），归一化后写回；只重写真实含 NUL 的行并带文本相等乐观条件，幂等可重放。逐行隔离：解析失败、结果无法编码 UTF-8（如未配对代理项）或单行数据库错误只汇报该行 id（UPDATE 走独立 SAVEPOINT，不中止整体事务），其余可修复行照常完成；返回 `scanned/repaired/failed_ids` 供运维核对。升级后运维执行一次即可恢复已损坏的 Session，否则写边界归一化只防新增。

### 实现方案

写入链路：worker/服务对 `Message.extra_metadata` 赋值 → SQLAlchemy flush → `NormalizedJSON.process_bind_param` 归一化 → json 序列化 → PostgreSQL `json` 原文入库；读取链路（`json_each`/`->` 分页、审计回读）不再遇到不可转换转义。存量修复链路：`python -m yuxi.migrations.json_nul_repair` → 初始化锁内全表扫含字面 `\u0000` 的 messages 行 → 逐行 loads/normalize/dumps → 带原文相等条件 UPDATE。列类型不变（仍为 `json`），无 Schema 迁移、无版本提号；`JSON_VALUE`（jsonb 变体）列写入时本就被 PostgreSQL 拒绝 NUL，fail-closed 行为不变、不在本次范围。

## 替代方案

- 把 `extra_metadata` 迁到 JSONB：写入当场即报错，NUL 会出现在 Run 输出持久化的正中间，工具结果落库失败把"读取端永久 500"换成"执行中断且输出丢失"，还要附带全量数据清洗迁移，改动面更大。
- 在每个写入调用者（tool_audit、checkpoint_messages、public_items.save 等）分别清洗：十处收敛点，未来新增写入路径会重蹈覆辙。
- 只修线上数据、不改写边界：同一工具输出模式（读二进制文件等）随时再次污染，已实证过。

## 后果

- 落库的公开内容/审计 JSON 中，NUL 一律以 U+FFFD 呈现；模型可见历史与公开输出与原始字节在这一字符上不再逐字节一致——原始 NUL 本就无法在 PG 文本域往返。
- `extra_metadata` 每次 flush 多一次 O(结构大小) 递归，与既有 json.dumps 同量级。
- 存量修复是显式脚本，运行时一次 messages 全表文本扫描；不运行则污染行保持原状（读取端仍 500）。

## 验证

- `backend/test/unit/storage/test_normalized_json_column.py`：嵌套字符串/键归一化、键冲突时两个值都保留（含与既有键重名和多替换键互撞）、绑定参数序列化后无 `\u0000` 转义、`Message.extra_metadata` 列类型断言。
- `backend/test/integration/api/test_public_session_items.py::test_items_page_survives_nul_in_model_output`：真实 ORM 写入含 NUL 的 public_items → 库内原文不含 `\u0000` → 真实 HTTP items 分页 200 且文本为 U+FFFD → `list_page` 可正常展开。
- `backend/test/integration/api/test_public_session_items.py::test_nul_repair_script_restores_poisoned_extra_metadata`：真实 asyncpg 直插污染行 → PG 侧 `json_each` 当场报错且 items 分页 500（缺陷在场证据）→ 执行 `repair_messages_nul_escapes` → 分页 200、内容保留 U+FFFD。
- `backend/test/integration/api/test_public_session_items.py::test_nul_repair_isolates_unfixable_row_and_keeps_others`：同会话插入可修复行与模拟编码失败行 → 修复返回 `repaired>=1` 且 `failed_ids` 恰含失败行 id，可修复行库内转义消失、失败行保持原状。
- 负向案例：临时把列回退为裸 `JSON` 后，`test_items_page_survives_nul_in_model_output` 确定性失败（已复跑确认）；修复恢复后转绿。
- 门禁：`verify_engineering_contracts`、其 unittest、ruff check/format/imports（本次文件）、`git diff --check` 均通过。
- 未验证范围：事发线上实例的存量修复需在该环境部署升级后执行一次修复脚本，本地以真实 PostgreSQL 上的注入-修复用例代替。
