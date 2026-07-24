# ARCHITECTURE.md

本文档是 Yuxi 的代码地图，只描述相对稳定的系统边界、目录职责、核心运行链路和架构不变量。它用于帮助贡献者判断“一个改动应该落在哪里”，不替代具体模块文档、测试规范或源码注释。

修改不熟悉的模块前，先阅读对应章节，再使用符号搜索定位具体类型、函数和路由。开发与运行拓扑始终以 `docker-compose.yml` 为准。

## 鸟瞰

Yuxi 是一个面向 RAG、知识图谱和多智能体工作流的知识库平台。用户通过 Vue 前端管理智能体、知识库、模型、工具、Skills、MCP 与 SubAgents；前端通过 `/api` 调用 FastAPI；后端服务层协调 PostgreSQL、Redis、MinIO、Milvus、Neo4j、LangGraph 和沙盒。

普通智能体请求先在 PostgreSQL 中保存为请求和消息，再立即派发或进入线程级 FIFO 队列。派发后的 `AgentRun` 通过 Redis/ARQ 交给独立 worker 执行，运行事件写入 Redis Stream，最终状态和业务记录写回 PostgreSQL，前端通过 SSE 消费排队与运行事件。

核心开发服务包括：

- `web-dev`：Vue 3 / Vite 前端，挂载 `web/src` 并热重载。
- `api-dev`：FastAPI API 服务，挂载 `backend/server`、`backend/package` 和测试目录并热重载。
- `worker-dev`：ARQ worker，执行已经派发的 AgentRun，并负责异常恢复扫描。
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
- `server/utils/lifespan.py` 管理数据库、内置模型/MCP/Skills、知识库、Redis、沙盒、LangGraph checkpoint 和通用 Tasker 的启动与关闭。
- `server/worker_main.py` 是 ARQ worker 入口，实际执行设置位于 `yuxi.services.run_worker`。

`LITE_MODE` 下保留认证、智能体、聊天、Skills、MCP、模型、工作区和系统管理接口，但不注册 `external_kb`、`knowledge`、`evaluation` 和 `graph` 路由，也不初始化知识库管理器。

### `backend/package/yuxi`

- `agents` 定义 LangGraph 智能体体系。`BaseAgent` 是智能体基类，`BaseContext` 是运行上下文；`buildin/chatbot` 和 `buildin/subagent` 放内置智能体；`middlewares` 组合文件系统、Skills、SubAgent、摘要、审批、模型兼容和用量统计；`toolkits` 管理本地工具；`backends` 对接沙盒、知识库和 Skills 文件系统；`skills` 与 `mcp` 管理扩展能力及其运行时加载。
- `services` 是用例层。智能体主链路重点分为请求接入与排队、Run 生命周期、运行时配置、worker 执行和 SubAgent 调用；聊天历史、附件、工作区、文件预览、评估、认证和观测等跨模块流程也从这里找入口。
- `repositories` 是 PostgreSQL 访问边界，封装业务对象、知识库元数据、AgentRun、请求队列、Task 和扩展配置查询。路由不应绕过 repository 直接拼装持久化逻辑。
- `storage/postgres` 管理 SQLAlchemy 模型、业务连接池和 LangGraph checkpoint 连接池。
- `storage/redis` 管理同步/异步 Redis 客户端和 ARQ 连接参数；业务 key、事件格式和缓存语义留在各自服务中。
- `storage/minio` 管理对象上传、下载和临时文件访问。
- `storage/neo4j` 管理共享 Neo4j Driver、生命周期和图查询辅助。
- `knowledge` 是知识库、文档解析、评估和图谱领域。`runtime.py` 暴露运行时知识库管理器；`implementations` 放 Milvus、Dify、Notion 和只读连接器；`parser` 统一封装 OCR/文档解析；`chunking` 管理分块策略；`graphs` 管理 Milvus 与 Neo4j 图谱能力。
- `models` 封装 chat、embedding 和 rerank 模型适配；`models/providers` 使用 PostgreSQL 保存模型供应商，并通过 Redis 缓存向 API 和 worker 提供一致视图。
- `config` 区分系统级配置和用户级配置。系统配置写入 `base.toml` 并同步 Redis 快照，用户配置保存在 PostgreSQL。
- `utils` 只放跨领域且足够通用的日志、时间、SSE 和轻量工具。

