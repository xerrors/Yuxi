# 显式存储域与 Kubernetes PVC 收敛

状态：archived
类型：simplification
Owner：docs/develop-guides/decisions/archived/0.7.3/2026-08-19-workdir-in-user-workspace.md

本记录中独立 Project 存储域与三挂载/PVC 契约已被
[Workdir 归属 UserWorkspace 并取消独立 Project 存储域](./2026-08-19-workdir-in-user-workspace.md)
取代，仅保留为部署设计演进的历史背景。

## 问题

API、worker 与 provisioner 曾共同挂载 `/app/saves`，provisioner 再从这个广域挂载反推宿主路径；
Kubernetes Pod 也把 Project、User Data 与 Skill 投影混在一个历史 thread PVC。即使实时 Workdir
语义已经成立，部署边界仍会让服务获得未声明的文件能力，并保留旧 thread/Skill 配置面。

## 决策

Compose 拥有 shipping 服务的显式存储挂载与迁移 gate；动态 Sandbox 的 Docker host root 与 Kubernetes
PVC/subPath 由 `docker/sandbox_provisioner/app.py` 拥有。

- shipping 服务不再挂载 `/app/saves`。API 为用户 Workspace UI 挂载显式 User Data，加上 Skill
  source/projection；worker 为 Agent 上下文读取挂载显式 User Data，加上 Skill source/projection；provisioner 只挂载 Project、User Data 与
  Skill projection 三个明确目录。
- PostgreSQL 是 LangGraph checkpoint 的唯一 Owner。API、worker 与 Agent 不读取后端选择环境变量，
  也不挂载本地 checkpoint 目录。
- `storage-migrator` 是唯一可挂载历史广域目录的停机迁移 Owner。它在 API、worker 与 provisioner
  启动前完成 Project 物化、旧 schema/对象清理和共享 Skill 源迁移；个人 Workspace Skill 原地保留；
  shipping 启动只校验 active gate，不再扫描历史宿主目录。旧业务 schema 已存在且切换尚未完成，
  或仍存在旧 Skill 源时，migrator 还必须校验 `scripts/migrate-storage.sh` 创建的一次性 quiescence
  proof；普通 `docker compose up` 不得执行破坏性删除。首次安装与已 active 的部署由迁移前持久 schema
  状态区分，不依赖可竞态的瞬时文件/行数判空。
- 停机脚本接收并复用 Docker Compose 参数，覆盖开发和生产配置，也能从已 stop/down 的部署以
  `--no-deps` 启动受控 provisioner。provisioner 在 quiesce 开始后拒绝新建 Sandbox，按 Docker 容器
  或 Kubernetes Pod 权威枚举删除并等待归零；枚举失败、Service 丢失但 Pod 仍在、Pod 仍处于
  Terminating 都不得签发 proof。
- Docker provisioner 从 `/app/projects`、`/app/user-data`、`/app/skill-projections` 三个挂载分别解析
  bind source，不再推导共同父目录。旧 `DOCKER_THREADS_HOST_PATH` 被
  `DOCKER_USER_DATA_HOST_PATH` 取代。
- Kubernetes 使用 `PROJECT_DATA_PVC` 承载 `projects/<workdir_id>` 和
  `user-data/shared/<uid>`，使用独立 `SKILLS_PVC` 承载 `skill-projections/<uid>`。Sandbox 内 Skills
  mount 始终只读；Project Data PVC 的目标存储类必须支持 RWX 才能跨节点实时共享。
- 双 PVC contract 面向后续 Kubernetes 新部署。历史 `THREAD_PVC` 不是运行时 fallback；仓库当前不
  拥有集群 StorageClass、PVC 大小、Secret 或完整应用 manifests，也不承诺自动原地迁移旧 PVC。
  旧部署必须保留原卷，由 operator 离线导出、校验并导入新布局；不能把新变量直接指向旧 claim。
- `SAVE_DIR`、`THREAD_PVC` 运行时 fallback、旧 base.toml shipping 读取、历史 Skill workspace fixture 与 API/worker
  启动期物化均从 shipping 配置面删除。

