# 主/子智能体协作机制调研：现状与 OpenAI Codex 对比（初稿）

> 本文是 2026-10-03 的初步调研分析，**未修改任何代码，不构成决策记录**。若后续推进，应走 spec-loop 形成提案与决策。源码结论均经直接读码或抽查核验（引用格式 `file:line`，以当前 develop/1.0 工作区为准）。OpenAI Responses API 部分来自官方文档（高可信），Codex CLI 产品细节部分来自第三方资料（已标注，建议使用前对照 openai/codex 仓库核实）。
>
> 调研动机：评估假设——「子智能体应该和主智能体拥有完全相同的功能，只不过它由主智能体调用，形成一个新的分叉树」。

## 一、当前现状：Yuxi 主/子智能体交互全景

### 1.1 实体模型：数据层已经同构

- SubAgent 与主 Agent 共用同一张 `agents` 表，仅以 `is_subagent` 布尔列区分（`backend/yuxi/modules/agents/models/definitions.py:62`）。子智能体是「一级智能体」，有独立 slug、配置与管理入口（`docs/agents/session-cooperation.md`）。
- 预置子智能体（general-purpose / web-search / research-explorer / fact-verifier）与普通 agent 走同一 preset 机制（`backend/yuxi/modules/agents/presets/subagents/`），全部挂 `SubAgentBackend`。
- 主 agent 通过 `config_json.context.subagents` 声明可调用的子 agent 白名单（`presets/deep_research.py:24`），候选列表按用户可见性过滤（`runtime/context.py:470-475`）。
- 子任务拥有**完全独立**的 Thread / Turn / Run / LangGraph checkpoint，通过 `SubagentThread` 委派关系（`parent_session_record_id` / `child_thread_id` / `created_by_run_id` 等，`models/threads.py:78-98`）关联父 Run。子线程 ID 由 `hash(parent_thread:slug:tool_call_id)` 确定性派生（`services/subagents.py:286-291`）。

**结论：在数据模型与生命周期层，子智能体与主智能体已经是同构的**——同表、同 Input/Turn/Run 状态机、同 ARQ 执行器（`run_type="subagent"` 走与 `chat` 相同的 `stream_agent_chat`，`services/runner.py:780-793`）、同 PostgreSQL checkpointer、同租约与恢复机制。

### 1.2 调用通道：四个工具构成的异步委派协议

中间件 `YuxiSubAgentMiddleware`（`runtime/middlewares/subagent_task.py`）为主 agent 装配四个工具：

| 工具 | 行为 | 关键实现 |
|---|---|---|
| `subagent_start` | 立即返回，不阻塞；校验白名单与父 Run 状态，创建子 Thread 委派关系，持久化 Input 并投递 ARQ | `subagent_task.py:139-186`；`services/subagents.py:227-329` |
| `subagent_status` | 查询单个子 Run 快照（status/output/error/token_usage） | `subagent_task.py:221-222`；`subagents.py:111-150` |
| `subagent_cancel` | 取消单个子 Run | `subagents.py:200-216` |
| `subagent_await` | 归属校验后轮询 PostgreSQL（0.5s 间隔）直至终态，上限 30 分钟，超时返回快照并附 `wait_timed_out` | `subagent_task.py:259-302`；`subagents.py:186-197` |

派发路径与普通用户输入完全一致。`accept_locked` 持久化 Input（幂等 receipt）→ owning transaction 提交 → ARQ 投递 → scheduler 领取时才建 Turn/Run（`run_type="subagent"`、`created_by_run_id` 记录委派链，`services/scheduler.py:59-84`）。这意味着子任务天然享有持久化、恢复、租约与失联收敛的全部基础设施。

### 1.3 上下文传递：单向、极简、不共享

- 子 agent 只收到**一条文本任务描述**（`subagent_task.py:156`），外加父对话的附件（`subagents.py:396-400`）、继承父 Run 的 `tool_approval_mode`（`subagents.py:402`）；**不传父对话历史**。
- 模型配置：子 agent 自己的配置优先，回退父 Run 的 model_spec（`subagents.py:383-388`）。
- 结果回传：子 Run 终态绑定 output Message，父 agent 经 `subagent_await`/`subagent_status` 读取该权威结果。这符合架构不变量「Turn 结果以 result_run_id 指向的 Run 为权威」。
- 父子共享同一 Project Workdir（执行器校验子 Run 与创建者同 project，`services/runner.py:109-130`），但各自使用自身 Thread 的 runtime scope 与沙盒租约。

