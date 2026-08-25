# ARCHITECTURE.md

本文档是 Yuxi 的代码地图，只描述相对稳定的系统边界、目录职责、核心运行链路和架构不变量。它用于帮助贡献者判断“一个改动应该落在哪里”，不替代具体模块文档、测试规范或源码注释。

修改不熟悉的模块前，先阅读对应章节，再使用符号搜索定位具体类型、函数和路由。开发与运行拓扑始终以 `docker-compose.yml` 为准。

## 鸟瞰

Yuxi 是一个面向 RAG、知识图谱和多智能体工作流的知识库平台。用户通过 Vue 前端管理智能体、知识库、模型、工具、Skills、MCP 与 SubAgents；前端通过 `/api` 调用 FastAPI；后端服务层协调 PostgreSQL、Redis、MinIO、Milvus、Neo4j、LangGraph 和沙盒。

普通智能体请求先在 PostgreSQL 中保存为请求和消息，再立即派发或进入线程级 FIFO 队列。派发后的 `AgentRun` 通过 Redis/ARQ 交给独立 worker 执行，运行事件写入 Redis Stream，最终状态和业务记录写回 PostgreSQL，前端通过 SSE 消费排队与运行事件。

核心开发服务包括：

- `web-dev`：Vue 3 / Vite 前端，挂载 `web/src` 并热重载。
- `api-dev`：FastAPI API 服务，挂载 `backend/server`、`backend/package` 和测试目录并热重载。
- `worker-dev`：ARQ worker，执行已经派发的 AgentRun 与注册的通用后台 Task；两类任务分别使用独立的 PostgreSQL 状态模型、attempt ownership、heartbeat 与失联收敛。
- `storage-migrator`：Compose 中唯一修改 Yuxi 数据库 Schema 的一次性迁移进程，同时处理受支持的历史存储切换；API 与 worker 等待其成功后只校验 Schema 版本。
- `sandbox-provisioner`：为智能体工具执行提供隔离沙盒。
- `postgres`：业务数据、知识库元数据、请求队列、AgentRun 与 LangGraph checkpoint。
- `redis`：ARQ 投递、运行事件、取消信号以及跨进程配置和模型缓存。
- `minio`：附件、知识库原始文件和其他对象数据。
- `milvus`、`etcd`：向量检索及其元数据协调。
- `graph`：Neo4j 知识图谱。
- `mineru-api`、`paddlex`：通过 `all` profile 可选启动的文档解析和 OCR 服务。

## 后端代码地图

后端分成两个顶层边界：`backend/server` 是 Web 应用入口与 HTTP 适配层，`backend/package/yuxi` 是业务和基础设施主体。新增领域逻辑通常优先放在 `yuxi` 包中，路由层只处理请求模型、认证上下文和响应装配。

### Web 与 worker 入口

- `server/main.py` 创建 FastAPI 应用、注册中间件，并将业务路由统一挂载到 `/api`。
- `server/routers` 是 HTTP 路由边界，所有路由集中在 `server/routers/__init__.py` 注册。
- `server/utils/lifespan.py` 管理数据库、内置模型/MCP/Skills、知识库、Redis、沙盒和 LangGraph checkpoint；通用 Task 只由独立 ARQ worker 执行。
- `server/worker_main.py` 是 ARQ worker 入口，实际执行设置位于 `yuxi.services.run_worker`。

`LITE_MODE` 的解析由 `yuxi.config.runtime` 统一拥有。该模式保留认证、智能体、聊天、非知识类 Skills、MCP、模型、普通工作区和系统管理接口，但不注册 `external_kb`、`knowledge`、`evaluation`、`graph`、知识域 Dashboard 与 `/workspace/knowledge/*` 路由，不注册 `knowledge-base` Skill 或知识库工具，也不宣告客户端或 CLI 知识能力。该模式不导入知识解析重运行时、创建 knowledge schema 或初始化知识库管理器；Web 从运行时 discovery 获取同一能力投影，聊天附件只有在实际解析时才惰性加载 parser。

### `backend/package/yuxi`

