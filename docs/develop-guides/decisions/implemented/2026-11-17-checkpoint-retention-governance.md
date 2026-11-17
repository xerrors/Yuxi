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
  只有一个实例执行。`sweep_checkpoint_garbage` 三类各限 500 线程/轮，跑不完留给下一轮：软删会话线程
  整删、超 K 线程复用同一裁剪、无会话且最后写入超过 7 天宽限的孤儿线程整删。目标线程的"超 K"筛选与
  裁剪同维（逐 `thread_id, checkpoint_ns`），否则各命名空间都未超 K 而总和超 K 的线程会被计入处理量
  却零删除；运维脚本的 P2 使用同一口径。
- 增量裁剪与在途执行的竞争：活跃 Run 探测与三条 DELETE 不在同一原子事务内。检查之后新提交的 Run 其
  checkpoint 只会进入每条语句各自重算的 keep（最新版本必然入选），因此不需要额外加锁；反过来，
  `finish_run_lifecycle` 先派发下一个输入再裁剪，steer/follow-up 已把新 pending Run 提交的线程本轮跳过，
  其历史由次日清扫兜底。
- 停机维护：`scripts/checkpoint-cleanup.sh`（report / purge-deleted / retain）按分批事务处理存量，
  带阻塞预检、锁超时重试与批次心跳，清理后归还磁盘需要 VACUUM FULL 窗口。
- 边界：checkpoint 是执行内部状态而非业务事实，因此裁剪运行在 Run 终态事务之外的独立会话，失败只
  告警并返回 `False`，不阻塞终态；清扫计数只统计成功处理，观测不把失败虚报为已处理。
- K=5 取常量而非配置项：恢复与审批只需最新版本，`interrupted`/`yielded` 虽属终态但续跑同样只读
  最新，留 5 版已覆盖排查需要，当前没有需要不同保留量的 consumer。
- 会话删除级联不在本批：当前基线只有把会话置为 `archived` 的写主，没有写 `status='deleted'` 的流程；
  读侧已普遍把 `'deleted'` 当作不可见态（`checkpoint_state`、`cooperation`、`messages` 等），清扫的
  deleted 分支与之一致，删除写主落地后即刻生效，此前的残留由 K 上限封顶。

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
- 同一清理语义在三处编码：`saver.adelete_thread`（整删）、模块手写 SQL（按版本裁剪）、脚本手写 SQL
  （存量分批）。saver 若改表结构或 `channel_versions` 形态，只有 `adelete_thread` 路径会自动跟进，
  另两处需同步修改，集成用例是它们的共同 oracle。
- 孤儿桶按 `checkpoint->>'ts'` 判龄：若历史里存在无法转成 `timestamptz` 的异常行，当轮孤儿清扫整体失败
  （只记日志， deleted/超 K 两桶不受影响，下一轮自愈）；不为该假设形态增加逐行容错。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 终态裁剪保留每个 `(thread_id, checkpoint_ns)` 最近 K 版，独占 blob/writes 同批删除，被最新保留版本引用的 blob 版本留下 | 最新版本或其在用 channel 数据被删 | checkpoint_retention.purge_thread_checkpoint_history | `pytest test/integration/services/test_checkpoint_retention.py` = 7 passed（真实 PG：8 版裁到 5、真实 saver 写 7 版后面板读仍拿到最新值） | ① `_DROP_BLOBS` 的 `channel_versions` 引用条件改为恒真 → 独占 blob 用例红；② 默认保留量置 0 → 钩子与清扫用例红；两处还原后复跑全绿 | Passed |
