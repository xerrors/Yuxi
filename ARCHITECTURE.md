# ARCHITECTURE.md

本文档是 Yuxi 的代码地图，只描述相对稳定的系统边界、目录职责、核心运行链路和架构不变量。它用于帮助贡献者判断“一个改动应该落在哪里”，不替代具体模块文档、测试规范或源码注释。

修改不熟悉的模块前，先阅读对应章节，再使用符号搜索定位具体类型、函数和路由。开发与运行拓扑始终以 `docker-compose.yml` 为准。

## 鸟瞰

Yuxi 是一个面向 RAG、知识图谱和多智能体工作流的知识库平台。用户通过 Vue 前端管理智能体、知识库、模型、工具、Skills、MCP 与协作会话；前端通过 `/api` 调用 FastAPI；后端服务层协调 PostgreSQL、Redis、MinIO、Milvus、Neo4j、LangGraph 和沙盒。

普通智能体消息先在 PostgreSQL Input 中保存原始内容，Receipt 保存幂等接收事实。调度器消费就绪输入时原子创建正式 Message、Turn 与首个 Run；steer 优先于 follow-up，运行中在安全边界接续当前 Turn。人工等待恢复沿用当前 Turn，产生下一段 Run。消费事务提交后，pending Run 通过 Redis/ARQ 交给独立 worker 执行。Redis Stream 保存短期增量，PostgreSQL 保存执行和业务终态，前端分别显示待处理队列和正式消息历史。

核心开发服务包括：

- `frontend`：Vue 3 / Vite 前端，挂载 `frontend/src` 并热更新。
- `api`：FastAPI API 服务，挂载 `backend/yuxi` 和测试目录；API 与 worker 的源码修改通过显式重启生效。
- `worker`：ARQ worker，执行已经派发的 AgentRun 与注册的 后台作业，并周期触发用户自建 Agent 定时任务；三者分别使用 PostgreSQL 中的运行租约、任务租约和调度锁闭合并发与恢复。
- `storage-init`：无网络的一次性进程，准备 API/worker/app-init 与 Milvus 挂载根的属主和模式，不递归修改已有文件；消费进程等待其成功退出。
- `app-init`：普通用户运行的部署任务，在 PostgreSQL advisory lock 内先建立或校验当前 Schema，再同步系统配置定义、内置供应商、Agent、MCP 与 Skill 来源文件。API 与 worker 等待全部成功。空库基线为 business 1、knowledge 2，直接包含个人 MCP 授权表、128 位用户归属字段及当前约束。当前版本跳过结构初始化，其他版本明确拒绝；历史部署的结构调整由部署侧显式完成；知识域 v1 路径目录通过离线审计/转换入口升级 v2，运行进程不自动迁移。共享同步失败保留已提交内容，修正后幂等重跑。
- Cube Linux/KVM 执行节点：每个用户拥有一个持久普通账号 VM，Workspace 与授权 Skills 共享宿主文件。
- `sandbox-runner`：选择 container 后端时由该独立进程持有 Docker socket，为每个 Run 懒创建临时容器并执行命令；API/worker 只调用认证 HTTP。挂载、镜像和资源限制由 runner 拥有，Run 归属与清理责任仍由 PostgreSQL 拥有。
- `postgres`：业务数据、知识库元数据、持久 Input 队列、Turn/Run 与 LangGraph checkpoint。
- `redis`：ARQ 投递、运行事件、取消信号以及跨进程配置和模型缓存。
- `minio`：附件草稿内容、知识库原始文件和其他对象数据；正式 Agent 附件归 Workdir，附件事实归 PostgreSQL。
- `milvus`、`etcd`：向量检索及其元数据协调。
- `graph`：Neo4j 知识图谱。
- `mineru-api`、`paddlex`：通过 `all` profile 可选启动的文档解析和 OCR 服务。

