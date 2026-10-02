# 收敛解析、运行映射与事件投影的重复事实

状态：archived
类型：simplification
Owner：backend/package/yuxi/knowledge/parser/capabilities.py

相关事实由下列源码 Owner 分别持有：

- 解析格式与 provider 能力：`backend/package/yuxi/knowledge/parser/capabilities.py`
- Conversation/Project/Workdir 授权映射：`backend/package/yuxi/services/workdir_service.py`
- Redis Run 事件行协议：`backend/package/yuxi/services/run_queue_service.py`
- 前端 Run 事件投影与终态收敛：`web/src/composables/useAgentRunStream.js`
- 当前 Thread 与 Project 列表：`web/src/stores/chatThreads.js`、`web/src/stores/projects.js`
- AgentRun 持久字段与业务 Schema 迁移：`backend/package/yuxi/storage/postgres/models_business.py`、`backend/package/yuxi/storage/postgres/manager.py`

## 问题

文档格式能力、Workdir 绑定和 Run 事件投影均存在同一事实被多处表达的情况。部分中间结果在用例链路中被丢弃后重新查询，解析与索引还保留没有内部消费者的旧旁路；前端则由多个组件分别维护当前线程和终态收敛逻辑。这些重复增加了修改时需要同时核对的表面，并已产生格式能力漂移。

## 决策

- 由无重型依赖的解析能力模块拥有格式分类与处理器装配位置，配置、服务、工厂和解析分派从这里派生；具体解析器只在真实解析时加载。
- 统一解析入口只返回当前消费者需要的 Markdown；删除无消费者的 artifact 结果和 `update_content` 解析旁路。
- 用例内部传递已经授权的 Workdir binding，避免在同一流程重复解析 Conversation、Project 和路径，但不改变持久化 Owner 或提交后物化时序。
- Redis 行解码、Run envelope 投影和前端终态收敛分别只保留一个窄实现；Request/Run、PostgreSQL/Redis 和主线程/子线程的语义边界继续分离。
- 删除没有运行时 Owner 的 Run cursor 字段与重复路径函数；当前 Thread 和 Project 列表分别使用现有 Pinia store 作为唯一可变 Owner。

## 替代方案

- 保留现状并补充注释：不能阻止格式清单继续漂移，也不能消除重复查询和分支收敛。
- 保留单独的 parser registry 兼容模块：仓库内消费者可以直接读取能力模块，继续维护派生映射和元数据包装没有独立价值。
- 引入统一运行上下文或通用状态机：能够集中更多代码，但会把解析、权限、队列和 UI 状态耦合成新的大型抽象，认知成本高于当前问题。
- 为旧 `update_content` 保留兼容包装：仓库内没有生产消费者，且包装会继续扩大非 canonical 链路；当前决定是不保留。

## 后果

- 解析能力查询不再加载 OCR、Office 或模型依赖，新增格式和处理器只需修改一个 Owner 及其能力测试；不再存在第二个 parser registry 模块。
- Workdir 的授权结果可以在一次请求链路内复用；跨事务仍需重新解析，数据库路径与 runtime 绝对路径仍保持分离。
- Run 的持久终态继续由 PostgreSQL 拥有；Redis 和前端只负责事件输送与投影，不新增兼容状态。
- 删除 `AgentRun.last_event_id` 需要业务 Schema v5 迁移；旧列允许通过幂等迁移移除。

## 验证

- `docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"`：Passed，1698 passed、44 skipped。
- 真实 PostgreSQL 的旧游标删除与幂等检查：Passed；当前用例合入 `test_v072_business_converges_current_schema_idempotently`，发布升级边界由[Schema 迁移 Owner](../05-storage-workdir/2026-08-24-versioned-schema-migration-owner.md)维护。
- `docker compose exec api python -m pytest test/integration/api/test_agent_run_events_router.py test/integration/api/test_agent_request_queue_router.py`：Passed，12 passed。
- `docker compose exec api python -m pytest test/e2e/test_attachment_and_agent_state.py::test_attachment_confirm_is_reflected_in_thread_metadata`：Passed。
- `uv run --group test pytest test/unit/services/test_agent_request_queue_service.py test/unit/services/test_run_submission_service.py`：Passed，54 passed。
- `rg -n "yuxi\\.knowledge\\.parser\\.registry|PROCESSOR_TYPES|get_parser_metadata" backend scripts`：Passed，无残余引用。
- parser、OCR、配置相关文件的 Ruff lint 与 format check：Passed。
- `pnpm test:unit`：Passed，196 passed。
- `pnpm lint:check`、`pnpm build`：Passed；build 仅保留既有大 chunk 警告。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`git diff --check`：Passed。

旧能力不存在：`update_content` 旁路、未消费的解析 artifact 结果、独立 parser registry 模块、`AgentRun.last_event_id`、重复 runtime path 函数和组件内第二份当前 Thread/Project 状态均不得保留为兼容入口。

重新引入条件：只有出现可问责的真实消费者，并能说明其 Owner、持久化或协议边界及独立验证时，才重新引入相应能力；不能仅因测试 fixture 或历史调用形状恢复。
