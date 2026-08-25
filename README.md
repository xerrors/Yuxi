
![Yuxi：可私有部署的多租户知识智能体平台](https://xerrors.oss-cn-shanghai.aliyuncs.com/posts/2026/08/20260818-151118-mac-1787037059154-8c08f48c.png)


[![](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
[![](https://img.shields.io/github/v/release/xerrors/Yuxi?color=046A82)](https://github.com/xerrors/Yuxi/releases/latest)
[![License](https://img.shields.io/github/license/bitcookies/winrar-keygen.svg?logo=github)](https://github.com/xerrors/Yuxi/blob/main/LICENSE)
[![DeepWiki](https://img.shields.io/badge/DeepWiki-blue.svg)](https://deepwiki.com/xerrors/Yuxi)
[![Bilibili](https://img.shields.io/badge/知识库演示-00A1D6?logo=bilibili&logoColor=fff)](https://www.bilibili.com/video/BV1erE26iEgv/?share_source=copy_web&vd_source=37b0bdbf95b72ea38b2dc959cfadc4d8)


<a href="https://trendshift.io/repositories/24335" target="_blank"><img src="https://trendshift.io/api/badge/repositories/24335" alt="xerrors%2FYuxi | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

[[项目主页]](https://xerrors.github.io/Yuxi/) · [[快速开始]](https://xerrors.github.io/Yuxi/intro/quick-start) · [[演示视频]](https://www.bilibili.com/video/BV1erE26iEgv/) · [[版本记录]](https://github.com/xerrors/Yuxi/releases) · [[English]](README.en.md)



> 📢 作者为江南大学软件工程博士研究生，研究方向为 AI Agent、知识图谱与大模型应用，预计 2027年12月毕业，现寻求**实习/全职**机会。联系邮箱：wenjie.zhang@stu.jiangnan.edu.cn

---

## Yuxi 是什么

Yuxi（语析）是一个**可私有部署的多租户知识智能体平台**。把 **RAG 检索、Milvus 知识库内知识图谱、LangGraph 多智能体编排、MCP/Skills、沙盒工具与权限管理** 放进同一个工作台。

管理员负责接入模型、建设知识库并配置用户与部门权限；用户在统一对话界面中调用知识、工具和子智能体，获得带来源引用、基于图谱上下文的推理以及可预览、可下载产物的回答。

## 为什么选择 Yuxi

- **知识与智能体真正协同**：知识库和知识图谱让 Agent 在运行时可检索、可引用的知识来源。
- **从回答到任务交付**：Skills、MCP、工具、子智能体和沙盒文件系统共同支持长任务执行与产物交付。
- **面向团队而非单用户 Demo**：提供多租户、用户/部门权限、统一模型配置和外部 API Key 集成。
- **部署路径清晰**：Docker Compose 开箱即用。

## 能力展示

Yuxi 把知识进入系统、Agent 执行任务和团队治理放在一条完整链路中。以下按六个核心模块介绍系统能力：

| 模块 | 解决的问题 | 代表能力 |
| --- | --- | --- |
| 统一智能体工作台 | 在一个界面完成提问、执行与交付 | 多轮对话、知识引用、任务状态、人工审批 |
| 知识库与可追溯 RAG | 让回答有可核查的知识依据 | 多格式入库、Embedding/Rerank、检索测试、RAG 评估 |
| 知识图谱与知识导图 | 发现实体关系并浏览知识库文件结构 | 图谱构建、子图浏览、节点详情、文件元数据导图 |
| 多智能体与扩展生态 | 把复杂任务拆给不同角色和工具 | SubAgents、Skills、MCP、Tools、Agent 配置 |
| 沙盒工作区与产物 | 把对话结果变成可继续使用的文件 | 隔离文件系统、文件生成、在线预览、下载 |
| 团队治理与运行管理 | 在多人环境中管理能力、权限和运行状态 | 多租户、用户与部门权限、模型配置、API Key、Dashboard |

### 01 · 统一智能体工作台

用户可以在同一个对话界面里引用知识库文档、个人文件或扩展 Skill；实时观察任务执行的每一步，并在对话中直接拿到带精准来源引用或完整交付文件的回答。

- 支持使用 `@` 快速引入知识库、文件与特定 Skill。
- 全程可视化展示任务拆解步骤、工具调用状态与上下文 Token 消耗。
- 支持在回答中随时点击来源溯源核对，或直接预览和下载生成的文件产物。

![Yuxi 统一智能体工作台](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825145022410.png)

<details>
<summary><strong>展开详细截图：对话、执行状态与人工审批</strong></summary>


**长任务执行状态与过程追踪**

后台异步执行复杂任务时，界面会实时呈现智能体的思考链路、步骤计划、子任务状态与工具调用日志，不再让长任务变成黑盒等待。

<!-- ![长任务执行状态](docs/images/features/01b-agent-run-status.webp) -->

**人工审批与交付卡片**

在执行涉及修改文件、调用外部高危接口等关键操作时，系统会弹出确认卡片等待人工审批；任务完成后自动汇总生成成果并提供交互式交付入口。

![人工审批与文件交付](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825163940350.png)

</details>

### 02 · 知识库与可追溯 RAG

支持将团队各类资料集中管理并解析为结构化知识库。Agent 检索时不仅能召回相关内容，更能精确定位到具体的原文件和切片段落，彻底告别“凭空捏造”。

- 集中管理文件与目录结构，实时查看解析进度、Chunk 切片与 Token 统计。
- 支持配置 Embedding 与 Rerank 算法，并在后台直接进行多路召回测试与调优。
- 内置 RAG 效果评估工具，通过实际问答集量化测试知识库检索与回答质量。

![Yuxi 知识库管理](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825155645273.png)

<details>
<summary><strong>展开详细截图：入库、检索与评估</strong></summary>

**文档解析与切片管理**

支持 PDF、Word、PPT、Excel、Markdown 等多种常见文档格式。内置 MinerU、PaddleX、RapidOCR 等深度解析引擎，精准提取图文、表格并自动切分为高质量 Chunk，生成向量索引供检索使用。

![文档入库与解析状态](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825155256725.png)

**知识库类型与外部数据源**

除了开箱即用的本地向量知识库，系统还支持直接连接 Dify、Notion 等外部知识库服务，由统一检索器桥接供 Agent 无缝调用，免去二次数据迁移。

![知识库类型](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825155356171.png)

**检索测试与重排序（Rerank）**

提供直观的检索测试工作台。输入测试 Query 即可实时查看 Embedding 向量初筛得分、混合检索结果以及 Rerank 重排序后的分数变化，方便直观验证召回效果。

<!-- ![可追溯 RAG 检索测试](docs/images/features/02b-retrieval-trace.webp) -->

**RAG 效果评估**

支持构建专属的基准问答评估集，自动批量运行评测并输出检索召回率、答案相关性等量化指标，帮助快速发现知识盲区与配置短板。

<!-- ![RAG 评估结果](docs/images/features/02c-rag-evaluation.webp) -->

</details>

### 03 · 知识图谱与知识导图

将非结构化文档深层提炼为“实体-关系”图谱网络。既支持在交互式拓扑图谱中探索实体关联，也支持根据文件层级和主题元数据自动生成清晰的知识导图。

- 从知识库中自动抽取实体与关系，在 Milvus/Neo4j 中构建知识图谱索引。
- 支持按关键词搜索实体、点击节点查看属性详情，并高亮探索关联子图。
- 结合知识库文件元数据自动生成多层级知识导图，快速纵览业务领域全景。

![Yuxi 知识图谱与节点详情](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825151056752.png)

<details>
<summary><strong>展开详细截图：图谱构建、节点关系与知识导图</strong></summary>

**图谱构建与索引状态**

解析文档时自动执行实体识别与关系抽取，构建面向具体业务领域的知识图谱。可直观查看实体总数、关系边数量与构建进度。

<!-- ![知识图谱构建与索引状态](docs/images/features/03a-graph-index.webp) -->

**节点详情与关联子图**

在可视化的力导向图谱中，点击任意实体节点即可在侧边栏查看其完整属性、标签与来源文件，并高亮展开多跳关联的邻接子图，辅助复杂逻辑推理。

<!-- ![知识图谱节点详情与关联子图](docs/images/features/03b-node-details.webp) -->

**知识导图**

基于文件的目录结构、分类标签与元数据特征，自动生成结构化的主题脑图/知识导图，方便用户以树状脉络快速浏览海量知识内容。

<!-- ![知识导图](docs/images/features/03c-knowledge-map.webp) -->

</details>

### 04 · 多智能体与扩展生态

一个 Agent 可以灵活组合模型、提示词、知识库、外部工具与专用子智能体。面对复杂任务，主智能体负责规划拆解，多个 SubAgents 分头异步并行执行，Skills 与 MCP 协议提供源源不断的能力扩展。

- 自由配置 Agent 的基座模型、知识挂载、工具调用与系统提示词。
- 支持多个 SubAgents 异步并行执行深度调研、数据分析或内容生成。
- 原生兼容 Skills 插件机制与 MCP（Model Context Protocol）标准协议。

![Yuxi 多智能体编排](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825152252874.png)

<details>
<summary><strong>展开详细截图：Agent 配置、子智能体与扩展能力</strong></summary>

**Agent 配置与行为定制**

智能体提供丰富的模块化配置项，可以按需组合大模型、挂载的知识库、自定义 Tools、MCP 服务、前置提示词与子智能体，并支持灵活配置在部门或团队内的共享可见范围。

![Agent 配置](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825151729055.png)

**子智能体并行执行**

支持主智能体将复杂的多步骤任务拆解后，派出多个专属 SubAgent 异步并行跑任务（例如分头检索不同领域的法规、分别撰写报告不同章节），全程互不阻塞，执行完毕后自动归拢汇总。

![子智能体并行执行](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825151559976.png)

**Skills、MCP 与生态扩展**

统一接入并管理 Skills 扩展技能与 MCP Servers 外部协议，支持针对不同角色分配权限与使用范围；借助渐进式披露机制，在真正需要时按需动态解析并加载工具。

![Skills](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825151310647.png)

![Skill 管理](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825153934215.png)

</details>

### 05 · 沙盒工作区与文件产物

每个任务都在安全隔离的沙盒文件系统中进行读写操作。智能体不仅能在对话中回答问题，还能把分析研究成果沉淀为 Markdown 文档、数据表格、HTML 页面或可执行代码，并在工作区中随时查看与下载。

- 任务在独立的沙盒目录中运行，保障文件与数据安全隔离。
- 支持一键生成图文报告、数据分析图表、Web 页面等多种格式产物。
- 浏览器内原生支持各类文件在线交互预览，支持一键打包下载。

![Yuxi 沙盒工作区与文件产物](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825152123583.png)

<details>
<summary><strong>展开详细截图：文件管理、在线预览与任务交付</strong></summary>

**工作区文件管理**

可视化管理任务运行过程中读取与产生的所有文件，清晰展示目录层级、文件类型与体积大小，方便在会话之间复用中间产物。

<!-- ![工作区文件管理](docs/images/features/05a-workspace-files.webp) -->

**HTML、PDF、图表与代码在线预览**

智能体生成的报告文档、可视化 HTML 网页、图片图表或代码脚本，无需下载即可直接在浏览器内置的预览器中渲染并进行交互查看。

<!-- ![产物在线预览](docs/images/features/05b-artifact-preview.webp) -->

**对话中的文件交付**

任务执行完毕后，对话气泡中会生成结构化的交付卡片，直观呈现文件摘要、格式与操作按钮，支持直接打开预览或保存到本地。

<!-- ![对话中的文件交付](docs/images/features/05c-file-delivery.webp) -->

</details>

### 06 · 团队治理与运行管理

专为企业和团队多人协作打造。管理员可以集中管理成员与部门组织架构、统一配置模型接入凭据与 API Key，并通过监控看板全面掌握平台的运行状况与调用指标。

- 支持按租户、用户与部门配置知识库、Agent 以及功能的读写权限。
- 集中配置和调度多供应商的大模型能力，统一管理 API Key 凭据。
- 实时统计分析使用量、请求趋势与资源负载，保障服务稳定运行。

<!-- ![Yuxi 团队治理与运行管理](docs/images/features/06-governance.webp) -->

<details>
<summary><strong>展开详细截图：权限、模型与运行数据</strong></summary>

**用户、部门与细粒度权限体系**

提供符合企业组织架构的多租户权限体系，支持按照部门或用户组精确控制对知识库、智能体、工具和沙盒工作区的访问与编辑权限。

<!-- ![用户、部门与权限管理](docs/images/features/06a-users-permissions.webp) -->

**模型供应商与统一凭据管理**

支持接入主流大模型供应商（OpenAI、Anthropic、DeepSeek、Qwen、本地 Ollama/vLLM 等），集中维护 API Key 凭据并统一分配模型能力，密钥对普通成员完全脱敏。

![模型供应商与模型能力](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260825152458034.png)

**Dashboard 与系统运行监控**

直观的运维数据看板，实时展示系统请求量、Token 消耗统计、知识库检索频次与长任务排队状态，为容量规划和成本核算提供数据支撑。

<!-- ![Dashboard 与运行状态](docs/images/features/06c-dashboard.webp) -->

</details>

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · Vite · Pinia |
| 后端 | FastAPI · LangGraph · ARQ (异步 worker) |
| 存储 | PostgreSQL · Redis · MinIO · Milvus · Neo4j |
| 文档解析 | MinerU · PaddleX · RapidOCR |
| 部署 | Docker Compose |

## 快速开始

**前置要求**：已安装 [Docker](https://docs.docker.com/get-docker/) 与 Docker Compose，并准备至少一个兼容 OpenAI 接口的大模型 API。

**1. 克隆代码并初始化**

```bash
git clone --branch v0.7.2.beta1 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi

# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1
```

**2. 使用 Docker 启动**

```bash
docker compose up --build
```

从旧文件布局升级到 v0.7.2 时不能直接 `up`；停机迁移、失败续跑、成套回滚与 Kubernetes 边界只在
[生产部署指南](docs/advanced/deployment.md#2-启动服务) 中维护。

**3. 访问平台**

等待启动完成后，浏览器打开 `http://localhost:5173`，使用初始化时生成的管理员账户登录即可。

> 💡 不需要知识库 / 知识图谱等重依赖时，可使用 `make up-lite` 以 LITE 轻量模式启动，加快冷启动速度。更多部署说明见 [项目文档](https://xerrors.github.io/Yuxi)。

详细配置、生产部署和故障排查请阅读[快速开始指南](https://xerrors.github.io/Yuxi/intro/quick-start)。最新开发动态见 [Changelog](https://xerrors.github.io/Yuxi/develop-guides/changelog)，规划中的能力见[开发路线图](https://xerrors.github.io/Yuxi/develop-guides/roadmap)。

## 致谢

本项目参考并引用了以下优秀开源项目，在此致以诚挚的感谢：

- [LightRAG](https://github.com/HKUDS/LightRAG) - 早期版本曾参考其图谱构建与检索思路
- [DeepAgents](https://github.com/langchain-ai/deepagents) - 直接引入作为深度智能体框架
- [DeerFlow](https://github.com/bytedance/deer-flow) - 参考了其 Sandbox 智能体架构的实现思路
- [RAGflow](https://github.com/infiniflow/ragflow) - 参考了其文档 Text Chunking 的分块策略
- [LangGraph](https://github.com/langchain-ai/langgraph) - 多智能体编排框架，本项目的核心架构基础
- [QwenPaw](https://github.com/agentscope-ai/QwenPaw) - 参考模型配置与个人文件区域设计

## 参与贡献

感谢所有贡献者的支持！

<a href="https://github.com/xerrors/Yuxi/contributors">
  <img src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=100&columns=10" />
</a>


## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=xerrors/Yuxi)](https://star-history.dera.page/#xerrors/Yuxi)


## 开源协议

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

Docker Compose 引入的第三方组件（Neo4j 社区版 GPL-3.0、MinIO AGPL-3.0 等）保留各自原始许可证，部署与再分发边界见[生产部署指南](docs/advanced/deployment.md)。



[![给 Yuxi 一个 Star](https://xerrors.oss-cn-shanghai.aliyuncs.com/posts/2026/08/20260818-184409-image-da91658b.png)](https://github.com/xerrors/Yuxi)
