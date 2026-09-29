# 删除无消费者入口并收敛重复运行时适配

状态：archived
类型：simplification
Owner：web/src/components/ToolCallingResult/toolRegistry.js

相关事实由下列源码 Owner 分别持有：

- Agent 运行上下文装配：`backend/package/yuxi/services/chat_service.py`
- 消息反馈事务与查询：`backend/package/yuxi/services/feedback_service.py`、`backend/package/yuxi/repositories/dashboard_repository.py`
- Mention 配置与搜索：`web/src/composables/useAgentMentionConfig.js`、`web/src/components/MessageInputComponent.vue`
- 知识库管理：`backend/package/yuxi/knowledge/manager.py`
- Sandbox 获取与缓存：`backend/package/yuxi/agents/backends/sandbox/provider.py`
- 工具调用参数解析：`web/src/components/ToolCallingResult/toolRegistry.js`
- Run 取消信号：`backend/package/yuxi/services/run_queue_service.py`
- 正式文档页面发现：`docs/.vitepress/config.mts`

## 问题

仓库中同时存在无生产消费者的 Agent runtime、反馈 Repository、旧 Mention composable、知识库管理别名和 Sandbox 获取入口，它们与真实链路并存并形成虚假的第二语义 Owner。多个工具组件还重复解析同一参数协议，Run 取消信号的批量发布与失败语义也分散在多个 service。

## 决策

- 删除无仓库内消费者的 `agent_runtime_service`、`message_feedback_repository`、`useMention`、三个知识库管理别名和 `ProvisionerSandboxProvider.acquire`。Sandbox 测试与生产代码一样使用 `get(create_if_missing=True)`。
- 工具参数协议由 `toolRegistry.js` 的 `parseToolCallArgs` 拥有；十七个只需字段级参数的组件直接消费解析结果。`BaseToolCall`、MySQL 摘要和 Task 内嵌调用的 malformed 原文展示有独立用户可见语义，不强行并入。
- Run 取消信号的单次 best-effort 发布和批量并发由 `run_queue_service.py` 拥有；`chat_service`、`agent_run_service` 和 `run_worker` 只提供 Run ID。PostgreSQL 事务始终先提交，普通 Redis 失败不覆盖权威终态，调用方任务取消继续传播。
- `docs/vibe/` 不是正式文档源，VitePress 页面扫描通过 `srcExclude` 排除该目录；正式文档的链接和资源错误仍会使构建失败。
- Durable Task registry、下载响应、Agent 管理状态和对话授权查询保持原有 Owner，不纳入本次收敛。

## 替代方案

- 保留无消费者入口并标记弃用：仍然需要理解两套入口，也会继续允许测试偏离生产路径。
- 保留每个工具组件的本地 parser：这些组件没有独立协议，复制不提供隔离价值。
- 在业务 service 中保留取消批量 helper：Redis 控制面的失败策略会继续分散。
- 新建统一 runtime 或前端工具框架：范围超过已确认的重复面，会引入新的迁移和抽象成本。

## 后果

- 内部维护者只需跟踪真实 service/repository/composable 和单一协议 helper；本次净删除为主，没有新建通用框架。
- 工具调用显式提供空 `args` 时不回退到 `function.arguments`，malformed JSON 统一解析为空对象；需要展示原文的三类路径不受影响。
- 本地临时计划可以保留未完成资源引用，不会污染正式文档构建。
- 仓库外如果直接 deep import 被删除的内部模块或方法，不获得兼容层；这些表面没有已文档化的外部契约。

## 验证

- 旧能力不存在：全仓符号搜索确认旧模块、旧 composable、知识库别名、Sandbox `acquire`、十七个本地参数 parser 和业务层取消 helper 均无消费者或已删除。
- 后端定向 unit 207 项通过；全部非慢 unit 1699 项通过、44 项按环境跳过；Ruff 与 format check 通过。
- 真实 PostgreSQL/HTTP integration 13 项通过，Sandbox/Project Workdir 重建 integration 1 项通过；deterministic API→worker→SSE→PostgreSQL 取消 E2E 1 项通过。
- Web unit 199 项通过，其中包含对象参数、`function.arguments`、空字符串、malformed JSON 和本地 parser 回归的负向案例；lint 与生产 build 通过。
- 工程契约检查、决策记录规则单测、docs build 和 `git diff --check` 通过；docs build 在保留含缺失截图引用的 Git 忽略 `docs/vibe/` 草稿时仍成功，证明临时目录没有进入页面集。
- 重新引入条件：只有出现仓库内或已文档化的外部消费者，并能说明独立语义、事务或协议边界及相应测试时，才重新引入被删除入口。
