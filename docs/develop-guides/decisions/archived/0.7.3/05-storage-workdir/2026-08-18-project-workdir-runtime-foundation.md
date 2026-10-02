# Project Workdir 与 Sandbox Runtime 基础

状态：archived
类型：simplification
Owner：docs/develop-guides/decisions/archived/0.7.3/2026-08-19-workdir-in-user-workspace.md

本记录已被 [Workdir 归属 UserWorkspace 并取消独立 Project 存储域](./2026-08-19-workdir-in-user-workspace.md) 完全取代，仅保留为历史背景。

`ProjectWorkdir` 与 Conversation/AgentRun 绑定由 PostgreSQL model、repository 和 schema migration 拥有；
Sandbox identity、generation 和挂载校验由 `agents/backends/sandbox/provider.py` 与
`docker/sandbox_provisioner/app.py` 拥有；用户 Skill 投影由 `agents/skills/service.py` 拥有。

## 问题

旧 Sandbox identity 同时混入文件 thread、Skills thread 和每 Run instance，文件授权、Agent 选择与运行
环境生命周期互相耦合。现有用户 workspace 只有隐式路径，没有可供未来多个 Conversation 共享的持久
工作目录身份；provisioner 也缺少可防止旧实例误删新实例的 generation 契约。Skills 文件按 thread
复制，既不能表达“用户授权全集”，也会在多 worker 同步共享来源时产生竞争与越界风险。

## 决策

- PostgreSQL 使用 `ProjectWorkdir` 保存 opaque ID、所属 uid、存储键与物化状态。顶层 Conversation
  创建默认 Workdir；子 Conversation 通过 `SubagentThread` 继承根 Conversation 的 `workdir_id`，跨
  用户绑定被拒绝。
- `AgentRun.runtime_scope_id` 持久保存根 Conversation runtime scope。Sandbox hash、cache key、wire、
  Docker label、Kubernetes annotation 和工具连接不再使用 `file_thread_id` 或 `skills_thread_id`；当前
  identity 由 uid、runtime thread 和可选 instance 组成，Workdir 作为不可漂移的挂载约束。
- provisioner 为每次 runtime incarnation 返回 generation。发现、缓存、删除、idle reaper 和
  Kubernetes 409 恢复都复核 identity/generation，旧观察不能删除或接管新的同名实例。
- Docker 与 Kubernetes 支持可选 Project/User/Skills mount contract。Project Workdir 在 Sandbox 中
  使用 `/home/gem/projects/project-<opaque-id>` 并作为显式 Workdir fixture 的默认目录；Kubernetes
  contract 要求 RWX PVC/subPath。当前 shipping 文件主链路尚未切换到该可选 contract。
- `/home/gem/skills` 是按 uid 汇总的共享/内置授权全集只读投影；个人 Skill 直接保留在 UserWorkspace。
  Agent 配置只控制 Prompt 和工具激活，不改变
  Sandbox identity 或文件可见集合。投影刷新在 PostgreSQL uid advisory lock 内重读最新授权，再以
  共享卷 flock 串行替换；授权上下文缺失时 fail-closed。
- 共享 Skill 投影使用从文件系统根逐组件 `O_NOFOLLOW` 的 fd-relative 快照，只复制普通文件和
  真实目录。symlink 竞态、Unix socket、FIFO、设备等特殊项会删除旧 slug 投影并阻止本次刷新。
- personal Skill 的持久源与单一路径已由
  [Skill 持久源与只读投影收敛](../09-skill-cli/2026-08-18-skill-source-convergence.md)接管；本记录只保留授权投影的
  并发与安全基础。
- 本决定落地时附件、outputs、Viewer 和 artifact 暂由旧 Owner 处理；后续
  [实时 Project Workdir 与独立 Sandbox Runtime](./2026-08-18-live-project-workdir-and-runtime.md)
  已完成全量物化、实时主链路切换和旧 revision/hydrate 表面删除。

## 替代方案

- 继续让 `file_thread_id`、`skills_thread_id` 参与 Sandbox identity：拒绝。它把历史文件 Owner 和
  Agent 选择错误地提升为运行环境隔离边界。
- 在 4R-A 直接切换 uploads/outputs/Viewer：拒绝。历史数据尚未完成全量物化、维护 fence 和 activation
  gate，局部切换会产生按 Conversation 混跑与空目录假成功。
- 用 MinIO 或 s3fs 模拟实时 POSIX Workdir：拒绝。对象存储不拥有 rename、partial write、锁和多进程
  可见性所需的完整文件系统语义。
- 把 personal Skill 复制进共享授权投影：拒绝。个人目录由 UserWorkspace 直接提供，投影只承载共享与内置 Skill。

## 后果

- Project 文件身份与 Sandbox runtime identity 已分离，未来 Project 只需让多个顶层 Conversation
  指向同一 `workdir_id`，无需再次改变文件协议。
- 同 uid 的父子 Agent 看到相同共享 Skill 投影与 UserWorkspace 个人 Skill，但各自 Prompt/工具仍保持选择隔离。
- 实时文件行为由后续 owning decision 负责；本记录只保留 Workdir/runtime identity 与 Skills 投影基础。
- Skills 投影刷新会对共享来源执行受限安全复制并跨 worker 串行化，换取授权一致性。
- 真实 Kubernetes RWX 行为仍需目标集群 smoke；Compose 和 Pod spec 测试不能替代该证据。

## 验证

- backend non-slow unit：`1377 passed, 26 skipped`；Skills 定向 unit：`60 passed`。
- 真实 PostgreSQL Workdir/schema/runtime scope 与 advisory-lock 撤权 integration：`5 passed`。
- 真实 Docker 双 Sandbox：同 Workdir 文件互见且 `/tmp` 隔离；Skills 跨 Sandbox 共享、跨 uid 隔离、
  只读写拒绝：`2 passed`。
- output revision 旧链路兼容 integration：`5 passed`。
- symlink 交错、特殊文件、执行位、确定性两进程 flock、缺失授权上下文和 generation/ABA 均有负向测试。
- 工程契约 `48 passed`，Ruff check/format、`git diff --check` 与 docs build 通过；Darwin Unix socket
  负控真实运行通过。
- 真实 Kubernetes RWX smoke：`Not run`。

旧能力不存在：Sandbox identity/wire/mount/tool 链路不再接受 `file_thread_id` 或
`skills_thread_id`，Skills 不再按 thread/Agent 选择创建文件投影，personal Skill 投影不再使用会跟随
symlink 的复制路径。

重新引入条件：只有新的产品边界明确要求不同文件 thread 或 Skill 选择拥有独立运行环境，并提供对应
生命周期、授权和真实并发证据时，才可重新把它们引入 runtime identity；按 thread 或 Agent 选择隔离
Skill 文件投影还必须有新的文件授权边界及生命周期/并发证据。用户可写 personal 来源不得恢复会跟随
路径的复制；只有来源先成为不可变的受信快照时，才可使用普通 tree copy。