API 在 `/api/mcp` 提供只读知识库 MCP，与 HTTP API 共用进程和端口。`modules/knowledge/mcp` 拥有服务端协议、内部认证、连接定义、产品读取和知识引用转换，API 只装配组件与生命周期。外部客户端使用 API Key，内部 Agent 使用 Knowledge 自己提供的 SDK Auth，按可信运行用户与会话选择签发五分钟凭据；Knowledge 每次调用复核当前用户权限交集。通用 MCP 复用现有工具发现、目录缓存与执行策略，不解释 Knowledge 的范围、Token 或结果结构。`knowledge-base` Skill 以 `mcp_dependencies` 门控；Agent 只调用 Knowledge 的产品函数。选择器与引用读取直接通过 SDK 调用 MCP，引用保留文件身份和行范围。关闭 Knowledge 时不装配路由与 manager，内置身份保留，能力发现和执行明确不可用。

企业认证中心由部署方现有的 OIDC 服务提供；Yuxi 仅消费标准 OIDC/MCP 协议，不保存 Keycloak 部署模板或运行认证数据库。

## 后端代码地图

后端只有一个 `backend/yuxi` Python 包，项目配置与构建元数据由 `backend/pyproject.toml` 拥有。`api` 和 `workers` 是进程适配层；`modules` 按业务域组织用例与持久化；`infrastructure` 提供技术连接与解析引擎；`bootstrap` 负责启动资源装配；`migrations` 独占 Schema 修改。HTTP 路由只处理协议、认证上下文和响应装配。上传的 `UploadFile` 在 `api/uploads.py` 转为小文件字节或借用的二进制文件流；业务服务消费中立输入，workspace 文件边界负责限量、原子落盘。输入流由 HTTP 请求拥有，服务在请求结束前完成消费，不保存上传对象。

### 进程入口与启动装配

- `yuxi/api/main.py` 创建 FastAPI 应用；`api/routers` 统一注册 HTTP 路由，`api/lifespan.py` 连接 FastAPI 生命周期。
- `yuxi/workers/main.py` 暴露 ARQ `WorkerSettings`；`workers` 拥有任务入口和健康上报，`bootstrap/worker.py` 装配连接、恢复循环和关闭顺序。
- `bootstrap/api.py` 装配 API 必需与可选组件；`infrastructure/postgres/models.py` 显式注册分域 ORM，维持 business 与 knowledge 两套 metadata。
- `bootstrap/app.py` 是 app-init 入口，调用 `migrations/main.py` 的空库初始化与各域共享内容同步；`migrations/schema.py` 拥有 DDL；`infrastructure/postgres/schema.py` 向 API/worker 提供只读版本检查。

Yuxi 默认交付完整知识能力。部署开关 `YUXI_KNOWLEDGE_ENABLED` 由启动装配读取；显式关闭时 API 不装配知识库、图谱、评估及知识视图入口，discovery 宣告知识能力关闭，worker 暂停知识作业派发和投影清理，保留持久数据与待执行意图。内置 Skill 文件保留，知识工具不进入可用目录，会话知识选择保留，普通聊天保持可用。Compose 切换方式由[部署指南](docs/advanced/deployment.md)维护。聊天附件仅在真实解析动作发生时加载 parser。

### 业务模块

- `modules/agents` 拥有 Input 接收、优先队列调度、Turn/Run、消息、事件、运行租约与 LangGraph Agent runtime。`services/run_lifecycle.py` 拥有终态的 Thread→Turn→Input→Run 锁序、steer 裁决、Run/Turn 投影及提交后清理；checkpoint、runner 与 lease 恢复用例继续拥有各自事务提交。`services` 编排用例，`repositories` 查询持久状态，`models` 定义 ORM；`runtime/agent_backends` 拥有执行图实现与显式注册，`runtime/sandbox` 拥有沙盒执行与生命周期；`runtime/middlewares` 拥有模型与工具策略，Context 装配留在 runtime，知识库可见范围解析由 knowledge service 拥有。
- `modules/knowledge` 拥有知识库、分块、检索、图谱与评估的业务状态；`services/uploads.py` 共用上传、URL 与工作区原件的哈希及独立 MinIO 暂存，路由校验权限并适配响应，管理用例调用来源安全读取入口；本地文件读取和目录操作直接访问知识域 service/repository，只有解析、索引、检索和外部阅读装配执行器。目录与导入 service 在同一 PostgreSQL 事务中发布真实目录、唯一名称、文件、幂等回执及作业，提交后投递；worker 处理已登记文件。`services/graph_build.py` 拥有图谱提交准入与 Job 登记，worker 独立消费 action，图谱执行器拥有配置及投影锁。`modules/documents` 解析配置与 MinIO 输入，并按调用方指定的位置发布资源；`infrastructure/document_parsing` 只生成完整本地 Markdown 目录，`parser.py` 拥有格式转换，`artifacts.py` 拥有产物路径与引用，`engines/` 集中 OCR 契约、注册与实现。
- `modules/workspace` 拥有 UserWorkspace 的路径映射、no-follow 文件操作、Workdir 和预览；Agent 沙盒与 Viewer 共用的 runtime 虚拟路径映射由 `modules/workspace/runtime_paths.py` 拥有。
- `modules/identity` 拥有用户、部门、权限、凭据与 OIDC 账号用例，以及 Public API 的 Key 校验与 App/end_user 身份解析；`modules/extensions` 拥有 Skills、MCP 与工具目录；`modules/models` 拥有模型适配和供应商配置。
- `modules/schedules` 拥有用户定时 Agent 定义和 occurrence；`modules/background_jobs` 拥有任务登记、执行方上报的观察记录和取消意图；后台作业模块的 dispatch 与 registry 拥有提交和定义，`workers` 拥有领取、执行和失联维护；`modules/system` 拥有系统配置、Dashboard 与跨域共享资源所有权治理。

