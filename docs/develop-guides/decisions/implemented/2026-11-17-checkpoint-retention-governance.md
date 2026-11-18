# checkpoint 线程历史保留：Run 终态裁剪、worker 每日兜底清扫与运维脚本

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/checkpoint_retention.py

## 问题

LangGraph saver 每个执行步骤都写入一个新 checkpoint 版本，而 Yuxi 只消费每个 `(thread_id,
checkpoint_ns)` 的最新版本：审批与 resume 走 `aget_tuple` 的 latest，面板状态读同一份
`channel_values`，没有任何路径回读历史版本。因此 `checkpoints`/`checkpoint_blobs`/
`checkpoint_writes` 随对话轮次无界增长，长会话线程把 PG 体积、备份与启动期
`setup_langgraph_checkpointer` 的探测都拖长，而这份增长没有对应价值。

## 决策

把保留策略做成三道防线，落在当前基线的会话与 Run 事实之上（`sessions`/`agent_runs.thread_id`）。

### 实现方案

- 增量裁剪（主路径）：`run_lifecycle.finish_run_lifecycle` 在 Run 进入终态后调用
  `purge_thread_checkpoint_history`，对每个 `(thread_id, checkpoint_ns)` 保留最近 K=5 个
  checkpoint 版本及其独占的 writes 与 blob；仍被保留版本 `channel_versions` 引用的 blob 版本
  必须留下。存在 `pending`/`running`/`cancel_requested` Run 的线程整体跳过，避免裁剪与在途
  执行竞争删掉尚未收敛的 writes。
- 每日兜底：`bootstrap/worker` 的 `_sweep_checkpoint_garbage_forever` 启动即追一次（补停机欠账），
  之后每 24h 一次；Redis 日槽位锁（`yuxi:checkpoint-sweep:slot:<槽>` + `WORKER_ID`）保证多副本
  只有一个实例执行。`sweep_checkpoint_garbage` 两桶各限 500 线程/轮，跑不完留给下一轮：超 K 线程复用
  同一裁剪、无会话且最后写入超过 7 天宽限的孤儿线程整删。目标线程的"超 K"筛选与裁剪同维
  （逐 `thread_id, checkpoint_ns`），否则各命名空间都未超 K 而总和超 K 的线程会被计入处理量却零删除；
  运维脚本的目标线程枚举使用同一口径。
- 增量裁剪与在途执行的竞争：活跃 Run 探测与三条 DELETE 不在同一原子事务内。检查之后新提交的 Run 其
  checkpoint 只会进入每条语句各自重算的 keep（最新版本必然入选），因此不需要额外加锁；反过来，
  `finish_run_lifecycle` 先派发下一个输入再裁剪，steer/follow-up 已把新 pending Run 提交的线程本轮跳过，
  其历史由次日清扫兜底。同一"每次删除现场重算保留集"的规则也是运维脚本 `retain` 的形态：脚本不预先
  物化全局 keep 清单（旧形态在生成清单后又有新写入时会按过期快照删除），而是每批 200 线程各自重算，
  与在线裁剪同形，并由 `test_trim_keeps_checkpoint_written_after_thread_selection` 钉住该时序。
- 在线裁剪必须有界：`purge_thread_checkpoint_history` 在同一事务内先 `SET LOCAL lock_timeout=2s` 与
  `statement_timeout=10s` 再删除。终态后处理不能变成无界等待；超时只回滚记日志，残留由每日清扫与
  脚本兜底（PR #1173 维护者意见 3）。
- 结果三态而非布尔：`PURGE_TRIMMED` / `PURGE_SKIPPED_ACTIVE` / `PURGE_FAILED`。清扫据此分别计入
  `trimmed_threads` 与 `skipped_active_threads`，"因在途执行什么都没删"不再与"真删成功"混成同一个数
  （维护者意见 4）；`failed` 两者都不计入。
- 停机维护：`scripts/checkpoint-cleanup.sh` 提供 `report` / `retain` / `vacuum-full`，分批事务、
  阻塞预检、锁超时重试与批次心跳。`retain` 先做**写入静止预检**：非终态 Run 不为 0 就直接中止并提示
  先停 api/worker（维护者意见 1）；`report` 同步输出该计数，让操作者在动手前看到同一事实。清理后
  归还磁盘需要 VACUUM FULL 窗口。
- 保留量入参必须合法：`purge_thread_checkpoint_history` 对 `keep < 1` 直接 `ValueError`（在触库之前），
  脚本的 `K` 也必须是正整数——`K=0` 或负数会把非活跃线程的历史全部清空，那是销毁数据而不是治理
  （维护者意见 2）。
- 边界：checkpoint 是执行内部状态而非业务事实，因此裁剪运行在 Run 终态事务之外的独立会话，失败只
  告警并返回 `PURGE_FAILED`，不阻塞终态；清扫计数只统计成功处理，观测不把失败虚报为已处理。
- K=5 取常量而非配置项：恢复与审批只需最新版本，`interrupted`/`yielded` 虽属终态但续跑同样只读
  最新，留 5 版已覆盖排查需要，当前没有需要不同保留量的 consumer。