| 命名空间维度独立：一个线程的多个 `checkpoint_ns` 各自保留 K 版，重名的 `checkpoint_id` 与 blob `version` 互不顶替 | 子命名空间历史被整页删除或整体保留 | 同上 | `test_purge_trims_each_namespace_independently`（8 版留 5 + 3 版全留，三表逐命名空间计数） | keep 集合的 `PARTITION BY checkpoint_ns` 去掉后该用例红（`命名空间 sub:agent-1 的保留集不符：[]`），还原绿 | Passed |
| 有未终态 Run 的线程整体跳过 | 裁剪删掉在途执行尚未收敛的 writes | 同上 | `test_purge_skips_thread_with_unfinished_run`（含阳性对照） | 删除活跃 Run 存在性探测后该用例红，还原绿 | Passed |
| 清扫的"超 K"筛选与裁剪同维，计数不虚报 | 各命名空间都未超 K 的线程被计入处理量却零删除 | checkpoint_retention.sweep_checkpoint_garbage | `test_sweep_skips_thread_whose_namespaces_are_all_within_k` | 把筛选改回 `group by thread_id` 后该用例红，还原绿 | Passed |
| 裁剪失败不泄进 Run 终态主链路 | 优化失败拖垮业务终态 | run_lifecycle.finish_run_lifecycle + retention | `pytest test/unit/services/test_checkpoint_retention_guards.py::test_purge_swallows_session_failure_and_reports_false` | 去掉 try/except 后该用例以异常外泄红 | Passed |
| 清扫只计成功、单实例执行 | 观测虚报处理量、多副本重复清扫或抢到锁却不清扫 | retention.sweep_checkpoint_garbage + bootstrap/worker | `test_sweep_survives_saver_failure_without_counting`、`test_sweep_counts_trimmed_only_when_purge_succeeds`、`test_sweeper_leader_slot_dedupes`、`test_sweeper_runs_once_when_slot_acquired` | 让 trimmed 无条件计数、或去掉 `if not acquired: return`，对应用例红；还原绿 | Passed |
| 清扫覆盖软删线程、超 K 线程与宽限期外孤儿 | 残留无界增长 | checkpoint_retention.sweep_checkpoint_garbage | `test_sweep_covers_deleted_over_k_and_orphans_with_grace` | 默认保留量置 0 时该用例红，还原绿 | Passed |

| 终态钩子接线 | 增量继续无界增长 | run_lifecycle | `test_finish_run_lifecycle_hook_trims_terminal_thread` | 钩子改为空操作后该用例红，还原绿 | Passed |
| worker 启动/关闭装配新循环 | 循环未创建或退出时泄漏 | bootstrap/worker | `pytest test/unit/services/test_worker_bootstrap.py test/unit/services/test_knowledge_delegation_startup.py` = 21 passed | 未在 startup/shutdown 登记时 bootstrap 的调用序列断言红 | Passed |
| 其余链路无回归 | 钩子与循环引入连带失败 | — | `pytest test/unit -m "not slow"` = 3886 passed / 128 skipped / 0 failed；`pytest test/unit/services/test_checkpoint_retention_guards.py test/unit/services/test_worker_bootstrap.py test/unit/services/test_knowledge_delegation_startup.py test/integration/services/test_checkpoint_retention.py` = 33 passed | — | Passed |
| 门禁 | — | — | `python3 scripts/ci/verify_engineering_contracts.py` 与其单测、backend 三项 ruff、`git diff --check` | — | Passed |
| worker 循环与 Run 生命周期在真实编排下无回归 | 新常驻循环干扰终态恢复、协作或后台作业 | bootstrap/worker + run_lifecycle | CI `system / 生命周期与 worker E2E`、`system / 会话协作 E2E`、`system / 后台作业与检索`、`system / 运行时契约与知识开关`、`service / PostgreSQL and Redis without Cube` | 不适用（由 CI 全量集成与 E2E 覆盖） | Passed |

本地开发栈未执行：需要 api 活体的集成用例在本地因测试管理员凭据未播种（`/api/auth/token` 401）无法运行，
同一面由上述 CI 作业覆盖。仍未执行的是 `scripts/checkpoint-cleanup.sh` 的 retain/purge-deleted 在真实大库上的
演练与 VACUUM FULL 归还磁盘（需要停机窗口）；本批验证了 `report` 只读路径、`bash -n`，以及
`PG_CONTAINER` 指定不存在容器与多栈无显式指定时的退避行为。

时钟偏移的后果：槽位号按 UTC 日取整，多副本时钟跨日可能让同一天扫两次（幂等，无副作用）；抢到槽位的
副本中途崩溃会丢失当日一次清扫，由下一日的槽位自愈。