`modules/extensions/mcp` 拥有远程服务器、个人 OAuth 连接和加密凭据。超管固定授权服务器信任根，浏览器事务绑定用户、浏览器和配置修订；回调结果由当前 JWT 用户确认后发布。PostgreSQL 连接行锁串行化刷新与断开，世代阻止旧回调恢复授权。MCP service 根据可信 runtime uid 统一解析本人的 MCP 可用性；Agent 与 Skill 只消费可用服务和工具，未连接时保持普通聊天可用，相关 Skill 依赖在执行边界关闭。工具目录归本次 Run 的 Context，服务按需发现一次后在运行内复用，新 Run 重新发现；每次出站复查当前用户、配置、工具和连接。 配置、协议、授权事务与个人 HTTP 适配分属对应模块，具体链路和适配边界见 [MCP 授权与运行身份](docs/mechanisms/mcp.md)。

`modules/extensions/web` 拥有网络搜索、网页抓取与供应商适配；工具和管理员测试复用同一服务，每次调用读取当前有效配置，未配置的能力不进入运行工具集合。凭据与默认供应商复用 system 的持久设置和脱敏边界。

`modules/extensions/skills` 按共享索引、个人来源、草稿、文件编辑和投影组织用例。共享编辑通过文件修订值拒绝过期保存，读取、运行快照与投影复制持有共享行锁；授权变更按共享行锁、用户投影锁的顺序提交并刷新投影。个人 Skill 文件由 `personal.py` 在 UserWorkspace 边界访问。

### 共享技术边界

领域用例使用 `shared/errors.py` 中的失败类型、消息与可选业务 code，API 的 `errors.py` 负责转换为 HTTP 状态及现有 detail 正文。定时执行、文件预览与模型工具直接按领域类型消费失败；持久错误消息使用业务文本。HTTPException 属于 API 适配层。

`infrastructure/postgres` 管理业务 session 与 LangGraph checkpoint pool；`redis` 提供连接，业务 key 与事件语义留在对应模块；`minio`、`neo4j` 和 `oidc` 封装远端客户端；`observability` 封装日志与 Langfuse SDK。`infrastructure/file_preview.py` 准备浏览器展示内容，`infrastructure/office_conversion.py` 独立执行 Office 到 PDF 转换；Workspace 与 Knowledge 拥有各自的预览缓存。`shared` 只保存不依赖业务域与 HTTP 的小型公共契约与通用逻辑，`shared/files.py` 拥有文件输入、文件结果、MIME 识别与共用原文件预览预算；各预览读取用例把预算传给文件或对象存储边界执行有界读取。`infrastructure/crypto.py` 读取部署级 Fernet 凭据密钥，业务域拥有加密载荷与领域错误；`infrastructure/runtime_settings.py` 管理进程环境、公开站点来源与运行目录；system 模块拥有品牌模板和持久系统配置。`api/responses` 负责文件与知识响应，业务服务提供已经授权的读取结果。

