# Workdir 归属 UserWorkspace 并取消独立 Project 存储域

状态：archived
类型：simplification
Owner：backend/package/yuxi/services/workdir_service.py

本决定替代此前关于独立 Project Workdir 和 Project 存储挂载的决定：

- [实时 Project Workdir 与独立 Sandbox Runtime](./2026-08-18-live-project-workdir-and-runtime.md)
- [显式存储域与 Kubernetes PVC 收敛](./2026-08-19-explicit-storage-domains-and-kubernetes-pvc.md)

个人 Skill 继续遵循[Skill source convergence](../09-skill-cli/2026-08-18-skill-source-convergence.md)：个人 Skill 属于 UserWorkspace，共享 Skill 仍由只读 projection 提供。

发布版升级边界由 [v0.7.1 存储迁移](./2026-08-20-v071-storage-migration-boundary.md) 独立拥有。

## 问题

把 Workdir 建模为独立的 `ProjectWorkdir` 存储域，会为同一用户文件能力引入单独的数据库表、storage key、物化状态、宿主机目录、容器挂载和 Kubernetes PVC，并与 UserWorkspace 形成两套根目录：

- Conversation 通过 `workdir_id` 间接定位独立 Project 目录；
- Agent、Viewer 和 Sandbox 还要在 Project、User Data、Skills 之间做路径和挂载转换；
- 启动期 materialization 需要维护全局状态、每个 Workdir 状态、epoch 和 inventory fingerprint；
- Compose、provisioner 和 Kubernetes 需要维护独立的 Project host path、mount 和 PVC。

新的 Thread 创建需求允许指定 UserWorkspace 内的目录；未指定时，系统在 UserWorkspace 的 `projects` 下自动创建目录。独立 Project 根目录与该需求的真实权限边界不一致，也使“选择一个 workspace 目录”错误地变成了“切换一个存储域和挂载域”。

## 决策

### Workdir 是 UserWorkspace 下的相对路径

Workdir 保留为对话的逻辑工作目录，但不作为独立存储域。后续引入的轻量业务 Project 保存一个相对于当前用户 `workspace` 的路径，Conversation 只保存 `project_id`：

```text
workdir_path = projects/<opaque-workdir-id>
```

宿主机和 Sandbox 的映射由 UserWorkspace Owner 统一完成：

```text
宿主机：user-data/shared/<uid>/workspace/<workdir_path>
容器内：/home/gem/user-data/<workdir_path>
```

`/home/gem/user-data` 直接表示当前用户的 UserWorkspace 根，不再在容器内重复一层 `workspace`。因此默认 Workdir 的容器路径是 `/home/gem/user-data/projects/<opaque-workdir-id>`，个人 Skill 的容器路径是 `/home/gem/user-data/agents/skills/<slug>`。

Workdir 在该设计中是 Conversation 的 cwd 和 Thread 文件 API 的默认视图，不是同一用户不同 Thread 之间的文件授权边界。Sandbox 可以访问当前 uid 的整个 UserWorkspace，uid 挂载负责跨用户隔离；Thread 文件 API 仍只暴露绑定的 `workdir_path`。因此 Project A 读取 Project B 的文件属于设计范围，可用于引用同一用户的其他项目资料。如果产品要求同一用户的不同 Thread 文件互不可见，则不能使用整个 UserWorkspace 的单挂载方案，需要重新引入更窄的挂载或等价的强制访问控制。

系统 Prompt 向 Agent 提供当前 `workdir_path`，并明确以下默认写入约束：

```text
整个 UserWorkspace 对当前 Sandbox 可见。可以读取其他目录作为参考；未经用户明确要求，
不得在当前 Workdir 之外创建、修改、移动或删除文件。
```

该约束定义默认模型行为，不构成安全或授权边界。用户明确要求跨 Project 修改、安装个人 Skill、更新用户上下文或执行其他 UserWorkspace 操作时，可以写入相应目录；后端仍只强制 uid 隔离、路径不越界和具体工具拥有的权限。

默认创建时由服务端生成 implicit Project 和 opaque Workdir UUID，避免依赖客户端 thread ID 的格式、可变性或安全性。用户也可选择绑定现有目录的 Project；允许同一用户的多个 Project 共享目录，跨用户不允许共享。

