# 实时 Project Workdir 与独立 Sandbox Runtime

状态：archived
类型：simplification
Owner：docs/develop-guides/decisions/archived/0.7.3/2026-08-19-workdir-in-user-workspace.md

本记录已被 [Workdir 归属 UserWorkspace 并取消独立 Project 存储域](./2026-08-19-workdir-in-user-workspace.md) 完全取代，仅保留为历史背景。

Project 文件授权与实时读取由 `viewer_filesystem_service.py`、`thread_files_service.py` 和受信任
file bridge 拥有；Conversation/Run 与 Workdir/runtime 的绑定由 PostgreSQL repository、worker lifecycle
和 provisioner 共同拥有；启动期历史导入与旧对象清理由
`project_workdir_materialization_service.py` 拥有。

## 问题

旧文件模型把 uploads、outputs 和用户 workspace 建成不同协议：附件与 outputs 以 MinIO 对象和
`ThreadOutputRevision` 为持久事实，每个 Run hydrate 临时副本，父子 Agent 再通过 checkpoint、projection
和三方合并交换文件。该模型无法提供“Agent、子 Agent 与 Viewer 同时读取同一 POSIX 字节”的实时语义，
还让终态发布、文件可见性和 Sandbox 生命周期互相耦合。

## 决策

- `ProjectWorkdir` 是 Project 文件的唯一实时事实，挂载到
  `/home/gem/projects/project-<opaque-id>` 并作为 Sandbox 默认工作目录。`uploads/`、`outputs/` 和其他
  子目录只有产品约定，没有独立权限、发布或存储协议；Agent 可以覆盖其中任意已授权普通文件。
- 顶层 Conversation 默认拥有独立 Workdir；子 Conversation 继承根 Conversation 的 Workdir 和稳定
  `runtime_scope_id`。同一 execution tree 的父子 Agent 共用一个 runtime 与 POSIX 工作目录；不同根
  Conversation 的运行环境隔离，未来可只共享同一个 Workdir。
- Viewer、搜索、预览、下载、上传、删除和 artifact 通过独立受信任 file bridge 读取实时 Workdir，
  不读取 revision/manifest。AgentPanel 只展示当前 Project；User Data 与当前用户授权 Skills 可由 Agent
  和 artifact 使用，但不进入 Project 树。
- 共享/内置 Skills 文件按 uid 投影授权全集并只读挂载；个人 Skill 保留在 UserWorkspace；Agent 选择只决定 Prompt 和工具激活。授权 mutation 与投影
  refresh 使用同一 uid PostgreSQL advisory lock，HTTP artifact 每次重新检查当前授权。
- 根 Run 终态事务原子取消活跃后代并设置 `runtime_cleanup_pending`。新 Run、retry claim 和 SSE `end`
  不能越过该 fence；cleanup 失败由 durable reconciler 重试。清理 runtime/进程不删除 Workdir 文件。
- 启动升级在全局 advisory lock 与维护 fence 下采集旧 thread uploads/outputs、正式附件对象和 current
  output revision，写入隔离 staging，核对全局 fingerprint 后一次 activation。持久附件和历史
  `present_artifacts` 路径在 activation 事务内改写为 Project 路径。
- activation 后，升级服务先删除旧正式附件和 output revision 的 MinIO 前缀，再事务性移除附件对象
  定位字段、`thread_output_revisions` 表和 Conversation current pointer。对象清理失败时 readiness
  失败且旧 schema 保留，重试不会丢失清理 Owner。新安装从不创建这些旧 schema。
- shipping composition 不包含 output revision repository/service、scope hydrate、snapshot/publish/merge、
  父子文件 projection 或正式附件 MinIO cache。MinIO 仍用于知识库和未确认的临时附件解析，不被伪装成
  Project POSIX 文件系统。

## 替代方案

- 保留 output revision 作为实时读取或双写来源：拒绝。它会恢复第二事实源，并重新引入发布前 404、
  父子同步和冲突合并。
- 每个 Sandbox 使用本地副本并高频同步 MinIO：拒绝。同步间隔不能提供同一 POSIX 文件系统的
  rename、partial write 和并发可见性。
- 用 s3fs/ossfs 把对象存储挂成 POSIX：拒绝。对象存储不拥有 shell 所需的完整 POSIX 语义，还会把
  对象凭据扩大到不可信运行时。
- 多个顶层 Conversation 共享完整 runtime：拒绝。产品只要求可共享文件；进程、`/tmp`、依赖和环境
  仍按根 Conversation 隔离。

## 后果

- Agent、父子 Agent、Viewer 和 artifact 观察同一 Workdir；文件写入不等待 Run 终态。
- 并行写同一路径遵循底层 POSIX 语义，系统不做应用层合并或冲突修复。
- completed、interrupted、failed、cancelled 都保留已经写入的文件；终态只负责 execution runtime 清理。
- 直接从旧版本升级仍保留一段窄的启动期 raw-schema/object importer；它不是 shipping 文件 API，并在
  activation 后自删除旧持久表面。
- Kubernetes 跨节点实时共享仍要求 RWX CSI/PVC；目标集群 smoke 与最终共享 `saves` 删除由后续决定
  负责，不在本决定中宣称完成。

## 验证

- 4R-B：backend unit、真实 PostgreSQL/MinIO/POSIX materialization、Docker 双 Sandbox、真实 HTTP
  Viewer/artifact/Skill 撤权链路、execution-tree lifecycle 与确定性 Agent E2E 均通过；Web lint、unit、
  build 和 docs build 通过。真实 Kubernetes RWX smoke 为 `Not run`。
- 4R-C：真实 PostgreSQL/MinIO 测试覆盖损坏 current pointer fail-closed、activation 中断重建、清理失败
  保留旧 schema、恢复后对象/schema 删除、损坏 outputs descriptor 下载前拒绝、orphan 正式对象清理、
  tmp/知识对象 sentinel 保留、已经 active 的附件对象/metadata 清理以及无旧 schema 幂等启动。
- 静态负向 gate 要求 production 除启动迁移 Owner 外不存在 `ThreadOutputRevision`、current pointer、
  output revision repository/service、`scoped_file_store` 或正式附件 hydrate consumer。
- 4R-C 当前验证结果：backend unit `1384 passed, 26 skipped`；真实 PostgreSQL/MinIO/HTTP 选择集
  `23 passed`；确定性 Agent E2E `3 passed`；工程契约 `48 passed`，静态检查、Python lint/format、
  docs build 与链接检查通过。真实 Kubernetes RWX smoke 仍为 `Not run`，由后续 Stage 6 负责。

旧能力不存在：shipping runtime 不再创建、hydrate、合并或发布 output revision，不再为父子 Agent 搬运
文件，不再要求 artifact 位于 outputs，也不再从正式附件 MinIO 对象恢复 Sandbox cache；新 schema 不再
创建旧表和 pointer。

重新引入条件：只有新的产品需求明确放弃实时同一 POSIX 文件事实，并为第二事实源、发布时序、父子同步、
冲突、恢复、授权和真实跨进程故障提供新的 Owner 与 E2E 证据时，才可重新引入 revision/hydrate/publish；
不能以备份、审计或对象 GC 的名义重新接入 shipping 读写链路。