- `agents` 定义 LangGraph 智能体体系。`BaseAgent` 是智能体基类，`BaseContext` 是运行上下文；`buildin/chatbot` 和 `buildin/subagent` 放内置智能体；`middlewares` 组合文件系统、Skills、SubAgent、摘要、审批、模型兼容和用量统计；`toolkits` 管理本地工具；`backends` 对接沙盒、知识库和 Skills 文件系统；`skills` 与 `mcp` 管理扩展能力及其运行时加载。
- `workspace` 是持久化 UserWorkspace Owner。`paths.py` 拥有 uid、宿主根和数据库 `projects/<uuid>` 映射，`filesystem.py` 拥有 no-follow 文件原语，`workdir.py` 提供以一个 Project 为根的持久化视图，`preview.py` 拥有 UserWorkspace 文件预览和 runtime 本地 Office 缓存。Agent Backend 单独拥有 `/home/gem/...` runtime 路径。
- `services` 是用例层。智能体主链路重点分为请求接入与排队、Run 生命周期、运行时配置、worker 执行和 SubAgent 调用；聊天历史、附件、工作区、文件预览、评估、认证和观测等跨模块流程也从这里找入口。
- `repositories` 是 PostgreSQL 访问边界，封装业务对象、知识库元数据、AgentRun、请求队列、Task 和扩展配置查询。路由不应绕过 repository 直接拼装持久化逻辑。
- `storage/postgres` 管理 SQLAlchemy 模型、业务连接池和 LangGraph checkpoint 连接池。
- `storage/redis` 管理同步/异步 Redis 客户端和 ARQ 连接参数；业务 key、事件格式和缓存语义留在各自服务中。
- `storage/minio` 管理对象上传、下载和临时文件访问。
- `storage/neo4j` 管理共享 Neo4j Driver、生命周期和图查询辅助。
- `knowledge` 是知识库、文档解析、评估和图谱领域。`runtime.py` 暴露运行时知识库管理器；`preview.py` 拥有 Knowledge metadata、MinIO 原始对象读取和 MinIO Office PDF 缓存；`implementations` 放 Milvus、Dify、Notion 和只读连接器；`parser` 统一封装 OCR/文档解析；`chunking` 管理分块策略；`graphs` 管理 Milvus 与 Neo4j 图谱能力。
- `models` 封装 chat、embedding 和 rerank 模型适配；`models/providers` 使用 PostgreSQL 保存模型供应商，并通过 Redis 缓存向 API 和 worker 提供一致视图。
- `config` 区分系统级配置和用户级配置。PostgreSQL 持久化系统配置和用户配置；Redis 只保存带版本失效的短缓存，旧 `base.toml` 只作为一次性迁移来源。
- `utils` 只放跨领域且足够通用的日志、时间、SSE 和轻量工具；`filepreview.py` 提供不依赖存储、领域或 HTTP 的格式识别、文本渲染和 Office 转换原语。

### 两类后台任务

项目中存在两套领域状态不同、但共享 PostgreSQL 事实与 Redis/ARQ 投递模式的后台执行机制，不应合并状态模型：

- AgentRun：拥有 Conversation、Message、LangGraph interrupt、线程 FIFO 和 execution tree 语义，通过专用 Run/Attempt 表维护状态、输出与租约。
- Durable Task：用于知识库解析、评估和图谱构建。API 只提交持久 `task_type + handler_version + payload`；`worker-dev` 从 registry 惰性加载领域 Handler，并通过 Task 行的唯一 owner、heartbeat 和 lease 执行。知识文件中间态绑定 Task/attempt owner，失联 failure hook 与 Task 终态同事务收敛文件错误态；PG pending 行由启动与周期 publisher 补发。共享 ARQ worker 明确提供 10 个执行槽，Durable Task 的 PG claim 上限为 4，至少保留 6 个槽供 AgentRun。LITE 不加载或消费知识 Handler。

测试代码位于 `backend/test`，按 `unit`、`integration`、`e2e` 分层。新增或修改后端行为时，测试应放在最能覆盖真实风险的层级。

## 前端代码地图

前端是 Vue 3 + Vite 应用，业务入口集中在 `web/src`。

- `main.js` 挂载应用，`App.vue` 是根组件。
- `router` 定义公开首页、登录、智能体、工作区、智能体管理、扩展和仪表盘路由，并负责认证、管理员和超级管理员守卫。
- `apis` 是后端接口封装边界。新增接口应在这里定义，复用 `base.js` 的请求、鉴权和错误处理。
- `stores` 保存用户、智能体配置、主题和其他跨页面状态。
- `views` 是页面级入口，`components` 是可复用界面块。智能体对话的主要交互位于 `AgentChatComponent`，由 `AgentView` 负责页面组合。
- `composables` 封装请求排队、Run SSE、流式消息、审批、线程状态、提及和其他可组合逻辑。
- `utils` 放轻量转换和展示辅助；全局样式集中在 `assets/css`，颜色和基础规范优先复用 `base.css`。