需要在同一事务中同时创建 Conversation、Message、Request 和 Run 的 Agent Call、Channel 与 Evaluation 入口，事务内创建 Project 并让 Conversation 持久化 `project_id`。调用方提交 PostgreSQL 后由可写 API 进程实体化 Project 的目录，确认成功后才向 ARQ 发布 Run；实体化失败时保留已提交的 pending Run 供恢复扫描重试，不允许先发布给只读或无目录的 worker。

父 Conversation 与子 Conversation 继承同一个 `project_id`。

### Execution runtime scope 与 Workdir 分离

`runtime_scope_id` 是持久化在 `AgentRun` 上的执行树分组键，当前取根 Conversation 的 thread ID：

- 根 Conversation 的 Run 使用自己的 `conversation_thread_id`；
- SubAgent Run 拥有自己的 `conversation_thread_id`，但继承创建者 Run 的 `runtime_scope_id`；
- 同一 `runtime_scope_id` 的父子 Run 复用一个 Sandbox runtime，并共享进程、`/tmp`、运行时依赖和环境；
- 根执行树终态时，worker 用该值确认没有仍活跃的子 Run；仍在执行的子 Run 先保留 `cancel_requested` 与 owner/lease，确认停止后再通过同一清理栅栏销毁 Sandbox；
- 两个顶层 Conversation 即使显式绑定同一个 `workdir_path`，也具有不同的 `runtime_scope_id`，因此只共享文件，不共享运行环境或生命周期。

`runtime_scope_id` 不定义文件路径、文件授权、LangGraph checkpoint identity 或 UserWorkspace 边界。它的语义 Owner 属于 AgentRun 创建、SubAgent 执行树和 worker runtime cleanup，不再由 Workdir binding/service 推导或拥有。

读取 LangGraph checkpoint 状态时仍需重建当前 Agent context：checkpoint thread 使用当前 Conversation 的 thread ID，Sandbox context 使用最新 AgentRun 持久化的 `runtime_scope_id`，并注入由 Project 解析的 `workdir_path`。缺失 Project 或 Workdir 时读取失败，不构造一个没有当前工作目录的降级上下文。

取消 `workdir-files-<id>` 文件桥接 Sandbox 后，正常执行路径直接用 `runtime_scope_id` 标识 execution Sandbox，不再保留 `sandbox_instance_id`。只有未来出现同一执行树必须同时拥有多个独立 Sandbox 实例的真实 consumer 时，才重新引入独立 instance identity。

本阶段不创建独立存储域或 `ProjectWorkdir` 物化资源。后续产品引入命名、独立创建和对话复用后，轻量业务 Project 已由 [Project 持久化与新建对话项目选择](../06-project-lifecycle/2026-08-22-project-persistence-and-selection.md) 实现；UserWorkspace 仍拥有 POSIX 字节、uid 隔离和真实路径边界。

### Thread 创建接口

Thread 创建接口只接受可选 `project_id`：不传时创建 implicit Project，传入时绑定当前用户已有的 selectable Project。接口不接受 `workdir_path`；路径只在 Project 创建边界校验和持久化，不接受宿主机路径、容器绝对路径、对象 URL 或其他用户的路径。

路径解析和授权由后端 repository/service 与宿主 `Workspace` 共同闭合。它接收 `uid + workspace-relative path`，不再通过伪造的 Project root 或 `workdir-files-<id>` scope 绕过真实路径 Owner。所有路径组件都在 owning filesystem boundary 内以 no-follow 方式校验，禁止 `..`、symlink 穿越和跨用户访问。

### 挂载边界

Sandbox 的持久文件挂载收敛为两个逻辑域：

```text
当前用户 UserWorkspace    -> /home/gem/user-data  rw
共享 Skill projection     -> /home/gem/skills     ro
```

Sandbox 的 cwd 设置为 `/home/gem/user-data/<workdir_path>`。因此不再需要容器内的 `/home/gem/user-data/workspace` 中间层，也不再需要独立 `/home/gem/projects` 根目录、Project bind mount、`DOCKER_PROJECTS_HOST_PATH` 或 `PROJECT_DATA_PVC`。每个 Thread 只改变 cwd，不改变挂载配置。