### 1.4 能力差异：三处真实的不同构

1. **独立执行后端**：子 agent 用 `SubAgentBackend`（`runtime/agent_backends/subagent/graph.py:107-110`）。不用 `ChatbotAgent`。两者的中间件栈 90% 相同（对比 `chatbot/graph.py:31-70` 与 `subagent/graph.py:77-104`），真实差异只有三处：
   - 子 agent **没有 memory 中间件**、**没有 subagent 中间件**（即不能递归派生）；
   - 子 agent 多一个 `_SubAgentToolFilterMiddleware`；
   - 子 agent 的 `SubAgentContext` 没有 `subagents` 字段（`subagent/context.py:9-15`）。
2. **禁用工具**：`present_artifacts` 与 `install_skill` 被双层拒绝——模型可见层过滤 + 执行层对显式调用返回错误 ToolMessage（`subagent/graph.py:30,55-74`）。注释明确「工具列表隐藏不构成执行边界」，这与仓库「权限在 executor fail-closed」的原则一致。
3. **配置不继承**：子 agent 使用自己的 `config_json.context`（工具、知识库、MCP、模型独立选择），不继承主 agent 的选择（`services/input_config.py:28-35`；`docs/agents/session-cooperation.md`）。

### 1.5 递归禁止：三处硬编码，深度恒为 1

子 agent 不能再派生子 agent，由三层机制阻止：

1. `SubAgentContext` 无 `subagents` 字段 → `create_subagent_task_middleware` 返回 None，工具根本不装配（`subagent_task.py:86-91`）；
2. 服务层校验 `creator_run.run_type == "subagent"` 则拒绝（`subagents.py:281-282`）；
3. 执行器校验子 Run 创建者必须是 `chat|resume` 类型（`runner.py:118-120`）。

没有可配置的深度参数。当前形态是**一层的扇出（fan-out）**。**不是树（tree）**。

### 1.6 并行、取消与一个提示词兜底的缺口

- **支持并行**：每个 tool_call 派生独立子线程、独立入队 ARQ。父 state 用 reducer 按 run_id 合并并发子 Run（`chatbot/state.py:24-59`）。提示词引导「先派发互不依赖的任务，再继续自己的工作」。唯一串行约束是同一子线程同时只能有一个活跃 Run（`SubagentRunBusy`，`subagents.py:366-370`）。
- **取消级联**：显式取消父 Turn 时，沿 `created_by_run_id` BFS 级联取消在途子 Turn，并经 Redis 发加速信号（`services/turns.py:186-206`；`repositories/runs.py:658-757`）。
- **缺口**：父 Run **正常完成**不会级联取消仍活跃的子 Run——`mark_run_terminal` 的 `cancelled_descendants` 恒为空（`runner.py:248-300`），目前靠系统提示词约束模型「应 await 或明确取消」（`subagent_task.py:55`）。孤儿子 Run 成为孤儿后仍继续消耗 worker 容量，这是**提示词兜底**。**不是结构保证**。
- 前端观测：每个子 Run 独立 HTTP+SSE 观察流，受 HTTP/1.1 连接预算限制 `MAX_CHILD_STREAMS = 3`，超限降级为 2 秒轮询（`frontend/src/modules/session/model/useSubagentRuns.js:6`）。子线程有独立视图（`SubagentThreadView.vue`）。这套「父流不复制子生命周期、独立观察」的设计来自 ADR `2026-09-17-subagent-independent-observation`。

### 1.7 同构性总评

| 层面 | 是否同构 | 说明 |
|---|---|---|
| 数据模型与生命周期 | ✅ 完全同构 | 同表、同 Input/Turn/Run、同租约/恢复/幂等 |
| 执行器与 checkpoint | ✅ 完全同构 | 同 runner、同 PG checkpointer，子 Run 独立 |
| 中间件栈 | 🔶 约 90% 同构 | 差异：无 memory、无递归、多工具过滤 |
| 工具能力 | 🔶 基本同构 | 仅禁 `present_artifacts`/`install_skill` 两个 |
| 配置模型 | ✅ 同构 | 各自持有 config（与主 agent 待遇一致） |
| 上下文输入 | ❌ 不同构 | 只有一条描述，无父历史、无可控继承 |
| 交互原语 | ❌ 不同构 | 无父→子运行中干预手段 |
| 递归能力 | ❌ 不同构 | 深度恒 1，三处硬编码禁止 |

