# Project 持久化与新建对话项目选择

状态：archived
类型：feature
Owner：backend/package/yuxi/services/project_service.py

## 问题

当前 Conversation 直接保存 `workdir_path`，Web 新建对话总是自动创建匿名 `projects/<uuid>`。该模型可以让多个 Conversation 共享路径，但无法表达可独立创建和命名的 Project、可选择列表、从历史对话复用项目，以及 Project 到用户所选 Workspace 目录的稳定归属。继续把名称和状态放入 Conversation metadata 会让同一 Workdir 的多个 Conversation 形成可漂移副本；让首条 Run 携带 Project 又会与附件、Viewer、Resume 和 SubAgent 建立第二套绑定协议。

## 决策

新增轻量 `Project` 业务资源。Project 持久化 `id`、`uid`、`name`、`selection_status`、`workdir_path`、`directory_mode` 和幂等创建事实；一期一个 Project 必须且只能对应一个 Workdir，但多个 Project 可以共享同一个 Workdir。`implicit` Project 由默认新对话自动创建且不进入选择器，`selectable` Project 由用户独立创建或从历史 Conversation 添加后进入选择器。

`managed` Project 由服务端分配并在数据库提交后物化。新路径使用上海时间与 Project ID 前 8 位组成 `projects/YYYY-MM-DD_HH-MM-SS_<project-id-prefix>`；同名文件或目录已经存在时，服务端依次追加 `-1`、`-2`，既有 `projects/<uuid>` 保持有效。`linked` Project 绑定当前 uid UserWorkspace 下用户选择的已有目录，不复制、移动、创建或接管目录内容。仅 Workspace 根、文件、symlink、路径穿越、宿主机/容器路径和其他用户目录被拒绝；`agents/`、`projects/` 及已被其他 Project 绑定的目录都允许选择。Project ID 是业务身份，Workdir 路径不是唯一键。

所有 Conversation 只绑定不可变 `project_id`，并通过非空列与 `(project_id, uid)` 复合外键拒绝缺失或跨用户绑定；Conversation 不保存 `workdir_path`。v0.7.1 发布 schema 尚无该路径列，因此一次性迁移直接为每个顶层 Conversation 创建 implicit managed Project，SubAgent Conversation 继承父 Conversation 的 Project，不保留当前开发分支曾引入的 path-only 中间态。统一 resolver 始终从 Project 读取 Workdir。AgentRun 不保存 `project_id`；Resume 使用原 Conversation，SubAgent Conversation 继承父 Conversation 的绑定。

新建对话草稿态提供“自动创建新项目”、选择已有 Project、新建 Project和添加历史项目。默认项在首次发送时创建 implicit managed Project；用户显式打开“新建 Project”时必须从 Workspace 选择一个目录并创建 selectable linked Project，不再暴露 managed/linked 模式选项。除 Workspace 根目录外的任意已有目录都可选择，也可在统一 Workspace 选择器中即时新建目录。“添加历史项目”只把所选 Conversation 的统一解析 Workdir 预填到同一个新建表单，不创建、不修改 Conversation，也不使用对话标题作为 Project 名称；用户命名并确认后仍调用普通 Project 创建接口。项目选择只在 Conversation 创建前出现，已有 Conversation 不可改绑。Project 的重命名、软删除和侧边栏分组由 [Project 对话分组与生命周期管理](./2026-08-30-project-conversation-sidebar-management.md) 继续拥有；GC、多 Workdir、ACL 和跨用户共享不属于当前范围。

统一 Workspace tree 从 Project repository 读取当前用户 selectable Project 的 Workdir。`/projects` 子树只返回这些目录、其祖先和其内部内容；implicit Project 与未归属任何 selectable Project 的匿名目录不返回。若 `projects` 根本身被选为 Project，则其完整子树可见。该规则同样作用于递归 tree，不在前端按 UUID 外观猜测。

## 替代方案