### 后台任务

项目中存在三套领域状态不同、但共享 PostgreSQL 事实与 Redis/ARQ 投递模式的后台机制，不应合并状态模型：

- Agent 生命周期：Thread 保存长期对话，Input 保存持久接收与优先队列，Turn 保存一轮工作，Run/Attempt 保存执行段、输出和租约；人工等待与 execution tree 绑定明确的 Turn/Run。
- 用户定时 Agent：任务定义和 occurrence 独立持久化；worker 锁定到期任务后复用统一 Thread/Input/Turn/Run 用例，定时 occurrence 保留来源关联。
- 后台作业：用于知识库解析、评估和图谱构建。业务 service 登记持久 `job_type + handler_version + payload`，事务提交后通过后台作业 dispatch 入口投递；`worker` 从 registry 惰性加载领域 Handler，并通过 BackgroundJob 行的唯一 owner、heartbeat 和 lease 执行。知识文件中间态绑定 BackgroundJob/attempt owner，worker 调用领域 failure hook，与 作业终态同事务收敛文件错误态；PG pending 行由启动与周期 publisher 补发。JobTracker 内部直接使用 repository 返回的持久记录，HTTP 观察按白名单序列化；列表不生成结果详情。进度与终态属于执行方，业务失败判定由领域 service 拥有；取消先保存请求，由执行方在安全检查点响应。后台作业只向管理员开放。共享 ARQ worker 的执行槽由 Compose 配置，后台作业 的 PG claim 上限为 4，不能占满 AgentRun 容量。

测试代码位于 `backend/test`，按 `unit`、`integration`、`e2e` 分层。新增或修改后端行为时，测试应放在最能覆盖真实风险的层级。

## 前端代码地图

前端是 Vue 3 + Vite 应用，业务入口集中在 `frontend/src`。

- `app/main.js` 挂载应用，`app/App.vue` 提供主题与根 RouterView，`app/router` 与 `app/layouts` 拥有路由、访问守卫和应用导航。
- `pages` 是路由适配与页面装配入口。`AgentView` 把路由选择交给串行协调器，并装配会话工作区与智能体选择器。
- `modules` 按 session、agents、projects、workspace、knowledge、extensions、identity、settings、background-jobs、dashboard 组织业务；`ui` 拥有界面，`model` 拥有状态与领域逻辑。会话编排、Input 排队、Thread SSE、审批和提及属于 session；智能体目录、选择与编辑属于 agents。
- `shared/ui` 与 `shared/lib` 保存通用界面和工具，`shared/model` 保存主题状态；全局样式集中在 `assets/css`，颜色和基础规范复用 `base.css`。
- `apis` 是集中 HTTP 边界，复用 `base.js` 的请求、鉴权和错误处理。模块不能依赖 app/pages，shared 不能依赖业务模块或 API；ESLint 检查别名、相对路径和动态导入。跨模块只使用 `modules/<domain>` 的模型/用例公开入口或 `modules/<domain>/ui/**/*.vue` 公开组件，不得直达 `model/` 或其他内部路径。
- 新 TypeScript 逻辑经 strict 类型检查，build 先执行 typecheck；现有 JavaScript 逐步迁移。sessionRuntime 按 Thread 拥有一份 item 状态，历史分页、SSE 与恢复快照写入同一 reducer，展示按需派生。sessionInput 拥有冻结提交、幂等重试和接收交接；输入队列观察者确认 Input 消费并触发持久运行恢复，共享 runtime 根据当前 Turn 选择唯一运行订阅。SessionWorkspace 负责编辑器草稿、附件消费与面板展示。视图存活期间的草稿分别保留，首次进入 Thread 从持久草稿初始化。独立 Thread 阅读与后续类型迁移见[重构提案](docs/develop-guides/decisions/proposed/2026-09-30-agent-view-frontend-refactor.md)。

`/` 是公开首页；登录后的核心工作区是 `/agent`。`/extensions` 对所有登录用户开放，其中 Skills 与 MCP 个人连接对普通用户可见，知识库和工具仅管理员可见，MCP 配置仅超级管理员可修改；Dashboard 仅超级管理员可访问。后端权限检查始终是最终边界，前端守卫只负责页面体验。

