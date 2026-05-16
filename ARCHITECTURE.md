# ARCHITECTURE.md

本文档是 Yuxi 的代码地图，参考 matklad 的 `ARCHITECTURE.md` 建议维护：只描述相对稳定的系统边界、目录职责和跨切面约束，避免同步易变的实现细节。新贡献者如果不确定“某个能力应该改哪里”，先读这里，再用符号搜索定位具体类型、函数或路由。

## 鸟瞰

Yuxi 是一个面向 RAG、知识图谱和多智能体工作流的知识库平台。用户在 Vue 前端中配置智能体、知识库、工具、Skills、MCP 与 SubAgents；前端通过 `/api` 调用 FastAPI；后端服务层协调数据库、对象存储、向量库、图数据库、LangGraph 运行态和沙盒；长耗时智能体运行交给 worker 异步执行，并通过事件流回到前端。

开发与运行拓扑以 `docker-compose.yml` 为准。核心开发服务包括：

- `web-dev`：Vue/Vite 前端，挂载 `web/src` 并热重载。
- `api-dev`：FastAPI API 服务，挂载 `backend/server` 和 `backend/package` 并热重载。
- `worker-dev`：ARQ 后台任务 worker，处理智能体运行等异步任务。
- `sandbox-provisioner`：为智能体工具执行提供沙盒环境。
- `postgres`、`redis`、`minio`、`milvus`、`graph`：分别承载业务/知识库元数据、运行事件与队列状态、对象存储、向量检索、Neo4j 图谱。
- `mineru-*`、`paddlex`：按 `all` profile 启动的文档解析/OCR 能力。

## 后端代码地图

后端分成两个顶层边界：`backend/server` 是 Web 应用入口与 HTTP 适配层，`backend/package/yuxi` 是可复用业务包。新增业务逻辑通常优先放在 `yuxi` 包中，路由层只做请求解析、认证上下文和响应装配。

- `server/main.py` 创建 FastAPI 应用，注册中间件，并把所有业务接口统一挂到 `/api`。
- `server/routers` 是 HTTP 路由边界。路由按领域拆分，集中在 `server/routers/__init__.py` 注册；知识库、图谱、评估和思维导图接口在 `LITE_MODE` 下不会注册。
- `server/utils` 放 Web 层通用能力，例如生命周期、认证、日志与迁移辅助。
- `server/worker_main.py` 是 worker 入口，实际 worker 设置来自 `yuxi.services.run_worker`。

`backend/package/yuxi` 是后端主体：

- `agents` 定义 LangGraph 智能体体系。`BaseAgent` 是智能体基类，`BaseContext` 是运行配置上下文；`buildin` 放内置智能体；`middlewares` 负责把知识库、Skills、MCP、附件、运行配置等能力挂到运行时；`toolkits` 放工具注册与内置工具；`backends` 对接沙盒和 Skills 等外部执行/资源后端。
- `services` 是用例层，负责串联 repositories、agents、knowledge、storage 和外部系统。聊天、运行队列、文件视图、Skills、MCP、SubAgents、评估等跨模块流程都从这里找入口。
- `repositories` 是数据库访问边界，封装业务对象和知识库元数据的 SQLAlchemy 查询。不要让路由绕过 repository 直接操作模型，除非已有局部模式要求这样做。
- `storage` 放持久化基础设施。`storage/postgres` 管理业务表、知识库表和 LangGraph checkpoint 所需连接池；`storage/minio` 管理对象存储。
- `knowledge` 是知识库和图谱领域。`KnowledgeBaseManager` 根据知识库类型分发到具体实现；`implementations` 放 LightRAG、Milvus、Dify 等知识库实现；`graphs` 放图谱适配与上传图谱服务；`chunking` 放文档分块策略。
- `plugins/parser` 是文档解析插件边界，统一封装 MinerU、PaddleX、RapidOCR、DeepSeek OCR 等解析实现。
- `models` 封装 chat、embedding、rerank 模型适配；`config` 维护应用配置和内置模型信息；`utils` 放跨领域但足够通用的工具。