Sandbox create 是 provisioner 拥有的同步长操作，可能包含镜像拉取与健康等待。调用方保留连接、写入和连接池的短超时，但不以更短的 read timeout 抢先终止服务端创建；普通 health、discover、touch 和 delete 请求仍使用短超时。

API/worker 仍可保留服务自身访问 UserWorkspace 和 Skill source/projection 所需的显式挂载；这些挂载属于服务的 UserWorkspace/Skill 访问，不构成第三个 Project 存储域。`uploads/`、`outputs/` 是当前 Workdir 下的目录约定，路径为 `/home/gem/user-data/<workdir_path>/uploads` 和 `/home/gem/user-data/<workdir_path>/outputs`，不再由 `/home/gem/user-data/uploads`、`/home/gem/user-data/outputs` 表示用户级全局运行时目录。两者都按首次使用创建：附件确认创建所需的 `uploads` 父目录，标准文件写入创建所需的 `outputs` 父目录；Sandbox provisioner 只验证挂载与 cwd，不预建业务目录，也不递归修改整个 UserWorkspace 权限。`saved_artifacts` 等用户主动保存的文件可以继续作为 UserWorkspace 内的普通目录存在，但不形成额外挂载或存储协议。

API、worker 与 Sandbox 数据面统一使用数值身份 `1000:1000` 访问同一 UserWorkspace。新目录和文件遵循 owner-only 权限；旧数据由 root storage migrator 在运行时启动前一次性收敛所有权与权限，provisioner 不再承担运行时权限修复。身份与迁移契约见[统一 Workspace 运行身份并删除权限补丁](./2026-08-20-unified-workspace-runtime-identity.md)。

Viewer 批量上传在写入前校验完整文件名集合，并拒绝同批重名或目标目录中的既有同名条目。实时 Workdir backend 还必须在最终目录 fd 上以原子 no-clobber 写入闭合并发创建和列表过滤 symlink 的竞态；冲突返回 409，不能用原子 rename 的覆盖语义替换用户或 Agent 已有文件。

### 附件只有一条确认链路

附件入口收敛为 `MinIO tmp -> 可选解析 -> 确认写入 Workdir`。旧的“直接上传到 Thread 并由后端静默解析”接口和前端死方法删除。确认后，原件写入当前 Workdir 的 `uploads/`，可选 Markdown 写入 `uploads/attachments/`；Workdir 是正式字节 Owner，MinIO 只拥有未确认临时对象。

Conversation 的附件 JSON 只保存文件 ID、文件名、MIME、大小、状态、上传时间、当前路径、原件路径和后续绑定的 `request_id`。完整 Markdown、hash、兼容 `file_path`、截断信息以及可由 `thread_id + path` 派生的 artifact URL 都不再持久化。旧记录中的额外字段无需运行时迁移；新写入不再产生它们，序列化继续只输出当前契约字段。

每个新 Agent Run 可以获知该线程全部历史附件，但附件名称和路径只追加到本轮模型可见的 `HumanMessage`。数据库 Message、流式 `init` 事件和历史接口仍保存原始用户输入，前端无需展示或过滤模型专用上下文；系统提示词和 LangGraph state 不再承载附件列表。中断恢复沿用 checkpoint 中该轮已有的用户消息，不重复注入。

确认接口不限制附件数量。服务逐项下载、校验并写入 Workdir，不把整个批次同时保存在内存；任一项失败时删除本批已写文件，数据库只在整批完成后提交。确认成功后尽力删除对应 tmp 分组。未确认对象采用最小过期策略：用户下一次上传时，顺手删除该用户最后修改时间超过 24 小时的 tmp 分组，不新增 scheduler、数据库状态或生命周期服务。

### 旧数据迁移

`storage-migrator` 收敛为一次性旧布局迁移 Owner，而不是正常启动链路中的 Workdir materialization Owner：

