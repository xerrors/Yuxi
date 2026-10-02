# 用户级 Memory 与历史回看边界

状态：archived
类型：feature
Owner：backend/package/yuxi/agents/middlewares/memory.py

## 问题

用户配置已经持久化 `enable_memory`，UserWorkspace 也初始化 `agents/MEMORY.md`，但旧运行时会把
`AGENTS.md`、`USER.md` 与 `MEMORY.md` 一并注入所有共用上下文。关闭开关不能关闭长期记忆，SubAgent
也会得到用户级 Memory；同时主 Agent 没有语义受限的长期记忆写入入口，也不能按需搜索和读取当前用户的
历史对话。

通用 `edit_file` 拥有比长期记忆更大的路径权限。把旧对话全部预装进 prompt，又会引入无界输入、过期信息和
历史提示注入。本机制需要在不新增 Memory 数据库、索引或后台总结任务的前提下，闭合开关、主/子 Agent
边界、受限写入和按需历史回看。

## 决策

`enable_memory` 是主 Agent Memory 能力的唯一开关。通用 `build_agent_input_context()` 只装配
`AGENTS.md` 与 `USER.md`，继续供 chat、resume、agent state 和 SubAgent 使用。只有 Chatbot graph
构造 `YuxiMemoryMiddleware`：它在每个新 Run 装配时重新查询 `UserConfig`；开关开启才读取最多 24 KiB
的 `/agents/MEMORY.md`、注入低信任数据提示并注册三个工具，关闭时既不读取文件也不注册工具。SubAgent
graph 不构造该 middleware，因此没有用户级 Memory prompt 或工具。

`remember_memory(content, replaces=None)` 不接收路径或身份参数，只操作当前 `uid` 的固定 Memory 文件。
`content` 和 `replaces` 各自最多 4 KiB UTF-8 bytes；新增追加至文件末尾，规范化内容已经存在时返回
`unchanged`；纠正只允许对完整文件中的唯一精确旧文本执行一次替换。源文件和结果文件均限制为 128 KiB。

工具 closure 从 `ToolRuntime.context` 获取 Chat service 写入的 `uid`、thread、run、request 和 `worker_id`。
执行处在同一数据库事务中取得按 uid 命名的 PostgreSQL transaction advisory lock，再以
`SELECT FOR UPDATE` 锁定 AgentRun，并复用 lease-owner 校验。Run 必须为当前用户同一 thread/request 的
`running` 顶层 chat 或 resume，worker owner 与未过期 lease 必须匹配；取得锁后再次读取
`enable_memory`。缺失或伪造身份、SubAgent、ownership 更替、过期或终态 Run、运行中关闭开关均
fail-closed。

文件发布由 `Workspace.replace_authorized_file()` 执行：在目标目录 fd 下创建 `O_EXCL | O_NOFOLLOW`
临时普通文件，完整写入并 `fsync`，再以同目录 rename 发布并 `fsync` 目录。advisory lock 覆盖授权重检、
有界读取、判重或替换和发布，跨 worker 的同 uid 读改写不会最后写入覆盖前一个结果。发布前失败保留旧文件。

`search_thread_messages` 和 `read_thread_messages` 直接查询 PostgreSQL Conversation、Message 与 ToolCall。
可见性限定为当前 uid、active、非 `agent_call` / `agent_evaluation` source 且不是持久化 SubAgent child
的普通线程。Message 只允许 user/assistant role，并排除 tool_call/tool_result 类型。搜索不加载 ToolCall，
最多返回 10 条、每条 512 bytes、总响应 16 KiB；读取最多 20 条、每条内容 8 KiB、内容合计 32 KiB、
最终响应 64 KiB。

读取默认完全不查询 ToolCall。只有 `include_tools=true` 时才单独加载 id、name、input、output、status、
error allowlist，最多 10 条、单条 4 KiB、合计 16 KiB。所有截断遵守 UTF-8 边界；Message metadata、
image、附件、feedback、system/tool 内容和工具类型 Message 不进入响应。middleware 明确把 Memory 与历史
标为低信任数据，历史指令不能覆盖当前系统约束，也不能触发长期记忆写入。

UTF-8 字节预算截断由 `yuxi.utils.string_utils.truncate_utf8()` 提供；Conversation repository 继续拥有历史
响应的字段选择、各级预算和截断标记策略，通用工具不接管协议结构或 JSON 响应裁剪。