测试代码放在 `backend/test`，按 `unit`、`integration`、`e2e` 分层组织。新增或修改后端行为时，测试应落在最能覆盖风险的那一层。

## 前端代码地图

前端是 Vue 3 + Vite 应用，业务入口集中在 `web/src`。

- `main.js` 挂载应用，`App.vue` 是根组件。
- `router` 定义页面路由和权限跳转。普通用户默认进入智能体对话，图谱、知识库、仪表盘、扩展管理等页面带管理员权限约束。
- `apis` 是唯一推荐的后端接口封装位置。新增后端接口时，同步在这里补对应 API 方法，复用 `base.js` 的请求、鉴权和错误处理。
- `stores` 放 Pinia 状态，例如用户、智能体配置、主题、图谱和任务状态。
- `views` 是页面级入口，`components` 是可复用界面块；智能体对话、知识库、图谱、扩展管理等复杂页面由 view 组合多个 component。
- `composables` 放可组合的前端运行逻辑，例如流式消息处理、运行事件订阅、审批、人机输入和智能体线程状态。
- `utils` 放前端通用工具和轻量转换逻辑；样式集中在 `assets/css`，颜色和基础规范优先复用 `base.css` 与现有 less 文件。

## 运行链路

一次典型智能体对话大致经过以下边界：

1. `AgentView` 及相关组件收集输入、附件和智能体配置。
2. `web/src/apis` 调用 `/api/chat` 相关接口。
3. `server/routers/chat_router.py` 进入后端，委托 `yuxi.services.chat_service` 或 `agent_run_service`。
4. 服务层读取 conversation、agent config、tools、skills、knowledge 等配置，必要时创建后台 run。
5. `worker-dev` 执行 LangGraph 智能体；中间件按上下文挂载知识库工具、Skills、MCP、附件与沙盒能力。
6. 运行事件写入 Redis，最终状态和业务记录写入 Postgres；文件和产物落到 `saves`、MinIO 或沙盒用户数据目录。
7. 前端通过 SSE/轮询消费运行事件，渲染消息、工具调用、引用来源、产物卡片和文件预览。

## 架构不变量

- Docker Compose 是开发环境的事实来源。开发时优先检查容器、日志和热重载，不要默认要求本地裸跑服务。
- HTTP 路由层应保持薄；领域流程放在 `yuxi.services`，持久化查询放在 `yuxi.repositories`。
- 前端 API 调用应集中在 `web/src/apis`，组件不要散落拼接后端 URL。
- 智能体能力通过 context、middleware、toolkits、backends 组合；知识库通过工具访问，不要把知识库、MCP、Skills 或沙盒逻辑硬编码进单个页面或路由。
- LITE 模式必须允许跳过知识库、图谱、评估等重依赖能力；新增相关接口或初始化逻辑时要尊重这个边界。
- 沙盒虚拟路径以 `SANDBOX_VIRTUAL_PATH_PREFIX` 为边界，用户可见路径与宿主机真实路径不要混用。
- 面向用户或外部系统的输入在边界校验；内部服务之间优先信任已有类型、仓储和框架约束，避免为了假设场景堆叠防御代码。

## 跨切面关注点

- **配置**：环境变量来自 Compose 和 `.env`，用户持久化配置由 `yuxi.config.app.Config` 管理，运行时配置通过智能体 context 进入 LangGraph。
- **权限**：前端路由守卫提供页面级跳转，后端认证与权限检查仍是最终边界。
- **状态与存储**：Postgres 存业务与知识库元数据，LangGraph checkpoint 使用独立连接池或 SQLite fallback，Redis 承载运行事件和取消信号，MinIO/本地 `saves`/沙盒目录承载文件。
- **文档处理**：上传文件先进入解析和分块边界，再进入知识库实现；解析插件和知识库实现应保持可替换。
- **观测与调试**：开发阶段优先使用 `docker logs api-dev --tail 100`、worker 日志和现有测试分层定位问题；Langfuse 相关逻辑集中在服务层和智能体运行配置附近。
