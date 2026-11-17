# backend/yuxi/modules 结构审查报告（2026-10-11）

## 完成状态（更新于 2026-10-13）

前八阶段已合入 `main`；第九阶段在主工作区完成，各阶段的提交或决策依据见下方。原始发现保留用于对照；代码行号和末尾问题计数属于 2026-10-11 审计快照。

- **【已完成】第一阶段：死代码复核与清理**，提交 `7e97aef4`；有当前消费者的入口明确保留。
- **【已完成】第二阶段：D1–D9 逐项复核与必要修复**，提交 `1432b719`；D1 原缺陷不成立，D9 原成因已更正。
- **【已完成】第三阶段：领域异常与 HTTP 协议分离**，提交 `a17c1503`。
- **【已完成】第四阶段：H1/H2 授权准备与刷新收敛**，提交 `f088bc28`；共用资源用例，按输入准备、实际请求与返回身份拆分工作；依据见[授权执行边界决策](../decisions/implemented/2026-10-12-runtime-authorization-boundaries.md)。
- **【已完成】第五阶段：运行、Skill 与工具边界收敛**，提交 `af8b6afd`；依据见[运行扩展决策](../decisions/implemented/2026-10-12-runtime-extension-contracts.md)。
- **【已完成】第六阶段：Agent 事务与生命周期收敛**，提交 `660661c4`；依据见[事务与生命周期决策](../decisions/implemented/2026-10-12-agent-transaction-lifecycle.md)。
- **【已完成】第七阶段：身份用例与事务归属收敛**，依据见[身份事务决策](../decisions/implemented/2026-10-13-identity-usecase-transactions.md)。
- **【已完成】第八阶段：知识存储与业务链路收敛**，依据见[知识存储决策](../decisions/implemented/2026-10-13-knowledge-storage-usecases.md)。
- **【已收敛并复核保留】第九阶段：共享技术与剩余审计**，依据见[共享技术决策](../decisions/implemented/2026-10-13-shared-technical-boundaries.md)。

第一至第五节的发现均有逐项结论：已修复、已清理、依据当前消费者保留或原问题不成立。复合条目的具体子范围分别说明，原始计数继续作为历史快照。

`【已修复】` 表示有对应代码修复与验证；`【已清理】` 表示删除完成；`【已复核：保留】` 表示当前消费者要求保留；`【已复核：问题不成立】` 表示原缺陷被证据否定；`【已收敛并复核保留】` 表示代码收敛与有依据的保留已分别说明；`【部分已完成】` 与 `【待处理】` 表示仍有未闭合范围。每阶段完成后按对应提交与证据更新下方条目。

## 方法与可信度

本报告回答一个问题：**一个新人从上到下阅读 `backend/yuxi/modules`（10 个业务域、263 个 Python 文件、约 5.4 万行）时，会在哪里被结构绊住？** 重点放在主链路上的绕路与冗余。

流程分两阶段：

1. **逐域分析**：11 个互不通气的分析者按域逐文件读完（agents 主链路/会话事件面/仓储模型/runtime、knowledge 核心/分块实现/图谱评估、extensions skills/tools+MCP、workspace+identity、五个小域），外加一个专门做跨域依赖全景的横切分析（AST 解析全部 import，34 条域级边、269 条文件级边）。
2. **独立复核**：12 个全新上下文的复核者逐条打开引用行号亲自读码、重跑 grep 验证。共 152 条候选发现，**2 条被驳回、约 25 条被修正或降级，其余确认**；复核还新发现约 27 条原分析遗漏的问题（含数个用户可见缺陷），已并入本报告。

所有死代码判断均经全仓 grep（含 test/、字符串引用、re-export、`__getattr__` 惰性导出）复核；横切分析的边计数经抽样验证零误差。分析基于 2026-10-11 工作区（`feat/agent-feedback-collection` 分支，含未提交的 feedback 相关新文件，按现状分析）。

## 总体结论

**主链路的骨架是可信的。** 逐跳核查后确认：HTTP → `inputs.py`（接收/幂等/冻结）→ `scheduler.py`（锁内建 Turn/Run/Message）→ ARQ → `runner.py`（租约+心跳）→ `execution.py`（LangGraph 流）→ `runs.py`/`messages.py`（终态收敛），每一跳都有真实职责，删掉任何一跳链路不成立；事件投影链（recorder → openai_events → event_writer → transport → SSE 相位机）同理；knowledge 摄取链（parse → chunk → embed → PG 先行/Milvus 重试）边界清晰；skills 的"授权 → 投影 → 沙盒 → 激活"链与 ARCHITECTURE.md 第 107、108、111 行不变量逐跳吻合。

问题不在链路的骨架，而在骨架周围的四类系统性模式。按对新人认知的破坏程度排序：

---

## 一、跨域系统性模式（根因级）

### 【已修复】模式 1：HTTPException 渗透全部域层（复核新增，横切最重）

原始发现（修复前）：`grep -rn "raise HTTPException" backend/yuxi/modules` 命中 **244 处、24 个文件**，遍布领域服务等模块。ARCHITECTURE.md:29/95 要求"路由只处理协议、用例归 services、路由保持薄"，但用例层直接构造 HTTP 状态码语义，路由只能透传——这也是下文幂等回执、Session 授权等模式无法沉淀为域内共享机制的结构性原因之一（五份幂等拷贝各自绑定 409+中文 detail 的 HTTP 表达）。同域内已有正确示范（`agents/services/password_login.py` 的领域异常模式、`identity/services/administration.py` 的 service 事务 Owner），但两种哲学并存，新人无从判断该学哪一种。

修复结果：全部 244 个抛出点及相应捕获已迁移为领域异常；API 适配器保留原状态码、消息与业务 code，定时、预览、工具和 worker 按领域契约消费。领域异常不携带 HTTP 属性。

### 【已收敛并复核保留】模式 2：并发敏感惯用法五份实现，无收敛点

- **【已收敛并复核保留】** **幂等回执四件套**（校验键 → sha256 意图指纹 → 拒绝异意图重放 → accepted）：agents 域内三份（`inputs.py:397` / `turns.py:498` / `threads.py:355`，细节已漂移：`threads.py:363` 的 `json.dumps` 缺 `default=str`、`_accepted` 返回形状三处不同），并泄漏到 `schedules/service.py:229` 与 `workspace/services/projects.py:38`，共五份。核心语义五份一致（复核确认无有害漂移），唯一分歧：schedules 对已软删任务允许同 request_id 重放、workspace 对已删 Project 返回 409。 **结果：三处 Agent 接口共用键、指纹和领域冲突，JSON 指纹字节由 shared/hashing 拥有；各域 accepted 形状、严格/字符串适配及删除重放按真实消费者保留。**
- **【已收敛】** **"建 Run + 绑输入消息 + set_current" 事务模式**在 4 处复制（`scheduler.py:59`、`runs.py:206`、`turns.py:107`、`cooperation.py:530`），每处重复拼装 create_run 的 15-17 个同名参数；resume Message 的同形状构造另有两份（`turns.py:87` / `cooperation.py:518`，复核新增）。 **结果：run_creation 共用创建与当前归属，交互/协作共用恢复 Message；steer 传递 Dispatch，事务提交后统一 deliver。**
- **【已收敛协议并复核保留】** **"幂等预检 → 锁 → 锁后复查" 调用点级样板**在 agents 全部入口重复约 13 处（复核新增）——这不是抽 helper 能解决的，是入口协议没有抽象表达。 **结果：共同比较由 receipts 叶子拥有，作用域、锁与锁后复查由各用例拥有；不引入隐藏提交点的回调框架。**
- **【已收敛】** **终态收敛序列三份**：`_finish_run`（runner.py:428）与 `_finish_user_cancel`（:466）之外，`process_agent_run` 的 committed 路径（:815-838）还有第三份内联副本，且内联版独有终态一致性断言（复核新增）。 **结果：共用持久结算和提交后的释放/发布序列，checkpoint 交付核对 PG 终态，取消保留 writer flush 与 owner 裁决。**

### 【已收敛并复核保留】模式 3：分层方向倒置与跨域循环

原审计识别的两个 options 文件级环均已消除；跨域读模型及类型契约按以下实际责任保留：