## 替代方案

- 保留一个共享 saves 并靠代码自律：拒绝。挂载本身已经扩大了服务能力，且路径推导继续耦合部署。
- 把 User Workspace 改成对象存储或通用 FileStore：拒绝。产品要求实时 POSIX；API 的专用 User Data
  挂载是明确 Owner，不应为了形式统一增加同步协议。
- 用 RWO、hostPath 或 MinIO FUSE 宣称跨节点实时共享：拒绝。它们不能满足多个 Sandbox Pod 对同一
  Project 的 POSIX 可见性契约。

## 后果

- Project、User Data、共享 Skill source 和共享 Skill projection 有独立配置与挂载；个人 Skill 属于 User Data。
- 历史 SQLite checkpoint 文件不再自动导入或删除；升级后无法从这些文件继续暂停、审批或摘要状态。
- 升级必须先停止旧 execution runtime，再运行一次性 migrator。迁移失败会阻止 shipping 服务启动，
  不会回退到旧目录。
- 动态 Kubernetes Pod spec 已使用两个 PVC 并校验 volume name、subPath 与 Skills read-only；真实目标
  集群仍需用其实际 RWX CSI 做上线 smoke，spec/unit 不能替代该部署证据。

## 验证

- Compose contract：恢复 API/worker/provisioner `/app/saves` 或缺少显式域会失败。
- Docker/Kubernetes provisioner unit：显式 host root、双 PVC、Project/User subPath、Skill 只读与错误
  volume 绑定均有负向案例。
- 一次性 migrator unit：Project 先切换、旧 schema 待切换但缺少一次性停机证明时 fail-closed、个人
  Skill 目录不会触发共享迁移且始终原地保留，失败仍关闭数据库。旧 `base.toml`、仅停机切换才执行的非终态 Run
  收敛和迁移完成标记均有负向案例；脚本负控覆盖已 down 的生产 Compose 参数；provisioner 负控覆盖
  K8s orphan Pod、错误 PVC claim、枚举失败、Terminating 等待和 quiesce 后拒绝 create。
- backend non-slow unit：`1372 passed, 34 skipped`；宿主 Compose 配置 contract：`39 passed`；工程
  contract：`48 passed`。
- 真实 PostgreSQL/MinIO/HTTP 与 Docker 集成（迁移、作用域、撤权、Project/User/Skill
  producer-consumer）：`18 passed`；真实主/子 Agent E2E：`2 passed`。远程 Skill 一次性 Sandbox
  的真实 Docker 负控证明其不创建持久 User Data 或 Skill projection UID 目录。
- 实际 Compose 停机迁移在运行中和已停止状态各执行一次，退出码均为 0；API ready 的 Project
  Workdir、Sandbox、Skills 等必需组件均为
  `ok`。容器 mount inspection 证明 API 的 User Data 为可写、worker 为只读，provisioner 只有显式
  Project/User/Skill projection 三个数据域且没有 `/app/saves`。
- Web lint、`43 passed` unit、生产 build 与 docs build 通过；Ruff check/format 和
  `git diff --check` 通过。
- 真实目标 Kubernetes RWX 双 Pod smoke：Not run；当前开发环境没有目标 CSI/PVC。

旧能力不存在：shipping 服务不接受 `SAVE_DIR`、`THREAD_PVC`、`DOCKER_THREADS_HOST_PATH`、
`LANGGRAPH_CHECKPOINTER_BACKEND` 或 `YUXI_CHECKPOINT_DIR`，不挂载 `/app/saves` 或 `/app/checkpoints`，
也不包含 SQLite saver 与 SQLite checkpoint 迁移路径。

重新引入条件：只有新的部署契约明确要求同一服务拥有多个存储域，并提供权限、迁移、跨副本并发和
目标环境证据时，才可增加挂载；不得恢复广域父目录或静默 fallback。重新引入 checkpoint 后端选择
还必须先证明 API/worker 跨进程一致性、暂停恢复和升级迁移，不得恢复进程本地 SQLite。
