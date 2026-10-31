![Yuxi: a self-hosted, multi-tenant knowledge agent platform](https://oss-img.xerrors.cn/posts/2026/08/20260818-151118-mac-1787037059154-8c08f48c.png)

Yuxi is a self-hosted, multi-tenant knowledge agent platform. It brings knowledge base retrieval, knowledge graphs, LangGraph multi-agent orchestration, MCP/Skills, sandbox tools, and access control into one workspace.

[![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
[![Release](https://img.shields.io/github/v/release/xerrors/Yuxi?color=046A82)](https://github.com/xerrors/Yuxi/releases/latest)
[![License](https://img.shields.io/github/license/xerrors/Yuxi.svg?logo=github)](https://github.com/xerrors/Yuxi/blob/main/LICENSE)
[![DeepWiki](https://img.shields.io/badge/DeepWiki-blue.svg)](https://deepwiki.com/xerrors/Yuxi)
[![Bilibili](https://img.shields.io/badge/Knowledge_Base_Demo-00A1D6?logo=bilibili&logoColor=fff)](https://www.bilibili.com/video/BV1erE26iEgv/)

<a href="https://trendshift.io/repositories/24335" target="_blank"><img src="https://trendshift.io/api/badge/repositories/24335" alt="xerrors%2FYuxi | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

[Project home](https://xerrors.github.io/Yuxi/) · [Quick start](https://xerrors.github.io/Yuxi/intro/quick-start) · [Demo video](https://www.bilibili.com/video/BV1erE26iEgv/) · [Releases](https://github.com/xerrors/Yuxi/releases) · [中文](README.md)

## What Yuxi Can Do

Yuxi is built for teams that need control over their data, models, and permissions:

- **Build knowledge-based Q&A**: Upload documents, parse and chunk them, build vector indexes, and let agents answer questions using retrieved content.
- **Execute multi-step tasks**: Combine tools, MCP, Skills, sub-agents, and sandboxes to produce previewable, downloadable files.
- **Connect knowledge graphs**: Extract entities and relationships from document chunks in Milvus knowledge bases, store them in Neo4j, and use them in retrieval.
- **Manage team access**: Control access to knowledge bases, agents, Skills, and models by user, department, and sharing scope.
- **Compare execution quality**: Evaluate knowledge base retrieval results. Complete agent evaluation can be built through the Public API; see [agent evaluation](docs/agents/agent-evaluation.md).

## Tech Stack

| Layer | Technologies |
| --- | --- |
| Frontend | Vue 3 · Vite · Ant Design · G6 |
| Backend | FastAPI · LangGraph · ARQ worker |
| Storage | PostgreSQL · Redis · MinIO · Milvus · Neo4j |
| Document processing | MinerU · PaddleOCR · RapidOCR |
| Deployment | Docker Compose |

## Quick Start

Yuxi starts with Docker Compose and the Container sandbox by default. Each agent run uses a temporary container while workspace files persist. No separate KVM node is required.

### Prerequisites

Install Git, [Docker Engine](https://docs.docker.com/get-docker/), and Docker Compose v2, and prepare a working LLM API. On macOS or Windows, use Docker Desktop in Linux container mode and allow access to the project directory.

This guide uses the `v0.8.0.beta6` beta release. Version 0.8 requires a fresh deployment with separate data directories and provides no automatic upgrade of older databases, indexes, or file layouts. See the [0.8.0 release notes](docs/develop-guides/releases/0.8.0.md).

Container shares the host kernel and suits local evaluation and trusted single-tenant workloads. For VM isolation or persistent user environments, select Cube in the [quick start](docs/intro/quick-start.md) and prepare a Linux/KVM execution node.

### 1. Get the Code and Initialize

```bash
git clone --branch v0.8.0.beta6 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi
./scripts/init.sh
```

On Windows, run `.\scripts\init.ps1` in PowerShell. The script prompts for a SiliconFlow API Key, generates independent application secrets, a runner authentication token, and an instance identifier, then prepares base images. Configure other model providers after signing in. Keep existing secrets when reusing `.env`; to switch to Container, set `SANDBOX_BACKEND=container` and rerun initialization.

### 2. Start Services

```bash
docker compose --profile container config --quiet
docker compose --profile container up --build -d
```

The `container` profile builds the sandbox image and starts its runner. Sandbox networking is disabled by default; see [Container sandbox](docs/advanced/container-sandbox.md) for configuration and limitations.

### 3. Verify the Deployment

```bash
docker compose --profile container ps
curl --fail http://localhost:5050/api/system/ready
```

Once `status` is `ready`, open [http://localhost:5173](http://localhost:5173), initialize the superadmin account, and sign in. Ask an agent to create `hello.txt`, then verify its contents in the workspace; see the [quick start](docs/intro/quick-start.md) for the full check. API documentation is available at [http://localhost:5050/docs](http://localhost:5050/docs).

## Documentation

- [Project overview](https://xerrors.github.io/Yuxi/intro/project-overview): Capabilities, concepts, and system boundaries.
- [Quick start](https://xerrors.github.io/Yuxi/intro/quick-start): Choose a sandbox and start a development environment.
- [Command-line client](docs/intro/cli.md): Sign in, chat, send attachments, and query knowledge bases.
- [Public Agent API quick start](docs/intro/agents-api-quickstart.md): Create sessions, submit messages, and read results from external applications.
- [Model configuration](https://xerrors.github.io/Yuxi/intro/model-config): Connect chat, embedding, and rerank models.
- [Knowledge base tutorial](https://xerrors.github.io/Yuxi/intro/knowledge-base): Create knowledge bases and verify retrieval.
- [Agent development](https://xerrors.github.io/Yuxi/agents/agents-config): Configure agents, tools, and extensions.
- [Production deployment](https://xerrors.github.io/Yuxi/advanced/deployment): Deployment, upgrades, backups, and troubleshooting.
- [Changelog](https://xerrors.github.io/Yuxi/develop-guides/changelog): Published changes.

## Feature Showcase

Yuxi connects knowledge ingestion, agent execution, and team governance into one workflow. The following six modules introduce its core capabilities:

| Module | What It Solves | Key Capabilities |
| --- | --- | --- |
| Unified agent workspace | Ask questions, execute tasks, and receive deliverables in one interface | Multi-turn conversations, knowledge retrieval, task status, human approval |
| Knowledge bases and RAG | Provide retrieved context for answers | Multi-format ingestion, Embedding/Rerank, retrieval testing, RAG evaluation |
| Knowledge graphs and mind maps | Discover entity relationships and browse knowledge base file structures | Graph construction, subgraph exploration, node details, file metadata maps |
| Multi-agent execution and extensions | Delegate complex tasks to specialized roles and tools | SubAgents, Skills, MCP, Tools, agent configuration |
| Sandbox workspace and artifacts | Turn conversations into reusable files | Isolated file systems, file generation, online preview, downloads |
| Team governance and operations | Manage capabilities, permissions, and execution in multi-user environments | Multi-tenancy, user and department permissions, model configuration, API Keys, Dashboard |

### 01 · Unified Agent Workspace

Reference knowledge base documents, personal files, or extension Skills in the same chat interface. View task execution status and receive answers or generated files directly in the conversation.

- Use `@` to quickly reference knowledge bases, files, and specific Skills.
- View task breakdowns, tool execution status, and context token usage.
- Preview and download generated artifacts.

![Yuxi unified agent workspace](https://oss-img.xerrors.cn/github/image-20260825145022410.png)

<details>
<summary><strong>More screenshots: conversations, execution status, and human approval</strong></summary>

**Long-running task status and execution tracking**

While complex tasks run asynchronously in the background, the interface shows the agent's reasoning progress, plans, subtask status, and tool logs in real time, making long-running work visible rather than leaving users waiting without feedback.

![Long-running task tracking](https://oss-img.xerrors.cn/github/image-20260826195730155.png)

**Human approval and delivery cards**

The default approval mode requires confirmation for command execution and file writes outside the current Project; writes inside the Project proceed without approval. Fully trusted mode disables these approvals. Delivery cards let users preview or save files when a task finishes.

![Human approval and file delivery](https://oss-img.xerrors.cn/github/image-20260825163940350.png)

</details>

### 02 · Knowledge Bases and RAG

Manage team materials centrally and parse them into structured knowledge bases. Agents can retrieve relevant document chunks as context for answering questions.

- Manage files and directories centrally, with live parsing progress, chunk details, and token statistics.
- Configure Embedding and Rerank algorithms, and test and tune multiple retrieval paths directly in the management interface.
- Use built-in RAG evaluation tools and real Q&A datasets to measure retrieval and answer quality.

![Knowledge base management](https://oss-img.xerrors.cn/github/image-20260830144756161.png)

<details>
<summary><strong>More screenshots: ingestion, retrieval, and evaluation</strong></summary>

**Document parsing and chunk management**

Support common formats including PDF, Word, PowerPoint, Excel, and Markdown. MinerU and PaddleOCR cloud services, optional self-hosted MinerU, and lightweight RapidOCR extract text, images, and tables for chunking and vector retrieval.

![Document ingestion and parsing status](https://oss-img.xerrors.cn/github/image-20260825155256725.png)

**Knowledge base types and external sources**

Alongside the built-in local vector knowledge base, Yuxi connects to external knowledge services such as Dify and Notion. A unified retriever makes them available to agents.

![Knowledge base types](https://oss-img.xerrors.cn/github/image-20260825155356171.png)

**Retrieval testing and reranking**

An interactive retrieval testing workspace lets you enter a query and inspect initial embedding similarity scores, hybrid retrieval results, and score changes after reranking, making recall quality easier to verify.

![Retrieval testing and reranking](https://oss-img.xerrors.cn/github/image-20260830144827319.png)

**RAG evaluation**

Build benchmark Q&A datasets and run batch retrieval recall evaluations. With reference answers and a judge model, evaluate answer correctness to identify knowledge gaps and configuration weaknesses.

![RAG evaluation](https://oss-img.xerrors.cn/github/image-20260830144852289.png)

Automatically generate single-hop and multi-hop Q&A pairs.

![Q&A generation](https://oss-img.xerrors.cn/github/image-20260826200055381.png)

![Multi-hop Q&A generation](https://oss-img.xerrors.cn/github/image-20260830144919806.png)

![Evaluation results](https://oss-img.xerrors.cn/github/image-20260830144950126.png)

</details>

### 03 · Knowledge Graphs and Mind Maps

Turn unstructured documents into entity–relationship networks. Explore relationships in interactive graphs, or generate knowledge maps from file hierarchies and topic metadata.

- Automatically extract entities and relationships from knowledge bases and build graph indexes using Milvus/Neo4j.
- Search entities by keyword, click nodes to inspect properties, and highlight related subgraphs.
- Generate multi-level knowledge maps from knowledge base file metadata to browse a business domain at a glance.

![Knowledge graph exploration](https://oss-img.xerrors.cn/github/image-20260830145027082.png)

<details>
<summary><strong>More screenshots: graph construction, node relationships, and mind maps</strong></summary>

**Graph construction and index status**

After an administrator submits a build job for indexed Milvus document chunks, the system extracts entities and relationships and writes them to Neo4j. Document parsing does not automatically start graph construction. View entity and relationship counts and background job progress.

![Graph construction and index status](https://oss-img.xerrors.cn/github/image-20260826200519930.png)

**Knowledge maps**

Generate structured topic mind maps from directory structures, category labels, and file metadata, making large knowledge collections easier to browse as a tree.

![Knowledge map](https://oss-img.xerrors.cn/github/image-20260830145125067.png)

</details>

### 04 · Multi-Agent Execution and Extensions

Combine models, prompts, knowledge bases, external tools, and specialized sub-agents in a single agent. For complex tasks, the main agent plans and delegates work while multiple SubAgents execute asynchronously in parallel. Skills and MCP provide additional capabilities.

- Configure the agent's base model, connected knowledge bases, tool access, and system prompt.
- Run multiple SubAgents asynchronously in parallel for deep research, data analysis, or content generation.
- Use native support for Skills extensions and MCP (Model Context Protocol).

![Yuxi multi-agent orchestration](https://oss-img.xerrors.cn/github/image-20260825152252874.png)

<details>
<summary><strong>More screenshots: agent configuration, sub-agents, and extensions</strong></summary>

**Agent configuration and behavior customization**

Modular configuration lets you combine models, knowledge bases, custom Tools, MCP services, initial prompts, and sub-agents. Configure sharing visibility within departments or teams as needed.

![Agent configuration](https://oss-img.xerrors.cn/github/image-20260825151729055.png)

**Parallel sub-agent execution**

The main agent can break a complex, multi-step task into independent assignments and dispatch specialized SubAgents in parallel—for example, researching regulations in different domains or writing separate report chapters. Their results are collected and summarized when they finish.

![Parallel sub-agent execution](https://oss-img.xerrors.cn/github/image-20260825151559976.png)

**Skills, MCP, and ecosystem extensions**

Connect and manage Skills and external MCP servers in one place, with role-specific permissions and access scopes. Progressive disclosure dynamically resolves and loads tools when they are needed. Online Skill installation supports skills.sh and the ModelScope community.

![Skills and MCP extensions](https://oss-img.xerrors.cn/github/image-20260830145227399.png)

View and edit Skills online.

![Online Skill editing](https://oss-img.xerrors.cn/github/image-20260830145217595.png)

Configure Skill permissions and dependencies.

![Skill permissions and dependencies](https://oss-img.xerrors.cn/github/image-20260830145359700.png)

Built-in tool list.

![Built-in tools](https://oss-img.xerrors.cn/github/image-20260826201207239.png)

</details>

### 05 · Sandbox Workspace and File Artifacts

Agents can read and write files through sandbox tools. Beyond answering questions in chat, agents can turn research and analysis into Markdown documents, spreadsheets, HTML pages, or executable code that users can view and download from the workspace.

- Limit the files tools can access through the sandbox.
- Generate illustrated reports, data analysis charts, web pages, and other artifacts.
- Preview supported file formats in the browser and download files as an archive.

![Yuxi sandbox workspace and artifacts](https://oss-img.xerrors.cn/github/image-20260825152123583.png)

<details>
<summary><strong>More screenshots: file management, online preview, and task delivery</strong></summary>

**Workspace file management**

Visually manage files read and produced in the workspace during task execution. Directory hierarchies, file types, and sizes are displayed clearly, making intermediate artifacts easier to reuse across conversations.

![Workspace file management](https://oss-img.xerrors.cn/github/image-20260826201931458.png)

**Online preview for HTML, PDF, charts, and code**

Render and interact with generated reports, HTML visualizations, images, charts, and scripts in the browser's built-in previewer without downloading them first. Markdown files can also be edited and saved online.

![Online file preview](https://oss-img.xerrors.cn/github/image-20260826202142495.png)

**File delivery in conversations**

When a task finishes, structured delivery cards appear in the conversation with file summaries, formats, and action buttons. Open a preview or save files locally.

![File delivery cards](https://oss-img.xerrors.cn/github/image-20260826202412695.png)

</details>

### 06 · Team Governance and Operations

Built for collaboration across teams and enterprises. Administrators centrally manage members and departments, configure model credentials and API Keys, and monitor platform operations and usage metrics through dashboards.

- Configure access and management permissions for resources such as knowledge bases and agents by user, department, and sharing scope.
- Centrally configure models from multiple providers and manage API Key credentials.
- Track usage, request trends, and resource load to support stable operations.

![Team governance and operations](https://oss-img.xerrors.cn/github/image-20260826201755760.png)

<details>
<summary><strong>More screenshots: permissions, models, and operational data</strong></summary>

**Users, departments, and fine-grained permissions**

User, department, and resource sharing scopes control access to and management of knowledge bases and agents. Sandbox workspaces are isolated by user.

![User and department permissions](https://oss-img.xerrors.cn/github/image-20260826201638124.png)

**Model providers and centralized credentials**

Connect providers including OpenAI, Anthropic, DeepSeek, Qwen, and OpenAI-compatible self-hosted endpoints such as vLLM. Maintain API Key credentials centrally and assign model capabilities while keeping secrets hidden from ordinary members.

![Model providers and capabilities](https://oss-img.xerrors.cn/github/image-20260825152458034.png)

**Dashboard and system monitoring**

Operational dashboards show session, model and tool calls, token usage, and VM statistics to support capacity planning and cost tracking.

![System dashboard](https://oss-img.xerrors.cn/github/image-20260826201346254.png)

![Usage and operational metrics](https://oss-img.xerrors.cn/github/image-20260826201430096.png)

</details>

## Contributing

Issues, documentation improvements, bug fixes, and new features are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and the [contribution guide](docs/develop-guides/contributing.md) for the full guidelines.

Thanks to all contributors for supporting this project!

<a href="https://github.com/xerrors/Yuxi/contributors">
  <img src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=100&columns=10" />
</a>

---

Yuxi's implementation and documentation draw on the following excellent open-source projects:

- [LightRAG](https://github.com/HKUDS/LightRAG): Early graph construction and retrieval ideas.
- [DeepAgents](https://github.com/langchain-ai/deepagents): Deep agent framework.
- [DeerFlow](https://github.com/bytedance/deer-flow): Sandbox agent architecture ideas.
- [RAGFlow](https://github.com/infiniflow/ragflow): Document chunking strategies.
- [LangGraph](https://github.com/langchain-ai/langgraph): Agent orchestration foundation.
- [QwenPaw](https://github.com/agentscope-ai/QwenPaw): Model configuration and personal file area design.

## License

Yuxi itself is licensed under the MIT License. See [LICENSE](LICENSE) for details. Third-party components included through Docker Compose retain their own licenses. Before redistribution or commercial deployment, check the upstream licenses and source-code obligations for the actual image versions you use; see the [production deployment guide](docs/advanced/deployment.md) for the relevant boundaries.

[![Give Yuxi a Star](https://oss-img.xerrors.cn/posts/2026/08/20260818-184409-image-da91658b.png)](https://github.com/xerrors/Yuxi)