**直接回答「子智能体与主智能体能力是否同构」：执行底座已经高度同构（这在该仓库是既成事实）。这不是愿景。不同构的只剩递归深度、上下文传播方式和两个交互原语。**

## 二、业界参照：OpenAI 的多智能体协作

### 2.1 Responses API Multi-agent（官方托管编排，高可信）

来源：[Multi-agent guide（OpenAI 官方）](https://developers.openai.com/api/docs/guides/responses-multi-agent)。

- **六个协作原语**（由 API 托管，以 `multi_agent_call` 项下发）：`spawn_agent`（创建并派任务）、`send_message`（向已有 agent 排队消息，不触发新回合）、`followup_task`（给非根 agent 追加任务并恢复其回合）、`wait_agent`（等待 mailbox 更新）、`interrupt_agent`（打断当前回合但**保留上下文**）、`list_agents`（返回整棵 agent 树与状态）。
- **上下文隔离是核心卖点**。每个 subagent 维护自己的上下文，compaction 独立于根与每个子 agent 服务端执行。
- **上下文传播可选**。`spawn_agent` 的 `fork_turns` 参数让**模型自己决定**向上游继承多少回合。
- **能力同构**：树中所有 agent 共享请求的模型与工具集，都能发起 function call。**允许递归 spawn，无树深限制、无总数限制**，唯一硬节流是 `max_concurrent_subagents`（默认 3，含孙代同时活跃数）。
- **通信可追踪**：`agent_message` 带 `author`/`recipient` 与加密内容。
- **适用性对照**：适合独立有界、可并行的任务；不适合单链顺序推理，不适合频繁写共享可变状态，也不适合被单一慢操作主导的任务。官方明确警告 token 用量随 subagent 增加。

### 2.2 Codex CLI subagents（产品层，部分来自第三方资料）

来源：[Firecrawl: Codex Multi-Agent Orchestration](https://www.firecrawl.dev/blog/codex-multi-agent-orchestration)、[Digital Applied: Codex Subagents GA](https://www.digitalapplied.com/blog/codex-subagents-ga-multi-agent-autonomous-coding-guide)、[Codex CLI config 指南](https://majesticlabs.dev/blog/202607/codex-cli-configuration-guide)。**以下参数建议对照 openai/codex 仓库 docs 核实后再作为设计输入。**

- **声明式 agent 定义**：`.codex/agents/*.toml`（项目级）/ `~/.codex/agents/`（全局），字段 `name`、`description`、`developer_instructions`，可选 `model`、`model_reasoning_effort`、`sandbox_mode`（read-only / workspace-write / 完全访问）、`mcp_servers`。**每个 agent 可以有完全独立的模型、推理等级、沙盒策略和 MCP**——即「定义上完全同构，配置上自由裁剪」。
- **嵌套与并发**：`agents.max_threads` 默认 6（并发），`agents.max_depth` **默认 1**（嵌套），文档警告提高深度会增加 token、延迟与资源消耗。即 **Codex CLI 的默认形态与 Yuxi 现状相同**。都是一层扇出；差异在于 Codex 的深度是可配置旋钮。它不是三处硬编码。
- **子 agent 继承父会话的沙盒策略**，可携带自己的 MCP 服务器，同样读取项目 AGENTS.md 约定。
- **产品编排理念**：explorer（只读建图）/ worker（范围内实施）/ default 三型分工。manager 不直接写代码，只做规划与派发、收集和冲突解决。explorer-first（先建上下文地图再派工）。任务描述写**结果导向**的成功标准。不写实现步骤。bounded context 包（接口摘要+任务+相关类型）。不是整个仓库。经验值 3-5 个并行 agent 最优，token 成本随数量线性增长。
- **写隔离**：Codex 用 git worktree/feature branch 隔离并行写入，改动经 PR review 合并。社区反馈 worktree「解决碰撞，不解决协调」。

### 2.3 收敛出的共同设计取向

1. 上下文隔离是收益（专注，省 token，可独立观测）。它不是缺陷；
2. 递归普遍被允许（Responses API）或至少可配置（Codex CLI），但默认保守（max_depth=1），主节流用并发上限。不用深度做主节流；
3. 补消息（send_message）、追加任务（followup_task）、打断保上下文（interrupt）、树级观测（list_agents）构成完整协议。交互不止 start/await；
4. 能力同构、配置异构。同一个 agent 运行时，每个实例自带模型/工具/沙盒/指令配置；
5. 并行写入靠版本隔离（worktree/branch）。不靠口头约定。

## 三、差距对比

| 维度 | Yuxi 现状 | Responses API | Codex CLI | 差距评估 |
|---|---|---|---|---|
| 实体与生命周期 | 同表同状态机 | agent 为 response 内对象 | TOML 声明式定义 | Yuxi 持久化最强（跨进程、可恢复） |
| 递归深度 | 恒 1，三处硬编码 | 无限制 | 可配置，默认 1 | **主要差距**；数据模型已支持任意深度 |
| 并发节流 | 无上限（仅单线程 busy） | `max_concurrent_subagents`=3 | `max_threads`=6 | **真实缺口**：worker 槽位共享预算可被饿死 |
| 上下文传播 | 仅一条描述 | `fork_turns` 模型自决 | bounded context 包 | 缺结构化的「上下文包」机制 |
| 运行中干预父→子 | 无 | send_message / interrupt_agent | 无（线程级 stop） | 缺 steer 延伸 |
| 追加任务/续用 | `subagent_start(thread_id)` 续线程 | followup_task | 线程 resume | 基本对齐 |
| 树级观测 | 逐 Run status + 前端并行流 | list_agents 树 | `/agent` 线程管理 | 缺树视图，前端受 3 流连接预算 |
| 等待机制 | 工具内轮询 30min，占父 worker 槽 | wait_agent mailbox | 后台线程 | 可优化（见 4.6） |
| 父终态→子 | 提示词兜底，不级联 | 根负责合成、显式等待 | manager 收集 | **结构性缺口** |
| 模型/工具异构 | ✅ 子 agent 独立配置+模型回退 | 共享请求配置 | 独立 model/sandbox/MCP | Yuxi 已达 Codex 水平 |
| 写隔离 | 共享 Project Workdir + 「结果交回主 agent」约定 | — | worktree/branch | 并行度提高后需重新评估 |

## 四、对「完全同构分叉树」愿景的评估与可优化点

### 4.1 愿景中已经成立的部分（无需建设）

同表同生命周期、独立 checkpoint、独立配置（含模型回退）、并行扇出、持久化恢复、委派关系与级联取消的 BFS 遍历——这些构成了「分叉树」的全部地基。**现状离愿景比直觉近得多**。剩余工作集中在「放开深度」和「补交互原语」。不是推翻重来。

### 4.2 P1（无论是否放开递归都应做）

1. **每父/全局并发上限**。当前父 agent 理论上可无限制扇出，子 Run 与用户交互 Run、Durable Task 共享同一批 worker 执行槽（Durable Task 已有 PG claim 上限 4 的先例）。缺上限时，一个激进的主 agent 就能饿死全平台交互链路。建议对齐业界默认（每父 3-6，全局预算另计），在服务层 `SubagentRunService.start` 闭合。
2. **父终态的结构性收敛**。把「父完成会取消子 Run」从提示词约束变成运行时保证。父 Run 进入终态时，要么级联取消在途子 Run（复用既有 BFS），要么显式 detach 并留下可观察记录。符合仓库不变量「预设条件不成立时显式失败」。也符合仓库不变量「非终态 Run 必须有明确结局」。e2e `test_agent_lifecycle_subagent_boundaries_e2e.py` 已有级联取消的测试基础可扩展。

### 4.3 P2（实现分叉树愿景的核心）

3. **深度可配置，三处硬编码收敛为一处语义 Owner**。放开递归时，`SubAgentContext` 无字段、服务层校验、执行器校验三个点必须收敛为单一 Owner（建议服务层，middleware 装配随之自然生效），否则违反「在真实语义 Owner 处闭合工程主张」。默认值维持 1（与 Codex CLI 一致），按 agent 或系统配置放宽。两个有利条件：委派链（`created_by_run_id`）与级联取消 BFS 天然支持任意深度；委派方向单向（子永远不能调用父），树无环。配套必须同时落地 4.2-1 的并发上限与 4.4 的预算保护，否则深度×扇出组合爆炸。ADR `2026-09-17` 需要修订。
4. **父→子 steer（对应 send_message）**。Yuxi 已有成熟的用户→主 agent steer 机制（pending steer 聚合、安全边界接管）。把同一原语沿委派关系延伸到父→子，是架构上顺势的补全，能让「运行中修正子任务方向」不靠取消重来。
5. **树级观测**。`subagent_status` 逐 Run 查询升级为按委派关系返回子树快照（对应 list_agents）。前端 `subagentRuns.js` 收敛模型可扩展为树渲染。深层级下 `MAX_CHILD_STREAMS=3` 的连接预算需要重新设计（多路复用或聚合流）。

### 4.4 P3（对齐业界但可延后）

6. **`subagent_await` 的等待语义**。当前父 agent 在工具调用内 0.5s 轮询最长 30 分钟，期间父 Run 占据一个 worker 槽位却无进展（N 个并行子任务消耗 N+1 个槽位）。可评估两条路：等待点复用人工等待的 interrupt/resume 机制释放槽位（代价是父 Run 分段），或事件驱动加速轮询（终态判定仍以 PostgreSQL 为准，符合证据规则）。深树下还需「任一完成即返回」的 wait-any 语义。此项改动触及 Run 分段语义，需单独决策。
7. **结构化上下文包**（对应 fork_turns / bounded context）：允许主 agent 在 `subagent_start` 时附带可选的结构化上下文（父对话摘要、相关文件/类型清单），由模型在描述外显式构造——兼顾 Codex「bounded context」实践与上下文隔离收益。不自动继承历史。
8. **`present_artifacts` 禁用重审**。artifact 本就绑定 Run（架构不变量），子 Run 展示自己的 artifact 在模型上可能是自洽的。禁用更像当前 UI 投影边界的历史产物。`install_skill` 涉及共享可变状态变更，建议维持禁用。若重审，按 ADR `archived/0.7.3/.../2026-09-08-subagent-tool-execution-boundary` 的框架论证。

### 4.5 愿景需要修正的部分

「完全相同的功能」里有一处应保留差异：**写隔离**。Codex 用 worktree 解决并行写冲突；Yuxi 父子共享 Project Workdir，目前靠执行边界约定，即默认模式子 agent 不直接写 Project、结果交回主 agent 处理（archived ADR 2026-09-08）。若树更深、并行更高，Project Workdir 的并行写入冲突会放大。届时要么引入运行级写域（类似 worktree 的 Yuxi 等价物），要么把「结果交回」从约定升级为工具层强制。这是放开递归前必须回答的问题，但不必在第一步解决。

### 4.6 风险清单

- **成本**：token 随子 agent 数量与深度线性放大（OpenAI 官方警告）。深度增加墙钟延迟。
- **容量**：子 Run 与交互 Run 共享 worker 槽位，无上限扇出会饿死交互链路（见 4.2-1）。
- **认知负担**：三处递归禁止点、提示词级生命周期约束，都是未来维护陷阱；收敛 Owner 本身就是收益。
- **观测爆炸**：深树 × 并行流超出前端连接预算，需先有树级聚合再放深度。

## 五、证据可信度与未验证范围

- 源码结论：探索代理全量调研 + 本文对关键主张的直接抽查核验（递归禁止 `subagents.py:281-282`、禁用工具双层边界 `subagent/graph.py:30,55-74`、中间件栈对比 `chatbot/graph.py` / `subagent/graph.py`、`is_subagent` 字段、引用的测试/文档/ADR 文件存在性），均一致。
- Responses API multi-agent：来自官方文档（2026-10 抓取），beta 期 schema 可能变化。
- Codex CLI 参数（TOML 字段、`max_threads=6`、`max_depth=1`）：来自第三方博客与社区文章，官方 learn 站点当前 404。**作为设计输入前应对照 openai/codex 仓库 `docs/config.md` 核实**。
- 前端投影细节（`useSubagentRuns.js`、`SessionWorkspace.vue` 行号）来自探索代理报告，未逐行抽查。
- 未做任何运行时验证（未起 Compose、未跑 e2e）。本报告不改变任何现有行为的结论。
