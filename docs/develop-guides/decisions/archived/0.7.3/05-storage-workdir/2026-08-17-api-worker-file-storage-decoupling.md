# API / Worker 文件存储解耦

状态：archived
类型：architecture
Owner：docker-compose.yml

日志与缓存路径由 `yuxi.config` 和 `logging_config.py` 拥有。本记录拥有 API、worker 与 provisioner 的
进程权限、日志和缓存解耦边界；当前文件边界由
[Workdir 归属 UserWorkspace](./2026-08-19-workdir-in-user-workspace.md)与
[Workspace Owner 收敛](./2026-08-21-workspace-owner-convergence.md)拥有。

## 问题

默认 Compose 曾让 API、worker 与 sandbox-provisioner 共享宿主机 `saves`，并让业务服务通过宿主机
路径、Docker socket 和隐式共享日志相互依赖。API 与 worker 因而不能独立收紧权限或部署，附件和
outputs 在 Sandbox 重建、父子 Agent 与并发 Run 中也缺少明确的恢复与发布事实。

## 决策

- API 与 worker 不再挂载 `/app/models` 或 Docker socket；只有 Docker sandbox-provisioner 持有
  Docker daemon 权限。测试清理通过 provisioner 的鉴权管理 API 完成。
- API 与 worker 使用独立 `YUXI_RUNTIME_DIR`。日志和 Office 预览缓存位于各自容器本地运行目录，
  不写入共享 `saves`；管理端日志接口只读取 API 进程日志。
- Conversation 附件与 outputs 直接使用 UserWorkspace 中的 Workdir。未确认的临时附件解析仍可使用
  用户隔离的 MinIO 前缀。
- 用户级 `/home/gem/user-data` 和按 uid 汇总的授权 Skills 只读投影通过显式 UserWorkspace 与 Skill
  projection 挂载进入 Sandbox；当前 Skill source/projection 边界由
  [共享 Skill 持久源与个人 UserWorkspace 边界](../09-skill-cli/2026-08-18-skill-source-convergence.md)拥有。API/worker 与
  provisioner 不共享 shipping `saves` 数据根；`storage-migrator` 的 v0.7.1 legacy mount 属于一次性升级边界。

## 替代方案

- 保留 API/worker Docker socket、models 和共享日志目录：拒绝。没有应用 consumer，权限与部署耦合
  大于收益。
- 附件继续依赖 host uploads：拒绝。MinIO 已拥有正式字节，Sandbox 重建不应要求同一宿主机。
- 把 MinIO 通过 s3fs/ossfs 挂成 POSIX：拒绝。对象存储不拥有 shell 所需的 rename、partial write
  和锁语义，并会扩大凭据边界。
- 整体重放旧 `feat/filestore-decouple`：拒绝。旧实现没有当前 RunAttempt、revision 与确认不明事实，
  且与附件、Viewer 和调度实现冲突。
- 共享 Project RWX POSIX 文件系统：文件事实已收敛到 UserWorkspace 中的 Workdir，不再维护独立
  Project 存储域。

## 后果

- API/worker 权限和宿主机耦合减少，日志、缓存与文件主链路可以独立部署和重建。
- API 日志不是 worker 日志聚合；历史日志留存由容器平台负责。Office 缓存和本地 runtime 可在重建时
  丢失。
- UserWorkspace Workdir 删除每 Run 文件副本、父子 projection/merge 与发布前 404；这些后果由当前
  Workdir owning decision 记录。
- 真实 Kubernetes 部署仍需要目标集群 smoke；一次性 legacy mount 不构成 shipping runtime 共享根。

## 验证

- Compose 与容器 mount inspection 验证 API/worker 不持有 Docker socket 或 models mount，Docker
  provisioner 保留 daemon 权限；配置、cleanup、health、日志与 Office cache 测试覆盖对应边界。
- 真实 HTTP、PostgreSQL、MinIO、worker、Viewer 与 Sandbox integration/E2E 覆盖附件和 Workdir 主链路；
  当前文件事实的完整证据归属 Workdir 与 Workspace owning records。
- 真实 Kubernetes 部署 smoke 为 `Not run`，不能由 Compose、Pod spec 或 unit 结果替代。
