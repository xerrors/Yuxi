# 收敛 Workspace 路径与文件访问 Owner

状态：archived
类型：simplification
Owner：backend/package/yuxi/workspace/filesystem.py

## 问题

UserWorkspace、Conversation Workdir、Workspace/Viewer API 路径与 Agent runtime 虚拟路径曾由多个模块重复解释。Workspace、Viewer、Thread Files、Attachment 和 artifact 分别创建文件 backend、规范化 scope、遍历目录和翻译错误；Workspace 与 Viewer 各自装配预览，Mention 还复制文件事实到 Redis 并要求所有写入口维护失效。

这已经造成真实契约漂移：底层文件接口改变后，Viewer、Thread Files 与 Attachment 的旧调用仍被测试 fake 掩盖。同一个持久化文件事实存在多个 Owner，是主要认知负担和漂移来源。

本决策只以 0.7.1 为兼容和数据迁移基线。0.7.2.dev0 到当前开发快照之间的 Thread 文件浏览接口、内部类位置、fake 接口与 Mention 缓存不构成兼容承诺。

## 决策

顶层 `yuxi.workspace` 由 `paths.py` 拥有 uid、UserWorkspace 与持久化 managed Workdir 路径，由 `filesystem.py` 拥有宿主文件根和 fd-relative no-follow 原语，由 `workdir.py` 提供以一个持久化 Workdir 为根的文件视图，由 `preview.py` 提供 UserWorkspace 文件预览与 runtime 本地缓存。通用渲染原语与 Knowledge/MinIO Preview 的分工由[分离 Workspace 与 Knowledge Preview Owner](./2026-08-21-preview-owner-separation.md)收敛。普通 `yuxi.services` 不取得 UserWorkspace 宿主 `Path`；storage migration 的宿主路径操作只存在于从 0.7.1 升级的显式流程中。

文件系统只保留两个边界：Agent `Backend` 拥有 Sandbox 生命周期、`/home/gem/user-data/...`、`/home/gem/skills/...` 与运行时文件协议；`Workspace` 拥有持久化 UserWorkspace 和 Project Workdir。`Workspace` 不解析 runtime 路径或 Skill projection，runtime 路径只在 Backend 或桥接 Agent artifact 协议的 Service 中出现。

`workdir_service` 保留 Conversation 查询和用户授权，并通过 `Workdir.open_existing()` 一次完成 canonical identity、存在性/no-follow 校验和持久化 capability 构造。浏览 API 的 scope-relative `/foo/bar` 直接由 Workdir 解析，不再先转换成 runtime absolute path。跨边界只保留三种契约：数据库保存 Project `workdir_path`，Workspace/Viewer API 使用 scope-relative `/foo/bar`，Agent 与 artifact 使用 runtime absolute path。

Conversation 的 `workdir_path` 在 0.7.1 cutover 后由数据库 `NOT NULL` 约束拥有完整性；运行时不再修复空绑定。新 Conversation 先分配 canonical 路径并提交数据库，再物化目录；SubAgent 只验证父子 Conversation 绑定一致，不维护第二套空值兼容状态。

删除 0.7.2.dev0 开发期的 `GET /api/chat/thread/{thread_id}/files` 与 `/files/content` 及其 schema、service、前端 consumer 和测试。文件树与内容统一使用 Viewer API；artifact 下载与保存保留原授权语义，由 `artifact_service.py` 编排。

`workspace.preview` 从已经授权读取的 UserWorkspace 字节产生 `PreviewResult` 并管理本地 Office cache；Service 只负责 HTTP 映射。格式识别和 Office 转换原语属于 `utils.filepreview`，其渲染入口直接返回 `PreviewResult`，不在 Owner 之间往返转换字典。Knowledge 的 MinIO Preview 属于 `knowledge.preview`。Workspace、Viewer 与 Mention 复用同一个 fd-relative 有界实时扫描，限制目录数、深度、每目录实际迭代数、总 entry 数和返回数，并拒绝 symlink 与特殊文件。Mention 的 Redis 文件索引、序列化、TTL 和失效链全部删除。

保留 `dir_fd`、`O_NOFOLLOW`、普通文件/目录检查、原子写、no-clobber、大小限制与失败清理。Owner 收敛不以文件移动或代码行数代替 consumer 与边界审计。

## 替代方案

