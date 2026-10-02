# 收敛 AgentRun 执行树与 Kubernetes Inventory 边界

状态：archived
类型：architecture
Owner：backend/package/yuxi/repositories/agent_run_repository.py

Run 同行形状由 `storage/postgres/models_business.py` 与 schema manager 拥有，worker 跨行执行边界由 `services/run_worker.py` 拥有，Kubernetes Sandbox inventory 由 `docker/sandbox_provisioner/app.py` 拥有。Workspace、Skill source/projection、Skill runtime module 与数据面身份分别由 [Workspace Owner 收敛](../05-storage-workdir/2026-08-21-workspace-owner-convergence.md)、[Skill source 收敛](../09-skill-cli/2026-08-18-skill-source-convergence.md)、[Skill runtime module 边界](../09-skill-cli/2026-08-20-skill-runtime-module-boundary.md)和[统一 Workspace 运行身份](../05-storage-workdir/2026-08-20-unified-workspace-runtime-identity.md)拥有；本记录不重复定义其文件、事务、缓存、路由或安装契约。

## 问题

取消执行树、Run 持久形状、worker 跨行归属和滚动升级期 Sandbox inventory 必须在各自副作用 Owner 处闭合。锁顺序不一致会形成 PostgreSQL 死锁；非法非终态 Run 会污染调度与恢复；worker 若不复核 creator/relation/scope/workdir 会执行错误的共享 runtime；只枚举新标签 Pod 会漏掉升级前仍在运行的 Sandbox。

## 决策

- `AgentRunRepository.request_cancel_execution_tree` 先锁定目标 root，再按 `created_at, id` 的确定顺序逐层锁定 active descendants，在一个事务中写入取消事实并返回需要发布取消信号的 Run ID。Service 只在 owning transaction 提交后发布 Redis 信号。
- PostgreSQL 使用 `CHECK ... NOT VALID` 约束新的非终态 Run：Chat 使用自身 Conversation scope 且没有 creator/relation；Resume 使用自身 Conversation scope、有 creator 且没有 relation；Subagent 必须有 creator 与 relation。历史终态行不因约束安装而被扫描或拒绝。
- Subagent 创建 service 校验 uid、creator、relation、父子 Conversation 与继承 scope。worker 在执行前复核 Chat/Resume scope，并对 Subagent 复核 creator、relation、父子 Conversation、继承 scope 与共享 Workdir；失败形成 `invalid_runtime_scope` 终态，不进入 Agent 执行。
- Kubernetes inventory 使用稳定的 `app=yuxi-sandbox` selector 枚举候选 Pod，再要求合法 `sandbox-id`。新增 `managed-by` 标签不能排除升级前 Pod；generation/precondition 继续保护删除操作。
- 首次执行的 Skill projection 冷启动与 Workdir 物化证据由 deterministic assembled-path E2E 覆盖；其 storage Owner 仍属于现有 Workspace/Skill records，不在本记录重新定义。

## 替代方案

- 在每个调用点分别增加取消、scope 或 bootstrap 检查：拒绝。新增 producer 容易遗漏，副作用与最终校验仍会分离。
- 只依靠 worker 校验，不增加数据库约束：拒绝。非法非终态事实仍可进入调度、恢复和运维查询。
- 使用数据库 trigger 校验全部跨行关系：拒绝。会复制 service/repository 的创建语义，并扩大迁移与调试表面。
- 一次性重写全部 Run transition 为通用状态机：拒绝。当前缺陷只需要聚焦的 execution-tree cancel、同行约束和执行边界校验。
- Kubernetes 只枚举带 `managed-by` 的 Pod：拒绝。滚动升级时会漏掉旧 Pod 并产生错误的 quiescence 结论。

## 后果

- 新的非法非终态写入由 PostgreSQL 拒绝；历史终态异常形状仍可读取。维护任务更新历史行时仍需满足当前约束。
- 跨行一致性由创建 service 与 worker 两个不同阶段校验；数据库约束不尝试表达 creator/relation 的完整图关系。
- 取消路径持有 root 后才等待 descendants，和终态路径保持同一方向；Redis 取消信号仍是提交后的通知，不是状态 Owner。
- Kubernetes unit/spec 只能证明 selector 与过滤逻辑，不能替代真实目标集群的滚动升级与删除 smoke。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| execution-tree 取消按 root 到 descendants 锁定 | cancel 等待 root 时已经持有 child，形成反向等待 | `AgentRunRepository` | `test_agent_run_lease.py::test_cancel_execution_tree_locks_root_before_descendants` 使用真实 PostgreSQL、独立 session、`pg_stat_activity` 与 `NOWAIT` | Session A 锁 root 后，Session B cancel 必须等待 root；第三个 session 仍能 `NOWAIT` 锁 child | Inspected；integration Not run |
| 新的非终态 Run 具有合法同行形状，历史终态行保留 | 非法 scope/creator/relation 进入持久层，或安装约束扫描并拒绝历史终态 | model constraint、schema manager | schema unit 与 `test_nonterminal_run_shape_constraint_preserves_terminal_legacy_rows` | 非法 pending Chat flush 被拒绝，异常形状 completed Subagent 可提交和回读 | Inspected；相关测试 Not run |
| worker 拒绝不属于当前 creator/relation/scope/workdir 的 Run | 错误 Subagent 复用其他执行树或 Workdir | `services/run_worker.py` 与 Run repository | worker unit、Subagent service unit 及代码路径检查 | Chat/Resume foreign scope、缺 creator、错误 relation、creator scope/workdir 不一致均失败 | Inspected；仅计入分支 HEAD，未提交差异不构成证据 |
| Kubernetes inventory 能发现 legacy Pod | 新 selector 排除无 `managed-by` 的旧 Pod | Kubernetes provisioner | provisioner unit/spec 与源码检查 | 仅含 `app=yuxi-sandbox`、`sandbox-id` 的 Pod 仍被列出 | Inspected；真实 Kubernetes smoke Not run |
| deterministic assembled path 从缺失 uid projection 冷启动并进入实际 workflow | 首次 Run 失败，第二次才因目录副作用成功；CI 未选择该测试或失败无 provisioner 日志 | E2E 与 `.github/workflows/system-tests.yml` | E2E 删除并确认 projection 不存在，首次 Run 后回读目录、Run、结果与执行事实；workflow 选择该文件并在失败采集 provisioner 日志 | 删除 projection 后只允许一次请求，不用重试掩盖 | Inspected；E2E 与 workflow Not run |

分支 `HEAD` 中的 Owner、测试定义与 workflow 接线为 `Inspected`。真实 PostgreSQL integration、deterministic E2E、真实 Kubernetes smoke 与本记录引用的产品测试为 `Not run`；未提交代码不构成该 lifecycle 结论的实现证据。
