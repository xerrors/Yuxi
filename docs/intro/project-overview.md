# 认识 Yuxi

Yuxi（语析）是一个可私有部署的多租户知识智能体平台。它把知识库检索、知识图谱、智能体编排、工具调用和文件产物放在同一个工作区里，适合需要自己管理数据、模型和权限的团队。

## Yuxi 适合什么场景

Yuxi 适合下面几类工作：

- 用企业内部资料搭建带来源的问答和检索应用。
- 让智能体调用搜索、MCP、Skills、子智能体和沙盒工具，完成多步骤任务。
- 对知识库的检索质量和智能体的任务表现进行评估。
- 在用户、部门和管理员之间划分知识库、智能体和扩展能力的访问范围。

如果只需要一个不带权限和文件操作的单文档聊天页面，Yuxi 的能力会偏多。

## 三个核心概念

- **智能体（Agent）**：由模型、提示词、工具、知识库、Skills、MCP 和子智能体共同组成的运行单元。
- **知识库**：保存文档并提供解析、分块、向量检索和来源信息。Milvus 知识库还可以构建和查询知识图谱；Dify、Notion 是只读连接器。
- **Workdir**：一个 Project 在用户工作区中的持久目录。智能体、文件查看器和交付物接口可以从不同入口访问同一份文件字节。

## 能力概览

### 智能体与任务执行

Yuxi 使用 LangGraph 组织智能体运行。管理员可以配置模型、系统提示词、工具、MCP、Skills、子智能体和上下文压缩策略。运行时间较长的任务由独立 worker 执行，事件通过 SSE 发送到前端，结果和运行状态保存到 PostgreSQL。

工具可以读取和修改 Workdir 中的文件、执行沙盒命令、搜索网页、请求用户审批或调用子智能体。完成的文件可以在界面中预览和下载。

### 知识库与 RAG

知识库支持把文档依次经过上传、解析、分块和索引，检索结果包含命中文本和文件来源。管理员可以使用 Milvus、Dify 或 Notion；其中只有 Milvus 支持在 Yuxi 中上传、解析和索引文档。

### 知识图谱

Milvus 知识库可以从已索引的文档块中抽取实体和关系，并将图谱信息写入 Neo4j。检索时，系统可以把图谱结果与文本块结果融合；知识库详情页提供图谱构建、查询和展示入口。

### 团队治理

Yuxi 提供用户、部门、共享范围、模型供应商和 API Key 管理。后端会在接口和数据查询处执行最终权限检查，前端隐藏入口只改善使用体验，不代替授权。

## 系统如何协作

一次普通的智能体请求大致经过这条链路：

```text
Web / CLI / 外部 API
        ↓
FastAPI 接收请求并保存 Message、Request
        ↓  PostgreSQL 提交后
Redis / ARQ 投递 AgentRun
        ↓
Worker 执行 LangGraph，写入事件和最终结果
        ↓
PostgreSQL 保存状态，前端通过 SSE 获取过程
```

这些组件的分工是：PostgreSQL 保存业务状态、知识库元数据和 LangGraph checkpoint，Redis 负责投递、短期事件、取消信号和缓存，MinIO 保存对象文件，Milvus 提供向量检索，Neo4j 保存可选的知识图谱。各组件在运行链路中的具体职责见[机制详解](../mechanisms/index.md)。

## 部署与沙盒

开发环境和单机部署使用 Docker Compose，默认启动知识库、图谱和评估所需的完整拓扑。应用通过 `sandbox-provisioner` 访问动态沙盒，底层可以使用 Docker 或 Kubernetes；沙盒规格与生产部署边界见[生产部署](../advanced/deployment.md)与[沙盒配置与运维](../advanced/sandbox-operations.md)。

开发环境和默认服务拓扑以仓库根目录的 [ARCHITECTURE.md](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md) 与 [docker-compose.yml](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml) 为准。

## 技术栈

| 层 | 技术 | 主要职责 |
| --- | --- | --- |
| 前端 | Vue 3、Vite、Pinia | 工作区、配置界面和实时运行展示 |
| API 与运行时 | FastAPI、LangGraph、ARQ | 请求接入、智能体编排和后台执行 |
| 持久化 | PostgreSQL | 业务状态、知识库元数据和 checkpoint |
| 缓存与事件 | Redis | 投递、短期事件、取消和缓存 |
| 对象与检索 | MinIO、Milvus、Neo4j | 文件对象、向量索引和知识图谱 |
| 文档处理 | MinerU、PaddleX、docling、RapidOCR 等 | 文档解析、版面分析和 OCR |
| 部署 | Docker Compose | 开发与单机部署拓扑 |

Yuxi 本体使用 MIT License；Compose 引入的第三方组件遵循各自许可证，生产部署和再分发边界见[生产部署指南](../advanced/deployment.md)。

## 从这里开始

- [快速开始](./quick-start.md)：启动一套可用的开发环境。
- [模型配置](./model-config.md)：接入聊天、嵌入和重排模型。
- [知识库与知识图谱](./knowledge-base.md)：创建知识库并让智能体检索文档。
- [智能体配置](../agents/agents-config.md)：了解配置字段如何进入运行时。
- [机制详解](../mechanisms/index.md)：深入查看沙盒、上下文压缩和知识库链路。