`/` 是公开首页；登录后的核心工作区是 `/agent`。`/extensions` 对所有登录用户开放，其中 Skills 对普通用户可见，知识库、工具和 MCP 管理能力仅管理员可见；Dashboard 仅超级管理员可访问。后端权限检查始终是最终边界，前端守卫只负责页面体验。

## 智能体运行链路

一次普通智能体请求经过以下边界：

1. `AgentView` 和 `AgentChatComponent` 收集文本、图片、附件、模型与审批配置。
2. `web/src/apis/agent_api.js` 调用 `POST /api/agent/runs`。
3. `server/routers/agent_router.py` 校验用户和智能体，将请求交给 `agent_request_queue_service`。
4. 服务在同一数据库事务中创建用户消息和 AgentRunRequest，并按用户、智能体和线程检查活跃 Run 与 FIFO 队头。
5. 请求可以立即派发、进入等待队列或按 `reject` 策略拒绝；只有数据库提交成功后才向 ARQ 投递 Run。
6. `worker-dev` 中的 `run_worker` 使用进程 identity 与 job-attempt token 取得 AgentRun lease；未取得 ownership 的重复任务不会执行。执行期间 heartbeat 在独立事务中续租，再加载智能体配置和运行上下文执行对应 LangGraph。
7. 智能体通过 middleware 组合 UserWorkspace 中的当前 Workdir、只读共享 Skills、MCP、SubAgent、审批、摘要和工具能力。根 Agent 与子 Agent 共享同一个 runtime 和 Workdir；知识库能力主要由内置 `knowledge-base` Skill 及其依赖工具按需开放。
8. Run 事件写入 Redis Stream，取消通过 Redis key/pubsub 传递；AgentRun、消息投递状态和最终结果写入 PostgreSQL。任何 assistant Message 发布前先在 Run 行锁内验证当前 attempt；正常输出、绑定和 `completed` 同事务提交。worker 失联后，过期 lease 会幂等收敛为带 `worker_lease_expired` 原因的 `failed`。该失败只证明执行 ownership 已丢失，外部副作用仍需按 at-least-once 语义核对。
9. 前端在排队阶段消费 Request SSE，派发后切换到 Run SSE，并根据数据库状态处理断线恢复和终态补偿。
10. Conversation 保存不可变 `project_id`，每个 Project 一期绑定一个 `workdir_path`，多个 Project 可以共享同一路径。v0.7.1 Conversation 在一次性迁移中直接获得 implicit Project，不形成 Conversation 路径中间态。managed Project 使用服务端创建的 `projects/<uuid>`；linked Project 可绑定当前 uid UserWorkspace 下除根目录外任意经过 no-follow 校验的已有目录。Workspace tree 仅展示 `/projects` 下属于 selectable Project 的目录子树，隐藏 implicit 与尚未归属 Project 的匿名目录。`yuxi.workspace` 唯一拥有宿主路径和 fd-relative 文件访问，统一 Workdir resolver 通过 Project 为 Viewer、附件、Artifact、Run 和 SubAgent 提供同一持久路径。Agent Backend 单独把该路径映射为 `/home/gem/user-data/...` runtime 路径。目录的持久 POSIX 字节是 Agent 文件、附件、Viewer 和 artifact 的实时事实源，`uploads/outputs` 只是按需创建的目录约定。Run 终态清理 runtime 进程但保留 Workdir。

审批或人机输入产生的 resume 请求会从 LangGraph checkpoint 恢复，并创建新的 AgentRun；它不重新进入普通消息 FIFO 接入流程。

## 架构不变量

