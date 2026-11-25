# Run 终态状态投影持久化

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/services/checkpoint_state.py

## 问题

`GET /api/v1/agents/sessions/{id}/state` 每次打开会话都反序列化全量 LangGraph checkpoint；多用户并发打开历史会话时，这是首屏延迟与 API 进程突发 CPU 的主要来源之一。面板真正消费的状态通道只有 `warnings`、`artifacts` 和 `token_usage`，与 checkpoint 中的消息、文件和工具状态全量相比小数个数量级；主动压缩在同一 Run 下产生新 checkpoint。按 Run ID 识别投影时，迟到的回填可以覆盖压缩后的投影，刷新失败也会使后续读取继续看到旧用量。

## 决策

把 Run 终态时的面板读模型持久化为 `sessions.agent_state_view`：历史会话打开时复用来自最新 checkpoint 的面板字段。PostgreSQL 的 checkpoint 保留执行状态，并提供投影校验使用的版本。

### 实现方案

- `sessions.agent_state_view`（JSONB）进入 ORM metadata，空库直接建立 business 6 / knowledge 2。正式 v0.8.0 的插件迁移固定到 business 5 / knowledge 2；随后执行[会话状态迁移](../../../advanced/deployment.md#会话状态迁移)，同一事务补列并登记 business 6。已有 business 5 / knowledge 2 的库直接执行第二步。
- 载荷包含 `values`、来源 `checkpoint_id`、Run 归属及可选的中断载荷。通道集固定为 `AGENT_STATE_PROJECTION_CHANNELS`，写入前按 `extract_agent_state` 的默认形态归一。
- Thread 作用域和终态 Run 条件通过后，快路径只查询最新根 checkpoint 的 ID，查询可利用现有主键索引。版本一致时复用投影。其余情况先结束请求级读事务，再读取完整 checkpoint。最新 Run 已终态且存在 checkpoint 时允许惰性回填。缺少 `checkpoint_id` 的存量 JSONB 按同一路径刷新。
- Run 终态钩子复用内存快照的值和版本，在结算提交后写入。主动压缩刷新和 `/state` 懒回填各自在一次 checkpoint 读取中取得值与版本。三条写入路径失败时告警，后续读取仍按 checkpoint 版本判断。
- `SessionRepository.save_agent_state_view` 以 checkpoint 的排序拒绝迟到旧值，只更新未删除会话的投影并保留 `updated_at`，以免回填改变排序和活跃统计。`/state` 先完成响应组装，再写入投影，因此写入失败后可以返回已读到的状态和协作关系。中断响应仍按既有 Turn、waitpoint 和 Run 的绑定关系校验。

## 替代方案

- 压缩时清除旧投影：checkpoint 和投影各自提交，仍需协调刷新失败以及压缩前已读到旧值的并发请求。沿用 checkpoint 自有版本使读取能独立发现失效。
- Redis 或进程内缓存投影：需要跨 api worker 进程失效协议，把 UI 读模型升级为第二套状态系统；同一行 JSONB 由既有 PostgreSQL 承载即可。
- 投影写并入 Run 结算同一事务：会在持 Session 行锁的结算事务内夹带第二写主并把 checkpoint 读拖进结算路径；投影损坏不应拖垮终态。
- 沿用 business 5：运行进程无法根据版本记录判断投影列是否已创建。提升版本让旧库在启动检查阶段被拒绝。
- 给 `extract_agent_state` 的全部字段（含消息引用、文件列表）建投影：面板 `/state` 不消费的通道只会放大投影体积与漂移面。
- worker 失败收尾路径顺带写投影：该路径 Run 尚非终态，定格会产生错误的新鲜投影；属主懒回填已覆盖冷线程。

## 后果

- 给 `extract_agent_state` 新增面板通道必须同步 `AGENT_STATE_PROJECTION_CHANNELS` 与快慢路径逐键等价守卫，否则快路径静默丢字段并在守卫用例上变红。
- 压缩链路新增一次 commit 后的 best-effort checkpoint 读（分钟级操作之后，不在轮询热路径）。
- 存量库完成离线迁移后才能启动当前版本；API、worker 和 app-init 会拒绝仍停留在 business 5 的库。
- 后续读取可在刷新失败时从 checkpoint 取得新用量，迟到请求仍可返回它已读到的状态。新鲜投影的快路径增加一次仅取版本的查询。
- 投影行随 Session 行生命周期回收，无独立清理任务；`/state` 响应形态对客户端不变。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新鲜投影命中时跳过 checkpoint 内容读取和回写，载荷逐键等价；非终态 Run 不放行快路径 | 快路径读回 checkpoint、顺手回写或竞态窗定格演进状态 | checkpoint_state.get_agent_state_view | `pytest test/unit/services/test_agent_state_projection.py`（fast_path / running_run_blocks / normalization，checkpointer 装载即 fail）+ `pytest test/integration/services/test_agent_state_projection_flow.py::test_terminal_projection_roundtrip_serves_fast_path_without_checkpoint_payload`（真实 PostgreSQL 行回读后禁读守卫） | 旁路新鲜度门后 stale 用例红；允许回写后 fast_path 用例红；去显式 None 归一后 normalization 用例红，还原全绿 | Passed |
| 陈旧/缺失退回全量读并懒回填重打最新 Run；非终态不写 | 错误 Run 的投影被当作新鲜服务 | checkpoint_state + repositories/sessions.save_agent_state_view | unit `test_stale_projection_falls_back_and_backfills_latest_run`、`test_running_run_falls_back_without_freezing_projection`；integration `test_stale_projection_falls_back_to_checkpoint_and_preserves_session_metadata` | 去终态门后 running 用例红，还原绿 | Passed |
| 插件迁移停在 v5，投影迁移补列后登记 v6，重复应用保留已有会话 | 缺列时提前登记 v6，或修改不兼容的库 | `migrations/agent_plugins.py`、`migrations/agent_state_view.py` | `test_agent_plugin_migration.py::test_plugin_migration_cli_registers_models_in_a_cold_process` 与 `test_schema_migration_version.py::test_agent_state_view_upgrade_is_explicit_and_preserves_existing_data`（真实 PostgreSQL） | 插件迁移登记 v6 时版本断言失败，放宽第二步版本检查时不兼容域用例失败 | Passed |
| 已删除线程不写；投影写失败不改变响应 | 回收线程复活数据、读模型故障拖垮主链路 | save_agent_state_view 条件写 + best-effort 钩子 | integration `test_deleted_thread_never_receives_projection`；HTTP `test_stale_projection_falls_back_to_checkpoint_and_preserves_session_metadata[True]`、unit `test_terminal_hook_failure_never_escapes` | 去掉 status 谓词后 deleted 用例红，还原绿 | Passed |
| 通道集与 extract 消费面一致、缺键按默认形态归一 | 快慢路径返回形态漂移 | AGENT_STATE_PROJECTION_CHANNELS | unit `test_projection_keeps_only_consumed_channels_and_json_safe`、`test_missing_channels_are_normalized_before_storage` | — | Passed |
| 终态/中断/失败三条执行收尾只按终态触发一次投影写主，并复用内存状态及其 checkpoint ID 和 Run ID | 钩子漏接使投影永不新鲜，或失败收尾定格错误投影 | execution.stream_agent_execution | `pytest test/unit/services/test_chat_service_langfuse_stream.py::test_terminal_paths_trigger_state_projection_once` | 删除或挪动 execution 中的钩子块：对应参数化用例红 | Passed |
| 空库基线含投影列，app-init 登记 v6，未迁移的 v5 库拒绝启动 | 版本记录与实际列漂移 | `infrastructure/postgres/schema.py` 与 `migrations/schema.py` | `test_schema_migration_version.py` 中的空库和不兼容版本用例，以及 `test_app_initialization.py` | 接受未升级的 v5 时版本拒绝用例失败 | Passed |
| 回填保留会话更新时间，写入失败仍返回完整 `/state` | 打开历史会话改变排序或活跃统计，回滚导致协作关系读取失败 | `SessionRepository.save_agent_state_view` 与 `checkpoint_state.get_agent_state_view` | `test_agent_state_projection_flow.py::test_stale_projection_falls_back_to_checkpoint_and_preserves_session_metadata` 经真实 HTTP 请求，回读 PostgreSQL 投影和时间 | 省略 `updated_at` 自赋值时时间断言失败，恢复先回填后组装的顺序时数据库故障导致 HTTP 500 | Passed |
| 压缩后刷新写主接线 | 压缩后面板永久陈旧 | compression.compress_thread_context | `pytest test/unit/services/test_context_compression_service.py` 事件序列含 commit 后的 refresh 调用 | 去掉 refresh 调用后事件序列断言红 | Passed |
| 压缩完成后拒绝迟到的旧回填 | 旧请求覆盖新投影 | `SessionRepository.save_agent_state_view` | `test_agent_state_projection_flow.py::test_compression_projection_survives_late_backfill` 通过真实 HTTP 暂停旧回填，再回读 PostgreSQL | 移除版本写入条件，旧用量覆盖已存新投影 | Passed |
| 压缩刷新失败后读到最新状态 | Run ID 一致使旧投影误命中 | `checkpoint_state.get_agent_state_view` | `test_agent_state_projection_flow.py::test_compression_projection_refresh_failure_falls_back_to_latest_checkpoint` 通过真实 PostgreSQL 注入写失败并回读响应和投影 | 移除版本读取条件，GET 返回旧用量 | Passed |