语义 Owner 分工如下：Memory middleware 拥有主 Agent prompt 和工具装配；Chat service、AgentRun
repository 和 PostgreSQL lease 拥有可信执行身份；Workspace filesystem 拥有原子发布；Conversation
repository 拥有历史可见性、字段 allowlist 和字节预算；通用敏感工具审批清单不接管这个无路径的专用写入。

## 替代方案

- 继续使用通用 `edit_file`：代码更少，但模型可以选择路径，授权和审批语义大于 Memory 行为本身，拒绝。
- 建立独立 Memory 表、向量索引或知识图谱：便于结构化检索，但会与用户可编辑的 `MEMORY.md` 形成双重事实源，
  一期拒绝。
- 自动总结、compaction flush、每日记忆或 after-agent 写入：减少显式操作，但引入自主持久化、来源追踪和清理
  策略；在基础授权边界闭合前拒绝。
- 按 Project 建立 Memory：隔离更强，但会静默改变现有用户级文件的作用域。只有出现明确产品需求时才以独立
  Owner 提案。

## 后果

用户级 Memory 跨 Project 共享，主 Agent 只能在用户明确要求记住或纠正时写入，不自动保存画像、凭据、
临时信息、推测或当前 Project 私密事实。用户删除和大文件整理继续使用 Workspace 文件界面；MEMORY.md
被删除或清空后读取侧视为无记忆（不注册 middleware），下一次 `remember_memory` 新增按空文件语义原子
重建该文件。

专用写入不触发通用文件审批，减少一次确认，但自然语言中的“明确要求”仍由模型策略判断。固定路径、执行处开关、
顶层 lease、可见工具事件和无后台调用缩小影响；若线上仍有误写，应为此工具增加定制审批，而不是恢复任意路径写入。

Memory 写入依赖 PostgreSQL 可用性；数据库或锁失败时保留旧文件，不降级为无锁写入。rename 已成功但结果返回前
进程崩溃时，新增重试通过内容判重收敛为 `unchanged`；精确替换重试因旧文本不存在而返回冲突，调用方需要重新
读取文件。本期不引入 mutation id。

## 验证

| 当前主张 | 直接证据 | 负向案例 | 结果 |
|---|---|---|---|
| 关闭开关或共用/SubAgent 上下文不会泄漏 Memory，开启时只注册固定工具 schema | context 与 middleware unit 检查最终上下文、prompt、工具名称和公开参数 | 关闭时 middleware 为 `None`；工具 schema 不含 uid/path/worker | Passed |
| 写入只允许当前有效顶层 Run，并原子串行更新固定文件 | 真实 PostgreSQL integration 使用独立连接并发写同一 uid，最终重读文件；repository/service/filesystem unit | 伪造 owner、运行时关闭开关、超限源文件、发布失败、重复新增及零/多匹配均拒绝或返回规定状态，文件不被破坏 | Passed |
| 历史查询遵守主线程可见性、字段 allowlist 和总预算 | SQLite unit 覆盖预算；真实 PostgreSQL integration 重读最终序列化值 | SubAgent child、tool role/type、metadata 和 image 唯一标记不泄漏；默认无 ToolCall，显式开启后仅返回 allowlist | Passed |
| 后端回归和静态规范不退化 | 宿主及 Compose API 容器内运行非 slow unit；聚焦 Ruff check/format；真实 PostgreSQL integration | 全量 unit 会覆盖共用上下文、graph、repository、service 与 filesystem 既有契约 | Passed：宿主 1520 unit；容器 1481 passed、39 skipped；50 聚焦 unit；宿主及容器各 3 integration |
| 工程契约与正式文档保持有效 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts`；`cd docs && pnpm run build`；`git diff --check` | decision 生命周期、相对链接或格式错误使门禁失败 | Passed：contract verifier、61 个 unit、docs build；最终 diff check 随交付执行 |
| 真实产品链路完成“明确要求记住 → 新线程可用” | 配置模型和 sandbox provisioner 后运行真实 HTTP + PostgreSQL E2E，最终重读文件和新线程响应 | 关闭开关重跑时不得写文件或暴露工具 | Not run：当前本地未配置可复现的模型与完整 E2E provisioner 环境 |