- 会话删除级联不在本批（按维护者意见从代码里去掉，不再作为"待接线的契约"保留）：当前基线只有把会话
  置为 `archived` 的写主，没有任何入口写 `sessions.status='deleted'`，因此整删分支当前不会命中任何线程。
  等删除与数据保留语义定下来后，连同脚本的对应子命令一起再补；在此之前删除类残留由 K 上限封顶。

## 替代方案

- 只做运维脚本、不做代码内裁剪：被否——存量清了增量还在涨，等于每天灭火。
- 把裁剪并入 Run 终态同一事务：被否——会在持会话行锁的结算事务里夹带第二写主，checkpoint 删除失败
  还会拖垮业务终态提交。
- 依赖 PostgreSQL 侧 TTL 或定时 `DELETE`：被否——不区分被引用与独占的 blob 版本，会删掉仍在读的
  channel 数据；且没有活跃 Run 保护，可能与在途执行竞争。
- 保留量做成 env 配置：被否——没有需要不同取值的 consumer，只增加一处需要文档与测试的分支。

## 后果

- 每个终态 Run 多一次尽力裁剪（自身 try/except，活跃线程跳过），审批与 resume 语义不变。
- worker 常驻循环 +1，与其他收敛循环同一取消与失败处理模式；停机超过一天的欠账由启动追补。
- 保留窗口之外的历史版本不可回读：面板与恢复都不依赖它，需要历史取证时应在此之前用
  `scripts/checkpoint-cleanup.sh report` 或数据库备份。
- `checkpoint-cleanup.sh` 的 retain 需要全表窗口排序，必须在低峰执行（脚本自带预检与心跳提示）。
- 清理语义有两份手写 SQL 形态（模块的按线程裁剪、脚本的按批裁剪）加一条 `saver.adelete_thread` 整删路径。
  脚本的 retain 形态与模块一致（每条语句现场重算保留集），因此两者共用同一时序不变量：saver 若改
  `channel_versions` 结构，两份 SQL 都要跟，集成用例是共同 oracle。
- 孤儿桶按 `checkpoint->>'ts'` 判龄：若历史里存在无法转成 `timestamptz` 的异常行，当轮孤儿清扫整体失败
  （只记日志，超 K 桶不受影响，下一轮自愈）；不为该假设形态增加逐行容错。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 终态裁剪保留每个 `(thread_id, checkpoint_ns)` 最近 K 版，独占 blob/writes 同批删除，被最新保留版本引用的 blob 版本留下 | 最新版本或其在用 channel 数据被删 | checkpoint_retention.purge_thread_checkpoint_history | `pytest test/integration/services/test_checkpoint_retention.py` = 8 passed（真实 PG：8 版裁到 5、真实 saver 写 7 版后面板读仍拿到最新值） | ① `_DROP_BLOBS` 的 `channel_versions` 引用条件改为恒真 → 独占 blob 用例红；② 默认保留量置 0 → 钩子与清扫用例红 | Passed |
