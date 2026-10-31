![Yuxi：可私有部署的多租户知识智能体平台](https://oss-img.xerrors.cn/posts/2026/08/20260818-151118-mac-1787037059154-8c08f48c.png)

**Yuxi = Cloud Agents + Knowledge RAG**，Yuxi 是一个可私有部署的多租户知识智能体平台。自定义智能体，权限可控，沉淀企业轨迹。它把知识库检索、知识图谱、LangGraph 多智能体编排、MCP/Skills、沙盒工具和权限管理放进同一个工作区。让每个用户都拥有一个云端 Codex。

[项目主页](https://xerrors.github.io/Yuxi/) · [快速开始](https://xerrors.github.io/Yuxi/intro/quick-start) · [演示视频](https://www.bilibili.com/video/BV1erE26iEgv/) · [版本记录](https://github.com/xerrors/Yuxi/releases) · [English](README.en.md)


[![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
[![Release](https://img.shields.io/github/v/release/xerrors/Yuxi?color=046A82)](https://github.com/xerrors/Yuxi/releases/latest)
[![License](https://img.shields.io/github/license/xerrors/Yuxi.svg?logo=github)](https://github.com/xerrors/Yuxi/blob/main/LICENSE)
[![DeepWiki](https://img.shields.io/badge/DeepWiki-blue.svg)](https://deepwiki.com/xerrors/Yuxi)
[![Bilibili](https://img.shields.io/badge/知识库演示-00A1D6?logo=bilibili&logoColor=fff)](https://www.bilibili.com/video/BV1erE26iEgv/)

<a href="https://trendshift.io/repositories/24335" target="_blank"><img src="https://trendshift.io/api/badge/repositories/24335" alt="xerrors%2FYuxi | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

## Yuxi 能做什么

Yuxi 是面向需要企业自部署的多用户 Agent 产品，且需要自己掌握数据、模型和权限的团队：

- **二开友好**：MIT 协议、代码简洁、可拓展性强、高并发部署友好。
- **Harness**：功能完善的云端多用户 Harness，自定义智能体，沉淀企业轨迹。
- **Knowledge**： 高效、精准的 Agentic RAG 套件，上传文档，解析、分块和向量索引。
- **执行多步骤任务**：组合工具、MCP、Skills、子智能体和沙盒，产出可预览、可下载的文件。
- **连接知识图谱**：从 Milvus 知识库的文档块中抽取实体和关系，写入 Neo4j 并参与检索。
- **管理团队使用范围**：按用户、部门和共享范围管理知识库、智能体、Skills 和模型。
- **对比运行质量**：评估知识库的检索结果；完整智能体评估可通过公开 API 自行构建，见[智能体评估](docs/agents/agent-evaluation.md)。


## 赞助商

| 赞助商 | 介绍 |
| :---: | :--- |
| <img src="https://oss-img.xerrors.cn/github/%E4%B8%8B%E8%BD%BD.jpeg" alt="Fluxion AI LOGO" width="180" /> | Fluxion AI面向个人开发者、技术团队与企业，通过统一API接入并管理全球主流AI模型；通过多线路动态调度提升可用性，模型表现、响应时间与费用透明可查。根据不同模型与线路，API调用成本较官方或基准价格可降低40%—98%。专属链接[注册](https://fluxionai.space/register?source=github&campaign=yuxi&promo=YUXI) 获 $7 API 额度 |

## 快速启动

语析（Yuxi）默认使用 Docker Compose 和 Container 沙盒启动。每段智能体执行使用独立临时容器，工作目录文件持久保留，无需额外准备 KVM 节点。

### 前置条件

安装 Git、[Docker Engine](https://docs.docker.com/get-docker/) 和 Docker Compose v2，准备一个可用的大模型 API。macOS/Windows 使用 Docker Desktop 的 Linux 容器模式，并允许项目目录共享。

本教程使用 `v0.8.0.beta6` 测试版本。0.8 仅面向全新部署，旧数据库、索引和文件布局不提供自动升级；请用独立数据目录试用，见[0.8.0 更新说明](docs/develop-guides/releases/0.8.0.md)。

Container 适合本地试用和单租户可信代码，共享宿主内核。需要 VM 隔离或持久用户环境时，在[快速开始](docs/intro/quick-start.md)选择 Cube 并准备 Linux/KVM 执行节点。

### 1. 获取代码并初始化

```bash
git clone --branch v0.8.0.beta6 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi
./scripts/init.sh
```

Windows 在 PowerShell 中运行 `.\scripts\init.ps1`。脚本提示填写 SiliconFlow API Key，生成独立的应用安全密钥、runner 认证密钥和实例标识，并准备基础镜像。其他模型供应商可在登录后配置。已有 `.env` 时保留原密钥；切换到 Container 需设置 `SANDBOX_BACKEND=container` 后重新运行初始化。

### 2. 启动服务

```bash
docker compose --profile container config --quiet
docker compose --profile container up --build -d
```

`container` profile 会构建沙盒镜像并启动 runner。默认禁止沙盒联网，适用范围与配置见[Container 沙盒](docs/advanced/container-sandbox.md)。

### 3. 验证部署

```bash
docker compose --profile container ps
curl --fail http://localhost:5050/api/system/ready
```

返回的 `status` 为 `ready` 后，打开 [http://localhost:5173](http://localhost:5173)，初始化超级管理员并登录。在智能体中执行创建 `hello.txt` 的任务，并在工作区核对文件内容；完整操作见[快速开始](docs/intro/quick-start.md)。API 文档位于 [http://localhost:5050/docs](http://localhost:5050/docs)。

## 文档导航

- [项目介绍](https://xerrors.github.io/Yuxi/intro/project-overview)：了解能力、概念和系统边界。
- [快速开始](https://xerrors.github.io/Yuxi/intro/quick-start)：选择沙盒并启动开发环境。
- [命令行工具](docs/intro/cli.md)：登录实例、对话、发送附件和查询知识库。
- [公开 Agent API 入门](docs/intro/agents-api-quickstart.md)：外部应用创建会话、提交消息和读取结果。
- [模型配置](https://xerrors.github.io/Yuxi/intro/model-config)：接入聊天、嵌入和重排模型。
- [知识库教程](https://xerrors.github.io/Yuxi/intro/knowledge-base)：创建知识库并验证检索。
- [智能体开发](https://xerrors.github.io/Yuxi/agents/agents-config)：配置 Agent、工具和扩展。
- [生产部署](https://xerrors.github.io/Yuxi/advanced/deployment)：部署、升级、备份和排障。
- [版本变更记录](https://xerrors.github.io/Yuxi/develop-guides/changelog)：查看已发布变更。

## 能力展示

Yuxi 把知识进入系统、Agent 执行任务和团队治理放在一条完整链路中。以下按六个核心模块介绍系统能力：


| 模块               | 解决的问题                           | 代表能力                                             |
| -------------------- | -------------------------------------- | ------------------------------------------------------ |
| 统一智能体工作台   | 在一个界面完成提问、执行与交付       | 多轮对话、知识库检索、任务状态、人工审批               |
| 知识库与 RAG | 为回答提供检索内容             | 多格式入库、Embedding/Rerank、检索测试、RAG 评估     |
| 知识图谱与知识导图 | 发现实体关系并浏览知识库文件结构     | 图谱构建、子图浏览、节点详情、文件元数据导图         |
| 多智能体与扩展生态 | 把复杂任务拆给不同角色和工具         | SubAgents、Skills、MCP、Tools、Agent 配置            |
| 沙盒工作区与产物   | 把对话结果变成可继续使用的文件       | 隔离文件系统、文件生成、在线预览、下载               |
| 团队治理与运行管理 | 在多人环境中管理能力、权限和运行状态 | 多租户、用户与部门权限、模型配置、API Key、Dashboard |

### 01 · 统一智能体工作台

用户可以在同一个对话界面里引用知识库文档、个人文件或扩展 Skill；查看任务执行状态，并在对话中获取回答或生成的文件。

- 支持使用 `@` 快速引入知识库、文件与特定 Skill。
- 展示任务拆解步骤、工具调用状态与上下文 Token 消耗。
- 支持预览和下载生成的文件产物。

![Yuxi 统一智能体工作台](https://oss-img.xerrors.cn/github/image-20260825145022410.png)

<details>
<summary><strong>展开详细截图：对话、执行状态与人工审批</strong></summary>

**长任务执行状态与过程追踪**

后台异步执行复杂任务时，界面会实时呈现智能体的思考链路、步骤计划、子任务状态与工具调用日志，不再让长任务变成黑盒等待。

![image-20260826195730155](https://oss-img.xerrors.cn/github/image-20260826195730155.png)

**人工审批与交付卡片**

默认审批模式要求确认命令执行和当前 Project 外的文件写入，Project 内写入自动放行；完全信任模式不要求审批。任务完成后，用户可以通过交付卡片预览或保存文件。

![人工审批与文件交付](https://oss-img.xerrors.cn/github/image-20260825163940350.png)

</details>

### 02 · 知识库与 RAG

支持将团队各类资料集中管理并解析为结构化知识库。Agent 可以检索相关文档片段，作为回答问题的上下文。

- 集中管理文件与目录结构，实时查看解析进度、Chunk 切片与 Token 统计。
- 支持配置 Embedding 与 Rerank 算法，并在后台直接进行多路召回测试与调优。
- 内置 RAG 效果评估工具，通过实际问答集量化测试知识库检索与回答质量。

![image-20260830144756161](https://oss-img.xerrors.cn/github/image-20260830144756161.png)

<details>
<summary><strong>展开详细截图：入库、检索与评估</strong></summary>

**文档解析与切片管理**

支持 PDF、Word、PPT、Excel、Markdown 等多种常见文档格式。接入 MinerU、PaddleOCR 云服务与可选自托管 MinerU，保留轻量 RapidOCR，提取文本、图片和表格内容并切分为 Chunk，生成向量索引供检索使用。

![文档入库与解析状态](https://oss-img.xerrors.cn/github/image-20260825155256725.png)

**知识库类型与外部数据源**

除了开箱即用的本地向量知识库，系统还支持直接连接 Dify、Notion 等外部知识库服务，通过统一检索器供 Agent 调用。

![知识库类型](https://oss-img.xerrors.cn/github/image-20260825155356171.png)

**检索测试与重排序（Rerank）**

提供直观的检索测试工作台。输入测试 Query 即可实时查看 Embedding 向量初筛得分、混合检索结果以及 Rerank 重排序后的分数变化，方便直观验证召回效果。

![image-20260830144827319](https://oss-img.xerrors.cn/github/image-20260830144827319.png)

**RAG 效果评估**

支持构建基准问答评估集并批量评测检索召回率；提供参考答案和评判模型时，还可评估答案准确性，帮助发现知识盲区与配置短板。

![image-20260830144852289](https://oss-img.xerrors.cn/github/image-20260830144852289.png)

自动生成单条 QA 和多跳 QA

![image-20260826200055381](https://oss-img.xerrors.cn/github/image-20260826200055381.png)

![image-20260830144919806](https://oss-img.xerrors.cn/github/image-20260830144919806.png)

![image-20260830144950126](https://oss-img.xerrors.cn/github/image-20260830144950126.png)

</details>

### 03 · 知识图谱与知识导图

从非结构化文档中抽取实体与关系，构建知识图谱。既支持在交互式拓扑图谱中探索实体关联，也支持根据文件层级和主题元数据自动生成清晰的知识导图。

- 从知识库中自动抽取实体与关系，在 Milvus/Neo4j 中构建知识图谱索引。
- 支持按关键词搜索实体、点击节点查看属性详情，并高亮探索关联子图。
- 结合知识库文件元数据自动生成多层级知识导图，浏览知识库结构。

![image-20260830145027082](https://oss-img.xerrors.cn/github/image-20260830145027082.png)

<details>
<summary><strong>展开详细截图：图谱构建、节点关系与知识导图</strong></summary>

**图谱构建与索引状态**

管理员对已索引的 Milvus 文档块提交构建任务后，系统执行实体识别与关系抽取并写入 Neo4j。图谱构建不随文档解析自动触发，用户可查看实体、关系与后台任务进度。

![image-20260826200519930](https://oss-img.xerrors.cn/github/image-20260826200519930.png)

**知识导图**

基于文件的目录结构、分类标签与元数据特征，自动生成结构化的主题脑图/知识导图，方便用户以树状脉络快速浏览海量知识内容。

![image-20260830145125067](https://oss-img.xerrors.cn/github/image-20260830145125067.png)

</details>

### 04 · 多智能体与扩展生态

一个 Agent 可以灵活组合模型、提示词、知识库、外部工具与专用子智能体。面对复杂任务，主智能体负责规划拆解，多个 SubAgents 分头异步并行执行，Skills 与 MCP 协议提供能力扩展。

- 自由配置 Agent 的基座模型、知识挂载、工具调用与系统提示词。
- 支持多个 SubAgents 异步并行执行深度调研、数据分析或内容生成。
- 原生兼容 Skills 插件机制与 MCP（Model Context Protocol）标准协议。

![Yuxi 多智能体编排](https://oss-img.xerrors.cn/github/image-20260825152252874.png)

<details>
<summary><strong>展开详细截图：Agent 配置、子智能体与扩展能力</strong></summary>

**Agent 配置与行为定制**

智能体提供丰富的模块化配置项，可以按需组合大模型、挂载的知识库、自定义 Tools、MCP 服务、前置提示词与子智能体，并支持灵活配置在部门或团队内的共享可见范围。

![Agent 配置](https://oss-img.xerrors.cn/github/image-20260825151729055.png)

**子智能体并行执行**

支持主智能体将复杂的多步骤任务拆解后，派出多个专属 SubAgent 异步并行跑任务（例如分头检索不同领域的法规、分别撰写报告不同章节），执行完毕后汇总结果。

![子智能体并行执行](https://oss-img.xerrors.cn/github/image-20260825151559976.png)

**Skills、MCP 与生态扩展**

统一接入并管理 Skills 扩展技能与 MCP Servers 外部协议，支持针对不同角色分配权限与使用范围；借助渐进式披露机制，在真正需要时按需动态解析并加载工具。Skill 在线安装支持 skills.sh 以及魔搭社区的 skill。

![image-20260830145227399](https://oss-img.xerrors.cn/github/image-20260830145227399.png)

可以查看并在线编辑 skill

![image-20260830145217595](https://oss-img.xerrors.cn/github/image-20260830145217595.png)

可以配置 skill 权限和依赖

![image-20260830145359700](https://oss-img.xerrors.cn/github/image-20260830145359700.png)

内置工具列表

![内置工具](https://oss-img.xerrors.cn/github/image-20260826201207239.png)

</details>

### 05 · 沙盒工作区与文件产物

智能体可以通过沙盒工具读写文件。智能体不仅能在对话中回答问题，还能把分析研究成果沉淀为 Markdown 文档、数据表格、HTML 页面或可执行代码，并在工作区中随时查看与下载。

- 通过沙盒限制工具可访问的文件范围。
- 支持生成图文报告、数据分析图表、Web 页面等多种格式产物。
- 在浏览器中预览受支持的文件格式，并打包下载。

![Yuxi 沙盒工作区与文件产物](https://oss-img.xerrors.cn/github/image-20260825152123583.png)

<details>
<summary><strong>展开详细截图：文件管理、在线预览与任务交付</strong></summary>

**工作区文件管理**

可视化管理任务在工作区中读取与产生的文件，清晰展示目录层级、文件类型与体积大小，方便在会话之间复用中间产物。

![image-20260826201931458](https://oss-img.xerrors.cn/github/image-20260826201931458.png)


**HTML、PDF、图表与代码在线预览**

智能体生成的报告文档、可视化 HTML 网页、图片图表或代码脚本，无需下载即可直接在浏览器内置的预览器中渲染并进行交互查看。markdown 还支持在线编辑保存。

![image-20260826202142495](https://oss-img.xerrors.cn/github/image-20260826202142495.png)


**对话中的文件交付**

任务执行完毕后，对话气泡中会生成结构化的交付卡片，直观呈现文件摘要、格式与操作按钮，支持直接打开预览或保存到本地。

![image-20260826202412695](https://oss-img.xerrors.cn/github/image-20260826202412695.png)


</details>

### 06 · 团队治理与运行管理

专为企业和团队多人协作打造。管理员可以集中管理成员与部门组织架构、统一配置模型接入凭据与 API Key，并通过监控看板查看平台的运行状况与调用指标。

- 支持按用户、部门和共享范围配置知识库、Agent 等资源的访问与管理权限。
- 集中配置和调度多供应商的大模型能力，统一管理 API Key 凭据。
- 实时统计分析使用量、请求趋势与资源负载，辅助运行维护。

![image-20260826201755760](https://oss-img.xerrors.cn/github/image-20260826201755760.png)

<details>
<summary><strong>展开详细截图：权限、模型与运行数据</strong></summary>

**用户、部门与细粒度权限体系**

通过用户、部门和资源共享范围控制知识库、智能体等资源的访问与管理权限；沙盒工作区按用户隔离。

![image-20260826201638124](https://oss-img.xerrors.cn/github/image-20260826201638124.png)


**模型供应商与统一凭据管理**

支持接入主流大模型供应商（OpenAI、Anthropic、DeepSeek、Qwen 及 OpenAI 兼容自建端点如 vLLM 等），集中维护 API Key 凭据并统一分配模型能力，密钥对普通成员完全脱敏。

![模型供应商与模型能力](https://oss-img.xerrors.cn/github/image-20260825152458034.png)

**Dashboard 与系统运行监控**

运维数据看板展示会话、模型和工具调用、Token 消耗以及 VM 状态等统计，为容量规划和成本核算提供数据支撑。

![image-20260826201346254](https://oss-img.xerrors.cn/github/image-20260826201346254.png)

![image-20260826201430096](https://oss-img.xerrors.cn/github/image-20260826201430096.png)

</details>

## 参与贡献

欢迎提交 Issue、改进文档、修复 Bug 和贡献功能。开发流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，完整规范见 [文档参与指南](docs/develop-guides/contributing.md)。

感谢所有贡献者的支持！

<a href="https://github.com/xerrors/Yuxi/contributors">
  <img src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=100&columns=10" />
</a>

---

## 许可证

Yuxi 本体采用 MIT License，详见 [LICENSE](LICENSE)。Docker Compose 引入的第三方组件遵循各自的许可证；再分发和商业部署前，请按实际镜像版本核对上游许可和源码义务，相关边界见[生产部署指南](docs/advanced/deployment.md)。

Yuxi 的实现和文档参考了以下优秀的开源项目：

- [LightRAG](https://github.com/HKUDS/LightRAG)：早期图谱构建和检索思路；
- [DeepAgents](https://github.com/langchain-ai/deepagents)：深度智能体框架；
- [DeerFlow](https://github.com/bytedance/deer-flow)：沙盒智能体架构思路；
- [RAGFlow](https://github.com/infiniflow/ragflow)：文档分块策略；
- [LangGraph](https://github.com/langchain-ai/langgraph)：智能体编排基础；
- [QwenPaw](https://github.com/agentscope-ai/QwenPaw)：模型配置和个人文件区域设计。

## Star History

<a href="https://www.star-history.com/?repos=xerrors%2Fyuxi&type=date&legend=bottom-right">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=xerrors/yuxi&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=xerrors/yuxi&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=xerrors/yuxi&type=date&legend=top-left" />
 </picture>
</a>


[![给 Yuxi 一个 Star](https://oss-img.xerrors.cn/posts/2026/08/20260818-184409-image-da91658b.png)](https://github.com/xerrors/Yuxi)