- 升级基线是 v0.7.1 的发布状态；每个历史顶层 Conversation 获得 implicit Project 和确定性派生的 canonical `projects/<uuid>`，子线程继承 owner Project，即使没有旧文件也创建目标 Workdir；
- v0.7.1 thread `uploads/outputs` 导入对应 Workdir，持久化的 `/home/gem/user-data/workspace/...` 改写为 `/home/gem/user-data/...`，旧顶层 `uploads/outputs` 路径改写到当前 Workdir；
- v0.7.1 `base.toml` 与共享 Skill 在同一停机迁移中切换到当前 PostgreSQL 和 Skill source Owner；
- 迁移在 runtime quiescence 和目标校验后提交数据库，再清理旧源；当前 Project schema 仍有旧 thread 源时按同一目标重试；
- 未发布的 `ProjectWorkdir`、`FileStorageMaterialization` 与 `workdir_id` 中间 schema 明确拒绝，不执行兼容导入；
- 新安装直接使用 UserWorkspace 布局，不创建独立 Project schema、Project root 或 Project PVC；
- 正常 API/worker 启动不扫描旧宿主目录，也不执行破坏性迁移。

新安装直接使用新 schema；已有旧数据的兼容只属于迁移器，不反向恢复独立 Project 存储域。

## 替代方案

