# 中间件系统

中间件是 Yuxi 扩展智能体运行行为的主要机制。它工作在 LangGraph Agent 的模型调用、工具调用、状态更新和文件系统访问路径上，用来把知识库、Skills、附件、子智能体、上下文压缩和运行观测接入同一条执行链路。

内置 `ChatbotAgent` 与 `SubAgentBackend` 都会在 `get_graph()` 中构建中间件列表。运行前的资源过滤不再依赖旧版运行时配置中间件，而是在创建 Graph 前由 `prepare_agent_runtime_context` 完成。

## 运行时准备

运行时准备不是中间件，但它决定后续中间件能看到什么资源。内置 Agent 创建 Graph 前会先执行以下步骤：

- `prepare_agent_runtime_context`：按当前用户权限过滤工具、知识库、MCP、Skills 和子智能体，并派生 `_visible_knowledge_bases`、`_prompt_skills`、`_readable_skills`
- `build_prompt_with_context`：基于 Context 生成系统提示词
- `load_chat_model(context.model)`：加载主模型
- `resolve_configured_runtime_tools(context)`：加载已配置的内置工具和 MCP 工具

这意味着中间件不负责重新判断“用户是否能访问某个资源”。它们消费的是已经归一化后的 runtime context。

## 内置中间件链路

当前内置 `ChatbotAgent` 的中间件顺序如下：

| 中间件 | 作用 |
| --- | --- |
| `create_agent_filesystem_middleware` | 接入沙盒文件系统、用户工作区、线程 uploads/outputs 与只读 Skills 路由，并在工具结果过大时把内容写入 `outputs/large_tool_results` |
| `save_attachments_to_fs` / `AttachmentMiddleware` | 从 LangGraph state 的 `uploads` 读取附件路径，把可读路径注入系统提示，提示模型按需使用 `read_file` |
| `SkillsMiddleware` | 注入可见 Skill 的提示段，监听读取 `SKILL.md` 后的 Skill 激活，并按依赖追加工具和 MCP 工具；知识库工具由内置 `knowledge-base` Skill 按需加载 |
| `YuxiSubAgentMiddleware` | 仅主 Agent 在存在可见子智能体时挂载，提供 `task` 工具调用真实子 Agent graph |
| `YuxiSummarizationMiddleware` | 基于 DeepAgents `SummarizationMiddleware` 做长上下文压缩，并清洗被摘要历史里的工具结果 |
| `TodoListMiddleware` | 提供待办状态，让前端状态面板可展示 Agent 运行进度 |
| `PatchToolCallsMiddleware` | 修正部分工具调用消息形态，提升工具调用兼容性 |
| `ModelRetryMiddleware` | 在模型调用失败时按配置重试 |
| `ImageInputCompatibilityMiddleware` | 仅为 OpenAI Chat Completions 兼容链路桥接 `read_file` 返回的图片；模型明确拒绝图片输入时自动改为 `ocr_parse_file` |
| `TokenUsageMiddleware` | 在 LangGraph state 写入本轮 token 使用快照，供前端状态面板查看 |

`SubAgentBackend` 使用同一组核心能力，但不会挂载 `YuxiSubAgentMiddleware`，并额外过滤 `present_artifacts`、`ask_user_question`、`install_skill` 等不适合子智能体直接使用的工具。

## 知识库工具

知识库访问能力沉淀为内置 `knowledge-base` Skill。Agent 读取 `/home/gem/skills/knowledge-base/SKILL.md` 激活该 Skill 后，`SkillsMiddleware` 会按依赖追加 `list_kbs`、`query_kb`、`find_kb_document`、`open_kb_document`、`get_mindmap` 等知识库工具。

实际可见知识库仍由 `prepare_agent_runtime_context` 根据当前用户和 Agent 配置写入 `_visible_knowledge_bases`，工具执行时只会在这批知识库中检索。`context.knowledges` 是资源范围，不是 Skill 本身。

系统不会把知识库文件树挂进沙盒。Agent 访问知识库内容应使用 `query_kb`、`find_kb_document` 和 `open_kb_document`，而不是遍历 `/home/gem/kbs` 这类旧路径。

## Skills 注入与激活

`SkillsMiddleware` 分两步工作：

1. 模型调用前读取 `_prompt_skills`，把可见 Skill 的名称、描述和 `SKILL.md` 路径追加到系统提示。
2. 工具调用后检查模型是否读取了 `/home/gem/skills/<slug>/SKILL.md`。如果该 Skill 在 `_readable_skills` 范围内，就把它写入 `activated_skills`，并在后续模型调用中追加它声明的工具和 MCP 依赖。

这种设计让 Skill 可以先作为说明可见，只有模型真正读取并激活后才扩展工具集，避免一开始就把所有依赖工具塞进上下文。

## 附件与文件系统

附件上传后会先落盘到线程文件系统，并在 LangGraph state 中记录 `uploads`。`AttachmentMiddleware` 只把文件名和可读路径注入提示词，不会把文件内容整体塞进模型上下文。模型需要查看附件时，应通过 `read_file` 读取对应路径。

