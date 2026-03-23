# 开发路线图

路线图可能会经常变更，如果有强烈的建议，可以在 [issue](https://github.com/xerrors/Yuxi-Know/issues) 中提。


### 看板

- 集成 LangFuse (观望) 添加用户日志与用户反馈模块，可以在 AgentView 中查看信息
- 部分场景应该使用默认模型作为默认值而不是空值
- 检索测试中，添加问答
- 集成 Memory，基于 deepagents 的文件后端实现
- 添加自定义向量模型和 rerank 模型的配置，在网页上面
- 调研轻便的文件展示与编辑器

### Bugs
- 部分异常状态下，智能体的模型名称出现重叠[#279](https://github.com/xerrors/Yuxi-Know/issues/279)
- 部分 local 的 mcp server 无法正常加载，但是建议在项目外部启动 mcp 服务器，然后通过 sse 的方式使用。【未复现】
- DeepSeek 官方接口适配会出现问题
- 部分推理后端与 langchain 的适配有问题：
- 目前的知识库的图片存在公开访问风险
- 工具传递给模型的时候，使用英文，但部分模型不支持中文函数名（如gpt-4o-mini，deepseek 官方接口）
- 生成基准测试会把所有的向量都计算一遍不合理

## v0.6

<!-- 添加到这里 -->

- 新增 API Key 管理功能，支持外部系统通过 API Key 调用 Agent 对话接口（`POST /api/chat/agent/{agent_id}`）。统一使用 `Authorization: Bearer <api_key>` 认证，API Key 以 `yxkey_` 开头。获取 API Key 即代表拥有绑定用户的所有接口访问权限。
- 将 后端代码 和 agents 解耦，agents 作为单独的 package 使用
- 添加 subagents

## v0.5

### 新增

- 优化 OCR 体验并新增对 Deepseek OCR 的支持
- 优化 RAG 检索，支持根据文件 pattern 来检索（Agentic Mode）
- 重构智能体对于“工具变更/模型变更”的处理逻辑，无需导入更复杂的中间件
- 重构知识库的 Agentic 配置逻辑，与 Tools 解耦
- 将工具与知识库解耦，在 context 中就完成解耦，虽然最终都是在 Agent 中的 get_tools 中获取
- 优化chunk逻辑，移除 QA 分割，集成到普通分块中，并优化可视化逻辑
- 重构知识库处理逻辑，分为 上传—解析—入库 三个阶段
- 重构 MCP 相关配置，使用数据库来控制 [#469](https://github.com/xerrors/Yuxi-Know/pull/469)
- 使用 docling 解析 office 文件（docx/xlsx/pptx）
- 优化后端的依赖，减少镜像体积 [#428](https://github.com/xerrors/Yuxi-Know/issues/428)
- 优化 liaghtrag 的知识库调用结果，提供 content/graph/both 多个选项
- 优化数据库查询工具，可通过设计环境变量添加描述，让模型更好的调用
- 优化任务组件，改用 postgresql 存储，并新增删除任务的接口
- 支持更多类型的文档源的导入功能（支持后端配置的白名单的 URL 导入）

### 修复

- 修复知识图谱上传的向量配置错误，并新增模型选择以及 batch size 选择
- 修复部分场景下获取工具列表报错 [#470](https://github.com/xerrors/Yuxi-Know/pull/470)
- 修改方法备注信息 [#478](https://github.com/xerrors/Yuxi-Know/pull/478)
- 修复多次 human-in-the-loop 的渲染解析问题 [#453](https://github.com/xerrors/Yuxi-Know/issues/453) [#475](https://github.com/xerrors/Yuxi-Know/pull/475)

## v0.4

### 新增
- 新增对于上传附件的智能体中间件，详见[文档](https://xerrors.github.io/Yuxi-Know/advanced/agents-config.html#%E6%96%87%E4%BB%B6%E4%B8%8A%E4%BC%A0%E4%B8%AD%E9%97%B4%E4%BB%B6)
- 新增多模态模型支持（当前仅支持图片），详见[文档](https://xerrors.github.io/Yuxi-Know/advanced/agents-config.html#%E5%A4%9A%E6%A8%A1%E6%80%81%E5%9B%BE%E7%89%87%E6%94%AF%E6%8C%81)
- 新建 DeepAgents 智能体（深度分析智能体），支持 todo，files 等渲染，支持文件的下载。
- 新增基于知识库文件生成思维导图功能（[#335](https://github.com/xerrors/Yuxi-Know/pull/335#issuecomment-3530976425)）
- 新增基于知识库文件生成示例问题功能（[#335](https://github.com/xerrors/Yuxi-Know/pull/335#issuecomment-3530976425)）
- 新增知识库支持文件夹/压缩包上传的功能（[#335](https://github.com/xerrors/Yuxi-Know/pull/335#issuecomment-3530976425)）
- 新增自定义模型支持、新增 dashscope rerank/embeddings 模型的支持
- 新增文档解析的图片支持，已支持 MinerU Officical、Docs、Markdown Zip格式
- 新增暗色模式支持并调整整体 UI（[#343](https://github.com/xerrors/Yuxi-Know/pull/343)）
- 新增知识库评估功能，支持导入评估基准或者自动构建评估基准（目前仅支持Milvus类型知识库）详见[文档](https://xerrors.github.io/Yuxi-Know/intro/evaluation.html)
- 新增同名文件处理逻辑：遇到同名文件则在上传区域提示，是否删除旧文件
- 新增生产环境部署脚本，固定 python 依赖版本，提升部署稳定性
- 优化图谱可视化方式，统一图谱数据结构，统一使用基于 G6 的可视化方式，同时支持上传带属性的图谱文件，详见[文档](https://xerrors.github.io/Yuxi-Know/intro/knowledge-base.html#_1-%E4%BB%A5%E4%B8%89%E5%85%83%E7%BB%84%E5%BD%A2%E5%BC%8F%E5%AF%BC%E5%85%A5)
- 优化 DBManager / ConversationManager，支持异步操作
- 优化 知识库详情页面，更加简洁清晰，增强文件下载功能

### 修复
- 修复重排序模型实际未生效的问题
- 修复消息中断后消息消失的问题，并改善异常效果
- 修复当前版本如果调用结果为空的时候，工具调用状态会一直处于调用状态，尽管调用是成功的
- 修复检索配置实际未生效的问题

### 破坏性更新

- 移除 Chroma 的支持，当前版本标记为移除
- 移除模型配置预设的 TogetherAI


## v0.3
### Added
- 添加测试脚本，覆盖最常见的功能（已覆盖API）
- 新建 tasker 模块，用来管理所有的后台任务，UI 上使用侧边栏管理。Tasker 中获取历史任务的时候，仅获取 top100 个 task。
- 优化对文档信息的检索展示（检索结果页、详情页）
- 优化全局配置的管理模型，优化配置管理
- 支持 MinerU 2.5 的解析方法 <Badge type="info" text="0.3.5" />
- 修改现有的智能体Demo，并尽量将默认助手的特性兼容到 LangGraph 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 中
- 基于 create_agent 创建 SQL Viewer 智能体 <Badge type="info" text="0.3.5" />
- 优化 MCP 逻辑，支持 common + special 创建方式 <Badge type="info" text="0.3.5" />
- LightRAG 知识库应该可以支持修改 LLM

### Fixed
- 修复本地知识库的 metadata 和 向量数据库中不一致的情况。
- v1 版本的 LangGraph 的工具渲染有问题
- upload 接口会阻塞主进程
- LightRAG 知识库查看不了解析后的文本，偶然出现，未复现
- 智能体的加载状态有问题：（1）智能体加载没有动画；（2）切换对话和加载中，使用同一个loading状态。
- 前端工具调用渲染出现问题
- 当前 ReAct 智能体有消息顺序错乱的 bug，且不会默认调用工具
- 修复文件管理：（1）文件选择的时候会跨数据库；（2）文件校验会算上失败的文件；