- **保留独立 `ProjectWorkdir` 表，只把它的 storage key 改到 UserWorkspace。** 拒绝。存储物化资源会维护第二事实源；当前轻量 Project 只表达产品归属并直接拥有 UserWorkspace 相对路径，不拥有独立存储域或挂载生命周期。
- **继续使用独立 `/home/gem/projects` 挂载，再允许 API 指向 workspace 目录。** 拒绝。一个 Thread 的文件边界会依赖两套根目录，授权、Viewer 和 Sandbox 路径会再次分叉。
- **把 UserWorkspace 挂载到 `/home/gem/user-data/workspace`。** 拒绝。该路径会在已经由 UserWorkspace Owner 定界的目录外再保留一层兼容命名，使个人 Skill、Workspace API 和 Workdir 都需要重复拼接 `workspace`；直接把同一个宿主目录挂到 `/home/gem/user-data` 即可保持单根语义。
- **每个 Thread 按用户指定目录动态创建宿主机挂载。** 拒绝。挂载配置不应成为用户输入的副作用；挂载整个当前用户 workspace 后，在容器内选择经过校验的相对路径即可。
- **把 UserWorkspace 改为对象存储或 FUSE POSIX 层。** 拒绝。实时 Workdir 需要普通 POSIX 的 rename、partial write 和并发可见性；对象存储仍不能成为不可信运行时的 POSIX 事实源。
- **直接把宿主机路径传给 API、worker 或 Agent。** 拒绝。宿主机路径、容器虚拟路径和对象 URL 继续分层，授权在产生副作用的 executor/`Workspace` 处最终执行。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Thread 默认创建 Project 与唯一 `projects/<id>`，显式 Project 只绑定当前 UserWorkspace 内的合法目录 | DB 绑定与实际目录不一致 | Project service、Conversation repository、UserWorkspace path service | 真实 PostgreSQL + 文件系统 integration；检查 Project、Conversation 与最终目录 | Conversation 直接传路径；`../`、绝对路径、宿主机路径、其他用户路径、非目录路径 | 相关 unit Passed；真实 integration 本轮因共享 API fixture 超时 Not run |
| 自动创建 Conversation 的 Run 在提交后、ARQ 发布前实体化 Workdir | worker 先收到 Run，但其边界中不存在 UUID 目录并以 `invalid_runtime_scope` 失败 | Run submission、request queue finalize 与 Workdir path service | finalize 顺序 unit + 自动创建 Conversation 的真实 HTTP/PostgreSQL/POSIX integration + queue recovery integration | 删除实体化步骤后事件顺序缺少 `materialize`；自动创建后宿主目录不存在；恢复 pending Run 时跳过实体化；提交前创建或创建前 enqueue | 通过：unit 证明 `commit -> materialize -> enqueue`，真实 Agent Call 自动创建后回读 Conversation 与最终目录，queue integration 覆盖恢复派发 |
| Workdir 路径不会越过 UserWorkspace 或绑定到保留目录 | 路径穿越、symlink 穿越、把 `agents/` 作为 Workdir | `backend/package/yuxi/agents/backends/sandbox/paths.py` 与 file bridge | path resolver unit + 真实 HTTP/Sandbox 文件访问 | `..`、绝对路径、symlink 组件、`agents/`、跨 uid | 通过：no-follow/path unit、5 个真实 HTTP Viewer integration 与 5 个真实 Docker provisioner integration |
| Sandbox 把当前 UserWorkspace 直接挂载到 `/home/gem/user-data`，并只额外挂载只读共享 Skill projection | Project mount、嵌套 workspace mount 或广域 host root 泄漏 | `docker-compose.yml`、`docker/sandbox_provisioner/app.py` | Compose/provisioner contract、Docker mount inspection、`python3 scripts/verify_engineering_contracts.py` | `/app/projects`、`DOCKER_PROJECTS_HOST_PATH`、`PROJECT_DATA_PVC`、`/home/gem/user-data/workspace` mount、shared root、可写 Skill mount | 通过：Compose 校验、provisioner unit、真实 Docker mount/integration、工程信任检查 |
| `uploads/outputs` 只在首次使用时创建，provisioner 不拥有业务目录 | 空 Thread 启动 Sandbox 后出现目录，或每次启动递归 chmod 全部项目 | Sandbox backend、attachment service、provisioner | backend/provisioner unit + Docker integration | K8s init 或 Docker command 包含预建 `uploads/outputs`、`chmod -R /home/gem/user-data` | 通过：provisioner/backend 正负向 unit 与真实附件 runtime 重建 E2E |
| Viewer 批量上传不覆盖既有文件，也不接受同批重名 | 两个响应条目指向同一最终文件，或用户/Agent 产物被静默替换 | Viewer filesystem service 与实时 Workdir backend | Viewer unit + 真实 HTTP/POSIX integration | 既有文件名、同批重复 basename、目录快照后并发创建、被列表过滤的目标 symlink；冲突后回读原字节或链接 | 通过：预检与最终 no-clobber 负向 unit；真实 HTTP 验证既有文件和目标 symlink 均返回 409 且原对象不变 |
| 附件正式字节、最小索引与模型上下文分别由 Workdir、Conversation 和本轮用户消息拥有 | JSON 复制 Markdown，系统提示反复增长，旧直传入口绕过确认流程 | attachment/chat service、Conversation repository | attachment unit、chat message unit、真实 HTTP integration/E2E | 非路径附件进入上下文、持久化 Markdown/hash、跨用户 tmp 路径、批次中途失败残留正式文件 | 通过：附件/消息 unit、15 个真实 HTTP integration 与附件 runtime 重建 E2E；旧直传端点返回 405 |
| Project 间读取属于同一 UserWorkspace 的正常能力，Prompt 默认禁止未经用户要求的跨 Workdir 写入 | Agent 误把可见性当作默认写权限，或错误宣称 Project 间不可读 | `backend/package/yuxi/agents/buildin/chatbot/prompt.py` | Prompt unit 检查当前 Workdir、跨目录可读和默认写入约束 | Prompt 缺少当前路径；禁止读取其他 Project；允许默认跨 Project 写入 | 通过：Prompt 正向与负向 unit 纳入全量 unit |
| `runtime_scope_id` 只分组父子 Run 的 execution Sandbox 与清理生命周期，不参与 Workdir 身份 | 共享 Workdir 的顶层 Conversation 错误共享进程，或子 Run 尚未确认停止时提前清理 Sandbox | AgentRun repository、SubAgent run service、worker runtime cleanup | AgentRun PostgreSQL integration + execution-tree E2E | 同 Workdir 的两个根 Run 使用同一 scope；子 Run 使用 child thread 作为 scope；子 Run 尚未确认停止时销毁 Sandbox | unit、真实双 Sandbox provisioner integration 和顶层 runtime 重建 E2E 通过；完整父子 execution-tree E2E 未运行 |
| 状态读取与同步 Sandbox 创建保留当前 Workdir/runtime scope，并由 provisioner 完成长操作 | 状态读取缺少 Workdir；子 Agent 使用 child scope；冷启动被客户端短 read timeout 中断 | Chat service 状态读取、provisioner client/create endpoint | chat/provisioner unit + deterministic assembled-path E2E | 缺失 Workdir；子 Run 的父 scope；create 只放宽 read timeout | unit 通过；真实冷启动由 PR Runtime System Tests 验证 |
| API、worker、迁移器与 Sandbox 数据面以固定 `1000:1000` 访问 UserWorkspace | 任一数据面写入者仍以其他身份创建对象，导致 Sandbox 不能原子替换 | Dockerfile、Compose、storage migration、provisioner | 身份契约 unit、迁移 unit 与真实镜像跨进程文件探针 | 删除固定身份或迁移 marker 前失败时拒绝启动 | 通过；完整证据见统一运行身份决定 |
| 父子 Conversation 使用同一 Workdir，但 runtime 生命周期仍按既有 scope 规则隔离 | 子 Agent 路径错绑或 runtime cleanup 误删文件 | Conversation Workdir service、worker lifecycle、provisioner | execution-tree E2E，验证最终 POSIX 文件和 runtime cleanup | 子 Conversation 指向不同路径；根 Run 终态删除 Workdir 文件 | E2E 契约收集通过；实际父子链路未运行（本地 deterministic provider 不可连接） |
| v0.7.1 数据迁移只在停机条件满足时执行，并在提交后清理旧源 | 无文件 Conversation 漏迁、迁移期间仍写入、失败后丢源数据 | `storage_migration.py` 与一次性 legacy importer | v0.7.1 schema、文件系统 integration、quiescence proof 与重试 | 空 Conversation、缺少 quiescence、未发布中间 schema、目标冲突、提前删除旧源 | 真实 v0.7.1 schema/文件系统 integration 覆盖有文件、空 Conversation、附件字段收敛和重试；相关 unit 与完整 gate 见迁移基线决定 |
| 个人 Skill 通过 `/home/gem/user-data/agents/skills` 直接属于 UserWorkspace，共享 Skill projection 仍只读 | 个人 Skill 被共享迁移删除或 Sandbox 可写共享 Skill | Skill service、Skill projection、UserWorkspace | 现有 Skill unit/integration/E2E 与 Docker mount inspection | 个人 Skill 被 legacy scan 删除；shared projection 可写；仍生成 `/home/gem/user-data/workspace/agents/skills` | unit 与真实 Docker mount/integration 通过；完整 Agent E2E 未运行（本地 deterministic provider 不可连接） |