文件系统中间件负责把 sandbox backend、线程 uploads/outputs、用户工作区和只读 Skills 组合成 Agent 可访问的虚拟文件系统。普通 Agent 默认使用当前 `thread_id` 作为文件作用域；子智能体使用 child `thread_id` 做 checkpoint，同时沿用父线程的 uploads/outputs，并使用子 Agent 自己的 Skills 作用域。

## 子智能体任务

主 Agent 如果配置了可见子智能体，会挂载 `YuxiSubAgentMiddleware` 并获得 `task` 工具。这个工具不会调用旧版独立 SubAgents 表，而是查找 `agents.is_subagent=true` 且后端为 `SubAgentBackend` 的真实 Agent 配置，然后启动对应子 Agent graph。

子智能体执行时会获得独立 child thread、独立 checkpoint 和 `agent_runs(run_type=subagent)` 记录；工具结果会返回 child thread ID，后续可以把该 ID 传回 `task` 继续同一个子任务。子智能体自身不会再挂载下一层 `task` 中间件，避免形成嵌套子智能体链路。

## Summary 上下文压缩

长对话压缩由 Yuxi 封装的 `YuxiSummarizationMiddleware` 负责。它基于 DeepAgents 的 `SummarizationMiddleware`，但针对 Yuxi 的知识库检索和工具调用结果做了额外处理。

触发条件来自 Agent Context：

| 字段 | 说明 |
| --- | --- |
| `summary_threshold` | 上下文超过该 K token 阈值后触发摘要；L2 摘要模型的待摘要历史输入上限也使用同一阈值 |
| `summary_keep_messages` | 摘要后保留最近消息数 |
| `summary_prompt` | 摘要模型使用的提示词 |
| `summary_tool_result_token_limit` | 工具结果 offload 阈值和预览 token 上限 |
| `summary_l2_trigger_ratio` | L1 后进入 L2 summary 的触发比例，建议 `0.1~1.0`，默认 `0.4` |

触发判断使用 Yuxi 自己的近似 token 计算结果，不使用模型返回的 `usage_metadata.total_tokens` 作为触发依据，避免 provider 的计费口径、累计口径或异常上报导致短对话过早压缩。

触发后，中间件先执行 L1 结构精简：在本次模型调用的临时消息视图里截断旧 `write_file`/`edit_file` 工具调用的大参数；`ToolMessage.content` 估算 token 数超过 `summary_tool_result_token_limit` 时，会写入当前 Agent 可见的 `outputs/large_tool_results`，消息内替换为工具名、近似 token 数、完整结果路径和不超过同一 token 上限的预览。未超过该上限的工具结果保持原样。这个步骤不修改 LangGraph state 中的原始消息。

L1 后会重新计算上下文大小；如果仍超过入口阈值乘以 `summary_l2_trigger_ratio`，才进入 L2 summary，把较早的 L1 视图消息压缩成一条 summary message，并保留最近窗口内的原始消息。比例越小越容易进入 L2；`1.0` 表示 L1 后仍超过原始触发阈值才进入 L2。L2 传给摘要模型的待摘要历史上限等于 `summary_threshold` 对应的 token 数，避免用过小的固定窗口丢掉早期关键信息。L2 不再对工具结果做第二轮 offload，只写入 `_summarization_event`，后续调用仍由 DeepAgents 的 cutoff 语义重建 effective messages。

这对知识库检索尤其重要：`query_kb`、`open_kb_document`、`find_kb_document` 等工具可能返回较长的片段、引用和文档内容。Summary 阶段保留“查过什么、结果在哪里、关键预览是什么”，同时避免把大量检索原文反复卷入摘要，减少上下文污染和 token 压力。压缩开始、完成或失败会以 `context_compression` 流事件同步到前端；摘要模型自身的 token 流不会作为聊天消息输出。

未达到入口阈值的常规模型调用不会额外清洗工具结果；达到入口阈值但 L1 后低于 L2 门槛时，会直接用 L1 精简后的临时视图调用模型，不生成 summary event。

## 自定义中间件

新增中间件时，将实现放入 `backend/package/yuxi/agents/middlewares`，再在具体 Agent 的 `get_graph()` 中加入 `middleware` 列表。新增前先确认它属于哪一种职责：

- 资源过滤、权限收敛和默认资源选择应放在 `prepare_agent_runtime_context` 一类的 Graph 创建前逻辑中。
- 模型提示注入、工具动态追加、工具结果处理和 state 更新适合做成 LangChain Agent middleware。
- 文件读写、工具结果卸载和 artifacts 展示应优先复用 `create_agent_filesystem_middleware` 与沙盒 backend。

仓库中仍保留 `DynamicToolMiddleware`，但当前内置 Agent 的工具和 MCP 加载已经由 `resolve_configured_runtime_tools(context)` 与 `SkillsMiddleware` 承担。新增功能时不要默认复用旧的动态工具中间件，除非确实需要“预注册后按请求筛选”的模式。