- 继续只保存 `Conversation.workdir_path`，从 Conversation 聚合项目：拒绝。名称、可选择状态和独立空 Project 没有单一 Owner，SubAgent 和软删除会污染聚合。
- Project 只保存名称，路径继续留在 Conversation：拒绝。形成两个路径事实源，无法保证同 Project Conversation 共享同一目录。
- Project ID 直接决定 `projects/<id>`：拒绝。用户明确需要绑定 Workspace 下任意合法已有目录，Project 必须显式拥有 `workdir_path`。
- Run 请求携带 `project_id`：拒绝。Thread 创建前附件、Viewer 和状态读取无法闭合，重试和 Resume 形成第二绑定协议。
- 一期引入 ProjectWorkdir 列表支持多目录：拒绝。当前 consumer 只有单 Workdir，提前引入集合、主目录和挂载语义会扩大维护面。
- 恢复独立 Project PVC、挂载和物化状态机：拒绝。UserWorkspace 继续拥有 POSIX 字节和 uid 隔离，Project 只是业务归属与 cwd。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Project 独立存在且一期各自绑定一个 Workdir | 空 Project 无法读取，或 Project 同时形成多份路径事实 | Project model/repository/service | 真实 HTTP/PostgreSQL/POSIX 探针回读 managed、linked、幂等和互斥绑定 | Project 缺少路径；两个 Project 共享路径仍保持不同 ID | Passed |
| managed 与 linked 创建保持各自目录语义 | 提交前遗留目录，managed 名称冲突后复用已有条目，或 linked 目录被复制/改写/静默创建 | ProjectService 与 `yuxi.workspace` | backend unit；真实 HTTP/PostgreSQL/POSIX 回读 managed 路径与目录 | managed 同名文件或目录依次追加编号；非法时间名拒绝；旧 UUID 路径继续有效；linked 路径不存在、文件、symlink 与重复路径绑定 | Passed |
| 手动创建 Project 必须选择目录 | 显式创建仍可提交 managed 或空路径 | ProjectService 与 Project UI | Project service unit；前端禁用状态；Playwright 新建弹窗 | `managed`、空 workdir、空 path 与 mode 缺失均返回 422 | Passed |
| Workspace 目录选择接受根目录外任意合法目录 | 合法保留目录被 API、worker 或 provisioner 误拒绝，或路径逃逸边界 | Workspace filesystem boundary、ProjectService 与 sandbox provisioner | Workspace/Project unit、provisioner unit、真实目录候选与创建探针 | `/`、文件、symlink、空路径组件、`..`、绝对路径和 URL 拒绝；`agents`、`projects` 和重复路径接受 | Passed |
| projects tree 隐藏匿名目录 | implicit 或未归属目录出现在通用 tree，或前端按 UUID 猜测 | WorkspaceService 与 ProjectRepository | Workspace 可见性 unit；真实 tree API | selectable 目录、祖先和内部内容保留；选中 projects 根时完整可见；普通目录不额外查 Project | Passed |
| Conversation 创建后不可改绑且只持久化 Project | Conversation 重新保存路径，或 Run/附件/Viewer 使用请求路径 | ConversationService、Workdir resolver、worker/SubAgent | backend unit；真实 PostgreSQL 回读非空 Project 外键 | Repository 传入 workdir_path 必须失败；父子 Project 不一致拒绝；linked 队列误物化 | Passed |
| 测试清理以 Project 为 Workdir Owner | 并发 Project 绑定期间误删目录，或删除最后一个 Conversation 时误删 selectable Project | 用户级 Project-Workdir 事务锁与测试清理器 | 真实 PostgreSQL 清理、锁竞争与保留测试 | 重叠 Project 拒绝；等待清理子目录的 linked 创建在删目录后重新校验并失败；selectable Project 保留 | Passed |
| 历史对话仅提供解析后的目录快捷选择 | 候选点击产生 Project，或新旧 Conversation 路径解析不一致 | ProjectRepository、Project UI | Project service unit、前端 unit 与真实页面 | 同目录候选去重；内部/非 active Conversation 不返回；名称保持为空 | Passed |
| 前端只在新对话显示紧凑选择器，发送与附件共享一次线程创建 | 已有对话可改绑，响应丢失重试重复创建，或旧异步响应覆盖新状态 | AgentChatComponent 与 Project UI | 前端 lint、85 unit、build；Playwright 默认/展开/375px/暗色页面，无 console error | single-flight reset、稳定 request ID、延迟响应上下文 guard、创建期间禁止切换 | Passed |
| 最近视图与 runtime 生命周期不因共享 Project 合并 | 最近视图错误合并 Conversation，或共享 Project 的顶层 Run 共享 Sandbox | Conversation 列表与 AgentRun runtime scope | 完整 backend/frontend unit 与真实页面检查 | 项目视图只组织入口；Run 不冗余 project_id | Passed |

## 后果

- Conversation 只有一种 Project 绑定形态；所有 Workdir consumer 必须使用统一 resolver，不能从 Conversation 或请求读取路径。
- linked Project 只建立业务绑定，不拥有目录字节；目录被外部删除、改名或替换时明确失败，不静默创建或降级。
- managed Project 在 PostgreSQL 提交后物化目录，稳定幂等 ID 允许响应未知或物化失败后恢复同一资源。
- 新 managed Workdir 名称提供秒级创建时间和 Project ID 前缀；名称冲突时选择首个未占用编号，目录名不承担 Project 身份或授权职责。
- Project 是工作目录和用户组织资源，不改变同 uid Sandbox 可读取整个 UserWorkspace 的现有边界。
- Project Workdir 路径不再唯一；多个业务 Project 可以显式共享同一目录，运行和文件变化也因此共享。
- `/projects` 下尚未归属 selectable Project 的匿名目录由后端统一 tree 隐藏；目录存在与可见性不能由前端目录名推断。
- 线程列表联查 Project，避免侧边栏按线程逐条解析 Workdir。
- Project 改绑、GC、多 Workdir、ACL 和跨用户共享继续留给独立需求。

## 风险

linked 目录可能在 Project 外被删除、改名或替换；运行时必须报告“项目目录不可用”，不能静默创建同名目录或降级到 managed Workdir。数据库事务与 POSIX 创建不是原子的，managed Project 需要幂等请求和提交后物化恢复。v0.7.1 迁移必须在同一数据库事务中创建 Project 并绑定全部 Conversation，避免产生无 Project 的可运行行。当前 Sandbox 可读取同 uid 整个 UserWorkspace，Project 不构成同一用户内部的强文件隔离边界。

完整 Project integration pytest 因共享测试环境中三条既有 queued request 在 fixture setup 阶段阻断，未进入本功能测试体；独立真实 HTTP/PostgreSQL/POSIX 探针提供了当前直接证据。Ruff 不存在于当前 API 镜像，`uvx` 临时下载超时；Python 相关完整 unit、工程信任 gate、前端 lint/build 和 `git diff --check` 已通过。
