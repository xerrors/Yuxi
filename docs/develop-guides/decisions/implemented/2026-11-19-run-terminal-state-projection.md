# Run 终态状态投影持久化

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/services/checkpoint_state.py

## 问题

`GET /api/v1/agents/sessions/{id}/state` 每次打开会话都反序列化全量 LangGraph checkpoint；多用户并发打开历史会话时，这是首屏延迟与 API 进程突发 CPU 的主要来源之一。面板真正消费的状态通道只有 `warnings`、`artifacts` 和 `token_usage`，与 checkpoint 中的消息、文件和工具状态全量相比小数个数量级；读模型只需要一个新鲜度判据即可与服务端权威状态区分。

## 决策

把 Run 终态时的面板读模型持久化为 `sessions.agent_state_view`：终态 Run 打开会话走纯 PostgreSQL 读，不再触碰 checkpoint；checkpoint 仍是执行侧唯一权威状态，投影只是可失效的 UI 读模型。

### 实现方案

- `sessions.agent_state_view`（JSONB）进 ORM metadata，fresh baseline 直接建列；`BUSINESS_SCHEMA_VERSION` 5→6 作为 shape fingerprint。存量 v5 库显式执行 `python -m yuxi.migrations.agent_state_view`（单条 nullable 列，幂等，仿 `mcp_personal_credentials` 先例）升 v6；v4 库先跑既有插件迁移到 v5；其余版本由 `require_current_schema` fail-closed 拒绝，不做原地伪装升级。
- 载荷为 `{"values": {warnings, artifacts, token_usage}, "run_id": ..., "interrupt": ...}`。通道集固定为 `AGENT_STATE_PROJECTION_CHANNELS`，写入前按 `extract_agent_state` 的默认形态归一（缺失/None→[]/None），保证快慢路径逐键一致；`run_id` 是新鲜度标签，中断载荷随行存储。
- 读门：投影为 dict 且最新 Run 已终态、标签与最新 Run 一致时走快路径（零 checkpoint、零回写）；否则保留既有边界——先结束请求级读事务再全量读 checkpoint（2026-11-18 的 `/state` 读事务释放行为不变），终态 Run 的兜底读为属主本人行幂等懒回填，冷线程打开一次即转快路径；非终态 Run 不放行快路径也不回填，避免把演进中的状态定格。
- 写主三个，全部 best-effort、失败只告警不回抛：Run 终态钩子（`execution.stream_agent_execution` 复用内存 `final_state.values`，零额外 checkpoint 读，终态事务提交后经独立会话写入）；压缩完成刷新（`compression.compress_thread_context` 在 commit 后回读 checkpoint 并以最新终态 Run 重打标签——压缩改写通道值但不产生新 Run，不重打标签新鲜度门永不放行）；属主打开 `/state` 的懒回填。
- repository 写为条件 UPDATE（`status != 'deleted'`），已删除线程不写；中断响应仍只在既有 Turn/waitpoint/run 绑定门下返回，快路径只替换载荷来源，不放宽任何门。

## 替代方案

- Redis 或进程内缓存投影：需要跨 api worker 进程失效协议，把 UI 读模型升级为第二套状态系统；同一行 JSONB 由既有 PostgreSQL 承载即可。
- 投影写并入 Run 结算同一事务：会在持 Session 行锁的结算事务内夹带第二写主并把 checkpoint 读拖进结算路径；投影损坏不应拖垮终态。
- 不提升 schema 版本、仅靠 `ADD COLUMN IF NOT EXISTS` 收敛存量库：运行进程无法区分"有列的 v4"与"无列的 v4"，读路径会 fail 在缺列 SQL 上；版本 bump 让未收敛库显式 fail-closed。
- 给 `extract_agent_state` 的全部字段（含消息引用、文件列表）建投影：面板 `/state` 不消费的通道只会放大投影体积与漂移面。
- worker 失败收尾路径顺带写投影：该路径 Run 尚非终态，定格会产生错误的新鲜投影；属主懒回填已覆盖冷线程。