| 被保留版本 `channel_versions` 仍引用的共享 channel blob 版本不被删除 | 在用 channel 数据被删空，恢复与面板读空 | retention `_DROP_BLOBS` 引用条件 | `test_purge_keeps_shared_blob_version_still_referenced` | 把引用条件改为恒真 → 该用例红 | Not run（本地无该分支的运行栈，由 CI 的 PostgreSQL 服务作业复跑） |
| 清理期间新写入的 checkpoint 不会被过期快照删掉（在线与脚本同形：每条 DELETE 现场重算保留集） | 按 T0 快照删掉 T0.5 写入的版本，正在执行的线程失去可恢复状态 | 同上 + `scripts/checkpoint-cleanup.sh` retain | `test_trim_keeps_checkpoint_written_after_thread_selection`；并在活库克隆 `yuxi_rehearse` 上实跑 `retain`：超 K 组 1→0、checkpoints 133→130（恰为 report 预告的 3 行超出） | 把 keep 集的 `ORDER BY checkpoint_id DESC` 改为 `ASC`（等价于"保留最旧"）后，时序用例与独占 blob 用例同时红，还原绿 | Passed |
| 命名空间维度独立：一个线程的多个 `checkpoint_ns` 各自保留 K 版，重名标识互不顶替 | 子命名空间历史被整页删除或整体保留 | 同上 | `test_purge_trims_each_namespace_independently`（根 8 版留 5、子 3 版全留，逐命名空间核三表计数） | 去掉 keep 集合的 `PARTITION BY checkpoint_ns` 后该用例红（`命名空间 sub:agent-1 的保留集不符：[]`） | Passed |
| 有未终态 Run 的线程整体跳过；跳过与真删在观测上分开 | 删掉在途执行尚未收敛的 writes；"什么都没删"被报成治理生效 | retention + sweep | `test_purge_skips_thread_with_unfinished_run`（含阳性对照）、`test_sweep_counts_trimmed_and_skipped_separately` | 删除活跃 Run 存在性探测后跳过用例红；把 skipped 并入 trimmed 计数后计数用例红 | Passed |
| 在线裁剪有界（锁等待 2s、语句 10s），超时回滚记日志 | 终态后处理变成无界等待，拖住 worker | retention.purge_thread_checkpoint_history | `test_purge_bounds_wait_before_deleting`（断言两处 SET LOCAL 早于第一条 DELETE） | 删除两处 `SET LOCAL` 后该用例红，还原绿 | Passed |
| 保留量必须是正整数（代码与脚本同一口径） | K<=0 清空非活跃线程全部历史，属数据销毁 | retention.purge_thread_checkpoint_history + 脚本 K 校验 | `test_purge_rejects_non_positive_keep`；脚本 `K=0/abc/-1 bash checkpoint-cleanup.sh report` 三次均退 1 并给出说明 | 移除 `keep < 1` 检查后该用例红；去掉脚本 case 校验后 K=0 会直接进入删除分支 | Passed |
| 清扫的"超 K"筛选与裁剪同维，计数不虚报 | 各命名空间都未超 K 的线程被计入处理量却零删除 | checkpoint_retention.sweep_checkpoint_garbage | `test_sweep_skips_thread_whose_namespaces_are_all_within_k` | 筛选退回 `group by thread_id` 后该用例红（断言"本线程不在被选中清单里"，不依赖共享库存量） | Passed |
| 清扫覆盖超 K 线程与宽限期外孤儿 | 残留无界增长 | 同上 | `test_sweep_covers_over_k_and_orphans_with_grace` | 默认保留量置 0 时该用例红 | Passed |
| 裁剪失败不泄进 Run 终态主链路 | 优化失败拖垮业务终态 | run_lifecycle.finish_run_lifecycle + retention | `test_purge_swallows_session_failure_and_reports_failed` | 去掉 try/except 后该用例以异常外泄红 | Passed |
| 每日清扫单实例执行 | 多副本重复清扫，或抢到锁却从不清扫 | retention + bootstrap/worker | `test_sweeper_leader_slot_dedupes`、`test_sweeper_runs_once_when_slot_acquired` | 去掉 `if not acquired: return` 后 dedupe 例红；抢锁成功却不执行时正向例红 | Passed |
| 终态钩子接线 | 增量继续无界增长 | run_lifecycle | `test_finish_run_lifecycle_hook_trims_terminal_thread` | 钩子改为空操作后该用例红 | Passed |
| worker 循环装配 | 循环未创建或退出时泄漏 | bootstrap/worker | `pytest test/unit/services/test_worker_bootstrap.py test/unit/services/test_knowledge_delegation_startup.py` = 21 passed | 未在 startup/shutdown 登记时 bootstrap 调用序列断言红 | Passed |
| 其余链路无回归 | 钩子与循环引入连带失败 | — | `pytest test/unit -m "not slow"`；`pytest test/unit/services/test_checkpoint_retention_guards.py test/unit/services/test_worker_bootstrap.py test/unit/services/test_knowledge_delegation_startup.py` = 28 passed；`pytest test/integration/services/test_checkpoint_retention.py`（克隆库）= 8 passed | — | Passed |
| 脚本 retain 前置写入静止预检 | 清理与在途写 checkpoint 的 Run 竞争 | scripts/checkpoint-cleanup.sh preflight | 在 `yuxi_rehearse` 把一条 Run 置回 `running` 后执行 `retain`：打印"仍有 1 个 pending/running/cancel_requested Run…请先停掉 api 与 worker"并 exit 1，恢复状态后正常完成 | 去掉预检的计数分支后 retain 会在有在途 Run 时继续删除 | Inspected |
| 门禁 | — | — | `python3 scripts/ci/verify_engineering_contracts.py` 与其单测、backend 三项 ruff、`git diff --check`、`bash -n scripts/checkpoint-cleanup.sh` | — | Passed |

本地开发栈未执行：需要 api 活体的集成用例在本地因测试管理员凭据未播种（`/api/auth/token` 401）无法运行，
同一面由 CI 作业覆盖。仍未执行的是 `scripts/checkpoint-cleanup.sh` 在**生产体量库**上的 retain 演练与
VACUUM FULL 归还磁盘（需要停机窗口）以及新增共享 blob 用例的本分支实跑（本地无该分支运行栈，由 CI 复跑）——本批只在活库克隆（百行级）上验证了 retain 的正确性与预检中止，
全表窗口排序的锁占用时长只有大库演练才能给出；`report` 只读路径、`bash -n`、`K` 非法值拒绝，以及
`PG_CONTAINER` 指定不存在容器与多栈无显式指定时的退避行为均已实跑。软删会话的整删分支按维护者意见移出本批，
待删除语义确定后与脚本子命令一并补回。

时钟偏移的后果：槽位号按 UTC 日取整，多副本时钟跨日可能让同一天扫两次（幂等，无副作用）；抢到槽位的
副本中途崩溃会丢失当日一次清扫，由下一日的槽位自愈。