## 智能体运行链路

一次普通智能体输入经过以下边界：

1. `AgentView` 装配 `SessionWorkspace`，后者收集文本、图片、附件、模型与审批配置，`frontend/src/apis/agent_api.js` 调用 Public Session API。Repository 批量读取事实，由唯一 Session 投影生成详情、列表、搜索和更新响应。
2. `api/routers/public_v1/agents` 将 JWT 或 API Key 身份转为完整 ActorScope，并把有序消息和配置交给 `modules/agents/services/inputs.py`。接入事务锁定 Thread，验证作用域与幂等回执，保存独立 Input 原始内容、提交级文件引用和 Receipt，并绑定附件到 Input；配置在接收时冻结，正文与原始文件 ID 永久保留。
3. `modules/agents/services/scheduler.py` 在线程锁下领取未暂停队列的就绪优先队头，原子创建 Turn、首个 pending Run 和正式 Message。follow-up 单条 FIFO；steer 按原接收序号消费连续就绪前缀，各 Input 保持独立。`services/queue_control.py` 从待办派生暂停，调用用例拥有 Thread 锁、事务与恢复意图；Input model 拥有恢复标记编码，repository 拥有排序与恢复边界。暂停后用户主动提交的恢复消息持久固定优先位置并单独消费，完成后继续原队列。消费固定 Input 的执行归属，并将各 Input 附件挂到自己的最后一条 Message；Receipt 保持接收时的命令目标，不回填执行归属。Message 的 received_at 保留接收时间，created_at 表示进入执行投影的时间。等待回答或审批时拒绝普通消息。
4. owning transaction 提交后才向 ARQ 投递 pending Run。恢复扫描可补投未成功投递的同一个 Run，不自动重试已经失败的工作。
5. `worker` 中的 `modules/agents/services/runner.py` 使用进程 identity 与 job-attempt token 取得 Run lease；未取得 ownership 的重复任务不会执行。runner 依次核验输入、执行用户与 Workdir 授权，并共用拒绝结算；启动独立事务续租的心跳后，由 `services/preparation.py` 准备 Context，在有效 lease 下提交 write-once 运行清单后才进入 LangGraph。Langfuse 使用 Turn 级 trace 与 Run 级 observation；远端观测不拥有业务终态。
6. 所有 Session 通过同一 Agent 后端与 middleware 组合 Workdir、Skills、MCP、审批、摘要及协作能力。协作成员拥有独立上下文、Thread/Input/Turn/Run，以父关系和树内名称关联；不继承父历史。协作工具也可创建继承当前项目的独立顶层对话，跨树按同用户和 APP 协作，稳定 ID 负责全范围寻址，路径只在本树内解析。全树共享 Project Workdir，执行名额由系统配置 `max_cooperation_concurrency` 限制，默认四个；同一用户的所有树共用一个 Cube VM，等待释放 worker 与执行名额。PostgreSQL 拥有协作消息、等待与终态事实；普通取消只影响指定 Turn，用户明确停止才停止整树。各 Run 的 lease、结果和终态发布独立，用户环境由 worker 在该用户无活动 Run 或操作时空闲休眠；具体行为见[会话协作](docs/agents/session-cooperation.md)。
7. 安全接管点在工具批次及 checkpoint 保存之后，或无工具的模型调用完成之后。pending steer 被固定为消费批次，旧 Run yielded，同一 Turn 创建下一 Run；提交后确认旧 Run 的 runtime 清理再投递接续，领取边界拒绝同 Thread 尚有清理责任的首次执行。普通工具循环保持同一 Run。人工等待使 Run interrupted、Turn waiting，并保存绑定该 Run 的等待点；结构化回答或审批消费等待点后，在同一 Turn 创建新 Run。
8. 完成、失败和取消由当前 owner 在数据库事务中收敛 Run 与 Turn。最终结果指向明确的顶层 result Run 的 output Message；Model/Tool 审计保留独立归属；普通历史只读取已登记的公开 item，不包含内部 prompt 或 checkpoint。父完成、失败或单轮取消保留后代独立工作；用户显式停止整树才取消所有成员在途 Turn。取消先持久化状态并暂停待消费输入队列，再发送 Redis 加速信号；失联 Run 由 lease reconciliation 形成可观察失败。外部副作用仍按 at-least-once 语义核对。
9. 工具真实开始与进度来自 BaseTool 回调，最外层 `ToolErrorGuardMiddleware` 按最终 ToolMessage 发布结局，原始与最终结果均成功时保留压缩前原文，`BaseAgent` 将其转为统一工具事件；中间件直接返回的结果不伪造 `started_at`。LangGraph v3 ProtocolEvent 由 `RunMessageRecorder` 统一记录模型和工具消息事实。工具审计仅在 `extra_metadata.output` 保存原始结果，`content` 固定为空且由数据库约束保护；审计文本、Memory 与公开内容使用同一工具结果契约派生，再由公开适配器转换为 OpenAI Agents 事件。消息事实与公开快照各自提交后发布事件，checkpoint 留在独立执行结果。Redis 保存逐条公开事件，SSE data 为事件本身，逻辑 event_id 与恢复 cursor 分开；断线或过期时客户端读取 PostgreSQL 快照恢复，不从相邻 Run 推断结果。
10. Thread 保存不可变 `project_id`，每个 Project 绑定一个 `workdir_path`，多个 Project 可以共享同一路径。managed Project 使用服务端创建的 `projects/YYYYMMDD/<六位标识>[-N]`，linked Project 绑定当前用户 UserWorkspace 内通过 no-follow 校验的已有目录。Thread 只归档；删除 Project 时拒绝仍有活跃 Turn 或待处理 Input 的情况，再软删除 Project 并归档所属 Thread。`yuxi.modules.workspace` 拥有宿主路径和 fd-relative 文件访问，Workdir resolver 为 Viewer、附件、Artifact、Run 和协作会话提供同一持久路径；Run 终态解除当前执行的清理责任，用户 VM 在无活动 Run 或命令时空闲休眠，Workdir 保留。

