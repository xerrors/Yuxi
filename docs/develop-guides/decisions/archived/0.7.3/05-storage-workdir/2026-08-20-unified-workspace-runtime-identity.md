# 统一 Workspace 运行身份并删除权限补丁

状态：archived
类型：simplification
Owner：docker/api.Dockerfile

## 问题

API、storage migrator 与 execution Sandbox 以不同 POSIX 身份写入同一 UserWorkspace。当前实现因此在文件创建、目录创建、旧数据迁移和 Sandbox 启动处反复使用 `0o777`、`0o666`、`fchmod` 与 `chmod a+rwx` 修复跨身份可写性。这些补丁把部署身份约束扩散进 `yuxi.utils.paths`、`Workspace`、Workspace API 和 provisioner，并让每个新写入入口都必须重复维护权限与失败回滚。

路径授权、root-to-leaf no-follow、普通文件检查和原子写入仍是 UserWorkspace 的安全边界，不属于本次删除对象。

## 决策

固定 Workspace 数据面身份为数值 `1000:1000`。API、worker 与 Sandbox 内实际执行文件和 shell 操作的 `gem` 服务使用该身份；storage migrator 保留 root，仅在 API/worker 启动前一次性把 Compose 持久目录迁移为该身份并收紧旧的 world-writable 权限。provisioner 继续以控制面身份管理 Docker/Kubernetes：Docker backend 只验证和挂载 Workspace；远程 Kubernetes PVC 不经过 Compose migrator，因此 root init container 只对当前 uid 子树执行带 marker 的一次性身份迁移。

新目录以 `0o700` 创建，新文件以 `0o600` 创建。当前 Sandbox 镜像必须以 root 完成受信任启动初始化，再由 supervisor 把文件 API、shell、Jupyter 和浏览器等数据面服务降权到 `gem:1000`；provisioner 强制注入固定 `USER/USER_UID/USER_GID`，不允许用户环境覆盖。Kubernetes `runAsUser` 保持 root 以满足镜像 bootstrap 契约，不再用 `fsGroup` 修改 storage migrator 已经拥有的持久权限。

保留所有 fd-relative、`O_NOFOLLOW`、`O_EXCL`、原子 rename、类型检查和业务 uid/Workdir 授权。

## 替代方案

- 保持不同 UID 并继续使用 `0o777/0o666`：改动最小，但不能删除当前补丁面，任何新增写入入口仍可能再次出现“能读不能改”。
- 不同 UID 共享专用组，使用 `2770/0660`：隔离性可接受，但新增 GID、setgid、umask、Docker supplemental group、Kubernetes `fsGroup` 与存储驱动兼容约束，比单一身份复杂。
- 让 Sandbox 以 root 运行：代码最少，但扩大不可信执行容器权限；在当前 `seccomp=unconfined` Docker 配置下不可接受。
- 由 API 代理所有写入：可以形成单一 writer，但 shell 命令仍直接操作挂载文件，若彻底代理需要重构 execution runtime，不属于简化。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| API、worker 与 Sandbox 数据面服务使用 `1000:1000` | 任一文件写入者仍依赖未固定的镜像默认身份或 root | Dockerfile、Compose、provisioner 环境与镜像 supervisor 契约 | Compose 配置单测、provisioner unit、真实镜像身份探针 | 删除 Compose user 或固定 `USER_UID/USER_GID` 后测试失败 | Passed：配置契约与真实 API/Sandbox 镜像探针通过 |
| 旧持久数据在 API 启动前一次性迁移为统一身份且不跟随 symlink | marker 提前发布、递归跟随 symlink 或旧数据仍不可写 | storage migration、Kubernetes uid init 与 `storage_migration.main` | migration/provisioner unit 与隔离目录探针 | symlink 保留且目标不变；失败中断可重试 | Passed：迁移 unit 与 bind mount 身份探针通过；真实 PVC 待部署环境验证 |
| 运行时写入不再执行权限修复 | `fchmod`、`chmod a+rwx` 或 world-writable mode 从其他入口残留 | Workspace/provisioner 创建代码 | `rg` 负向搜索与相关 unit | 重新加入旧调用后静态契约失败 | Passed：静态负向契约与相关 unit 通过 |
| 路径安全与原子写入保持不变 | 简化误删 no-follow、越界授权或失败清理 | `open_directory_fd`、`Workspace` | 相关 unit 与文件 E2E | symlink、目录冒充文件、失败写入仍被拒绝 | Passed：相关 unit 与真实跨进程 rename 探针通过 |
| Skill 基线不受权限简化影响 | 当前未提交 Skill 重构被混入或破坏 | Skill runtime 与 middleware | 指定 Skill unit 以及后端相关回归 | Skill 依赖闭包/门控测试恢复缺陷时失败 | Passed（21 tests） |

旧能力不存在：生产路径不包含为跨 UID 可写而设置 `0o777`、`0o666`、`fchmod(..., 0o777/0o666)` 或 `chmod a+rwx` 的逻辑；测试不再把 world-writable 权限作为成功契约。

重新引入条件：只有出现必须以不同 POSIX 身份直接写同一持久目录的当前 consumer，并有部署与真实文件系统证据证明统一身份不可行时，才重新评估共享组；不得直接恢复 world-writable 补丁。

## 后果

- Linux bind mount 与既有部署可能保存 root 或其他 UID 拥有的文件；Compose 一次性迁移必须在 root storage migrator 中完成并在成功后才发布 marker，Kubernetes PVC 由当前 uid 的 root init 迁移。
- 外部 Sandbox 镜像当前需要 root bootstrap；验证必须确认实际文件 API 和 shell 服务由 `gem:1000` 执行，不能把容器初始 root 身份误写成数据面身份。
- 将 API 改为非 root 会暴露 `/app/runtime`、模型缓存和其他持久挂载的所有权假设；镜像与 Compose 必须显式准备这些路径。
- 旧用户文件可能有执行位；迁移收紧 group/other 权限时必须保留 owner execute 语义。
