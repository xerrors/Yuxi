# Agent 运行时上下文

本页解释 Yuxi 如何从持久化 Agent 配置构建一次运行，以及配置、权限、文件和 LangGraph state 在运行中分别负责什么。Agent 配置和扩展开发见[配置和开发智能体](../agents/agents-config.md)。

## 运行入口

Public Thread 接入把普通消息保存为 Input 与 Receipt，调度器消费时建立 Turn/Run 和正式 Message；回答或审批恢复在同一 Turn 建立下一 Run。协作工具先创建成员的独立 Thread，再通过相同输入链路提交工作，以创建者关系关联父执行。worker 只执行已持久化的 Run：

```mermaid
flowchart LR
    Input["已持久化的 Run 与输入 Message"] --> Lease["worker 取得执行权"]
    Lease --> Prepare["准备 Context：配置、身份、工作区提示词与资源"]
    Prepare --> Manifest["从 Context 派生并提交 manifest"]
    Manifest --> Identity["复查线程归属与 Agent 可见性"]
    Identity --> Graph["get_graph 复用同一 Context"]
    Graph --> State["LangGraph state + PostgreSQL checkpoint"]
    State --> Result["消息、事件、文件和产物"]
```

worker 在取得 lease 并校验输入后，从 Input 冻结的可配置 Context 装配 Run 模型与审批模式、当前运行身份和 Workdir。准备期间持续续租，manifest 从准备结果派生并提交；固化失败时执行不开始。chat/resume 流和 BaseAgent 传递同一个 Context，构图只消费已准备的资源与 Skill 内容。当前 Agent 的专属 Skill 自动加入预加载，系统提示词继续生效；执行边界复查权限，保留本 Run 的专属根文本，中途新增绑定在下一 Run 加载。执行流复查 Agent 可见性，后端发生变化时显式失败。

执行流要求明确的 Thread、Turn 和 Run 身份，并检查 Thread 及其 Project 属于当前用户、APP 和 Agent 作用域。缺失身份、归属不一致或资源已归档时显式失败。Thread 创建和用户消息写入由接入用例负责；流中的 init 消息用于展示已经保存的输入。

运行入口读取用户工作区的 `agents/AGENTS.md` 和 `agents/USER.md`，把非空内容追加到系统提示词；文件不存在或不可读不会阻断运行，每个文件最多读取 64 KiB。`prepare_agent_runtime_context` 按当前用户权限解析工具、知识库、MCP、普通 Skills 和显式选择插件的配套内容。准备结果仅属于该 Context 对象，独立执行入口对新 Context 显式准备；`get_graph(context)` 创建模型、工具和中间件。LangGraph state 保存消息、待办、产物、插件激活、协作消息消费位置和用量，checkpoint 只使用 PostgreSQL。

API/worker 不信任浏览器内存中的完整配置。请求可以提供受限的单次覆盖值，例如模型或工具审批模式；配置快照也不能替代实时授权。

状态查询在 Session 与 Workdir 授权后读取 PostgreSQL checkpointer 的根 namespace，返回最近完整快照及同批 pending writes 中的中断，仅在最新 Run 为 `interrupted` 时展示审批。读取不创建 Context 或模型，业务 pending writes 的合并由执行图完成。文件面板经文件系统接口读取当前 Workdir。

HTTP 状态查询提供待办、产物和用量，Turn SSE 提供执行增量；`/api/v1/agents/sessions/{thread_id}/cooperation` 从持久关系批量读取成员、最新 Turn 和输入队列，不读取 checkpoint 或结果正文。同一 Thread 的页面与侧栏共享历史、状态、流订阅和 Input 观察器，最后一个观察视图卸载时释放订阅。终态和 resync 触发新的历史读取，结果归属以持久快照为准。

`sessionRuntime` 拥有前端每 Thread 的共享观察、恢复与命令，`useAgentThreadState` 创建运行版本、协作等待及审批提交等状态的确定初值；清理后重新观察使用新的独立状态。