## 架构不变量

- Docker Compose 是开发环境的事实来源。开发时先检查容器、日志和源码生效时点；后端使用显式重启，前端保留热更新，不默认要求本地裸跑服务。
- HTTP 路由保持薄；用例流程放在各业务域 `services`，持久化查询放在同域 `repositories`。
- 接收事务保存 Input 原始内容和 Receipt；消费事务原子生成正式 Message、执行与附件归属，提交后投递 ARQ。未消费取消不生成消息；数据库唯一来源约束防止重复投影。
- 同一用户、APP、智能体和 Thread 的 follow-up Input 按 FIFO 单条领取，steer 优先并在消费时合并连续就绪前缀；未就绪队头阻止越过领取。每次提交身份独立，运行中在安全边界消费 steer；多个 Input 通过 consumed_run_id 归属于同一 Run。
- PostgreSQL 保存业务事实状态；Redis 承担投递、事件、取消和缓存，不作为 AgentRun 最终状态的唯一来源。
- `pending` Run 是持久化投递意图；`running` / `cancel_requested` Run 必须由唯一 attempt lease 拥有。Heartbeat 只能由当前 owner 续租，终态或 retry publication 清除 lease，过期 ownership 不能被另一个执行者静默接管。
- Turn 结果以 `result_run_id` 指向的顶层 Run 及其 `output_message_id` 为权威；消息、事件和 artifact 均绑定明确的 Input/Turn/Run，禁止从未完成、其他 Turn 的子 Run 或相邻 Run 猜测输出。每个子任务的 Turn 结果只属于自身 Run。
- `/api/system/health` 只表达 API 进程 liveness；Compose 以 `/api/system/ready` 判断启动完成、PostgreSQL/Redis 可用且存在完成启动的兼容 worker。worker 同时续租短 TTL ARQ 消费健康、AgentRun lease reconciliation 与 后台作业 reconciliation 成功事实；持久 key、超长 TTL、错误 Redis DSN 或持续无法收敛失联执行都不能维持 readiness。业务正确性仍由真实链路测试证明。
- Yuxi 数据库 Schema 只由 `app-init` 在 PostgreSQL advisory lock 内修改并记录 business/knowledge 域版本；API 与 worker 不建表或同步发布内置定义，并在任一域版本缺失、过旧或过新时拒绝启动；各自保留真实环境配置校验、连接和缓存初始化、运行恢复与 readiness。
- app-init 在同一 PostgreSQL advisory lock 内同步代码拥有的共享定义与内置 Skill 文件，任一步骤失败均阻止 API/worker 启动；内置 MCP 定义同步只处理本地发布内容，外部 MCP 的连接与调用按启用状态执行。
- 跨 repository 的身份管理用例只有一个 service 事务 Owner；Department 与 User 同一提交。API Key 由独立服务端主密钥和客户端幂等 ID 确定性派生，只保存 hash；原始创建意图使用不可变指纹校验，撤销保留 request-id tombstone，同一请求可恢复响应但不能复活已撤销凭据。
- 前端 API 调用集中在 `frontend/src/apis`，组件不要散落拼接普通 HTTP 接口。
- 智能体能力通过 context、middleware、toolkits、Skills、MCP 和 backends 组合；不要把知识库、沙盒或扩展逻辑硬编码进单个页面或路由。
- 受管理 Skill 的当前完整内容由 PostgreSQL 目录引用拥有；内容更新先准备不可变包再提交引用，专属历史记录复用该包。内容保存由运行准备刷新用户投影；权限撤销在授权提交前撤下旧投影。内容读取与引用清理共同使用资源锁，当前或历史引用的目录不能清理。
- Skill 的依赖工具只有在对应 Skill 被显式预加载或动态激活后才对模型开放；基础工具与受 Skill 门控的工具保持边界。
- Shipping 默认装配知识库、图谱和评估能力；显式关闭 Knowledge 时集中停止相关入口与后台工作，保留数据库结构和数据，Agent 通过 MCP 不可用路径继续运行；实际动作使用的解析器保持惰性加载。
- 文件边界只使用三种跨层路径：数据库中的 Project `workdir_path`、Viewer 当前 scope 相对 `/foo`、Agent/artifact runtime 绝对 `/home/user/workspace/...`；宿主 `Path` 由 `yuxi.modules.workspace` 持有，普通 Service/Repository 不得取得。
- 沙盒虚拟路径由当前 Project Workdir、Workspace 与共享 Skills 根共同约束；个人 Skill 保存在 UserWorkspace 的 `agents/skills`，沙盒标准入口 `~/.agents/skills` 链接到该目录，CLI 全局安装直接写入个人目录，运行时直接扫描；Container 单独挂载个人目录。共享与内置 Skill 才投影到只读 `/home/user/skills`。Cube 的惰性创建与恢复不得绕过 uid、代次及 Run/attempt owner 校验。Run 终态必须确认所属命令清理；用户环境在无活动 Run 与命令时空闲五分钟休眠。重建保留 Workspace 与 Skills，文件工具访问其他 VM 路径会自动唤醒。用户可见路径、对象存储 URL 与宿主机真实路径不能混用。
- 面向用户和外部系统的输入在边界校验；内部服务优先依赖已有类型、事务和仓储约束，避免用静默回退掩盖设计错误。

## 跨切面关注点

- **配置**：Compose 和 `.env` 提供部署配置；管理员系统配置、用户配置与模型供应商以 PostgreSQL 为持久化 Owner，Redis 只提供可失效缓存。
- **权限**：前端路由和页面标签提供体验级约束，FastAPI 认证依赖和 repository 可见性查询提供最终授权。
- **状态与存储**：PostgreSQL 保存 Thread、Input、Receipt、Turn、Run、Message、Project 的 `workdir_path`、业务和知识库元数据，也是 LangGraph checkpoint 的唯一 Owner。Redis 保存短期事件、取消信号、ARQ 和跨进程缓存；每个用户的 UserWorkspace 拥有 Workdir 与个人 Skill 字节，MinIO 继续拥有知识库与临时上传对象。
- **文档处理**：Agent 附件在接收时绑定 Input，经统一准备进入实时 Project Workdir；知识库上传仍先进入对象存储和文件元数据边界，再经过解析、分块和知识库实现。解析器、分块策略和知识库连接器保持可替换。
- **观测与调试**：优先通过 Compose service 查看 `api`、`worker` 和相关依赖日志；Langfuse 集中在服务层和 AgentRun 上下文；SSE 问题同时检查 Redis 事件与 PostgreSQL 终态。
