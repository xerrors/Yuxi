![Yuxi enterprise agent platform](.github/media/hero.svg)

**Yuxi is a self-hosted platform for building and running enterprise agents.** Teams can give an agent their internal documents, tools, and working instructions, then have an administrator share it with selected people or departments. Colleagues can use the same agent to look up sources they have permission to read or to produce reports, charts, and files in a project workspace.

<a href=".github/media/community-qr.png"><img align="left" width="104" src=".github/media/community-qr.png" alt="Scan to join the Yuxi WeChat community" /></a>

**[Watch the demo (Chinese)](https://www.bilibili.com/video/BV1erE26iEgv/) · [Run locally](#quick-start)** · [Documentation](https://xerrors.github.io/Yuxi/) · [中文](README.md)

<a href="https://trendshift.io/repositories/24335"><img src="https://trendshift.io/api/badge/repositories/24335" alt="Yuxi on Trendshift" width="170" /></a>

<br clear="left" />

![Food safety compliance analysis with findings in the conversation and two files in the output panel](https://oss-img.xerrors.cn/github/image-20260825145022410.png)

*In this food safety compliance review, the team can read the findings in the conversation and open the Markdown report or HTML compliance matrix in the panel on the right.* [Explore the screenshot gallery](#feature-showcase)

## What Yuxi Can Do

- **Answer questions using company knowledge.** Index policies, product manuals, or project documents. Readers can follow a citation back to the source to check an answer. External assistants can also query authorized sources through the read-only knowledge MCP service.
- **Deliver files from multi-step tasks.** Agents can delegate to sub-agents, call tools, and run commands in an isolated sandbox. Team members can review approval requests and preview, download, or reuse the resulting files in the project workspace.
- **Share agents across a team.** Capture a procedure as a Skill, select the agent’s model, sources, and tools, and set who can use it. Employees can work in the web app, while business applications can create sessions, submit tasks, and retrieve results through the Agents API.

## System Architecture

[![Yuxi system architecture, with labels in Chinese](.github/media/architecture.svg)](.github/media/architecture.svg)

<details>
<summary>Show the business and platform capability overview</summary>

![Yuxi enterprise agent platform: business applications, agent configuration and execution, knowledge services, governance, and enterprise resources (Chinese labels)](.github/media/business-architecture.svg)

</details>

## Quick Start

Install Docker with Compose v2 and get a [SiliconFlow API key](https://cloud.siliconflow.cn/i/Eo5yTHGJ) for initialization.

```bash
git clone --branch v0.8.0 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi
./scripts/init.sh
docker compose --profile container up --build -d
```

In Windows PowerShell, use `.\scripts\init.ps1` in place of `./scripts/init.sh`.

To try the [v0.8.1.beta1 prerelease](https://github.com/xerrors/Yuxi/releases/tag/v0.8.1.beta1), use `v0.8.1.beta1` as the clone tag. [Full installation guide](https://xerrors.github.io/Yuxi/intro/quick-start) · [Production deployment and isolation](docs/advanced/deployment.md)

## Feature Showcase

<details>
<summary>Conversations, task status, and approvals</summary>

Follow a running task and see the command an agent asks you to approve.

**Long-running task tracking**

![Long-running task tracking](https://oss-img.xerrors.cn/github/image-20260826195730155.png)

**Human approval and file delivery**

![Human approval and file delivery](https://oss-img.xerrors.cn/github/image-20260825163940350.png)

</details>

<details>
<summary>Knowledge bases, retrieval testing, and RAG evaluation</summary>

A self-hosted Milvus knowledge base supports parsing, chunking, and hybrid keyword and vector retrieval. Administrators can build a Neo4j knowledge graph from indexed chunks or compare retrieval results against a question set. Read-only connectors can also query existing Feishu, Dify, and Notion sources, subject to each connector’s capabilities.

**Knowledge base management**

![Knowledge base management](https://oss-img.xerrors.cn/github/image-20260830144756161.png)

**Document ingestion and parsing status**

![Document ingestion and parsing status](https://oss-img.xerrors.cn/github/image-20260825155256725.png)

**Knowledge base types**

![Knowledge base types](https://oss-img.xerrors.cn/github/image-20260825155356171.png)

**Retrieval testing and reranking**

![Retrieval test showing matched passages and reranking results](https://oss-img.xerrors.cn/github/image-20260830144827319.png)

**RAG evaluation**

![RAG evaluation](https://oss-img.xerrors.cn/github/image-20260830144852289.png)

**Q&A generation**

![Q&A generation](https://oss-img.xerrors.cn/github/image-20260826200055381.png)

**Multi-hop Q&A generation**

![Multi-hop Q&A generation](https://oss-img.xerrors.cn/github/image-20260830144919806.png)

**Evaluation results**

![Evaluation results](https://oss-img.xerrors.cn/github/image-20260830144950126.png)

[Document processing and OCR](docs/advanced/document-processing.md) · [Knowledge graphs](docs/advanced/knowledge-base-graph.md) · [Retrieval evaluation](docs/intro/evaluation.md)

</details>

<details>
<summary>Knowledge graphs and knowledge maps</summary>

Explore entities and their relationships in a knowledge graph, or use the knowledge map to browse files by topic.

**Knowledge graph exploration**

![Knowledge graph exploration](https://oss-img.xerrors.cn/github/image-20260830145027082.png)

**Graph construction and index status**

![Graph construction and index status](https://oss-img.xerrors.cn/github/image-20260826200519930.png)

**Knowledge map**

![Knowledge map](https://oss-img.xerrors.cn/github/image-20260830145125067.png)

</details>

<details>
<summary>Agent collaboration, Skills, and MCP tools</summary>

Sub-agents maintain separate conversation histories and exchange files through a shared project directory within a collaboration tree. Team members can inspect tool calls, answer questions, and review approval requests during a run. They can open generated files, such as an HTML brief, in the browser.

**Yuxi multi-agent orchestration**

![Yuxi multi-agent orchestration](https://oss-img.xerrors.cn/github/image-20260825152252874.png)

**Agent configuration**

![Agent configuration](https://oss-img.xerrors.cn/github/image-20260825151729055.png)

**Parallel sub-agent execution**

![Parallel sub-agent execution](https://oss-img.xerrors.cn/github/image-20260825151559976.png)

**Skills and MCP extensions**

![Skills and MCP extensions](https://oss-img.xerrors.cn/github/image-20260830145227399.png)

**Online Skill editing**

![Online Skill editing](https://oss-img.xerrors.cn/github/image-20260830145217595.png)

**Skill permissions and dependencies**

![Skill permissions and dependencies](https://oss-img.xerrors.cn/github/image-20260830145359700.png)

**Built-in tools**

![Built-in tools](https://oss-img.xerrors.cn/github/image-20260826201207239.png)

[Session collaboration](docs/agents/session-cooperation.md) · [Managing Skills](docs/agents/skills-management.md) · [Connecting MCP tools](docs/agents/mcp-integration.md)

</details>

<details>
<summary>Sandbox workspace, file previews, and delivery</summary>

Open a generated brief, inspect its contents, and save the delivered file from the conversation.

**Yuxi sandbox workspace and artifacts**

![Workspace preview of a generated DeepAgents release brief](https://oss-img.xerrors.cn/github/image-20260825152123583.png)

**Workspace file management**

![Workspace file management](https://oss-img.xerrors.cn/github/image-20260826201931458.png)

**Online file preview**

![Online file preview](https://oss-img.xerrors.cn/github/image-20260826202142495.png)

**File delivery cards**

![File delivery cards](https://oss-img.xerrors.cn/github/image-20260826202412695.png)

</details>

<details>
<summary>Team access, model configuration, and usage</summary>

Administrators can grant access and management rights by user or department, configure model providers and credentials, and review model, tool, and token usage. When teammates use a shared agent, retrieval is still scoped to each user’s permissions and the knowledge bases selected for the session.

**Team governance and operations**

![Team governance and operations](https://oss-img.xerrors.cn/github/image-20260826201755760.png)

**User and department permissions**

![Department and user directories in the administration panel](https://oss-img.xerrors.cn/github/image-20260826201638124.png)

**Model providers and capabilities**

![Model providers and capabilities](https://oss-img.xerrors.cn/github/image-20260825152458034.png)

**System dashboard**

![System dashboard](https://oss-img.xerrors.cn/github/image-20260826201346254.png)

**Usage and operational metrics**

![Usage and operational metrics](https://oss-img.xerrors.cn/github/image-20260826201430096.png)

[Resource permissions](docs/mechanisms/resource-permissions.md) · [Model configuration](docs/intro/model-config.md) · [Usage tracking](docs/advanced/model-usage-tracking.md)

</details>

## Documentation

| Task | Where to start |
| --- | --- |
| Build an agent for a team | [Configure and share agents](docs/agents/agents-config.md) · [Develop an agent backend](docs/agents/agent-backend-development.md) |
| Embed an agent in an application | [Agents API quick start](docs/intro/agents-api-quickstart.md) · [CLI](docs/intro/cli.md) · [Local API reference](http://localhost:5050/docs) |
| Query company knowledge from an external assistant | [Knowledge MCP service](docs/advanced/knowledge-mcp.md) · [API keys](docs/advanced/api-key-integration.md) |
| Plan a production rollout | [Deployment](docs/advanced/deployment.md) · [Sandbox isolation](docs/mechanisms/sandbox.md) · [Concurrency and capacity](docs/advanced/agent-concurrency-capacity.md) |
| Get oriented in the codebase or investigate an issue | [Find code by task](docs/mechanisms/index.md#从一个任务开始) · [Architecture and execution flow](ARCHITECTURE.md) |

[Project overview](docs/intro/project-overview.md) · [Changelog](docs/develop-guides/changelog.md) · [DeepWiki](https://deepwiki.com/xerrors/Yuxi)

## Contributing

Issues, documentation improvements, bug fixes, and new features are welcome. See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for the development workflow and the [contribution guide](docs/develop-guides/contributing.md) for the full guidelines.

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

[![Give Yuxi a Star](https://oss-img.xerrors.cn/github/yuxi-star-cta-c3ec1417.svg)](https://github.com/xerrors/Yuxi)