前端 SSE 连续 60 秒没有收到字节时关闭失效读流，回读当前 Turn；仍在运行时按原 cursor 重连，已经终止时加载最终历史并刷新产物。服务器默认空闲心跳为 15 秒；超时只恢复订阅，不取消任务。

Input 接收、附件就绪、消费与 Run 投递分别拥有明确提交点；worker 使用输入冻结配置，并重新取得当前身份、Workdir 与授权资源。FIFO、steer、安全接管、消息与附件归属、取消和队列恢复的完整契约由[输入队列与调度](./agent-request-queue.md)维护。

断线后调用方从 Public Session、Input、Turn、Run 与 Items 查询读取明确的接收、消费和结果归属。排队 Input 尚无 Turn/Run；最终输出只属于 Turn 的 `result_run_id` 所指顶层 Run。HTTP `202`、SSE 中断和 Run `yielded` 都不是整轮成功证明。

## 会话标题

未显式命名的新会话在首次聊天执行时，先保存首条用户文本的前 30 字作为临时标题，再由 worker 并行调用系统 `fast_model` 概括主题。输入最多 2000 字符，整个附属任务最多等待 8 秒。失败或进程退出保留临时标题，不重试，不影响聊天结果；纯图片输入保留默认标题。没有自动命名资格的已有会话与协作子会话不补生成标题。

显式指定标题或手动改名会取消自动命名资格，迟到的模型结果不能覆盖用户修改。标题保存在 Session，前端通过已有详情读取和侧栏同步显示。实现见[标题服务](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/titles.py)。

## 回答来源标注

completed Turn 的最终回答存在有效知识库或网页检索内容时，Web 回答下方提供“标注来源”。入口由 Web 内部路由提供，仅允许产品用户登录 JWT；API Key 包括 full Key 均被拒绝，Public 协议与 OpenAPI 不声明该能力。用户点击后，API 使用最终 Run 冻结的聊天模型发起独立调用，根据本轮检索快照匹配支持回答的证据。调用读取同一 Turn 各 Run 成功完成的 `query_kb`、`open_kb_document`、`find_kb_document` 和 `web_search` 结果，排除其他 Turn 与失败工具结果；它不会继续执行智能体或改写回答。知识库与网页任一种来源存在即可标注。

标注 service 校验模型返回的来源编号、回答 Markdown 行范围和逐字证据摘录。知识库 chunk 的位置使用入库时保存的原文范围，编号原文窗口能进一步定位摘录行；网页证据以搜索返回的内容片段为准。模型匹配表达材料对回答的支持关系，实际生成时的因果来源与结论真伪仍需用户核验。未匹配的结论保持原样，一个回答范围可以对应多个来源。

最终回答 Message 的 `extra_metadata.references` 保存标注、回答内容 hash、来源工具记录、文档代次、模型、用量和生成时间。Web 通过内部 GET 读取同一份持久结果，Public Turn metadata 不包含引用；来源读取和重新展示按当前知识库权限过滤。前端在对应 Markdown 块尾显示域名或文件名胶囊，同段多个来源合并为 `+N`。悬浮或聚焦展开来源卡片，显示完整标题、证据、知识库原文行号，并可切换来源；正文仅在查看来源时轻量高亮。向下键进入卡片，Escape 关闭并返回胶囊。单来源点击直接打开，多个来源在卡片中选择。支持文档的知识库来源打开文件预览，Dify、Notion 等只读检索连接器显示证据片段；网页来源打开 HTTP(S) 地址。知识库行号属于完整解析文本；文档代次变化或原文窗口无法核验时，预览提示位置失效并禁用旧行号高亮。回答保留原始空行与 SVG/HTML 预览，引用按原始 Markdown 范围匹配。

同一 Turn 的标注请求由 PostgreSQL 事务锁拒绝并发重复调用，成功结果直接复用，刷新页面后恢复。独立调用最多等待 90 秒，回答与来源 JSON 输入最多 60000 字符；超预算、模型调用失败和无效输出明确返回错误，失败结果不保存为成功标注，用户可以重试。源码由 [引用用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/references.py) 与 [来源查询](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/repositories/references.py) 拥有；验证入口是 [HTTP 与 PostgreSQL 集成测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_turn_references.py) 和 [真实页面检查](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/turnReferences.js)。