### 两类后台任务

项目中存在两套用途不同的后台执行机制，不应混用：

- AgentRun：通过 PostgreSQL 保存事实状态，使用 Redis/ARQ 投递到 `worker-dev`，支持运行事件、取消、恢复和线程请求队列。
- `services/task_service.py` 中的 Tasker：运行在 API 进程内，用于知识库解析、评估和图谱构建等通用后台任务；任务摘要持久化到 PostgreSQL，但可执行 coroutine 和内存队列不具备跨进程重建能力。

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
6. `worker-dev` 中的 `run_worker` 加载 AgentRun、智能体配置和运行上下文，执行对应 LangGraph。
7. 智能体通过 middleware 组合沙盒文件系统、附件、Skills、MCP、SubAgent、审批、摘要和工具能力。知识库能力主要由内置 `knowledge-base` Skill 及其依赖工具按需开放。
8. Run 事件写入 Redis Stream，取消通过 Redis key/pubsub 传递；AgentRun、消息投递状态和最终结果写入 PostgreSQL。
9. 前端在排队阶段消费 Request SSE，派发后切换到 Run SSE，并根据数据库状态处理断线恢复和终态补偿。
10. 附件和对象数据保存在 MinIO；智能体需要操作的文件映射到线程隔离的沙盒路径，生成物写入用户可见的输出目录。

审批或人机输入产生的 resume 请求会从 LangGraph checkpoint 恢复，并创建新的 AgentRun；它不重新进入普通消息 FIFO 接入流程。

## 架构不变量

- Docker Compose 是开发环境的事实来源。开发时先检查容器、日志和热重载，不默认要求本地裸跑服务。
- HTTP 路由保持薄；用例流程放在 `yuxi.services`，持久化查询放在 `yuxi.repositories`。
- 请求接入与 Run 执行是两个阶段：先提交 PostgreSQL 事实，再投递 ARQ，不能让队列消息先于数据库状态可见。
- 同一用户、智能体和线程的普通请求通过 FIFO 队列串行派发；排队请求与运行中的 Run 使用不同状态模型和 SSE。
- PostgreSQL 保存业务事实状态；Redis 承担投递、事件、取消和缓存，不作为 AgentRun 最终状态的唯一来源。
- 前端 API 调用集中在 `web/src/apis`，组件不要散落拼接普通 HTTP 接口。
- 智能体能力通过 context、middleware、toolkits、Skills、MCP 和 backends 组合；不要把知识库、沙盒或扩展逻辑硬编码进单个页面或路由。
- Skill 依赖工具只有在对应 Skill 激活后才对模型开放；基础工具与受 Skill 门控的工具要保持边界。
- LITE 模式必须允许跳过知识库、图谱和评估等重依赖能力，新增导入、路由和启动逻辑时要尊重该边界。
- 沙盒虚拟路径以 `SANDBOX_VIRTUAL_PATH_PREFIX` 为边界，用户可见路径、对象存储 URL 与宿主机真实路径不能混用。
- 面向用户和外部系统的输入在边界校验；内部服务优先依赖已有类型、事务和仓储约束，避免用静默回退掩盖设计错误。

## 跨切面关注点

- **配置**：Compose 和 `.env` 提供部署配置；管理员系统配置写入 `base.toml` 并通过 Redis 快照同步；用户配置与模型供应商以 PostgreSQL 为事实来源。
- **权限**：前端路由和页面标签提供体验级约束，FastAPI 认证依赖和 repository 可见性查询提供最终授权。
- **状态与存储**：PostgreSQL 保存请求、Run、消息、业务和知识库元数据；LangGraph checkpoint 使用 PostgreSQL，必要时可回退 SQLite/内存；Redis 保存短期事件、取消信号、ARQ 和跨进程缓存；MinIO、沙盒与本地 `saves` 分别承载不同生命周期的文件。
- **文档处理**：上传文件先进入对象存储和文件元数据边界，再经过解析、分块和知识库实现；解析器、分块策略和知识库连接器保持可替换。
- **观测与调试**：优先查看 `api-dev`、`worker-dev` 和相关依赖日志；Langfuse 集中在服务层和 AgentRun 上下文；SSE 问题同时检查 Redis 事件与 PostgreSQL 终态。