## 后果

- 给 `extract_agent_state` 新增面板通道必须同步 `AGENT_STATE_PROJECTION_CHANNELS` 与快慢路径逐键等价守卫，否则快路径静默丢字段并在守卫用例上变红。
- 压缩链路新增一次 commit 后的 best-effort checkpoint 读（分钟级操作之后，不在轮询热路径）。
- v4 存量库部署本版本必须按发布流程显式收敛或重建，启动拒绝是预期行为。
- 投影行随 Session 行生命周期回收，无独立清理任务；`/state` 响应形态对客户端不变。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新鲜投影命中时零 checkpoint、零回写，载荷逐键等价；非终态 Run 不放行快路径 | 快路径读回 checkpoint、顺手回写或竞态窗定格演进状态 | checkpoint_state.get_agent_state_view | `pytest test/unit/services/test_agent_state_projection.py`（fast_path / running_run_blocks / normalization，checkpointer 装载即 fail）+ `pytest test/integration/services/test_agent_state_projection_flow.py::test_terminal_projection_roundtrip_serves_fast_path_without_checkpoint`（真实 PostgreSQL 行回读后禁读守卫） | 旁路新鲜度门后 stale 用例红；允许回写后 fast_path 用例红；去显式 None 归一后 normalization 用例红，还原全绿 | Passed |
| 陈旧/缺失退回全量读并懒回填重打最新 Run；非终态不写 | 错误 Run 的投影被当作新鲜服务 | checkpoint_state + repositories/sessions.save_agent_state_view | unit `test_stale_projection_falls_back_and_backfills_latest_run`、`test_running_run_falls_back_without_freezing_projection`；integration `test_stale_projection_falls_back_to_checkpoint_and_retags_latest_run` | 去终态门后 running 用例红，还原绿 | Passed |
| v5 存量库补列升级幂等、非 v5 拒绝且保留数据 | migrations/agent_state_view.py | `pytest test/integration/services/test_schema_migration_version.py::test_agent_state_view_upgrade_is_explicit_and_preserves_existing_data`（真实 PostgreSQL） | 去掉版本守卫 → 99 参数用例红 |
| 已删除线程不写；投影写失败不改变响应 | 回收线程复活数据、读模型故障拖垮主链路 | save_agent_state_view 条件写 + best-effort 钩子 | integration `test_deleted_thread_never_receives_projection`；unit `test_terminal_hook_failure_never_escapes`、`test_compression_refresh_degrades_when_run_lookup_fails` | 去掉 status 谓词后 deleted 用例红，还原绿 | Passed |
| 通道集与 extract 消费面一致、缺键按默认形态归一 | 快慢路径返回形态漂移 | AGENT_STATE_PROJECTION_CHANNELS | unit `test_projection_keeps_only_consumed_channels_and_json_safe`、`test_missing_channels_are_normalized_before_storage` | — | Passed |
| 终态/中断/失败三条执行收尾只按终态触发一次投影写主，并复用内存 state 与正确 run_id | 钩子漏接使投影永不新鲜，或失败收尾定格错误投影 | execution.stream_agent_execution | `pytest test/unit/services/test_chat_service_langfuse_stream.py::test_terminal_paths_trigger_state_projection_once` | 删除或挪动 execution 中的钩子块：对应参数化用例红 | Passed |
| Schema：fresh 基线含列、v4 拒绝、app-init 记 5 | 声明与库形态漂移 | infrastructure/postgres/schema.py + migrations/schema.py | `pytest test/integration/services/test_schema_migration_version.py`（session 列断言、(4,2) 进入不兼容参数集、常量==5）与 `test_app_initialization.py` 四处 business=5 | — | Passed |
| 压缩后刷新写主接线 | 压缩后面板永久陈旧 | compression.compress_thread_context | `pytest test/unit/services/test_context_compression_service.py` 事件序列含 commit 后的 refresh 调用 | 去掉 refresh 调用后事件序列断言红 | Passed |
