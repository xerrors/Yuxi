# 通用后台任务的持久执行权与可重建 Handler

状态：archived
类型：architecture
Owner：backend/package/yuxi/services/task_service.py

## 问题

知识库、图谱和评估等通用后台任务需要跨 API 进程生命周期执行。任务摘要持久化但 coroutine 和队列属于 API 内存时，API 退出会丢失执行意图；running 状态没有唯一 owner/lease，重复投递与迟到写入也没有数据库拒绝边界。

## 决策

PostgreSQL `tasks` 行拥有通用任务的持久执行意图、当前状态、去重键、Handler 版本、attempt owner、heartbeat 与 lease。API 提交 `task_type + handler_version + payload`，Task 行提交后向 Redis/ARQ 发布 task_id；发布失败保留 pending 事实，由 worker 启动和周期 publisher 补发。

独立 `worker` 从静态 registry 惰性加载领域 service Handler。worker 用唯一 attempt token 原子 claim pending Task；只有 lease 仍有效的当前 owner 可以更新进度、结果和终态。领域 callback 完成后以 PostgreSQL 实时时钟再次验证 lease，失权会回滚 owning transaction。共享 worker 的 ARQ 槽位由 Compose 的 `ARQ_MAX_JOBS` 配置，Durable Task 的数据库 claim 上限固定为 4，使长知识任务无法占满 AgentRun 容量；每个终态 Task 接力发布有限数量的 pending intent，正常容量竞争不依赖周期修复器补位。重复 ARQ 消息不能取得运行中任务，旧 owner 在 lease 过期后不能提交迟到结果。worker 的 Durable Task reconciler 与 ARQ、AgentRun reconciler 一同发布单一短 TTL readiness 事实。

共享 worker 的事件循环同时承载 AgentRun 与 Durable Task heartbeat。知识执行器构造以及 Milvus 集合创建、加载、查询删除等同步 RPC 在线程边界执行；执行器缓存以异步锁维持单实例语义，避免 offload 后的并发重复初始化。Task 进度仍按数值增量节流，但新消息必须持久化；timeout 只要求正有限值，worker 默认值可配置为超过 24 小时，单任务可向下覆盖但不能超过该值，确保 ARQ 外层超时不会先于持久任务契约取消执行。

Task 失联时统一失败，failure hook 在 Task 终态事务内将仍属该 Task 的文件收敛为对应错误态；旧 attempt 不能提交迟到文件终态。评估数据集与 Run 的增量 checkpoint 在 Task 行锁事务中验证 attempt，知识文件的 `parsing/indexing` 中间态绑定 Task 与 attempt owner。数据集失联后通过显式 resume 创建新 Task。通用 runtime 不持久化 Python 调用栈，也不猜测领域 checkpoint；只有领域 Handler 已证明副作用幂等并具备 fencing 时，才连同它的恢复语义引入自动重试。

知识库 ingest/parse/index、图谱和评估 Handler 位于各自 service，HTTP 路由只提交序列化 payload。评估领域对象与 Task intent 同事务创建或关联；数据库唯一约束拥有活跃任务去重，终态释放 dedupe key。worker 对 shipping registry 中的全部任务执行相同的 claim、取消、删除、裁剪与失联收敛规则，并只在执行对应任务时惰性加载知识 Handler。

Durable Task 结构通过 0.7.2 发布版到当前版本的完整业务升级引入，当前 business schema 版本由[版本化 Schema 迁移 Owner](../05-storage-workdir/2026-08-24-versioned-schema-migration-owner.md)统一定义；knowledge schema 为 v2。既有 Task 保留非终态和取消意图并标记 `handler_version=0`，只有该明确 legacy 版本使用当前 failure hook；其他未知 Handler 版本 fail-closed。worker 使用当前类型的 failure hook 原子收敛。knowledge 1→2 为文件增加 Task/attempt owner 字段，并把升级前无法归属 attempt 的 `parsing/indexing` 行一次性改为对应错误态；未版本化 baseline 创建当前结构后记录当前版本。

## 替代方案

- 保留 API 内存队列并在启动时 reset 文件状态：只能缓解一个故障结果，不能保留 queued work、提供跨进程 ownership 或数据库去重。
- 把通用任务合入 AgentRun：会把 Conversation、Message、LangGraph interrupt 与线程 FIFO 语义错误地带入知识库和评估领域。
- 持久化 Python coroutine：函数闭包、调用栈与进程资源没有稳定升级和反序列化协议。
- 所有失联任务自动重试：索引和图谱存在跨存储副作用，无法证明任意重放安全。

## 后果

- API 不执行通用后台 coroutine；API 退出后，已经提交的 pending Task 仍可由 worker 执行。
- ARQ 保持至少一次投递；PG claim/lease 提供单一有效 owner，不承诺 exactly-once 外部副作用。
- 取消 running Task 先保存意图，worker 心跳或 Handler 控制点完成清理后才进入终态；未执行 Task 可以立即取消。
- 数据集生成失联后明确失败；用户显式 resume 时从已持久化数量继续，并复用 dataset 级数据库 dedupe。其他任务同样需要从文件、chunk、向量或图谱事实判断是否重试。
- 暂停/checkpoint API 与 Redis Stream 进度 SSE 不属于本决定，后续能力必须继续由领域安全点和短期事件面实现。

## 验证

- `uv run --group test pytest -q test/unit -m 'not slow'` 覆盖提交顺序、发布失败保留 pending、容量释放接力、数据库去重、Handler 重建、legacy v0/未知版本边界、重复投递、进度消息、timeout/cancel/shutdown、Milvus 阻塞调用 offload、缺失可选 MinIO bucket 时的幂等清理和 worker/readiness 装配。
- 临时 PostgreSQL Schema 中运行 `test_schema_migration_version.py` 与 `test_durable_task_repository.py`；覆盖 legacy baseline 与 handler version 0、business v2 完整收敛、knowledge 1→2、并发 claim/dedupe、数据库时钟、领域 callback 与文件行锁等待、迟到 owner、Durable Task 容量上限、所有 Task 终态 hook、评估 checkpoint fencing，以及超过 200 条任务的摘要。
- shipping worker path：临时 PostgreSQL 数据库与独立 Redis DB 中故障注入首次 ARQ publication 失败，提交进程退出后回读 Task 与 Dataset 均为 pending；随后启动真实 `arq server.worker_main.WorkerSettings`，由 worker startup publisher 将 Task 与 Dataset 收敛为 `success/completed`，`attempt_count=1` 且 owner 已释放。同一阶段还通过真实 worker 失败收敛验证 Dataset failure hook，并启动真实 Milvus，从 HTTP 提交知识文件后回读 Task 与文件最终状态。该多进程测试已接入 `system-tests.yml` 的 Durable Task 阶段。
- `uv run --group test ruff check package server test`：通过；本次修改 Python 文件的 `ruff format --check` 通过。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，61 tests passed。