- **【已收敛并复核保留】** **repositories 反向依赖 services**：`agents/repositories/input.py:14`、`feedback.py:10`、`definitions.py:364`（函数内引真实用例逻辑，构成实质倒置）；`knowledge/repositories/bases.py:8` 同型。未成环仅因被依赖模块恰是纯叶子。 **【已收敛】agents 输入消息、作用域和权限移到叶子契约，治理/序列化移 service，仓储无 service 反向导入；【已收敛】共享授权校验归 identity/repositories/resource_grants，持有身份读取锁，知识仓储不再反向导入 service。**
- **【已收敛并复核保留】** **靠函数内延迟 import 维持的环**：agents 域 14 处（cooperation ↔ inputs/turns/threads 双向）；knowledge 域 graphs ↔ implementations/milvus.py；**【已收敛】skills 包内 shared ↔ content，Skill 快照运行模块反向依赖工具注册；访问契约拆分与工具门控归属调整消除目标环**。新人无法区分"解环"与"惰性加载"——复核确认 agents 域 10+ 处延迟 import 中只有 1 处是真实解环。 **【已收敛】cooperation_notifications 拆出持久通知，核心只依赖通知，协作命令单向调用核心，相关 service 导入已提到模块级；【已收敛】knowledge 图层依赖 storage 公共装配，消除对 implementations 的反向引用。**
- **【已收敛并复核保留】** **modules → 进程适配层反向依赖**：JobDefinition 与惰性字符串表均归 background_jobs/registry；有界健康协议归 infrastructure/worker_health，Compose 的 workers/health 只做执行入口。BackgroundJobContext 的 TYPE_CHECKING 引用保留 worker 执行上下文类型，不产生运行时导入。
- **【已收敛并复核保留】** **平级域互相穿透**：`background_jobs/dispatch.py:8` 取 agents 域的 `get_arq_pool`（本质是 infrastructure 级 ARQ 池访问器）；`identity/repositories/users.py:112-115` 直接 DELETE schedules 域的 ORM 表（软删使 DB 级联永不触发，应用层删除确属必要，但删除策略被两域共同拥有）；`system/repositories/dashboard.py:10-14` 直接 select agents ORM 并跨域构造 AgentRepository。 **【已收敛】identity 仓储不再导入 schedules ORM，删除账号 service 调用 schedules 清理并一起提交；ARQ 连接归 infrastructure/redis/queue，Agent Run 与 BackgroundJob 各自拥有投递策略；Dashboard 仓储直接查询报表事实，移除跨域 AgentRepository 构造。跨域 SQL 是报表读模型的真实职责，保留。**

### 【已修复】模式 4：下划线私有属性构成跨域公共契约

`BaseContext` 的真实运行时契约由 **12 个未声明的动态私有属性**构成，散布 6 个文件：`_skill_runtime_snapshot`（skills→agents 3 域消费，字符串 key 取裸 dict）、`_mcp_tool_servers`/`_registered_builtin_tool_names`（tools/runtime 写、skills 中间件防御性重写、authorization 读——**`getattr` 默认空值意味着属性缺失时工具检查静默放行（fail-open）**，复核加重的加重项）、`_enabled_mcps`（**命名与语义相反，存的是 disabled_tools 集合**，复核新增）。测试直接手工构造这些属性，证明它们已是事实上的公开契约。

**修复结果：运行派生字段在 Context 中声明且排除用户配置，Skill 快照具有类型；本地、middleware 和 MCP 来源显式登记。缺装配与未知工具拒绝，禁用集合更名为 `disabled_mcp_tools`，动态 MCP 沿用同一来源记录。**

### 【已收敛并复核保留】模式 5：同名异义与命名失真

- **【已收敛并复核保留】** **【已收敛】`KnowledgeExecutor` 区分执行器与 ORM，`KnowledgeGraphService` 表达图业务职责**；`modules/models` 域名与全仓 "models=ORM" 惯例撞车（ORM 被迫叫 tables.py，bootstrap 注册表 18 项里它是唯一例外）；`catalog`/`runtime` 在 extensions 两个子域与 agents.runtime 三方撞名，**【已收敛】工具门控归 tools/runtime，catalog/runtime 各自保留域命名空间；安装暂存更名 skills/install_draft.py**；`events.py` 是读取端而 `event_writer.py` 才写 Redis；`messages.py` docstring 说"读取"但 2/3 是 Run 结算写路径；**【复核保留】Skill 修订对应整包/单文件/根说明接口各自比较对象，目录算法已统一；409 是 HTTP 适配，领域冲突由服务返回，当前字段消费者与比较对象见 Skills 管理**。 **【已收敛】读取/订阅/写入分别命名 checkpoint_state、subscriptions、checkpoint_messages，审计读取拆为 message_audits；模型域保留已文档化的 modules/models Python 入口，ORM 继续使用 tables.py；不同命名空间各自标明职责。**
- **【已收敛并复核保留】** 16 个 repository 与 services 存在 4 对同名文件（definitions/public_items/references/state）；`repositories/state.py` 是唯一不接收 AsyncSession 的"仓储"（LangGraph checkpoint 包装）。 **结果：state 读视图更名 checkpoint_state；definitions/public_items/references 各自有持久查询和用例消费者。AgentStateRepository 由主动压缩消费 CompiledStateGraph，以保留 reducer/版本语义，不应改成 ORM Session 接口。**

### 【已收敛并复核保留】模式 6：路径与校验规则多处漂移

