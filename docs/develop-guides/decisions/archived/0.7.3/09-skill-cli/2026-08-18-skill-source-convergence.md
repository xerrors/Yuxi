# 共享 Skill 持久源与个人 UserWorkspace 边界

状态：archived
类型：simplification
Owner：backend/package/yuxi/services/skills/shared.py

Skill 持久目录配置由 `yuxi.config` 拥有；Prompt 与激活路径由
`agents/middlewares/skills.py` 拥有；Sandbox 只读挂载仍由 provisioner 拥有。

## 问题

共享/内置 Skill、用户投影和安装草稿曾隐式依赖广域 `SAVE_DIR`。存储解耦过程中一度又把个人 Skill
迁到 `skill-sources/personal` 并复制进 `/home/gem/skills`，这破坏了个人 Skill 原本由 UserWorkspace
拥有且不进入共享数据库和投影的边界。

## 决策

- `YUXI_SKILL_DATA_DIR/shared/<slug>` 只保存共享与内置 Skill；其元数据和授权由 PostgreSQL `skills` 表拥有。
- 个人 Skill 始终保存在 UserWorkspace 的 `workspace/agents/skills/<slug>`，不进入 PostgreSQL，也不进入
  `YUXI_SKILL_DATA_DIR`。个人 Skill 列表按请求直接扫描该目录，不维护 Redis metadata cache。
- `YUXI_SKILL_PROJECTION_DIR/<safe-uid>` 只物化当前 uid 获授权的共享与内置 Skill，并只读暴露为
  `/home/gem/skills`。个人 Skill 由既有 UserWorkspace mount 直接暴露为
  `/home/gem/user-data/agents/skills`。
- Agent 选择只影响 Prompt 与工具激活；个人与共享 Skill 同 slug 时，逻辑解析仍由个人版本覆盖共享版本，
  但不会把个人目录复制到共享投影。
- API 与 worker 都是受信任的个人 Skill service consumer，并以固定 `1000:1000` 写 UserWorkspace；worker
  的写能力用于主 Agent `install_skill` 工具的原子安装。共享 Skill projection 继续只读，Sandbox 的普通
  Project 文件写入仍通过其受限 UserWorkspace 挂载与文件边界执行。
- Skill 安装草稿属于进程可丢弃状态，使用 `YUXI_RUNTIME_DIR/skill_import_drafts`，不再进入持久卷。
- 一次性 `storage-migrator` 在 PostgreSQL advisory lock 下只迁移已识别的旧共享来源。迁移使用
  fd-relative `O_NOFOLLOW` 快照、校验 `SKILL.md` slug，并在目标冲突时拒绝切换。UserWorkspace 中的
  个人 Skill 原地保留，不参与共享 Skill 迁移判定，也不会被复制或删除。
- PostgreSQL `skills.dir_path` 仅保存相对 Skill 数据根的共享来源路径，例如 `shared/<slug>`；
  个人来源由 uid 目录拥有，不伪装成共享数据库记录。

## 替代方案

- 把个人 Skill 迁到独立 `skill-sources/personal`：拒绝。个人 Skill 的 Owner 就是 UserWorkspace；另建来源会制造迁移和双写。
- 把个人 Skill 再复制到 `/home/gem/skills`：拒绝。它制造第二份运行时内容，并混淆共享授权投影的语义。
- 把个人 Skill 迁入 Project Workdir：拒绝。Project 可能由多个 Conversation 共用，而个人 Skill 授权
  与生命周期属于 uid。
- 把所有 Skill 内容迁入 PostgreSQL 或 MinIO：拒绝。Sandbox 需要真实目录与可执行文件，只读 POSIX
  投影已是更直接的运行边界。
- 为个人 Skill 增加共享表记录：拒绝。全局唯一 slug 会破坏不同用户独立使用同名个人版本的语义。

## 后果

- 共享/内置 Skill 从 `/home/gem/skills/<slug>` 读取；个人 Skill 从
  `/home/gem/user-data/agents/skills/<slug>` 读取。
- 共享 Skill source/projection 可以在 Compose/Kubernetes 中使用独立语义挂载；个人 Skill 随 User Data
  持久域和 UserWorkspace 生命周期存在。
- 历史来源损坏、包含链接/特殊路径或与新 Owner 内容冲突时，启动 fail-closed 并保留旧数据；不会静默
  选择任一版本。个人目录不属于该迁移器的删除范围。
- Kubernetes 与共享卷装配由 `docker/sandbox_provisioner/app.py` 和 shipping deployment config 拥有；
  数据面身份由[统一 Workspace 运行身份](../05-storage-workdir/2026-08-20-unified-workspace-runtime-identity.md)拥有。

## 验证

- backend non-slow unit：`1380 passed, 34 skipped`；Linux 个人 Skill 定向 unit：`73 passed`；
  PostgreSQL 投影 integration：`2 passed`。
- 真实 Docker/HTTP integration：`6 passed`，覆盖共享投影只读挂载、Project Workdir 与 artifact 授权；
  共享迁移保留个人 UserWorkspace 目录、个人目录不触发共享迁移、共享投影不调用个人 Skill 合并均有负向案例。
- 真实主 Agent 个人 Skill E2E：`1 passed`，同时证明个人文件保留在 UserWorkspace 且不存在同 slug 投影副本。
- 负向测试覆盖 UserWorkspace 根路径链接、个人文件链接越界和安装期间并发同名目录保留。
- Compose 契约覆盖 development/production worker 的 UserWorkspace 可写挂载，防止受支持的 Agent 内安装
  因只读文件系统退化；共享 Skill projection 的只读契约保持不变。Runtime System Tests 还以 shipping
  worker 的固定身份在真实个人 Skill 目录写入、回读并清理探针，闭合 mount、uid 与宿主权限；两份 Compose
  契约 `2 passed`，重建后的 shipping worker mount inspection 为 `RW:true`，ARQ health check 通过。
- docs build、Ruff check/format、工程 contract 脚本及其 `62 passed` unittest、`git diff --check` 通过。

旧能力不存在：生产代码不再把个人 Skill 写入 `skill-sources/personal`，不把个人 Skill 复制到
`skill-projections/<uid>`，也不把它映射到 `/home/gem/skills`。Skill 安装草稿仍不写入持久 `SAVE_DIR`。

重新引入条件：只有新的产品授权明确要求个人 Skill 脱离 UserWorkspace，并提供数据迁移、备份恢复、
并发更新和用户可理解的生命周期证据时，才可改变其 Owner；不得以统一路径为由复制进共享投影。