## 配置和运行态的区别

| 数据 | 来源 | 生命周期 |
| --- | --- | --- |
| `config_json.context` | Agent 管理页面/管理 API | 跨运行保存的配置 |
| Session `config_snapshot` | 创建时的可配置字段与 Schema 默认值 | 会话配置，显式模型/审批更新后作用于新输入 |
| Input `context_snapshot` | 接收时复制会话配置并应用单次覆盖 | 对应输入及消费它的 Run |
| `runtime.context` | 配置 + 用户身份 + 运行身份 + 权限快照 | 当前 Run |
| LangGraph state | Graph 执行和中间件 | 当前 checkpoint thread |
| PostgreSQL Input/Receipt/Turn/Run/Message | 接收服务、调度器和 worker 提交 | 业务接收、执行与最终结果 |

`skill_runtime_snapshot` 中的授权 Skill、预加载内容和插件入口正文在 Context 准备时派生；中间件在运行期间维护 token 等状态。身份与运行标记由 worker 注入，持久 Agent 配置通过 `update_config` 仅装载 configurable 字段。接入和执行使用同一装载规则。运行事件的模型、审批与 Workdir 元数据从准备后的 Context 投影。

Context 在 dataclass 中声明 Skill 快照、MCP 来源、本地和 middleware 注册集合及 MCP 禁用集合，均不进入可配置 schema 或接收快照。`resolve_configured_runtime_tools` 完成本地及 MCP 来源装配，shipping graph 收集 middleware 工具名；动态插件 MCP 加载延续同一来源记录。模型与工具执行只接受已记录且当前获授权的来源，缺失装配和未知工具名均拒绝。`disabled_mcp_tools` 保存服务器当前禁用的工具名。

会话创建时保存完整可配置 Context，模型依次取创建显式值、Agent 配置和系统默认，空会话也验证有效模型。保存的 Agent 与系统默认值修改不改变已有会话。每个时点以完整 `context_snapshot` 为唯一配置来源；执行拒绝缺失快照，不从 metadata 或最新 Agent 补值。Session 更新与输入接收共用会话锁，后接收的 Input 复制更新后的配置，已接收输入保持原快照。单次 follow-up 覆盖随消息原子持久化；活动 steer 与等待恢复继承当前 Run 的配置；空闲 steer 合批采用首个 Input 的冻结配置和来源，其他 Input 配置只保留审计。运行身份不进入快照，`all` 或固定列表保存资源选择意图，实际资源授权在准备和执行时解析。协作会话继承派发方实际模型和能力配置，显式选择其他 Agent 时保存目标配置；模型历史、checkpoint 和附件上下文各自独立。

manifest v3 的配置摘要来自准备后的可配置字段，包含模型覆盖、schema 默认值和工作区提示词，排除用户、线程、worker 等运行身份。Skill 条目的来源、版本与哈希来自首次授权解析；预加载内容另保存实际读取字节的摘要，manifest 生成不再次查询 Skill。插件条目记录 ID 与声明摘要，入口正文记录实际读取内容的摘要；插件声明与部署 revision 一起帮助识别运行组成，不提供旧代码自动回放。完整提示词和 Skill 正文不持久化到 manifest。MCP 工具发现、Memory 与文件动态读取发生在后续执行边界，manifest 不承诺冻结其实际可用性或字节。

## 资源权限

角色上限、共享范围、共享范围修改与治理由[资源权限机制](./resource-permissions.md)解释。Context 首次准备与执行刷新共用资源规范化及 Skill 快照应用规则，保留原始资源选择与同次执行已预加载的专属根正文。模型输入准备和每次工具调用前刷新当前资源与 Skill 授权快照；实际模型请求、重试与摘要前复查身份和 MCP 策略并同步 Skill 文件投影，工具边界不重建文件投影。模型返回后、审批等待点之前仅复查持久账号、Run/Thread 和 Agent 使用权。账号或 Agent 失权形成失败终态，仅依赖失权则返回能力受限提示并继续可执行部分。