- **【已修复】** 沙盒绝对路径规整三处实现宽严不一（`tool_approval.py:56` 拒 `..`/`\\`/`://`，`sandbox/backend.py:96` 仅拒 `..`，`sandbox/paths.py:47` 全拒）。 **结果：共用 runtime 路径校验，直接 backend 与审批一致拒绝 `..`、URL、反斜杠和 NUL。**
- **【已收敛并复核保留】** workspace 路径校验 4 处各自实现（`://` 只有两处检查；子目录名校验三种机制三种错误类型；ELOOP 被翻译成两种异常）。 **结果：scope 与目录名按 owning 文件边界共用；目录 fd 原语保留 ELOOP，普通文件与远端 wire 映射权限异常，差异见 [沙盒机制](../../mechanisms/sandbox.md#文件边界)。**
- **【已修复】** **"用户可见路径" allowlist 三处手写且已漂移**（复核修正）：`present_artifacts.py` 缺 `..` 检查、`agents/services/artifacts.py` 缺 workdir 空值守卫、异常类型各异——`paths.py:88` 的 `is_runtime_path()` 已存在但无人复用。 **结果：产物发布/读取共用 normalize_artifact_path，拒绝跳转与空绑定，保留 HTTP 领域异常和工具 ValueError 的入口表达。**

### 【已收敛并复核保留】模式 7：同一数据/契约的多份表示

- **【已收敛并复核保留】** 摄取 chunk 使用 `IngestionChunk`，删除无人消费的文件名、source 和重复 id。PG 行、Milvus 列数组与公开检索结果保留明确转换；来源通过当前可见 PG 文件回读。
- **【已收敛并复核保留】** 文件读取共用执行器实现；真实文件元数据、虚拟目录和公开列表继续保留各自字段。Milvus 文档与图谱的公共字段、索引和 embedding 装配只有 storage 一个 Owner，固定 schema 负例拒绝单侧漂移。
- **【复核保留】** KB 列表读取持久统计避免逐库聚合，详情与文件页读取实时聚合；两者都由 `query_kb_file_stats` 定义字段，D4 在 owning transaction 更新失败后的持久投影，真实 PG/HTTP 用例验证最终一致。
- **【已收敛】** Milvus/Neo4j 共用有界线程卸载实现，各自保留 8 个预算；取消等待不会提前释放运行线程容量。
- **【已收敛并复核保留】** advisory lock 的配置同步与 checkpoint 建表裸值改为 owning 模块具名常量，保留原值；身份 DDL/应用和协作/回收共用原有常量。事务锁与 session 锁分别沿用事务结束和同连接显式释放；已有 hashtext/hashtextextended 的作用域与算法保留以兼容滚动实例，不另建可漂移注册表。配置同步与旧值互斥由真实 PG 独立连接验证；后续新键使用 owning 域前缀，不能只为命名统一迁移既有键。

### 【已完成复核与清理】模式 8：死代码与"仅测试供养"的生产代码

逐项复核结果如下；原报告的“零引用”不能直接作为删除结论，测试与正式扩展消费者也计入保留依据。

- **【已清理】`services/commands.py`**：整文件及孤立测试删除。
- **【已清理】`get_run_langfuse_link`、`get_active_run_by_runtime_scope_for_user`、`get_latest_for_thread`**：死服务与查询删除。
- **【已清理】`get_allowed_agent_access_levels`、`normalize_agent_share_config`**：两个无消费者策略函数删除。
- **【已清理】`SessionRepository.create_session`**：无消费者创建入口及孤立测试删除。
- **【已清理】Agent 域五个孤立 ORM `to_dict`（含 ToolCall 的传递死亡）**；**【已复核：保留】`Agent.to_dict`、`AgentRun.to_dict`**：前者有生产消费者，后者有测试消费者。
- **【已清理】knowledge `list_file_tree` 三层链与专用遍历辅助**；**【已复核：保留】下载入口使用的原文件路径辅助**。
- **【已清理】knowledge repository 的 `delete_by_kb_id`、`mark_graph_indexed`、`get_all`、`list_by_file_ids`、`list_by_kb_id_after`**；**【已复核：保留】知识文件存在性查询**。
- **【已清理】`chunk_file`、`infer_heading_level`、`get_whitelist_info`、`GraphExtractorFactory.supported_types`**。
- **【已清理】identity repository 的 `list_users`、`get_by_phone`、`get_admin_count_in_department`、`soft_delete`**；**【已复核：保留】`AuthUtils.generate_api_key`、`decode_token`**：真实测试资源创建与登录校验仍消费这些入口。
- **【已清理】`tools/utils.py`**：整文件删除，生产工具信息入口由 catalog 拥有。
- **【已清理】MCP 的 `get_tools_from_all_servers`、`get_servers_config`**；**【已复核：保留】单服务器工具加载入口**。
- **【已清理】`SENSITIVE_BACKEND_TOOLS`、`MILVUS_AVAILABLE` 恒真守卫、`SOURCE_CITE_PROMPT`**。
- **【已清理】`middlewares/context.py`**：两个死函数、文件及导出删除。
- **【已清理】模型适配层 `resolve_model_spec`、`is_full`、`embed_state`、同步 `compute_score` 及未消费的包级导出分支**；**【已复核：保留】模型包的 `select_model`**。
- **【已清理】transport 的 `normalize_after_seq`、`get_last_run_stream_seq`、专用序号校验及死导出**；**【已复核：保留】单事件 Redis 写入包装**。
- **【已清理】workspace `__init__.py` 无消费者导出**。
- **【已复核：保留】`fallback=`、`list_top_level_runs_after_sequence` 等仅测试引用入口**：此处记录当时保留理由，不是当前入口契约。`list_top_level_runs_after_sequence` 的测试可改用生产订阅查询并保留序号断言；其退役依据见[局部可理解性决定](../decisions/implemented/2026-11-17-local-understanding-rules.md)。其他入口仍须逐项核对。

完成依据：`7e97aef4` 与 `docs/develop-guides/decisions/implemented/2026-10-12-dead-code-cleanup.md`。

---

## 二、高优先级发现（影响正确性、性能或新人判断方向）

### 【已修复】H1. 授权中间件每个模型步骤重复 3-4 次全量重授权【性能+结构，复核确认高并加大量级】

`middlewares/authorization.py` 的 `refresh_execution_authorization`（:79-143）无任何缓存/短路：每次开新 DB 会话约 8-12 次往返（User、Run、Agent 可见性 ×2、资源配置、MCP、Skills 行锁查询），随后 `sync_agent_context_skills` 再开第二个会话做 `pg_advisory_xact_lock` + 行锁 + 目录扫描重建投影。LangGraph（langchain 1.3.17，容器内核实）的 `before_model`、`wrap_model_call`、`after_model` 之间**不存在任何授权状态变化事件**，却在同一窗口连做两遍，`after_model` 第三遍，每次工具调用第四遍；摘要路径 `_authorize_summary_request` 再加一轮。单个模型步实际成本约 **10-15 次 DB 往返 + 2-3 次文件系统扫描**。构图期（graph.py:94）已 sync 一次。重授权的 fail-closed 设计本身正确，问题纯粹是同一无事件窗口内的重复次数与每次的重量。

完成依据：输入准备仍读取当前资源，实际模型、重试、摘要与工具调用前重新授权并同步投影；返回阶段仅复查持久身份，先于审批等待点。普通无重试模型轮次从三次投影同步降为一次，真实 PG/文件测量为来源与目标哈希扫描 **6 → 2 次**、Agent 可见性 SQL **6 → 3 次**；构图同步另计。仍保留两次资源读取与三次身份检查，未引入授权缓存。原记录“边界之间不存在授权状态变化事件”的前提已更正：MCP 发现和模型等待等 await 期间可以发生外部撤权，因此输入与实际请求的检查分别保留。验证及范围见[执行边界决策](../decisions/implemented/2026-10-12-runtime-authorization-boundaries.md)。

### 【已修复】H2. 准备与刷新是两份几乎同构的平行实现（复核新增）

`prepare_agent_runtime_context`（runtime/context.py:485-536）与 `refresh_execution_authorization`（authorization.py:79-143）各自实现"读用户 → normalize 配置 → resolve_runtime_skills → 写回 `_skill_runtime_snapshot`/`_capability_limited`"全流程，互不调用、过滤规则各自维护（如准备路径不校验 Run/Thread 归属而刷新路径校验）。授权刷新每次执行的正是与准备流程漂移风险最高的复制逻辑——这是 H1 的结构根因。

完成依据：准备与刷新均调用 `refresh_agent_context_resources`，统一原始选择、资源规范化、Skill 快照、专属根正文保留与能力受限判断。刷新将同一事务已授权的 Agent 传入 Skill 解析，消除重复可见性查询。首次准备与 Run/Thread 执行刷新保留各自身份入口；资源撤权及重新授权恢复、专属根正文冻结与共享正文更新均通过真实 PG/文件回读验证。

### 【已收敛】H3. repositories/definitions.py 承担 service 职责，路由绕过 services 直采【复核确认高】

`repositories/definitions.py` 导出整个 Agent 治理策略库（:41-78 权限函数、:512-562 含越权拒绝的配置合并、:463-509 调 runtime backend 组装 DTO），`api/routers/agents/management.py:12-17` 绕过 services 直接 import 这些策略函数，:126/:136 还直接调 `repo.ensure_default_agent()`（内部 commit）。同一"配置越权校验"创建路径在 service 做（services/definitions.py:20-27）、更新路径却在 repository 做（:363-371）。复核补充：`api/routers/identity/users.py:276` 同样直接调 `AgentEnvRepository(db).upsert()`。

### 【已收敛】H4. sessions.py 名不副实，messages 表无归属仓储【复核确认高】

`repositories/sessions.py`（803 行）混合 4 类职责：Session 持久化、Message/ToolCall 写 API（:226-339）、memory 历史协议（:574-712）、字节预算响应装配（:729-803，service 层职责）。Message 表的写入分散在 **7 个 repository 文件**（sessions、input、model_audit、tool_audit、runs、public_items、references），读取另有 turn.py 与 system/dashboard。新人问"消息在哪写、在哪改可见性"必须跨 8 个文件。

### 【已收敛】H5. identity 域大量用例没有 service 事务 Owner【复核确认高，并扩大】

API Key 创建整个用例在 `api/routers/identity/users.py:150-200` 路由内（derive/commit/异常翻译），而 CLI 同构用例却由 `services/cli_auth.py:151-193` 拥有事务——同一"创建派生 API Key"两个 Owner；api_keys repository 的 update/delete 又自带 commit。部门 update、UserConfig.save 同型。**复核补充：`auth.py` 的用户 CRUD 全套（create_user :444-533、update_user、delete_user、update_profile、upload_avatar）同样整体无 service Owner**，规模大于原发现点名之和。对照域内 `administration.py`（正确示范）风格分裂。

完成依据：用户/资料/头像、部门、偏好与 Web 凭据的 service 拥有提交/回滚，API Key 仓储只 flush；Web/CLI 复用签发操作，CLI 消费与 Key 同事务。偏好使用原子 upsert，删除用户在同事务调用 schedules 的清理入口。凭据并发重放、撤销 tombstone、跨部门拒绝和提交失败均由真实 HTTP/PG 回读验证。

### 【已修复】H6. tools/runtime 与 skills 中间件的私有属性契约含 fail-open 缺口【复核确认高并加重】

见模式 4。`authorization.py:73,76` 的 `getattr(context, "_mcp_tool_servers", {})` 默认值意味着任何绕过 `resolve_configured_runtime_tools` 构造 context 的路径，工具检查会**静默放行**而非拒绝。生产路径因初始化无条件执行而当前安全，但契约没有结构保证。

**修复结果：工具检查要求来源与 Skill 快照完成装配；替代 Context 缺失字段或值为 None 均拒绝，已注册框架工具继续可用。**

### 【已收敛并复核保留】H7. skills/shared.py 是 633 行、6 类职责的依赖中心【复核确认高】

共享 Skill 用例、内置同步、运行时锁、权限原语、依赖校验、读取入口、路径工具全部在一个文件，且是包内依赖中心（与其他 15 个文件互相纠缠）；包内唯一没有模块 docstring 的文件之一。"内置 Skill 同步"在 `builtin/` 目录里找不到——在 shared.py 里。

**收敛结果：access 拥有权限、依赖及带锁读取，builtin/sync 拥有内置同步，shared 保留共享安装与治理用例，content 拥有不可变内容发布；包内消费者直接使用职责 Owner。现有扩展/测试的 shared 显式导出保留，依据见 [Skills 管理](../../agents/skills-management.md#运行时文件行为)。**

### 【已收敛】H8. knowledge 图层复制了实现层的存储装配知识【复核确认高并扩展】

40 行并发原语逐字两份（模式 7）之外，复核新增：`_get_embedding_function` 与 Milvus collection schema/索引参数在 `implementations/milvus.py` 与 `graphs/milvus_graph_vector_store.py` 各一份——这是比信号量副本更接近数据层的漂移风险。伴随 `MilvusGraphVectorStore` 构造含真实 RPC（`db.list_database`）却在每次图检索时随意 new。

完成依据：公共 schema、索引和 embedding 归 `knowledge/storage/milvus.py`，并发预算归 `storage/offload.py`。文档、实体、三元组的固定 schema 断言和单侧漂移负例独立验证装配；Milvus 执行器复用图服务与向量客户端，初始化取消先等线程落定并保存客户端，再释放锁。

### 【已收敛并复核保留】H9. 幂等回执五份 + 预检样板 13 处（模式 2 的主实例）【agents 内部三份经复核降为中：漂移是潜在的而非现行 bug；跨域泄漏两份维持中】

**结果：共同键/指纹/冲突由叶子契约拥有，严格 JSON 与字符串适配保持旧字节；各域响应、作用域、授权/锁序和软删策略按模式 2 的复核保留。**

### 【已清理】H10. `services/commands.py` 整文件死代码【复核确认高】

清理结果：文件与孤立测试已删除。原始发现：整文件实现 Channel slash command 解析，全仓（后端+前端+docs）零生产引用，仅一个孤立单测供养（当日另一份审计文档也已标记该测试孤立）。

### 【已清理】H11. knowledge `list_file_tree` 三层整链死代码【复核确认高】

清理结果：三层链与专用遍历辅助已删除，下载辅助保留。原始发现：base 抽象 → manager 转发 → ReadOnlyConnectors 拒绝实现，整条能力链零调用方；前端文件树实际走 `list_document_files`。连带 base.py:521-599 整簇私有辅助死亡。

---

## 三、复核发现的用户可见缺陷（原分析遗漏）

以下保留原始发现，并附逐项复核与修复结果；D1 原缺陷不成立，D9 原成因更正。

- **【已复核：问题不成立】D1. Milvus 知识库的 Agent 引用标题恒为"未知来源"**：`implementations/milvus.py:875,1274` 检索侧 `metadata.source` 硬编码占位符，`agents/services/references.py:309` 的 truthy 判断短路了 file_id 兜底；而摄取侧 dispatcher 记录携带真实 `filename` 却无人消费——两半数据恰好错开。dify/notion 路径正常。 **复核结论：向量、关键词、混合及图谱结果在返回前已有 PostgreSQL 文件名补齐；原缺陷不成立，检索装配到引用标题的回归测试验证现有行为。**
- **【已修复】D2. Redis 故障被伪装成配置错误**：`providers/cache.py:104-116` 模型缓存 Redis 异常时返回 `{}` 且**无 PG 回源**（rebuild 仅在启动与管理员保存时触发），故障期间所有模型解析以"未找到模型 'xxx'。可用聊天模型 (0): []"的 ValueError 暴露——基础设施故障呈现为用户配置错误，且空列表误导排查。 **修复结果：Redis 读取失败、快照缺失或非法明确抛出基础设施错误；有效空快照仍合法，未新增 PostgreSQL 回源。**
- **【已修复】D3. 知识库重名是 check-then-create 竞态**：`manager.py:498` 先查后建，`models.py:32` 的 name 列**无 unique 约束**，并发创建同名知识库可双双穿透，无数据库兜底。 **修复结果：活动知识库名称由 lower(name) 部分唯一索引约束，并发创建/重命名冲突返回 409。** 历史增量迁移由[首个 Schema 基线决策](../decisions/implemented/2026-10-19-schema-baseline-one.md)取代，当前 ORM 在空库直接创建索引。
- **【已修复】D4. 后台失败绕过统计刷新**：`services/background_jobs.py:36` → `files.py:836-858` 直接置错误态不经 `_run_with_stats_refresh`，列表页持久化统计与详情页实时聚合在此路径上漂移。 **修复结果：Job、文件错误态与受影响知识库的持久统计在同一事务收敛，沿用 KB→File 锁序；详情短期缓存仍按 TTL 过期。**
- **【已修复】D5. 内置 Skill 的 admin"可见但不可管理"语义冲突**：`repository.py:33` 让 admin 在管理页看到可管理卡片，`shared.py:493` `user_can_manage_skill` 却只认 superadmin——所有管理操作会被拒，UI 与权限规则互相矛盾。 **修复结果：列表、响应与管理用例消费同一权限解析；有效 superadmin 管理内置 Skill，admin 只读取符合可读规则的内置项。**
- **【已修复】D6. web_search 无 provider 时静默缺席**：无凭据时直接 return 不注册、无任何日志，违反"降级必须结构化、可观察"（AGENTS.md）。 **修复结果：缺少凭据或注册失败产生结构化降级日志，能力缺席有可观察原因。**
- **【已修复】D7. MCP 两条建连路径失败语义相反**：Agent 运行时吞异常返回空工具列表，管理 UI 显式报错——同一台坏服务器两个视图给出相反信号。 **修复结果：MCP 建连与工具发现异常传播为运行失败，正常零工具结果仍合法；配置装配与 Skill 中间件不再吞异常。**
- **【已修复】D8. `delete_file_graph` 在异步上下文走同步 property**：首次构造会在事件循环内做 Milvus 网络 IO（`milvus_graph_service.py:760`），恰是异步版 `get_graph_vector_store()` 要防的阻塞。 **修复结果：通过异步 get_graph_vector_store() 初始化入口取得客户端，同步构造在线程执行。**
- **【已修复（原成因已更正）】D9. `UserRepository._session()` 双模式陷阱**：`db_session=None` 时自建会话而 save/create 只 flush 不 commit——当前所有写调用方恰好都注入了 db，未来无参写调用会静默丢数据。 **复核更正与修复结果：PostgresManager 的自建会话上下文本身会 commit；实际风险是独立 save 的 detached 实体与会话不匹配。所有用户写操作现要求注入调用方事务，只 flush、由调用方提交或回滚；独立读取继续支持。**

---

## 四、中优先级发现（明显绕路/重复，影响可控）

**agents 域**

- **【已收敛】** Session 授权决策三处实现（`threads.require_thread` 规范版 / `attachments._require_user_session` / `compression` 内联，谓词与文案各异），8+ 文件复用规范版说明另两处是平行实现。 **结果：共同 Thread 边界移到 thread_access，附件与压缩复用；压缩仍只允许 active。**
- **【已收敛】** `feedback.py` 内联重写 Turn/Run 作用域查询（:95-108，谓词与 `get_for_scope` 一致），同类场景其余 15 处都走 repository。 **结果：Turn 与 Run 使用 owning repository 完整作用域查询，最终回答绑定检查保留。**
- **【已收敛】** 工具错误文案两层独立派生且默认值不同（审计"Tool 执行失败"×3 处 vs 公开"工具执行失败"），更深层：工具输出在审计层（JSON 字符串化）与公开层（结构化透传）**序列化规则分叉**，与"工具结果在该入口规整一次"的意图有张力。 **结果：工具入口规整 ToolResult，审计与公开消费相同内容/错误；checkpoint 复用文本规则。PG 回读和固定结构化/空错误 oracle 已验证。**
- **【已收敛（agents、identity）】** `repositories/` 事务归属两种模型并存（12 处自行 commit vs 13 文件绑定调用方事务）；`commit: bool = True` 默认值当前零使用（三个调用点都显式传 False）——未引爆的脚枪；`mark_thread_viewed`/`ensure_default_agent` 在用例/路由流程中真实内部提交。 **结果：Agent、环境、Message 与 viewed 仓储只 flush，移除 commit 开关；服务提交，默认/环境/viewed 无变化不空提交。identity 的 API Key 自提交和偏好内部提交/重试已移除，部门写入要求调用方事务。**
- **【已复核：保留并明确锁契约】** repositories 之间内联互构（`definitions.py:350,416` 构造 UserRepository、两个 audit repository 各自构造 AgentRunRepository、sessions/public_items 方法内构造他仓储），锁序契约无静态表达。 **结果：组合仓储复用 Run lease/output guards、用户活动校验及 Session 锁；保持各自原子事实，无 service 反向依赖。Input→Run 终态/恢复锁顺序已注释，协作与回收共用键，真实 PG/worker 竞争已验证。**
- **【已收敛】** cooperation 与核心生命周期双向依赖、14 处延迟 import；`_stream_agent_execution`（execution.py:446-651）约 200 行混合事件路由/steer 循环/checkpoint 提取/收尾，是本域可读性最差单点。 **结果：通知叶子消除反向依赖；stream_agent_execution 保留线性 steer/checkpoint/收尾，单事件投影独立可读，不再保留两套透传。**
- **【已收敛】** `middlewares/summary.py` sync/async 两份 65 行压缩流程（复核否定了"框架固有权衡"辩护：其他中间件同步份只有 3-6 行；且 sync 版生产不可达、缺授权调用）；三个近似摘要函数仅一词之差。 **结果：正式自动/主动/溢出链路共用异步摘要请求与授权；同步入口明确拒绝，原测试消费者迁移后保留原业务断言。**
- **【已收敛】** 沙盒同步配置读取归到 AgentEnvRepository，provider 消费仓储结果；异步 API 与同步沙盒保留各自执行方式，读取同一 PG 用户记录。连接串驱动转换由 PostgreSQL 基础设施拥有，三种 URL 的缺失、替换、删除和失败已真实验证。
- **【待处理】** `Workspace` 与 `ProvisionerSandboxBackend` 平行实现同名 `*_authorized_*` 五组契约，签名各异、无共享协议；`extensions/tools/knowledge/tools.py:262` 直接实例化他域 backend。
- **【已修复】** `BaseContext` 12 个动态私有属性（模式 4）；运行字段已声明，快照类型及初始化规则明确。
- **【已收敛并复核保留】** `run_type` 校验后残留不可达 else；`stream_agent_chat/resume` 纯透传双胞胎；连续两次空 commit；用户加载四种写法两种过滤口径（inputs.py:93 **与 ：343** 均无 is_deleted 过滤）；投递 ARQ 两条惯例（messages.py:580 绕过 deliver）；游标分页骨架 4 份复制。 **结果：不可达分支、双胞胎透传与连续空 commit 已清理；输入拒绝失效用户，deliver 与四处游标排序统一。用户加载的入口、worker 和权限刷新有不同事务时序，保留 owning 查询。**
- **【已收敛】** events/messages 命名错位（"events.py 只读、event_writer 才写 Redis"这一误解正是任务下发时分析者自己踩的坑——命名误导的直接证据）。 **结果：subscriptions、checkpoint_messages、message_audits 与 checkpoint_state 明确读写职责，当前消费者与决策 Owner 链接同步迁移。**

**knowledge 域**

- **【已收敛】** `KnowledgeBaseRepository.mark_deleted` 拥有 KB/文件 tombstone 和批量 outbox；清理任务用 `purge` 明确表达最终物理删除。
- **【已收敛并复核保留】** Manager 保留配置、类型分派、权限摘要和公开响应；文件操作转发负责取得当前 executor，避免路由/工具重复装配。移除统计空转发，修正 aquery 返回类型；统计列表保留批量读取持久投影，详情/文件页读实时聚合，后台失败与成功用同一聚合 Owner 更新持久值，保留 D4 事务证据。
- **【已收敛】** `file_status.py` 汇总当前写入状态和历史 `done/failed` 读取映射，列表与跨库搜索使用相同映射。目录继续写 `done`，存量文件状态不自动迁移。
- **【已收敛并复核保留】** PG/MinIO 文件读写归 `KnowledgeExecutor`，删除仅 Milvus 实现的旧抽象钩子；摄取使用 `IngestionChunk`，删除无人读取的 filename/source 和重复 id。PG 字段、Milvus 列数组、检索 metadata、虚拟目录与 HTTP 文件列表的转换保留当前消费者和各自公开字段，见[知识库机制](../../mechanisms/knowledge-base.md#存储装配与内部边界)。
- **【复核保留】** 可见范围仍由 knowledge service 拥有；identity 的共享授权校验依赖属于跨域治理，留在 Step 9 复核，不将现有授权边界内联到知识路由。
- **【已收敛】** 名称预检使用有效名称索引对应的 EXISTS，移除全表扫描和私有 `_initialized` 读取；D3 的数据库唯一约束继续拒绝并发冲突。
- **【已收敛】** 大 KB 删除使用批量 UPDATE 与 INSERT SELECT；1,200 文件真实 PG 测试证明查询量固定、outbox 幂等及其他 KB 行锁不阻塞删除。
- **【已收敛】** 评估四个钩子的持久化归 repository，保留 registry 要求的 service 导出以参与 BackgroundJob 原事务；筛选分页归 SQL。图抽取任务复用模型客户端；正常并发和首次初始化取消均有生命周期负例。
- **【已收敛】** chunking 入口与 presets 归应用层，ragflow_like 明确为本地适配且保留 source_spans 契约；移除恒为空的伪默认函数。语义分块显式接收模型 spec，模型故障显式失败。检索从同一 dataclass 叠加默认、持久选项和请求，不改写存量配置。
- **【已收敛并复核保留】** `KnowledgeGraphService` 协调 PG/Neo4j/Milvus，graphs 不再反向引用 implementations；归一化边界统一实体/关系去重，payload 只转换局部 ID。graph_utils 内均为纯转换、标识与 Cypher 模板，保留图域内工具归属；没有额外用例副作用需要搬迁。

**extensions 域**

- **【已收敛并复核保留】** skills：shared↔content 与 Skill 运行快照→工具注册的反向依赖已消除；SkillRuntimeSnapshot/RuntimeSkill 声明契约；内置与包 content_hash 共用目录/可执行位算法并修正注释；ADMIN_ROLES 使用身份权限叶子常量；安装暂存更名 install_draft。ResolvedSkill 来源字段和两张卡片的覆盖标记、uid 全集合投影补偿、不同发布点的覆盖/撤权回滚规则保留，均有当前消费者。远程安装和草稿已经共用 download_remote_skills，双下载管线结论不成立。来源/修订比较对象与保留理由见 [Skills 管理](../../agents/skills-management.md#运行时文件行为)。
- **【已收敛并复核保留】** tools：**【已清理】tools/utils.py、debug 空壳注册导入与确认无消费者的 MCP 导出**；**【已修复】web_search 通过 registry 公共注册、返回容器/元数据独立、catalog 现读注册单一来源**；**【已修复】sandbox scope 只读取 Context，共用身份解析**；**【复核保留】get_common_kb_tools 与知识库包导出**（[工具系统](../../agents/tools-system.md#知识库工具)拥有正式 Python 扩展消费者）；**【复核：已不存在】mark_for_improvement 已为 builtin，与 ToolsCardList 的 builtin/knowledge 筛选一致。**
- **【已清理】** mcp：工具加载 docstring 与真实参数一致，删除没有实体的 Global Cache & State 注释，缺失/停用 warning 由客户端加载边界统一表达。

**workspace + identity 域**

- **【已收敛并复核保留】** runtime 路径映射归 workspace/runtime_paths，Viewer 不再导入 Agent runtime；ProjectHasPendingAgentWorkError 归 workspace/errors。agents 仓储不再导入 workspace 仓储，原仓储互指已不存在；Project 归档仍查询并锁定 Agent 事实以拒绝有未完成工作的删除，Workdir 授权 service 读取 Session，保留真实用例依赖。顶层死导出已清理。namespace package 的导入、打包与完整启动有效，不新增空文件或兼容导出。
- **【已收敛】** identity/oidc.py 只保留配置与协议常量，登录与事务已归 services/oidc.py；配置由消费入口读取，API 启动仍执行 required 校验，移除 import 时固化单例。领域异常已统一。
- **【已收敛】** 密码、OIDC 与初始化共用登录结果和 HTTP Token 模型，头像沿用 User 的公开 URL；真实 HTTP 验证本地/OIDC/个人资料一致。
- **【已收敛】** `verify_api_key` 与 CLI 重放共用 `APIKey.is_valid()`，用户失效与身份种类仍按入口校验。
- **【复核保留】** 四种路径表示分别对应 DB 相对标识、Workdir 浏览、uid Workspace 与 runtime 虚拟空间，转换由路径 Owner 明确提供；Workdir 转发将已绑定根附加到每次文件操作，Viewer 使用受限 capability，个人工作区使用 uid 根。relative_path/root_path/workdir_path 分别指相对标识、浏览根与持久绑定，不能混用；依据见 [沙盒机制](../../mechanisms/sandbox.md#文件边界)。
- **【已收敛并复核保留】** 路径与子目录名共用 owning 校验；files/viewer 条目共同字段和目录优先排序共用 file_entries。异常表达保留用例差异：个人树缺失返回空列表、已绑定 Viewer 缺失返回 404；上传批次回滚、冲突和预览策略由各 service 拥有。不同物理文件协议不新增无人消费的统一抽象。
- **【复核：已由 OIDC 专项替代】** state/result 已存 Redis 并绑定浏览器，身份与会话存 PostgreSQL；同名兑换函数不存在。保留五分钟授权、一分钟结果重放和撤销回读协议，不另换存储。
- **【已收敛】** resource_ownership.py 归 system/services 的跨域治理，Agent/Skill 沿用各自发布入口；identity 只提供当前身份查询。

**五个小域 + 跨域**

- **【已复核：保留】** modules/models 是模型供应商、聊天、embedding 与 rerank 的文档化 Python 入口；tables.py 拥有 ORM。架构说明明确区分，改域名会扩大扩展接口变更，保留命名空间。
- **【已收敛】** 健康键归 infrastructure/worker_health，ARQ 连接归 infrastructure/redis/queue，JOB_DEFINITIONS 归 background_jobs/registry。并发初始化复用一个客户端；shutdown 保留关闭失败并清理共用 Redis。
- **【已收敛并复核保留】** system/services/options 拥有 Option 读取对象、校验和管理员提交；repositories/options 拥有 SQL；bootstrap/options 显式同步域定义，system/options 只汇总定义。配置服务不反向导入域定义，两个环消失。DashboardService 归 services，保留 HTTP 用例入口和会话详情组装；统计转发维持路由与查询之间的稳定边界，无新增 facade。
- **【已修复并复核保留】** 文本适配器重新抛出原异常，load_chat_model 与 select_model 明确分别服务完整 LangChain runtime 和文本任务。encode/aencode 共用 payload、解析、重试规则，保留同步 requests 与可取消 async httpx 传输及现有异常契约，真实 TCP 固定响应验证两侧成功、429 重试、400 和恢复。模型快照读取 fail-closed，Option 非敏感值缓存失败回源并用版本 CAS 防旧回填，知识配置读取可回源但删除/锁失败阻止修改；共用 Redis 连接已在基础设施，剩余容错是域策略，不引入泛型吞错包装。
- **【已清理】** 模型适配层死代码群（resolve_model_spec/is_full/embed_state/同步 compute_score/未消费的包级惰性导出分支）；有消费者的 select_model 保留。
- **【已收敛】** models/providers/service 拥有 DeepSeek OCR 当前支持的供应商凭据与端点解析，documents 只消费结果；provider/cache 和 service 共用 credentials 叶子解析，消除额外文件环。当前仅支持硅基流动的协议承诺保留，不添加供应商选择配置。
- **【已收敛并复核保留】** 回执按模式 2、锁按模式 7 的约束保留。PREPROCESSED_UPLOADS_KEY 由知识上传元数据边界拥有，router 与元数据准备共用；`_preprocessed_map` 是 FileUploadModal 和持久 Job payload 的既有 wire 键，保留字节与 URL 导入语义，从单文件处理参数中过滤。
- **【已复核：保留】** namespace package 与有显式导出的包均有有效 import/打包消费者；完整 unit 与 API/worker 启动验证导入，不为目录外观增加空文件。

---

## 五、低优先级（局部瑕疵，修复廉价）

- **【已完成复核与清理】死导出与死常量**：以模式 8 的逐项结论为准，有消费者的入口明确保留。
- **【已清理并保留公开形状】`ATTACHMENT_ALLOWED_EXTENSIONS` 恒空配置壳**：常量删除，公开 limits 保持 `allowed_extensions: []`，上传不按后缀限制，解析能力在实际入口校验。
- **【已清理并复核保留】零星参数**：agents 的 commit 开关、无消费者 is_default 透传及 knowledge 的无效 refresh 参数删除；commit_agent_definition 的 user 仍由绑定刷新消费，其他明确测试/扩展参数按模式 8 保留。
- **【已清理】`_get_file_meta` 纯别名与 `del refresh`**：下载直接调用 _load_file_meta，未使用 refresh 参数删除。
- **【已清理】`MILVUS_AVAILABLE` 恒真守卫、`SOURCE_CITE_PROMPT`**。
- **【已收敛】`base.py` 和 `manager.aquery` 注解**：KnowledgeExecutor 的配置读取返回 dict、查询返回 list，manager.aquery 返回 list[dict]；Step 8 已与实际消费者一致。
- **【复核：已不存在】`_enabled_mcps`**：Step 5 已改为 disabled_mcp_tools，配置与工具过滤消费同一含义。
- **【已复核：保留】promote_input 缺锁前检查**：先在授权 Thread 锁内重放，保留已删除/不可见资源的既有响应；该入口不增加锁前快速路径，真实 PG 与 worker 验证重放及提升/领取竞争。
- **【已复核：保留】`get_pending_steer` 丢弃返回值**：runner 和 lease 恢复只取得 Input 行锁，以保持 Input→Run 锁序；其余消费者真实使用返回行，两个锁调用点已注释。
- **【已清理】`SessionRepository.create_session` 与其返回值自校验**。
- **【已收敛并复核保留】advisory 常量**：协作/回收、身份、配置同步与 checkpoint 各由 owning 模块的具名常量维护；其余按模式 7 保留哈希作用域与历史键值。
- **【复核：已不存在】OIDC 两个近似重复函数**；当前入口按 start/callback/exchange 区分授权阶段。
- **【已复核：保留】tool_result 排除语义**：有测试 fixture 专门写入验证，属于遗留数据防御。
- **【已收敛】advisory lock 超管常量镜像的绑定注释**：DDL 与应用共用身份模型的具名常量，注释明确既有锁值的升级兼容约束。

---

## 六、诚实记录：被驳回与被修正的发现

复核驳回 2 条、实质性修正约 25 条。重要的修正：

| 原发现 | 复核结论 |
|---|---|
| `commit_agent_definition` 的 `user` 参数从未使用 | **不属实**：`definitions.py:73` 的 `refresh_binding` 分支真实使用 |
| `runtime/__init__.py` 导出无消费者 | **不属实**：`docs/agents/agent-backend-development.md:63`、`agents-config.md:102` 以 import 形式消费，是文档化的扩展 API |
| messages.py 三处调用触发隐式提交 | **不属实**：三处均显式传 `commit=False`，缺陷现不存在（默认 `True` 是未引爆的脚枪） |
| 状态筛选两链路用户可见分叉（高） | 降为低：搜索链路无任何调用方传 status，差异只影响存量数据 |
| knowledge 授权落点违反架构（中） | 降为低：ARCHITECTURE.md:42 域内条款支持现落点，未发现绕过路径 |
| `_turn_event` 与 `adapter._event` 拼同一信封（中） | 修正：event_id 派生与载荷有意不同（读端确定性重放 ID），可合并的仅骨架 |
| tool_result 是幽灵值 | 修正：4 处测试 fixture 专门写入验证排除行为，models 列注释列其为合法值 |
| middlewares/context.py 死代码（高） | 降为低；**【已清理】**两个函数、文件与导出已删除 |
| catalog/runtime 三方撞名（高） | 降为中：各自命名空间无冲突，代价是门控语义分散 |
| identity/oidc.py"services/oidc.py 是薄壳" | 修正：后者是事务 Owner 与登录编排，违反的是用例归属与风格分裂 |
| skills 路径解析 4 处重复（中） | 降为低：校验语义 Owner 唯一（`validated_shared_skill_parts`），重复仅一行 joinpath 包装 |
| tools/runtime 工具名三处硬编码 | 修正：tools/runtime.py 用 `.name` 属性引用（正确写法），字符串硬编码实为 2 处 |
| 幂等回执 threads 缺 `default=str` 是现行 bug（高） | 降为中：现调用只传字符串，属潜在漂移；已发生的漂移在 `_accepted` 返回形状 |
| repository 反向 import"违反 ARCHITECTURE.md" | 修正：文档无依赖方向明文；definitions.py:364 一处构成实质用例倒置，另两处是契约放错层 |

方法可信度抽查：横切分析的 34 条域级边中抽样 3 条人工验证，import 计数零误差。

---

## 七、实施步骤与验收范围

前四步保留完成状态，后续合为五组。按共同根因、运行链路和修改边界组织：运行授权与 Skill/工具装配一起处理，幂等与 Run/消息生命周期一起处理，知识存储与知识用例一起处理；命名、局部清理和文档随所属组完成。具体修改先在当前源码和消费者处复核，再按 [Spec Loop](../spec-loop.md) 确认方案，原审计快照中的行号与数量不作为新实现的直接依据。

| Step | 状态 | 合并后的范围 |
|---|---|---|
| 1 | 已完成 | 模式 8：确认消费者后清理死代码，`7e97aef4` |
| 2 | 已完成 | D1–D9：复核与必要修复，`1432b719`；D1 不成立，D9 成因更正 |
| 3 | 已完成 | 模式 1：领域异常与 HTTP 表达分离，`a17c1503` |
| 4 | 已完成 | H1/H2：资源准备与刷新共用、投影成本收敛，`f088bc28` |
| 5 | 已完成 | 运行与扩展边界：Context、授权、路径、摘要、Skill、工具及沙盒配置读取 |
| 6 | 已完成 | Agent 事务与生命周期：幂等、Run 创建/恢复/终态、消息与用例分层 |
| 7 | 已完成 | 身份用例：API Key、用户/部门/配置、登录/OIDC 与事务归属 |
| 8 | 待处理 | 知识链路：存储装配、数据表示、管理/删除/统计、图谱/评估/分块与性能 |
| 9 | 待处理 | 共享技术与跨域依赖：进程适配、模型/缓存、剩余命名及整份审计闭合 |

### Step 5：【已完成】运行、Skill 与工具边界一起收敛

覆盖 H6/H7、模式 4/6，以及 agents/extensions/workspace 对同一运行契约的重复实现。声明 Skill 快照、工具来源、注册集合和 MCP 禁用集合的初始化与消费规则，缺失必要装配时拒绝执行；一起处理 `_enabled_mcps` 命名、注册表可变活引用、web_search 私有注册、catalog 两套读模型及 MCP 的失实注释/重复文案。H1/H2 已保留的输入、实际请求与返回身份检查继续有效。

整理 shared/content 与 skills/tools 双向依赖、ResolvedSkill 来源表达、revision/content_hash 多语义、安装双管线、原子发布回滚和重复角色常量。摘要 sync/async 压缩流程与三个近似函数在同一运行装配中复核：按正式扩展及测试消费者决定同步入口去留，保留流程共用规则和相应授权语义。投影刷新粒度与补偿机制按当前消费者复核，保留有依据的策略。

路径部分覆盖 runtime 与 UserWorkspace 的各自校验、子目录名、异常翻译、文件条目组装/排序、Workspace/Provisioner 文件协议、sandbox scope、产物发布/读取及 Workdir 转发。各路径空间在其 owning boundary 校验，通过明确转换连接；同时核对 `..`、URL、空绑定、符号链接、ELOOP。沙盒 provider 的裸 psycopg 读取、AgentEnvRepository 归属与连接串规整一起处理，保留同步消费者所需的执行边界。相关的 runtime/catalog/draft 命名、空 facade、死参数及文档在此组收尾。

验收：真实 HTTP/PG/文件与正式图、worker、Docker 沙盒验证合法工具、动态 Skill/MCP、未注册/未激活/禁用工具、替代 Context 缺失装配、撤权与路径隔离。回读主动/自动摘要、溢出与撤权边界的历史/checkpoint，以及安装失败回滚、编辑冲突和投影字节；provider 用真实 PG 对照 AgentEnvRepository，验证不同连接 URL、缺失/删除配置与同步启动行为；同步/删除入口核对消费者，修订比较对象明确，目录读取不能修改全局注册结果。新拒绝边界有能恢复目标缺陷的负例。

完成结果：H6、Context 与路径缺口已修复；Skill 职责/目录哈希/工具来源与读目录收敛；有当前消费者的来源、修订、投影、文件协议和回滚差异已逐项写明保留依据。实现与取舍见[运行扩展边界决定](../decisions/implemented/2026-10-12-runtime-extension-contracts.md)。

验证：隔离 Compose 的完整 non-slow unit **2700 passed、60 skipped**；相关真实 PG/HTTP integration **93 passed**；worker、Docker 沙盒、撤权与专属 Skill E2E **15 passed**，个人 Skill 的确定性真实读取 E2E 单独复验 **1 passed**。个人场景原先依赖环境默认模型，没有产生输出；测试改为明确注册本地 HTTP 回放，保留最终输出、Run 清单、来源字节与无共享投影的独立断言。六组隔离缺陷恢复各自在业务断言上变红，恢复修复后 22 项通过。工程契约与 64 项 unit、变更 Python Ruff、相对链接/docs build 和 diff 检查通过；全新独立 Review 在提交前核对最终候选。真实远程源故障恢复、Kubernetes 与生产部署未运行，unit skip 不记为通过。

### Step 6：【已完成】Agent 事务与生命周期一起收敛

覆盖 H3/H4/H9 与模式 2。沿接收、消费、执行、恢复、结算整理五份幂等惯用法及锁前/锁后复查、四份 Run 创建与两份 resume Message、三份终态序列、ARQ 投递、cooperation/核心生命周期双向依赖和 execution 的混合职责。Receipt 键、意图与冲突语义共用一致部分，各域响应和已删除资源重放策略保留；受影响的 advisory lock 常量、DDL 镜像及锁序明确归属。

治理/配置流程、Session 授权与 feedback 作用域查询随这条链路回到 service；Message/ToolCall 写入、Memory 历史、字节预算/分页装配按真实职责整理。用例拥有事务，处理 repository 内部提交、`commit=True`、default Agent/mark_thread_viewed、连续空提交、用户失效过滤和工具结果序列化/错误文案。完成、取消与已持久终态保留必要差异，状态提交后发布。events/messages/state 命名、无效分支、透传、游标及丢弃返回值在所属入口一并清理。

验收：真实 HTTP/PG 并发与 worker/SSE E2E 覆盖同键同/异意图、用户/APP 隔离、锁等待变化、回滚、FIFO、steer、回答/审批/协作恢复、成功/失败/取消、重复投递、lease 失效及崩溃恢复。回读 Receipt/Input、唯一 current Run、正确 Message/Turn/Run 绑定、result_run_id、同 Run 结果/用量/错误和终态事件；schedules 软删重放、workspace 删除冲突、公开可见性与预算保留各自契约，不从相邻 Run 猜结果。

结果：上述共同部分、治理事务、消息/Memory、读写命名与执行投影已收敛，必要差异逐项标为复核保留。全量 unit **2707 passed / 60 skipped**；真实 HTTP/PG **207 项独立场景通过**；worker/SSE E2E **32 项通过**。E2E 首轮缺少协作 replay 时两项超时，补齐后两项重跑通过；失败记录保留，不将首轮写为通过。六类隔离缺陷恢复中，五类在业务不变量断言失败、一类在失效用户入口错误归属断言失败；候选恢复后 **9 项 PG oracle 通过**。工程契约及 **64 项工程检查 unit**、变更 Python Ruff/format、相对链接、docs build、diff 与独立 Review 随最终候选完成。正式部署、真实外部模型与 Kubernetes 未验证，skip 不计通过。依据见[事务与生命周期决策](../decisions/implemented/2026-10-12-agent-transaction-lifecycle.md)。

#### 【已优化】Step 5/6 结构审阅的六项后续

- **【已优化】完整 Skill 快照直接消费**：清除中间件、工具注册与刷新中的字典兜底；None 表示未准备，实际授权入口仍拒绝缺失装配，激活范围保持有效 Skill 交集。
- **【已清理】环境和路径透明转发**：删除无消费者的 normalize_env、load_user_agent_env 及 sandbox._normalize_path；直接调用环境仓储和 owning 路径边界，保留领域错误翻译。
- **【已优化】Skill 基础读取的导入边界**：MCP service 只在实际依赖校验时加载。
- **【已优化】模型前事务提交明确归属**：execution 主流程提交 runtime/附件/trace 准备事务；追踪 helper 只写入，失败或取消回滚并结束 observation 后进入既有收敛，图不启动。
- **【已优化】终态读取与 steer 裁决**：复用同事务已经锁定的 Thread/Turn 和裁决后的 steer；保留 current Run/lease、锁顺序、批次消费与提交后投递。
- **【已优化】checkpoint 事务参数**：显式传入 AsyncSession，局部复用仓储，移除以 SessionRepository 承载整个事务的转发。

六项合为一个后续提交 `e1011628`；决定与边界见[运行结构简化决定](../decisions/implemented/2026-10-12-agent-structure-simplification.md)。该结构优化提交不包含 Step 7；身份后续结果见下节。

验证：完整 non-slow unit **2713 passed / 60 skipped**，相关真实 PG **113 passed**，真实 HTTP **22 passed**，worker/SSE、Docker 沙盒、协作及专属/个人 Skill E2E **34 passed**。协作 **7 项**串行复验及含 checkpoint 的 PG/HTTP **23 项**串行复验均通过，与 PG 113 项合计 **136 个独立 integration 场景**。准备/提交失败及取消的六项负向案例证明图未启动、未提交事务被回滚与 observation 实际结束。完整 unit 曾因授权缺属性拒绝契约失败，保留该授权 guard 后全量通过；扩展 integration 首轮 114 项因跨环境 inventory 清理在 setup 报错，限定本环境 prefix 后重新执行。HTTP/PG checkpoint 测试曾因旧接口接线失败，迁移到 owning checkpointer 后业务断言通过，但与 E2E 重叠的 teardown 清理报错；串行复验 23 项全数通过，清理错误不计通过。ignored provisioner 副本只过滤本环境 inventory，生产代码、业务执行与 oracle 保持候选。工程契约、64 项工程检查 unit、变更 Python Ruff/format、相对链接、docs build、diff 与独立 Review 随最终候选执行；真实外部模型、Kubernetes 与生产部署未验证，skip 不计通过。

### Step 7：【已完成】身份用例与事务归属一起收敛

覆盖 H5、API Key Web/CLI 双 Owner、repository 自提交、用户 CRUD/资料/头像、部门与 UserConfig。service 统一用例及提交/回滚，HTTP 与 CLI 消费同一领域结果；登录响应、avatar、API Key 有效性判断、OIDC 用例归属/配置单例/同名函数一并处理。账号删除与 schedules、resource_ownership 的跨域策略通过明确域边界编排。OIDC state/login_code 按当前进程部署与有效期契约决定存储，不额外扩张部署能力；身份命名、包布局与文档随本组完成。

结果：身份写用例与仓储提交边界、凭据签发和登录投影已收敛；OIDC 的 Redis/PG 存储、短期重放协议和 namespace package 经复核保留。完整 non-slow unit **2741 passed / 60 skipped**，真实 HTTP/PG/Redis/MinIO **61 passed**；恢复仓储内部 commit 的独立负向试验在数据库回读断言失败。CLI 提交失败负例暴露并闭合回滚缺口；旧测试的调用路径与 OIDC Redis 事件循环绑定已修正后复验。命令、取舍与未验证范围见[身份事务决策](../decisions/implemented/2026-10-13-identity-usecase-transactions.md)。

### Step 8：【已收敛并复核保留】知识存储与业务用例一起收敛

覆盖 H8、模式 7 及 knowledge 剩余问题。统一 Milvus schema/索引/embedding 装配与并发卸载原语、图客户端生命周期、chunk/KnowledgeFile 内部表示；必要的摄取、持久化、检索与公开形状通过明确转换保留。graphs/implementation 依赖、过时抽象、`build_graph_payload` 去重和 graph_utils 用例归属随同一知识链路处理。

整理 manager 门面/转发、软删归属、状态词表与历史映射、统计交付、重名全表扫描/_initialized、删除 N+1、evaluation 钩子/分页、每 chunk 模型重建、chunking 隐式参数/降级/移植边界及检索默认值。KnowledgeBase 多义、MilvusGraphService 命名、类型注解、相关死辅助与文档在所属实现一并处理；D3 唯一约束与 D4 失败统计保持有效，存量配置不静默迁移。

验收：真实 HTTP/PG/worker、Milvus 和图谱验证摄取、各检索模式及来源、索引、软删、统计、分页和失败恢复；独立固定输入验证分块、检索与图谱去重，回读节点/边/文件关联。实际测量客户端重建、并发预算、大 KB 查询/锁范围；单侧装配漂移有负例，配置错误与降级可观察，面板与运行默认值一致。可选服务缺失明确记为未验证。

结果：公共装配、有界卸载、图客户端/抽取模型复用、批量软删、评估 SQL 分页与分块契约已收敛；Manager 类型路由、公开形状转换和统计交付差异有当前消费者。真实 PG/Redis/HTTP 与图谱集成 76 项、完整 Milvus/图谱 E2E 和 non-slow unit 2747 项通过（60 项按环境跳过）。1,200 文件删除查询量固定，客户端并发与取消有独立负例，单侧 schema 漂移能恢复缺陷并被拒绝。命令、证据范围与保留取舍见[知识存储决策](../decisions/implemented/2026-10-13-knowledge-storage-usecases.md)。

### Step 9：【已收敛并复核保留】共享技术依赖与剩余审计一起收尾

覆盖模式 3 剩余的健康键、ARQ 访问器、JOB_DEFINITIONS、system/dashboard 跨域查询、options 文件级环与平级域穿透；整理 models 的 sync/async HTTP、加载双入口、异常类型、PG/Redis 共用容错部分和 documents 供应商解析。共同技术/叶子契约回到合适位置，各域缓存失效及业务策略保留原 Owner。此组处理跨域 models/包命名、system/options facade、尚未归属的 API limits、category 标签、带内魔法键及局部清理；域内命名与文档由前面各组随实现完成。

验收：符号/导入分析确认目标倒置和真实循环消失，完整 API/worker 启动证明 readiness、注册、能力发现与投递；真实故障/恢复证明异常与缓存结果。删除/改名搜索包括文档、注册、字符串导入、测试及协议消费者，明确保留的测试/扩展入口继续按当前承诺验证。

第一至第五节逐条闭合：复合条目按实际子范围标为修复、清理、有依据的保留/不成立，或明确未完成，不凭相关提交整行关闭；原问题计数保持历史快照。每组连续实施并验证、最终合为一个提交，更新 owning 文档、非平凡决策和本报告；逐项检查与验收属于组内工作，不再各列独立 Step。工程契约、相关真实链路、文档链接/build 和全新独立 Review 通过后交付，未运行与 skip 不记为通过。

结果：完整 non-slow unit 2750 项、真实集成 20 项及完整 Milvus/图谱 E2E 1 项通过，60 项根目录相关 unit 按环境跳过；恢复无锁池初始化或异常改型包装的独立实验均被对应断言拒绝。实际验证命令见[共享技术决策](../decisions/implemented/2026-10-13-shared-technical-boundaries.md)。API 的分页 Query 限制、知识上传 100 MiB 与 Agent 附件 5 MiB 按各自输入/用例 Owner 保留，公开 limits 从附件服务生成；未发现需要中央配置的共同 consumer。

---

## 附：分域问题计数（原始审计快照，未扣除已完成项）

| 域 | 高 | 中 | 低 | 备注 |
|---|---|---|---|---|
| agents | 6 | 14 | 15 | 主链路骨架本身无问题 |
| knowledge | 2 | 15 | 16 | manager.py 与 graphs↔impl 是重灾区 |
| extensions/skills | 1 | 10 | 4 | 命名可学习性最差的域 |
| extensions/tools+mcp | 1 | 7 | 4 | 私有属性契约含 fail-open |
| workspace+identity | 2 | 9 | 4 | 事务 Owner 缺失面最大 |
| models/schedules/bg-jobs/documents/system | 0 | 5 | 5 | 唯一可靠性问题在 ModelCache |
| 跨域横切 | 3 | 4 | 2 | 模式 1-3 均为横切 |
