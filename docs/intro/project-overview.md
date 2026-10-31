# 认识 Yuxi

Yuxi（语析）是一个可私有部署的知识智能体平台。它把知识库检索、知识图谱、智能体编排、工具调用和文件产物放在同一个工作区里，适合需要自己管理数据、模型和团队权限的组织。

## Yuxi 适合什么场景

Yuxi 适合下面几类工作：

- 用企业内部资料搭建带来源的问答和检索应用。
- 让智能体调用搜索、MCP、Skills、会话协作和沙盒工具，完成多步骤任务。
- 对知识库的检索质量和智能体的任务表现进行评估。
- 在用户、部门和管理员之间划分知识库、智能体和扩展能力的访问范围。

如果只需要一个不带权限和文件操作的单文档聊天页面，Yuxi 的能力会偏多。

## 三个核心概念

- **智能体（Agent）**：由模型、提示词、工具、知识库、Skills、MCP 组成的可复用配置；Session 是独立上下文和输入队列的会话实例。
- **知识库**：Milvus 知识库保存文档并提供解析、分块、检索和图谱；飞书、Dify、Notion 连接器按配置的凭据只读访问外部内容。
- **Workdir**：一个 Project 在用户工作区中的持久目录。智能体、文件查看器和交付物接口可以从不同入口访问同一份文件字节。

## 能力概览

### 智能体与任务执行

Yuxi 使用 LangGraph 组织智能体运行。用户可以创建私有智能体，在授权范围内选择模型、工具、MCP 和 Skills；管理员还可发布共享定义。请求由独立 worker 执行，事件通过服务端事件流（SSE）发送到前端，结果和运行状态保存到 PostgreSQL。

工具可以读取和修改 Workdir 中的文件、执行沙盒命令、搜索网页、请求用户审批或创建协作会话。协作成员使用独立上下文、共享项目文件，用户可进入成员会话回答问题、审批或调整方向，见[会话协作](../agents/session-cooperation.md)。完成的文件可以在界面中预览和下载。

### 知识库与 RAG

Milvus 知识库支持文档上传、解析、分块和索引，提供向量、BM25 与混合检索；用户可从命中片段和回答引用继续核对来源正文。外部知识源通过只读连接器查询，支持的操作由连接器决定，不在 Yuxi 中上传或同步源文档。

外部应用通过[知识库 API](../advanced/knowledge-base-api.md)或[知识库 MCP](../advanced/knowledge-mcp.md)查询和阅读授权范围内的资料。

### 知识图谱

管理员在 Milvus 知识库详情页配置抽取模型并提交构建任务，系统从已索引的文档块中抽取实体和关系并写入 Neo4j。图谱完成后可以查询、浏览关联，并与文本块检索融合，见[知识图谱](../advanced/knowledge-base-graph.md)。

### 团队治理

Yuxi 提供用户、部门、共享范围、模型供应商和 API Key 管理。外部应用通过 APP 与终端用户身份隔离会话，资源访问由后端授权，见[资源权限](../mechanisms/resource-permissions.md)和[API Key 接入](../advanced/api-key-integration.md)。

## 系统如何协作

一次普通的智能体请求大致经过这条链路：

```text
Web / CLI / 外部 API
        ↓
FastAPI 接收请求并保存 Input 原文、Receipt
        ↓
调度器消费就绪输入，创建 Message、Turn 与 Run
        ↓  消费事务提交后
Redis / ARQ 投递 AgentRun
        ↓
Worker 执行 LangGraph，写入事件和最终结果
        ↓
PostgreSQL 保存状态，前端通过 SSE 获取过程
```

这些组件的分工是：PostgreSQL 保存业务状态、知识库元数据和 LangGraph checkpoint，Redis 负责投递、短期事件、取消信号和缓存，MinIO 保存对象文件，Milvus 提供向量检索，Neo4j 保存可选的知识图谱。各组件在运行链路中的具体职责见[机制详解](../mechanisms/index.md)。

## 部署与沙盒

开发环境和单机部署使用 Docker Compose，默认启动知识库、图谱和评估所需的完整拓扑。应用默认通过 Container runner 为每个 Run 提供临时执行容器；需要持久用户 VM 和强隔离时显式选择 Cube，执行节点要求 Linux/KVM。部署边界见[生产部署](../advanced/deployment.md)与[Container 沙盒](../advanced/container-sandbox.md)。

开发环境和默认服务拓扑以仓库根目录的 [ARCHITECTURE.md](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md) 与 [docker-compose.yml](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml) 为准。

## 技术栈

| 层 | 技术 | 主要职责 |
| --- | --- | --- |
| 前端 | Vue 3、Vite、Pinia | 工作区、配置界面和实时运行展示 |
| API 与运行时 | FastAPI、LangGraph、ARQ | 请求接入、智能体编排和后台执行 |
| 持久化 | PostgreSQL | 业务状态、知识库元数据和 checkpoint |
| 缓存与事件 | Redis | 投递、短期事件、取消和缓存 |
| 对象与检索 | MinIO、Milvus、Neo4j | 文件对象、向量索引和知识图谱 |
| 文档处理 | MinerU、PaddleOCR、docling、RapidOCR 等 | 文档解析、版面分析和 OCR |
| 部署 | Docker Compose | 开发与单机部署拓扑 |

Yuxi 本体使用 MIT License；Compose 引入的第三方组件遵循各自许可证，生产部署和再分发边界见[生产部署指南](../advanced/deployment.md)。

## 从这里开始

- [快速开始](./quick-start.md)：启动一套可用的开发环境。
- [模型配置](./model-config.md)：接入聊天、嵌入和重排模型。
- [知识库与知识图谱](./knowledge-base.md)：创建知识库并让智能体检索文档。
- [智能体配置](../agents/agents-config.md)：了解配置字段如何进入运行时。
- [机制详解](../mechanisms/index.md)：深入查看沙盒、上下文压缩和知识库链路。