- Context 准备阶段将 `"all"` 展开为当前执行用户可用的资源列表，将固定列表与可用资源取交集，空列表保持为空；后续构图消费解析后的列表。字段默认值、界面操作与 API 写入规则见[智能体配置](../agents/agents-config.md)。
- MCP 选择控制直接加载的服务器；已启用插件经 `load_plugin` 激活后仍可按需开放引用的 MCP。
- Agent 的知识库选择只能缩小用户已经拥有的读取权限。Context 准备阶段只保存归一化后的知识库 ID；知识工具在每次执行时按当前用户权限与会话选择重新解析，撤权后的资源不能继续读取。权限查询失败通过工具错误边界暴露，具体机制见[知识库与检索](./knowledge-base.md#agent-如何看到知识库)。
- Skill 选择控制说明目录和预加载，不授予工具能力；共享 Skill 的文件投影按用户授权生成，个人 Skill 位于 UserWorkspace。
资源快照只解决运行时“能看见哪些资源”。产生文件、知识库、MCP 或外部系统副作用的工具还要在执行处校验具体目标和当前身份。

## 插件激活与恢复

插件由部署代码注册，Agent 的 `plugins` 保存允许使用的 ID 列表，默认空且不支持 `all`。尚未激活时，插件只向模型提供目录和统一 `load_plugin` 工具；普通 Skill 仍使用摘要与文件读取，不产生激活副作用。

`load_plugin` 检查当前选择、入口及配套 Skill 的读取权限，以及 MCP 的可用性和工具名称冲突。成功时在同一 `Command` 中写入入口正文工具结果和 `activated_plugins`；工具从下一次模型请求开始开放。同批并发加载结果由平台 reducer 合并去重，不能依赖同批业务工具调用顺序。入口、配套说明、连接或工具名称冲突分别返回对应错误，不新增激活记录；重复成功加载可重新读取说明。仅由插件引入的辅助 Skill 摘要在插件有效激活后披露，独立选择的普通 Skill 保持原有披露方式。

`activated_plugins` 是平台统一的 PostgreSQL checkpoint 状态。恢复时继续使用记录，但有效能力还必须满足本次插件选择和当前资源授权；已有激活不提供永久权限。协作新会话继承原始插件选择，不继承激活记录或派生工具选择。对话压缩可能丢失入口正文，模型可重复加载；不需要额外保存 Skill 激活状态。

每次构图按部署注册顺序创建普通插件对象及平台 `PluginMiddleware`；平台只调用模型与工具包装钩子，业务对象的框架同名方法不会新增图节点或自定义 checkpoint 字段。取消清理图保留平台激活通道，不通过发现外部 MCP 才能完成清理。发布兼容性和离线迁移见[部署插件决定](../develop-guides/decisions/implemented/2026-11-17-agent-plugins.md)。

## 文件和 Memory

当前 Project 的 `workdir_path` 决定 Agent 的默认工作目录。同树会话共享 Project Workdir，各自拥有消息历史、Thread checkpoint、Run lease、heartbeat 和命令清理责任。默认 Container 为每个 Run 创建临时执行环境，终态后清理容器并保留 Workdir。显式选择 Cube 时，同用户不同 Project 共用持久 VM；用户没有活动 Run、操作或待清理命令时才按空闲策略休眠，详见[沙盒生命周期](./sandbox.md#生命周期与并发)。

用户配置 `enable_memory=true` 时，Memory middleware 提供受限的记忆工具并读取 `agents/MEMORY.md`；文件不存在或内容为空时仍可写入第一条记忆。未保存配置的用户默认启用 Memory，已保存的关闭选择继续生效。用户可在账户设置中切换开关，或点击“查看 Memory”进入个人空间并打开该文件。它是用户主动维护的参考资料，不是系统指令。Memory 读取和更新有独立的用户、Run、worker 和文件大小校验。

聊天输入框输入 `@` 后，“对话”分组按标题搜索当前用户的活跃 Web 对话，空查询显示最近对话。选择后显示标题，消息保存稳定的线程 ID 与标题快照；同名或改名不改变引用身份。Agent 在 Memory 已启用时直接把该 ID 传给现有 `read_thread_messages` 工具，按需读取真实历史，不根据标题猜测内容。历史读取沿用当前用户授权与消息预算；关闭 Memory、对话已归档或不可见时不能读取。引用不创建协作任务，也不修改目标对话。

Viewer、正式附件和 artifact API 通过持久化 Workspace/Workdir 读取文件，不连接 Agent execution runtime。附件表拥有文件信息、用户/APP、Input 与最终 Message 归属、准备状态和存储位置。上传建立隔离的 draft；接收事务通过附件行锁绑定 Input；统一准备函数写入 Workdir 并提交 ready 后清理临时来源。恢复循环调用同一准备与调度流程，回执读取或重放只读事实。Message 通过归属关系读取附件，不复制记录；取消排队保留正式文件。OCR 预解析与图片辅助处理只在产品 JWT 私有入口开放，Agent 的 OCR 工具继续按需读取 Workdir。图片与文件提交契约见[公开 API](../advanced/agents-public-api.md#图片与文件附件)。沙盒虚拟路径、Viewer scope、对象 URL 和宿主机路径在各自边界中转换，不能互相替代。

Web 附件上传框在打开时固定目标会话草稿；上传期间切换会话，确认后附件仍归原草稿。首次创建正式 Thread 时，未发送附件与仍打开的上传流程一起迁移到该 Thread。

## 用量统计

Dashboard 的 Token 趋势、会话审计和会话汇总从 `AgentRun.token_usage` 累加每个执行段自己的快照，不读取 Message 的旧用量元数据，也不累加线程累计快照。恢复 Run 和协作子会话各自计入一次。快照在运行结算时保存；执行中的用量可能尚未上报。实时上下文面板读取当前 LangGraph state，因此观察时点与已持久化统计不同。

Token 趋势按 Run 创建时间归入上海时区的十四个小时、日或周，排除未来记录、已删除会话、已删除用户及无法解析 Agent 的会话。会话审计只汇总所选会话的全部 Run，允许超级管理员检查历史归属；会话汇总沿用当前可见会话范围，其时间筛选只限制每日趋势。比较结果前须对齐会话范围和 Run 创建时间。

有上报的快照累加观测值；未上报保留 `null`，部分缺报返回已知部分并标记 `token_usage_complete=false`。全缺报不能显示成零，明确上报的零才是零消耗；没有运行的趋势区间返回零并显示无运行记录。会话没有 Run 快照时审计返回未知。页面在部分缺报时将汇总标为“已报”，图表保留未知间隙。

“上报完整”只表示当前采集范围没有缺报，不代表完整费用：统计覆盖主 Agent 模型调用，内部摘要、标题、来源标注、知识处理等独立模型调用不计入；不可信供应商用量的排除规则由[中间件](../agents/middleware.md#token-用量)维护。供应商账单仍是费用对账依据。聚合和证据入口见 [Dashboard repository](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/system/repositories/dashboard.py) 与[真实 HTTP 用量回归](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_dashboard_usage.py)。

## 用户定时 Agent

用户定时 Agent 由 `scheduled_agent_jobs` 保存 Project、Agent、提示词、审批模式和计划，worker 在 PostgreSQL 行锁下为到期任务创建唯一 occurrence。每次 occurrence 通过统一接收用例创建绑定原 Project 的独立 Thread、首个 Input 和 Turn/Run；触发记录保存输入关联，排队与执行状态分别从 Input、Turn 和 Run 读取。明确的领域错误终结 occurrence，未知瞬时错误在下一轮用确定的幂等键重查已接收输入；单条失败不阻断同批任务。Redis/ARQ 只负责唤醒。

任务 API 只返回当前用户拥有且未删除的任务，并在创建、更新和触发时重新校验 Project 归属与 Agent 可见性。停用或任务软删除只阻止未来触发；账号软删除在同一事务删除任务及 occurrence。任务支持 Run now、5 段 cron 和 IANA 时区，数据库保存 UTC 下一次触发时间；错过多个周期只合并一次，已有非终态执行时记录 skipped。

任务详情按每页十条查看全部运行历史，包含失败原因、跳过及排队状态，有对应会话时可跳转。`GET /api/scheduled-tasks/{job_id}/runs` 是产品内部接口，按当前用户归属查询，支持 `limit`（1–100，默认 10）与非负 `offset`，返回 `items/total/limit/offset`；未知、其他用户及已软删除任务均返回 404。排序使用计划触发时间和 ID 倒序；新记录可能使相邻 offset 页移动，刷新从第一页重读，不承诺跨请求快照。历史读取不改变调度或执行状态。实现与验证见 [repository](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/schedules/repository.py) 和[HTTP 分页回归](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_scheduled_agent_history.py)。

## 恢复和失败

审批或用户问题中断时，系统把等待点绑定在 Turn 的当前 interrupted Run 和 PostgreSQL checkpoint。结构化回答或审批只消费该等待点一次，在同一 Turn 创建恢复 Run；worker 再为该 Run 准备 Context 并固化 manifest。

worker 读取 Input 的完整配置快照，为当前 Run 重新准备工作区提示词与授权资源；动态文件与权限在各自读取或执行边界生效。等待恢复和 steer 接管复制原 Run 的配置；Run 与生命周期事件通过有序 `input_ids` 表达合批来源，消息通过 `source_input_id` 表达单条来源，执行归属由 `turn_id` 与 `run_id` 固定。

准备期间收到取消时，worker 使用已提交的取消状态完成取消收尾；manifest 失败不能把取消请求留待 lease 超时。manifest 使用 write-once 指纹，已有旧版 manifest 的 Run 重试若与新准备结果不一致会显式失败；历史 manifest 保留原记录。

最终 AI 包含无法解析的工具调用时，checkpoint 结算拒绝正常完成，由既有失败通道保存原因。用户继续输入后，Chat Completions 发送边界将历史畸形调用转为失败文本，并移除同批补齐结果；合法调用与结果、原始 checkpoint 保留。参数不会被猜测修复后执行。

## 源码和验证入口

- [Context 与资源归一化](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/context.py)
- [BaseAgent](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/base.py)
- [Chatbot graph](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/agent_backends/chatbot/graph.py)
- [会话协作 middleware](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/middlewares/cooperation.py)
- [Memory middleware](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/middlewares/memory.py)
- [运行时上下文 unit](https://github.com/xerrors/Yuxi/tree/main/backend/test/unit/agents)
- [Agent 主链路 E2E](https://github.com/xerrors/Yuxi/tree/main/backend/test/e2e)

修改配置、权限、模型可见输入、文件作用域或恢复语义时，同时验证对应的 unit、integration 或 E2E，并回读最终 state、消息、文件或协议结果。


## 线程阅读数据

公开内容通过 Session 或指定 Turn 的 `/items` 分页读取，数据库按授权作用域、公开身份和稳定顺序执行 limit+1，包含已消费的用户输入和已经公开的正文、工具参数及结果；pending/cancelled 输入仅保留于 Input，不进入正式历史。页面 `yuxi.runs` 只投影该页引用的执行段身份、状态与耗时。公开 item 身份与 output_index 保存于 Message 元数据，Turn 锁分配递增索引；实时与历史复用同一 serializer。Session 提供当前概览，Turn 列表发现零消息轮次，指定 Turn 的详情按 result_run_id 读取最终输出。完整 Model/Tool 审计由独立管理员接口读取。

智能体删除或当前用户失去执行权限后，已有 Session 和消息保留，历史读取仍按用户与 APP 归属授权。页面独立加载历史和智能体详情，在原输入区显示“智能体当前不可用”，隐藏输入与审批入口，关闭消息交互、继续队列和压缩动作，标题保持原样。403/404 或详情 `can_run=false` 表示当前不可用；网络及服务错误显示独立的加载失败与重试。打开或重新激活会话时复核智能体详情，后端发送、恢复与压缩仍校验当前执行权限。状态由 [SessionWorkspace](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/SessionWorkspace.vue) 派生，不写入 Session。

调试面板通过 `/api/v1/agents/sessions/{thread_id}/audits` 读取所属 Thread 的持久 Message 和 Run，仅允许没有 API Key、没有 APP 作用域的超级管理员使用。消息包含用户、系统、模型和工具记录；工具结果唯一保存在 `extra_metadata.output`，工具 Message 的 `content` 固定为空，接口文本和 Memory 由同一工具结果转换器派生。Repository 按 Run 创建时间、Run ID、操作 sequence、Message ID 返回最新 500 条的正序窗口，并用 `truncated` 标明截断。Run 内用户输入排在操作前，其他无 sequence 消息排在操作后并按 Message ID 排列；无 Run 消息使用自身创建时间。面板直接使用数据库响应，不合并普通历史或 SSE 正文，读取失败保留上次快照并提示重试。顺序与展示取舍见[调试数据库投影决策](../develop-guides/decisions/implemented/2026-10-10-debug-database-projection.md)。

History 读取不改变已读标记。页面加载后以 `POST /api/v1/agents/sessions/{thread_id}/viewed` 显式标记已查看；未知、跨 APP 或跨用户的 Thread 返回 404。多个查询遵循数据库事务隔离，运行中变化通过 Thread SSE 与持久快照重读收敛。

接口契约由 `modules/agents/services/public_items.py` 装配、`PublicItemRepository` 查询和三端 Items consumer 共同拥有；真实 HTTP 测试回读公开 item、Run 归属和 PostgreSQL 已读标记。公开协议取舍见[原生事件与 Agents API 决策](../develop-guides/decisions/implemented/2026-09-30-langgraph-agents-events.md)。

## 原生流与公开协议

BaseAgent 保留 LangGraph v3 ProtocolEvent 的通道、顺序、内容块和 namespace，独立 GraphExecutionResult 返回 checkpoint。同一 Run 因取消让位批次而续图时，执行层在审计与公开投影前将各图段 seq 衔接为 Run 内递增序号，不修改源事件对象、内容和 namespace。Model/Tool 审计消费原生结构；agents service 的唯一 OpenAIEventAdapter 消费相同原生事件，Redis 逐条保存公开事件。批量写入仅优化传输，不合并事件。业务结果通过独立 RunExecutionResult 与 PostgreSQL 收敛，不从公开流反推。

正常执行由 checkpoint 持久化用例在同一事务形成消息、最终输出归属与 Run/Turn 状态，runner 核对已提交结果后清理执行责任并发布通知。未提交的失败与用户取消由 runner 的共同用例尽力发布已缓冲的公开事件、回读用量并以有效 lease 提交终态；该结果同样只经过一次清理和通知。等待和 yield 保留各自的接续语义，已提交终态的重投递按持久清理标记补做清理、必要通知和后续 Input 调度。原始推理和业务状态使用明确的 yuxi 扩展；官方 item 与 content 事件保持官方结构。详情见 [Agents Public API](../advanced/agents-public-api.md)。

子 Thread 在委派事务中保存配置快照，各 Input 接收时继续冻结本次配置。直接向子 Thread 提交新的 follow-up 会创建独立的用户 Turn，不继承旧 Turn 的 `created_by_run_id`。Thread 委派关系记录其用户、APP 和共享 Project；执行与控制仍复查当前授权，并按本轮明确归属确定取消和 tracing 范围。成员身份、结果读取与停止语义见[会话协作](../agents/session-cooperation.md)。