旧能力不存在：shipping runtime 与迁移器都不创建、读取、导入或删除独立 `ProjectWorkdir`、`FileStorageMaterialization`、Project host root、Project mount 或 Project PVC；迁移器只保留对未发布中间 schema 的拒绝检查，以及自身的 proof、marker 和 staging 状态。

重新引入条件：只有产品明确需要 Workdir 级 ACL、配额、重命名、生命周期、跨用户共享元数据，或部署/合规要求独立的物理存储域时，才可重新引入 Workdir resource 或 Project storage domain；届时必须同时提供新的语义 Owner、迁移契约、权限负向案例、并发/恢复证据和真实部署验证。

## 后果

- 显式路径复用会让多个 Thread 共享文件，文件并发和删除责任需要保持保守；本提案不自动删除被引用或无法确认归属的目录。
- 整个 UserWorkspace 对同一 uid 的 Sandbox 可见；Workdir 只选择 cwd 和 Thread 文件视图，不提供同一用户 Thread 间隔离。若该产品语义变化，本提案的单挂载前提失效。
- Prompt 的默认写入约束不能阻止有缺陷或被注入的 Agent 跨 Project 写文件；这是接受 Project 间可见与可操作所带来的行为风险，不应使用“已隔离”或“后端禁止写入”描述该边界。
- 不设 Workdir 表会暂时缺少 Workdir 级别的名称、ACL、配额和 GC 元数据；这些需求出现时需要重新建立资源模型，而不能把路径字符串继续扩展成隐式状态机。
- `agents/` 是 UserWorkspace 的一部分但不是普通 Workdir；若未来确实允许 Agent 将其作为工作目录，必须重新定义 Skill、context 和系统文件的写权限边界。
- 旧部署的迁移复杂度不会因为新布局消失，只会从“长期运行时双域”收敛为“有限的一次性导入”。迁移完成前必须保留旧源和可验证的恢复路径。
- 临时附件过期清理是上传触发的尽力而为机制；长期没有后续上传的用户，其未确认对象可能超过 24 小时继续存在。若对象规模证明需要强时效，再由存储生命周期规则接管，而不是先引入应用层调度状态机。
- 附件数量不设产品上限；逐项处理降低确认时的内存峰值，但总处理时长仍随附件数线性增长。