- 只修复漂移调用：不能消除重复 Owner，拒绝。
- 整体把 Viewer、Service 或所有文件模块搬进 `workspace`：会把授权和 HTTP 用例混入持久化边界，拒绝。
- 保留 Thread API 并代理 Viewer：继续维持第二套公开协议；0.7.1 不要求该接口，拒绝。
- 保留 Redis Mention 索引：当前没有 workload 或延迟证据证明缓存必要，却复制文件事实并扩散失效责任，拒绝。
- 新增通用 `RootedFilesystem` 或可插拔 backend：当前只有 Backend runtime 与 Workspace persistence 两个真实边界，新增第三层抽象没有 consumer，拒绝。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Workspace、Viewer、Attachment 与 artifact 使用同一真实持久化文件契约 | fake 继续提供旧接口并掩盖真实漂移 | `yuxi.workspace.filesystem` 与用例 Service | 1453 host unit；11 个 Viewer/Workdir/v0.7.1 integration | fake 删除旧 Backend 方法后 Attachment 仍通过 | Passed |
| Workdir 只解析一次，浏览 API 的 `/` 始终是当前 scope 根 | Service 把 runtime absolute path 当浏览路径 | `yuxi.workspace.workdir` | Workdir unit；Viewer HTTP integration | 跨 Workdir、`..`、反斜杠、runtime/host path 均失败 | Passed |
| 0.7.1 数据只接受 canonical `projects/<uuid>`，迁移不跟随父目录 symlink | 非 canonical 数据或父目录 symlink 逃逸持久化根 | `yuxi.workspace.paths` 与 `v071_workdirs` migration | `test_v071_workdirs.py` 12 passed | 预置 symlinked `projects` 父目录时迁移失败且外部目标不存在 | Passed |
| Workspace API 保留 0.7.1 `virtual_path` wire contract | 重构删除既有 wire 字段 | `workspace_service` | file、directory、root entry unit | 运行时 prefix 改变时字段仍由当前 mapper 派生 | Passed |
| Thread 文件浏览旧能力不存在 | 代理或遗留 consumer 维持第二套协议 | Router、Web API 与 Viewer consumer | 全局 consumer 搜索；旧 URL HTTP 测试 | 恢复 route、service 或前端调用会触发 gate | Passed |
| artifact 下载、保存与并发 no-clobber 保持 | 同名保存覆盖或读取错误源 | `artifact_service.py` 与 `Workspace` | artifact unit；5 个 integration test bodies 已回读真实内容，suite teardown environment-blocked | 并发同 basename 产生两个路径并回读两份内容 | Not run |
| Workspace 与 Viewer 使用同一预览入口 | 两条链路重新产生格式或限制漂移 | `yuxi.workspace.preview` 与 `yuxi.utils.filepreview` | preview unit；Workspace/Viewer HTTP | 私有 Office renderer 和重复临时复制不存在 | Passed |
| Mention 不依赖 Redis 文件索引且立即观察最终文件状态 | 缓存陈旧或写入口漏失效 | `Workspace` 扫描与 `mention_search_service` | 新增/删除实时可见 unit | 无失效调用时新增立即可见、删除立即消失 | Passed |
| 实时扫描限制实际目录迭代 | 宽目录先全量枚举或 stat 再切片 | `yuxi.workspace.filesystem` | counting `scandir` unit | 深度、宽度、总 entry 和 symlink 用例 | Passed |
| 普通 Service 不获得 UserWorkspace host `Path` | 上层绕过 owning filesystem 再次打开宿主路径 | `yuxi.workspace` 与工程 gate | `verify_engineering_contracts.py` | Service 导入 host-path provider 或读取宿主根环境变量时 gate 失败 | Passed |
| 实现形成单一 Owner | 新包复制旧 Owner，形成第二份可编辑事实 | 完整 production diff | consumer 与 Owner 全局搜索 | 恢复旧 Thread、Mention cache、preview 或 path owner 时拒绝 | Inspected |

旧能力不存在：Thread `/files` 与 `/files/content` 路由及 schema、旧前端 API、`threadFilesMap`、Mention Redis 文件索引与失效函数、Service 私有的重复 preview renderer、`yuxi.agents.backends.sandbox.paths` 中的 UserWorkspace/Workdir Owner 均不存在。全局搜索只允许 0.7.1 migration、历史 decision 或明确的负向测试提及已删除名称。

重新引入条件：真实 workload 证明有界实时扫描不能满足已定义的延迟或资源目标时，可以另立 feature decision，引入由文件 Owner 管理且有一致性协议的索引；外部稳定客户端确实需要新的非 Viewer 文件协议时，可以另立 API decision。不得恢复 0.7.2.dev0 的旧 fake、alias 或双协议作为默认兼容方案。

## 后果

开发期 Thread 文件浏览 URL 现在返回 404。本项目只承诺从 0.7.1 升级，因此接受该结果并在 changelog 记录，不保留运行时代码。

实时扫描在极大 Workspace 上仍可能比索引慢；扫描预算保证实际枚举和 stat 数有界，预算耗尽后可能漏掉较晚条目。若真实观测表明不可接受，再以明确的一致性 Owner 引入索引。

Workspace、Agent context、Personal Skill、migration 和测试 imports 只引用当前 Owner，不用旧模块 re-export 维持两套位置；v0.7.1 migration 在经过 no-follow 验证的 `projects` dir fd 下 staging 和 rename，拒绝父目录 symlink。

验证结果：container unit 全量 1417 passed、39 skipped；此前 host unit 全量 1453 passed；工程 verifier、其 61 个 unit、前端 lint/52 unit/build 与 docs build 通过。Viewer/Workdir/v0.7.1 integration 的最新执行在 setup 被既存 `projects/legacy-*` 数据阻断，结果为 `Not run`。artifact integration 的测试本体结果不能替代失败的 suite 命令，因此不记为 Passed；Agent E2E 在清理前置处阻断，结果为 `Not run`。