- Docker Compose 是开发环境的事实来源。开发时先检查容器、日志和热重载，不默认要求本地裸跑服务。
- HTTP 路由保持薄；用例流程放在 `yuxi.services`，持久化查询放在 `yuxi.repositories`。
- 请求接入与 Run 执行是两个阶段：先提交 PostgreSQL 事实，再投递 ARQ，不能让队列消息先于数据库状态可见。
- 同一用户、智能体和线程的普通请求通过 FIFO 队列串行派发；排队请求与运行中的 Run 使用不同状态模型和 SSE。
- PostgreSQL 保存业务事实状态；Redis 承担投递、事件、取消和缓存，不作为 AgentRun 最终状态的唯一来源。
- `pending` Run 是持久化投递意图；`running` / `cancel_requested` Run 必须由唯一 attempt lease 拥有。Heartbeat 只能由当前 owner 续租，终态或 retry publication 清除 lease，过期 ownership 不能被另一个执行者静默接管。
- Run 结果以 `output_message_id` 指向的同 Run assistant 消息为权威；只有历史 `completed` Run 可在缺少指针时兼容读取同 conversation、相同 `run_id` 的 assistant 消息，禁止从未完成或相邻 Run 猜测输出。
- `/api/system/health` 只表达 API 进程 liveness；Compose 以 `/api/system/ready` 判断启动完成、PostgreSQL/Redis 可用且存在完成启动的兼容 worker。worker 同时续租短 TTL ARQ 消费健康、AgentRun lease reconciliation 与 Durable Task reconciliation 成功事实；持久 key、超长 TTL、错误 Redis DSN 或持续无法收敛失联执行都不能维持 readiness。业务正确性仍由真实链路测试证明。
- Yuxi 数据库 Schema 只由 `storage-migrator` 在 PostgreSQL advisory lock 内修改并记录 business/knowledge 域版本；API 与 worker 不建表或执行收敛 DDL，版本缺失、过旧或过新时拒绝启动。LITE 只迁移和校验 business 域。
- 内置 Skills 是默认 Agent shipping contract 的 required 组成，API/worker 通过 PostgreSQL advisory lock 串行同步；内置 MCP 定义是 optional，但失败必须形成可观测 degraded 而非被组件内部吞掉。
- 跨 repository 的身份管理用例只有一个 service 事务 Owner；Department、User 与强制 OperationLog 同一提交。API Key 由独立服务端主密钥和客户端幂等 ID 确定性派生，只保存 hash；原始创建意图使用不可变指纹校验，撤销保留 request-id tombstone，同一请求可恢复响应但不能复活已撤销凭据。
- 前端 API 调用集中在 `web/src/apis`，组件不要散落拼接普通 HTTP 接口。
- 智能体能力通过 context、middleware、toolkits、Skills、MCP 和 backends 组合；不要把知识库、沙盒或扩展逻辑硬编码进单个页面或路由。
- Skill 的依赖工具只有在对应 Skill 被显式预加载或动态激活后才对模型开放；基础工具与受 Skill 门控的工具保持边界。
- LITE 模式必须允许跳过知识库、图谱和评估等重依赖能力，新增导入、路由和启动逻辑时要尊重该边界。
- 文件边界只使用三种跨层路径：数据库 `projects/<uuid>`、Viewer 当前 scope 相对 `/foo`、Agent/artifact runtime 绝对 `/home/gem/user-data/...`；宿主 `Path` 由 `yuxi.workspace` 或显式 v0.7.1 storage migration 内部持有，普通 Service/Repository 不得取得。
- 沙盒虚拟路径由当前 Project Workdir、User Data 与共享 Skills 根共同约束；个人 Skill 保存在 UserWorkspace 的 `agents/skills`，共享与内置 Skill 才投影到只读 `/home/gem/skills`。用户可见路径、对象存储 URL 与宿主机真实路径不能混用。
- 面向用户和外部系统的输入在边界校验；内部服务优先依赖已有类型、事务和仓储约束，避免用静默回退掩盖设计错误。

## 跨切面关注点

- **配置**：Compose 和 `.env` 提供部署配置；管理员系统配置、用户配置与模型供应商以 PostgreSQL 为持久化 Owner，Redis 只提供可失效缓存；旧 `base.toml` 仅用于一次性迁移已有系统配置。
- **权限**：前端路由和页面标签提供体验级约束，FastAPI 认证依赖和 repository 可见性查询提供最终授权。
- **状态与存储**：PostgreSQL 保存请求、Run、消息、Conversation 的 `project_id` 与 Project 的 `workdir_path`、业务和知识库元数据，也是 LangGraph checkpoint 的唯一 Owner。Redis 保存短期事件、取消信号、ARQ 和跨进程缓存；每个用户的 UserWorkspace 拥有 Workdir 与个人 Skill 字节，MinIO 继续拥有知识库与临时上传对象。
- **文档处理**：Agent 附件确认后进入实时 Project Workdir；知识库上传仍先进入对象存储和文件元数据边界，再经过解析、分块和知识库实现。解析器、分块策略和知识库连接器保持可替换。
- **观测与调试**：优先查看 `api-dev`、`worker-dev` 和相关依赖日志；Langfuse 集中在服务层和 AgentRun 上下文；SSE 问题同时检查 Redis 事件与 PostgreSQL 终态。
