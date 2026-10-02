# 0.7.3 发布决策变更记录

本页汇总 v0.7.3 发布前生效的全部 86 份工程决策记录（2026-08-16 至 2026-09-08），按主题归并为 14 组；原文件随发布移动到本目录，状态统一改写为 `archived`，正文未改写。各记录保存当时的问题、决策、替代方案、后果与验证；机制的当前行为由代码、[机制详解](../../../../mechanisms/index)与仍在 `implemented/` 的后续记录拥有，本页不构成运行时事实源。生命周期约定见[工程决策记录](../../README.md)。

归档边界以决策日期为准：v0.7.3 正式 tag 创建于 2026-09-09，发布日期当天作出的[候选发布验证与 CLI 独立发布](../../implemented/2026-09-09-release-validation.md)与[QA 之外的书籍分块采样修复](../../implemented/2026-09-09-book-chunk-sampling.md)两份记录保留在 `implemented/`，进入下一个发布周期。日期早于 2026-09-02（v0.7.2 正式 tag）的记录多数随 v0.7.2 交付，2026-09-03 之后的记录属于 0.7.3 周期；用户可见变更的发布口径以[版本变更记录](../../../changelog.md)为准。

## 工程信任与文档体系

这一组建立 0.7.x 开发流程的基座：以语义 Owner 与 verifier 派生工程契约、以 Spec Loop 约束非平凡变更、以 Diátaxis 四类页面与证据强度词约束文档写作；首页品牌刷新是该信息架构的视觉落地，四份流程记录共享同一工程信任机制。

- [2026-08-16-agent-first-engineering-trust](01-engineering-trust-and-docs/2026-08-16-agent-first-engineering-trust.md) — 以语义 Owner 为权威，`verify_engineering_contracts.py` 从代码、测试、workflow 与决策记录派生审计投影，每项检查配负向测试，trust.yml 在 main 与 PR 上无路径过滤阻塞。
- [2026-08-16-yuxi-spec-loop](01-engineering-trust-and-docs/2026-08-16-yuxi-spec-loop.md) — Spec Loop 统一 Scope、权威重建、Propose、实现、Verify、独立 Review、Converge 与 Learn；非平凡变更实现前先建 tracked proposed，与 PR 共用同一组验收字段。
- [2026-08-17-documentation-information-architecture](01-engineering-trust-and-docs/2026-08-17-documentation-information-architecture.md) — 文档按教程、参考、机制、治理、决策与事故分层，机制页从真实装配链说明状态、权限与失败语义；公开路径保持不变。
- [2026-08-26-human-centered-documentation](01-engineering-trust-and-docs/2026-08-26-human-centered-documentation.md) — 采用面向读者的四类文档与 `Passed`/`Inspected`/`Not run`/`Inferred` 证据强度词，站点启用严格站内链接检查。
- [2026-09-04-docs-home-brand-refresh](01-engineering-trust-and-docs/2026-09-04-docs-home-brand-refresh.md) — 文档首页以 `#f3ba32` 品牌色重构为七类任务入口；大图与 OG 图由项目 OSS 交付，交互尊重 reduced motion。

## 依赖治理与供应链

审计 gate 与更新策略成对拥有依赖治理：前者拥有漏洞与许可证审计，后者拥有常规版本更新（Dependabot 常规 PR 关闭、安全更新保留）；工具链刷新与漏洞修复是该策略的两次执行落地。

- [2026-08-18-dependency-supply-chain-gates](02-dependency-governance/2026-08-18-dependency-supply-chain-gates.md) — 依赖审计以 shipping 锁文件为事实来源，`uv audit`、`pnpm audit` 与 `pip-licenses` 许可证报告为 gate；删除无消费者的 `langgraph-cli[inmem]`。
- [2026-08-19-dependency-update-policy](02-dependency-governance/2026-08-19-dependency-update-policy.md) — Dependabot 七类生态保留周计划但 `open-pull-requests-limit: 0` 关闭常规 PR；升级改由安全、兼容或功能需求触发并人工执行。
- [2026-08-26-dependency-toolchain-refresh](02-dependency-governance/2026-08-26-dependency-toolchain-refresh.md) — 本地、manifest、Docker 与 CI 固定 pnpm 11.24.0 与 uv 0.12.6，pnpm 11 安全 overrides 迁至 `pnpm-workspace.yaml`，四份锁文件刷新。
- [2026-09-05-dependency-vulnerability-remediation](02-dependency-governance/2026-09-05-dependency-vulnerability-remediation.md) — 删除无源码消费者的 `unstructured`、LlamaIndex 与 NLTK，分句改用标准库；部署拓扑不再创建或迁移 NLTK 数据目录。

## 发布工程与部署面

两份记录收敛交付面：第三方镜像的版本钉位与许可边界，以及发布脚本对活动文档版本入口的定点同步。

- [2026-08-17-thirdparty-image-license-boundary](03-release-engineering/2026-08-17-thirdparty-image-license-boundary.md) — Neo4j 与 Redis 引用钉版为 `neo4j:5.26.29` 与 `redis:7.4.10-alpine`；许可证、离线再分发与商业替代由部署指南「第三方组件和许可证」拥有。
- [2026-08-26-beta2-version-bump-coverage](03-release-engineering/2026-08-26-beta2-version-bump-coverage.md) — `bump-version.sh` 正式模式定点更新活动文档的版本说明、clone 分支与部署 checkout 目标；changelog 历史版本不参与替换。

## 测试体系

清理基础设施先行，随后一轮审计先精简测试、再复核证据边界；确立「删除测试须先指出语义 Owner、现存独立 oracle 与负向覆盖」的规则，测试运行器改用 readiness gate。

- [2026-08-19-test-conversation-cleanup](04-testing/2026-08-19-test-conversation-cleanup.md) — 真实 HTTP 测试创建的 Conversation 统一 `YUXI_TEST_CONVERSATION_` 前缀，E2E 与 integration 共享两阶段 SHARE 锁 teardown，删除前阻止非终态 Run。
- [2026-09-07-test-suite-simplification](04-testing/2026-09-07-test-suite-simplification.md) — 保留语义 Owner 或观察边界不同的 unit、provider 探针与 E2E 三层，只删除无独立断言的实例与低信息量单测；readiness 检查 `/api/system/ready` 而非 liveness。
- [2026-09-07-test-suite-audit-follow-up](04-testing/2026-09-07-test-suite-audit-follow-up.md) — 精简后的证据边界收敛：prompt 负向断言、benchmark 事件同步、512 token 分块精确 oracle，Web 测试按实际观察边界取舍。

## 存储与 Workdir 单域收敛

该线从多存储域收敛到 UserWorkspace 单根：进程权限与挂载解耦，Workdir 收敛为 UserWorkspace 相对路径，共享 no-follow 目录原语与 `1000:1000` 统一运行身份作为基础设施，随后完成 Workspace 代码 Owner 与预览 Owner 的收敛；迁移契约从 v0.7.1 一次性边界演进为版本化 Schema Owner。前三份前置记录是收敛前的早期存储方向，已被 Workdir 归属 UserWorkspace 的决定取代。

- [2026-08-18-live-project-workdir-and-runtime](05-storage-workdir/2026-08-18-live-project-workdir-and-runtime.md) — 前置：实时 Project Workdir 与独立 Sandbox Runtime 的早期设计；已被 [Workdir 归属 UserWorkspace](05-storage-workdir/2026-08-19-workdir-in-user-workspace.md) 取代。
- [2026-08-18-project-workdir-runtime-foundation](05-storage-workdir/2026-08-18-project-workdir-runtime-foundation.md) — 前置：Project Workdir 运行时基础的早期设计；同被上述记录取代。
- [2026-08-19-explicit-storage-domains-and-kubernetes-pvc](05-storage-workdir/2026-08-19-explicit-storage-domains-and-kubernetes-pvc.md) — 前置：显式存储域与 Kubernetes PVC 的早期设计；同被上述记录取代。
- [2026-08-17-api-worker-file-storage-decoupling](05-storage-workdir/2026-08-17-api-worker-file-storage-decoupling.md) — API/worker 不再挂载 Docker socket 或模型目录，只有 provisioner 持有 Docker 权限；日志与 Office 预览缓存留在各自容器本地运行目录。
- [2026-08-19-workdir-in-user-workspace](05-storage-workdir/2026-08-19-workdir-in-user-workspace.md) — Workdir 不再是独立存储域，而是 UserWorkspace 下 `projects/<opaque-id>` 相对路径；Sandbox 挂载收敛为 `user-data` 与只读 Skill 投影，同一用户 Thread 之间不提供文件隔离。
- [2026-08-19-shared-no-follow-directory-walker](05-storage-workdir/2026-08-19-shared-no-follow-directory-walker.md) — `yuxi.utils.paths.open_directory_fd` 统一逐层 no-follow 打开、`0o700` 创建与 symlink/非目录错误分类，Workdir、Workspace、Skills 只留薄 wrapper。
- [2026-08-20-unified-workspace-runtime-identity](05-storage-workdir/2026-08-20-unified-workspace-runtime-identity.md) — 数据面固定 `1000:1000`，新目录 `0o700`、新文件 `0o600`；root migrator 启动前一次性收敛旧目录身份，删除运行时权限补丁。
- [2026-08-20-v071-storage-migration-boundary](05-storage-workdir/2026-08-20-v071-storage-migration-boundary.md) — 一次性 `storage-migrator` 只兼容 v0.7.1 发布状态，检测到未发布中间 schema 明确拒绝；文件移动与 schema 切换不可逆，必须停机迁移。
- [2026-08-21-workspace-owner-convergence](05-storage-workdir/2026-08-21-workspace-owner-convergence.md) — 顶层 `yuxi.workspace` 由 paths、filesystem、workdir、preview 四模块分工；删除 0.7.2.dev0 开发期 Thread 文件浏览 API 与 Mention Redis 文件索引，不为开发快照保留兼容层。
- [2026-08-21-preview-owner-separation](05-storage-workdir/2026-08-21-preview-owner-separation.md) — 通用预览原语下沉 `yuxi.utils.filepreview`，Workspace 与 Knowledge 预览分离，Knowledge 获得独立的 MinIO 原始对象读取与持久 PDF 缓存。
- [2026-08-24-versioned-schema-migration-owner](05-storage-workdir/2026-08-24-versioned-schema-migration-owner.md) — `storage-migrator` 成为唯一 Schema 修改者，双域版本记录；API 与 worker 只在版本精确匹配时启动，裸进程启动前必须先运行迁移器，不承诺回滚。

## Project 资源与生命周期

Project 先成为持久化业务资源，随后补齐 Project 内写审批豁免、软删除与侧边栏组织入口，最后闭合 SubAgent 与并发场景的生命周期旁路。

- [2026-08-22-project-persistence-and-selection](06-project-lifecycle/2026-08-22-project-persistence-and-selection.md) — Project 持久化 id/uid/name/workdir_path，Conversation 绑定不可变 `project_id`；managed Project 由服务端分配目录，linked Project 绑定用户已有目录，创建后不可改绑。
- [2026-08-26-project-write-approval-exemption](06-project-lifecycle/2026-08-26-project-write-approval-exemption.md) — 默认审批模式下 `write_file`/`edit_file` 位于当前 Project Workdir 内时豁免人工审批；其他路径与 `execute` 仍触发审批，豁免不改变 Sandbox 文件权限。
- [2026-08-30-project-conversation-sidebar-management](06-project-lifecycle/2026-08-30-project-conversation-sidebar-management.md) — Project 增加 `active/deleted` 软删除与重命名，删除保留 Workdir 字节；侧边栏新增「项目」与「最近」分组。
- [2026-09-01-project-conversation-lifecycle-closure](06-project-lifecycle/2026-09-01-project-conversation-lifecycle-closure.md) — SubAgent 按父 Run → Project → 父 Conversation 锁序复核 active 状态，deleted 资源拒绝创建或继续运行；前端共享 store 修复侧边栏排序偏差。

## 知识库目录与解析

知识库先建立真实目录树与历史虚拟目录迁移；解析侧固定 docling-slim 依赖，并修复 QA 分块边界与统计刷新缓存两个缺陷。新目录上传的自动实体化仍在 `proposed/` 跟踪，未随本版本发布。

- [2026-08-24-knowledge-folder-move-and-rename](07-knowledge-parsing/2026-08-24-knowledge-folder-move-and-rename.md) — 真实目录树由 `knowledge_files.parent_id` 表达，移动以 advisory lock 串行化防环检查，重命名只更新单记录元数据；前端提供拖放与面包屑。
- [2026-08-24-knowledge-virtual-folder-migration](07-knowledge-parsing/2026-08-24-knowledge-virtual-folder-migration.md) — 知识库级迁移任务每批 500 条、每事务剥离一层路径，已提交批次即恢复事实；不改变文件 ID、对象路径与向量身份。
- [2026-09-02-knowledge-aware-utc-timestamps](07-knowledge-parsing/2026-09-02-knowledge-aware-utc-timestamps.md) — Knowledge 与 Evaluation 时间字段统一 aware UTC 默认值；不改列类型、不迁移数据、不提升 schema 版本。
- [2026-08-27-qa-chunk-length-limit](07-knowledge-parsing/2026-08-27-qa-chunk-length-limit.md) — QA 解析修复问答边界识别，全部 chunk 限长 4000 字符（面向 bge_m3 4096 token 的保守兜底），超限按段落→行→硬切降级。
- [2026-09-05-knowledge-stats-refresh](07-knowledge-parsing/2026-09-05-knowledge-stats-refresh.md) — 统计刷新改为同一事务行锁内聚合并写回，不再命中 Redis 读缓存回填列表投影，消除并发旧快照覆盖。
- [2026-09-03-docling-slim-office-parser](07-knowledge-parsing/2026-09-03-docling-slim-office-parser.md) — Office 解析固定 `docling-slim` 2.122.0，完整 docling/Torch 从依赖删除（镜像约 -276MB）；XLS 环境须提供 LibreOffice Calc。

## Agent 运行时与执行边界

先以不可变 attempt 历史与 manifest 指纹建立 Run 的执行事实，再收敛取消、约束与跨行校验四条执行边界；DeepAgents 0.7 升级及其暴露的 Sandbox 异步读取、checkpoint 连接池两类缺陷构成稳定性收尾，用户级 Memory 与上下文压缩是运行时能力的两块独立拼图。

- [2026-08-16-agent-run-attempt-facts](08-agent-runtime/2026-08-16-agent-run-attempt-facts.md) — 不可变 `agent_run_attempts` 表拥有执行历史与失败事实，恢复与 reconciler 只读不改写；历史 Run 无 attempt 行即 legacy。
- [2026-08-16-agent-run-manifest-fingerprint](08-agent-runtime/2026-08-16-agent-run-manifest-fingerprint.md) — Run 行 write-once 固化实际采用的配置、模型、工具与 Skill 的 SHA-256 指纹；固化失败执行不开始，历史 Run 不从当前配置反推。
- [2026-08-20-run-execution-boundary-convergence](08-agent-runtime/2026-08-20-run-execution-boundary-convergence.md) — 执行树按 root→descendants 单事务取消，数据库 `CHECK ... NOT VALID` 约束非终态 Run 同行形状，worker 执行前复核 scope，K8s inventory 以 selector 枚举。
- [2026-08-23-deepagents-07-migration](08-agent-runtime/2026-08-23-deepagents-07-migration.md) — 升级 `deepagents>=0.7.7` 与 LangChain 底座并批量升级 26+ 依赖；每 Run 构造独享 `CompositeBackend`，middleware 不再接受 backend factory。
- [2026-08-23-langgraph-checkpoint-pool-recovery](08-agent-runtime/2026-08-23-langgraph-checkpoint-pool-recovery.md) — LangGraph 连接池在 checkout 边界启用 `check_connection`，PostgreSQL 恢复后 worker 无需重启；Agent 层不重试整次执行。
- [2026-08-23-sandbox-native-async-read](08-agent-runtime/2026-08-23-sandbox-native-async-read.md) — Sandbox 异步读取改用原生异步文件 API，不再依赖 shell stdout JSON 与临时 base64 文件；二进制类型保持拒绝。
- [2026-08-23-user-memory-mvp](08-agent-runtime/2026-08-23-user-memory-mvp.md) — `enable_memory` 是用户级 Memory 唯一开关，写入固定操作当前 uid 的 `MEMORY.md` 并原子发布；Memory 与历史为低信任数据，SubAgent 无用户级 Memory。
- [2026-09-02-context-compression-pressure-and-manual-action](08-agent-runtime/2026-09-02-context-compression-pressure-and-manual-action.md) — `summary_threshold` 成为唯一压缩阈值（删除 `summary_l2_trigger_ratio`），完整工具结果先写 Workdir 再替换模型可见消息；手动压缩持 Conversation 行锁，忙时 `409`。

## Skill 运行时、资源选择与 CLI 检查

先定共享/个人 Skill 的存储与授权 Owner，再收敛运行时解析 Owner；预加载与共享智能体资源选择在两者之上建立首轮可见性与写边界。完整资源选择协议由仍在 `implemented/` 的[统一资源选择决策](../../implemented/2026-09-27-explicit-resource-selection.md)继续拥有。

- [2026-08-18-skill-source-convergence](09-skill-cli/2026-08-18-skill-source-convergence.md) — 共享 Skill 只存 `YUXI_SKILL_DATA_DIR/shared/<slug>`，个人 Skill 始终由 UserWorkspace 拥有；投影只读，同 slug 个人版本逻辑覆盖共享版本。
- [2026-08-20-skill-runtime-module-boundary](09-skill-cli/2026-08-20-skill-runtime-module-boundary.md) — `agents/skills/runtime.py` 统一拥有 Skill 解析与激活包派生，Middleware 只保留请求包装与注入；删除无消费者 Python API 且不留 re-export。
- [2026-08-17-skill-preload](09-skill-cli/2026-08-17-skill-preload.md) — `preload_skills` 允许智能体预加载少量 Skill，首轮注入完整说明；预加载文件不可读时 Graph 创建显式失败。
- [2026-09-05-shared-agent-resource-selection](09-skill-cli/2026-09-05-shared-agent-resource-selection.md) — 共享智能体保存把 `config_json.context` 作为字段补丁，行锁内合并并保留不可见引用；运行期只使用操作者可访问资源的交集。
- [2026-08-27-cli-agent-inspection](09-skill-cli/2026-08-27-cli-agent-inspection.md) — CLI 新增 `yuxi agent list` 与 `yuxi agent show`，经 discovery 能力声明拒绝不支持契约的旧服务端。

## 增量审计与调试可观测

审计按阶段推进：先固化 trace 与 stream 基础事实（阶段一），再闭合 Model/AIMessage（阶段二）与 ToolMessage（阶段三）写入，随后修正大结果卸载下的终态对账，最后收紧读模型边界并统一读接口；调试面板与 Langfuse 跳转是同一线的消费端。

- [2026-08-28-agent-run-audit-foundation](10-audit-debug/2026-08-28-agent-run-audit-foundation.md) — AgentRun 保存可空 `langfuse_trace_id`，lease owner 在模型执行前幂等固化；trace 固化失败执行不开始。
- [2026-08-24-agent-run-langfuse-jump](10-audit-debug/2026-08-24-agent-run-langfuse-jump.md) — 调试跳转按 Run 自身 trace ID 惰性解析精确 URL，仅接受与 `LANGFUSE_BASE_URL` 同源的 HTTP(S) 地址，路由要求超级管理员。
- [2026-08-24-message-debug-panel](10-audit-debug/2026-08-24-message-debug-panel.md) — Debug 模式下按连续 `run_id` 对消息做 Run 视觉分组并提供 JSON 树；调试投影与聊天展示共享同一历史事实源。
- [2026-08-28-model-message-incremental-audit](10-audit-debug/2026-08-28-model-message-incremental-audit.md) — Message 新增七个审计列，`message-start/finish` 短事务幂等维护 `model_audit` 行；关键审计持久化失败使 Run 显式失败。
- [2026-08-30-tool-message-incremental-audit](10-audit-debug/2026-08-30-tool-message-incremental-audit.md) — `tools` stream 为 Tool lifecycle 来源，`tool_audit` 行保存 effective input 与原始 output/error；普通 History/Dashboard 排除审计行。
- [2026-08-30-tool-audit-offloaded-state-reconcile](10-audit-debug/2026-08-30-tool-audit-offloaded-state-reconcile.md) — 已关闭审计的终态 State 不再二次提交或覆盖，消除大结果卸载与审计原始 output 两种合法表示的冲突。
- [2026-09-03-message-audit-boundary-hardening](10-audit-debug/2026-09-03-message-audit-boundary-hardening.md) — 普通 History 只返回带 `state_reconciled` 证明的兼容行，Model metadata 走公开字段 allowlist，Dashboard 统计排除审计行。
- [2026-09-03-unify-message-audit-read-api](10-audit-debug/2026-09-03-unify-message-audit-read-api.md) — `/api/chat/thread/{id}/audits` 成为唯一审计读接口（最新 500 条并以 `truncated` 明示）；Model-only `/model-audits` 删除且不保留兼容。

## 后台任务与定时调度

通用后台任务以 PostgreSQL 行为持久执行意图，用户定时任务复用同一 worker reconciliation loop；两者都通过完整业务升级引入并依赖统一迁移契约。

- [2026-08-25-durable-task-execution](11-background-tasks/2026-08-25-durable-task-execution.md) — `tasks` 行拥有执行意图、去重键、Handler 版本与 lease，API 退出后 pending Task 仍可执行；失联任务统一失败不自动重试，claim 上限 4。
- [2026-08-26-user-agent-scheduled-tasks](11-background-tasks/2026-08-26-user-agent-scheduled-tasks.md) — 用户定时智能体任务（cron、时区、`FOR UPDATE SKIP LOCKED` 领取）每次触发创建绑定原 Project 的新 Conversation；停机错过多周期合并为一次。

## 并发容量与时延

容量基线与协议先建立，前端消费同一套时延口径，随后的模型前时延优化在协议之上延伸并给出正式评测矩阵，同时接管更早的八份并发决策（已删除，不在本批）。

- [2026-09-04-agent-concurrency-capacity](12-concurrency/2026-09-04-agent-concurrency-capacity.md) — 默认容量基线（`ARQ_MAX_JOBS=140`、PostgreSQL `max_connections=600`）与 SSE 自适应轮询、取消协议、Sandbox 惰性创建（默认 core profile、独立地址池）；AgentRun write-once 持久保存五个阶段时间点。
- [2026-09-05-frontend-optimization](12-concurrency/2026-09-05-frontend-optimization.md) — History 独立返回 `runs`，消息经 `run_id` 关联并删除 Run 时间副本；调试面板统一时间轴，流式正文由 `useStreamSmoother` 缓冲播放；前后端须同步发布。
- [2026-09-07-agent-concurrency-optimization](12-concurrency/2026-09-07-agent-concurrency-optimization.md) — 按读取、派发、执行、收尾保留最小优化，新增 `first_model_request_at` 与正式主指标；3150 请求矩阵显示多 Worker 将 100 并发准备阶段 P50 从 4442ms 降至 138ms（4 Worker）。

## 表面收敛与能力移除

两个整块能力移除与三批无消费者表面收敛；每项以「旧能力不存在 + 重新引入条件」验证，移除均不建兼容层。部署模式收敛是本组对升级者影响最大的部分。

- [2026-09-03-remove-lite-mode](13-removals/2026-09-03-remove-lite-mode.md) — 取消 LITE 运行模式，Compose 始终声明完整知识拓扑；既有轻量安装升级前必须补齐 Milvus、etcd、Neo4j 及完整知识运行资源。
- [2026-09-03-remove-content-guard](13-removals/2026-09-03-remove-content-guard.md) — 完整移除内置内容审查能力及其配置入口与文档页；需要内容安全策略的部署须在模型供应商、网关等边界自行接入。
- [2026-09-03-reduce-pipeline-redundancy](13-removals/2026-09-03-reduce-pipeline-redundancy.md) — 解析能力由 `capabilities.py` 单一 Owner 派生，删除 `AgentRun.last_event_id`（业务 Schema v5 幂等迁移）与未消费 artifact。
- [2026-09-03-remove-secondary-redundancy](13-removals/2026-09-03-remove-secondary-redundancy.md) — 删除无消费者的 `agent_runtime_service`、`message_feedback_repository`、`useMention` 等内部表面；工具参数协议与 Run 取消信号各归单一 Owner。
- [2026-09-04-remove-redundant-surfaces](13-removals/2026-09-04-remove-redundant-surfaces.md) — 删除无生产消费者的组件、helper 与依赖（净减 2762 行），Web 测试收敛唯一正式根；被删 Compose no-op 变量不再表现为有效配置。

## 前端体验与交互

#### Agent 面板与文件来源

面板三篇构成「边界划分 → 结构承载 → 文件身份与刷新」的三段式；状态面板、输入区与交付物保存共享同一组选择器与授权原语。

- [2026-08-19-frontend-detail-optimizations](14-frontend-ux/2026-08-19-frontend-detail-optimizations.md) — 五项局部体验优化（流式预览、侧栏按钮组、面板最大化、耗时聚合行、知识库创建向导）；AgentPanel 议题刻意切给后续聚焦决策。
- [2026-08-20-agent-panel-sections](14-frontend-ux/2026-08-20-agent-panel-sections.md) — AgentPanel 扁平一级 Tabs：文件树、文件预览与子智能体线程平级，隐藏保持挂载，每个运行中子线程 Tab 各占一条 SSE。
- [2026-08-21-agent-panel-filesystem-refresh](14-frontend-ux/2026-08-21-agent-panel-filesystem-refresh.md) — 三种文件来源（Viewer、Workspace、artifact runtime）保留各自 wire identity，读取接口由打开时固化的来源决定；刷新只在可见且 Run 执行期间轮询。
- [2026-08-22-chat-input-extra-region](14-frontend-ux/2026-08-22-chat-input-extra-region.md) — `AgentInputArea` 通用 Extra 插槽承载 Project 选择 chip，`WorkspacePathPicker` 成为共享目录选择器。
- [2026-08-26-artifact-save-destination](14-frontend-ux/2026-08-26-artifact-save-destination.md) — 交付物保存支持可选 `destination_path`，显式目标必须是已存在真实目录，路径校验由服务端拥有。
- [2026-08-25-state-panel-display](14-frontend-ux/2026-08-25-state-panel-display.md) — 状态面板固定/悬浮 `max-height` 用 `ResizeObserver` 计算，子线程运行收敛为最新一项；其子任务状态来源后被 [SubAgent 独立观察](../../implemented/2026-09-17-subagent-independent-observation.md)取代。

#### Dashboard 与用户管理

Dashboard 先定分层与数据能力，再统一组件与统计口径；用户管理以并存双契约引入服务端分页。

- [2026-08-24-dashboard-architecture-and-thread-analytics](14-frontend-ux/2026-08-24-dashboard-architecture-and-thread-analytics.md) — Dashboard 收敛为 Thin Router → Service → Repository，文件统计批量 SQL 聚合，新增会话多维分析；运营统计排除已注销与已删除资源。
- [2026-08-25-dashboard-analysis-ui](14-frontend-ux/2026-08-25-dashboard-analysis-ui.md) — 共享指标卡弃用 `a-statistic`，会话统计新增 `include_subagents`，审计 `all` 语义默认排除已删除会话，新增 120 天活跃热力图。
- [2026-08-24-user-management-server-pagination](14-frontend-ux/2026-08-24-user-management-server-pagination.md) — 新增 `/api/auth/users/page` 分页契约，过滤先于分页执行；旧数组契约保留兼容。

#### 对话模型绑定与消息展示

对话绑定模型、流式与推理展示构成「渲染节奏 → 数据保真与供应商协议 → 展示统一」的完整链路；子智能体工具边界在执行层强制。

- [2026-08-25-conversation-model-selection-restoration](14-frontend-ux/2026-08-25-conversation-model-selection-restoration.md) — Conversation 在 `extra_metadata.model_spec` 持有绑定模型，解析顺序为请求显式 > 对话绑定 > 智能体 > 系统默认；被拒绝的请求不改变绑定。
- [2026-08-23-web-presentation-and-streaming-details](14-frontend-ux/2026-08-23-web-presentation-and-streaming-details.md) — 流式消息按动画帧平滑释放、历史大文本快速放行；知识库检索结果按文件身份分组，同名不同身份不串组。
- [2026-09-07-provider-reasoning-adapter](14-frontend-ux/2026-09-07-provider-reasoning-adapter.md) — 推理内容在解析边界写入标准内容块（仅硅基流动、OpenCode、智谱/Z.ai 开启）；新消息 content 是标准块列表，纯文本消费者应使用 `message.text`。
- [2026-09-07-stream-tools-and-opencode-session](14-frontend-ux/2026-09-07-stream-tools-and-opencode-session.md) — 前端只消费当前语义 stream_event，工具结果按同 Run 调用 ID 关联；OpenCode 请求附加会话路由头，修复首块工具调用不显示。
- [2026-09-08-thinking-tool-result-rendering](14-frontend-ux/2026-09-08-thinking-tool-result-rendering.md) — 相邻推理与工具调用统一折叠归组，错误状态判定 error/failed 优先于成功；仅改展示层，不改协议与持久化。
- [2026-09-08-subagent-tool-execution-boundary](14-frontend-ux/2026-09-08-subagent-tool-execution-boundary.md) — 子智能体禁用工具在执行层强制：注册前排除、调用时返回绑定原 tool call 的错误结果；默认模式子智能体仍不能直接写当前 Project。

#### 独立前端修复与性能

- [2026-08-27-extension-detail-and-evaluation-workspace](14-frontend-ux/2026-08-27-extension-detail-and-evaluation-workspace.md) — Skill/MCP/知识库详情收敛到共享布局组件，知识库评估进入独立三级路由。
- [2026-08-25-mention-chip-deletion-boundary](14-frontend-ux/2026-08-25-mention-chip-deletion-boundary.md) — mention chip 删除边界由 DOM `data-mention-raw` 与节点位置拥有，Backspace 紧邻即删。
- [2026-08-26-lucide-vue-package-migration](14-frontend-ux/2026-08-26-lucide-vue-package-migration.md) — 图标依赖从废弃的 `lucide-vue-next` 迁移到官方 `@lucide/vue` 1.34.0，图标名称与样式保持兼容。
- [2026-09-05-api-key-http-request-id](14-frontend-ux/2026-09-05-api-key-http-request-id.md) — API Key 创建改用 `crypto.getRandomValues()` 生成请求 ID，修复普通 HTTP 环境 `randomUUID` 缺失导致的无响应。
- [2026-09-08-performance-and-bundle-optimization](14-frontend-ux/2026-09-08-performance-and-bundle-optimization.md) — 按需加载与缓存预算优化构建体积（模型目录约 4595→944KB 等），删除 LangChain Community/Classic；原文留有全新镜像启动与生产部署未验证面。

## 升级与部署影响索引

| 影响点 | 要求 | 记录 |
| --- | --- | --- |
| 部署资源 | LITE 移除，轻量安装须补齐 Milvus、etcd、Neo4j | [remove-lite-mode](13-removals/2026-09-03-remove-lite-mode.md) |
| 内容安全 | 内置审查移除，须在供应商或网关边界自行接入 | [remove-content-guard](13-removals/2026-09-03-remove-content-guard.md) |
| Sandbox 规格 | 默认 core，浏览器自动化与完整服务须显式选择 profile；独立地址池 `10.253.240.0/20` 需自查冲突 | [agent-concurrency-capacity](12-concurrency/2026-09-04-agent-concurrency-capacity.md) |
| 数据库迁移 | 唯一 migrator 拥有双域版本，启动前必须执行；business 与 knowledge 多次幂等升级一次到位 | [versioned-schema-migration-owner](05-storage-workdir/2026-08-24-versioned-schema-migration-owner.md)、[workdir-in-user-workspace](05-storage-workdir/2026-08-19-workdir-in-user-workspace.md)、[durable-task-execution](11-background-tasks/2026-08-25-durable-task-execution.md) |
| 运行身份 | 数据面固定 `1000:1000`，旧目录身份由 root migrator 启动前收敛 | [unified-workspace-runtime-identity](05-storage-workdir/2026-08-20-unified-workspace-runtime-identity.md) |
| 镜像与依赖 | docling-slim（XLS 需 LibreOffice Calc）；NLTK、unstructured、LlamaIndex、LangChain Community/Classic 移除 | [docling-slim-office-parser](07-knowledge-parsing/2026-09-03-docling-slim-office-parser.md)、[dependency-vulnerability-remediation](02-dependency-governance/2026-09-05-dependency-vulnerability-remediation.md)、[performance-and-bundle-optimization](14-frontend-ux/2026-09-08-performance-and-bundle-optimization.md) |
| 接口契约 | History 独立返回 `runs`；`/model-audits` 404；`/api/auth/users/page` 新增；`config_json.context` 补丁语义 | [frontend-optimization](12-concurrency/2026-09-05-frontend-optimization.md)、[unify-message-audit-read-api](10-audit-debug/2026-09-03-unify-message-audit-read-api.md)、[user-management-server-pagination](14-frontend-ux/2026-08-24-user-management-server-pagination.md)、[shared-agent-resource-selection](09-skill-cli/2026-09-05-shared-agent-resource-selection.md) |
| 消息内容 | 推理内容进标准块，content 为块列表，纯文本消费者用 `message.text` | [provider-reasoning-adapter](14-frontend-ux/2026-09-07-provider-reasoning-adapter.md) |
| 配置项 | `summary_l2_trigger_ratio` 删除，`summary_threshold` 为唯一阈值 | [context-compression-pressure-and-manual-action](08-agent-runtime/2026-09-02-context-compression-pressure-and-manual-action.md) |
| Langfuse | 跳转 URL 仅接受与 `LANGFUSE_BASE_URL` 同源的 HTTP(S) | [agent-run-langfuse-jump](10-audit-debug/2026-08-24-agent-run-langfuse-jump.md) |
| 前端依赖 | `lucide-vue-next` → `@lucide/vue`，依赖与导入 specifier 更名，图标名称与样式不变 | [lucide-vue-package-migration](14-frontend-ux/2026-08-26-lucide-vue-package-migration.md) |

## 取代与吸收关系

- [Workdir 归属 UserWorkspace](05-storage-workdir/2026-08-19-workdir-in-user-workspace.md) 取代本目录三份前置存储设计（live-project-workdir-and-runtime、project-workdir-runtime-foundation、explicit-storage-domains-and-kubernetes-pvc）。
- [Tool 审计终态对账](10-audit-debug/2026-08-30-tool-audit-offloaded-state-reconcile.md) 修正 [ToolMessage 增量审计](10-audit-debug/2026-08-30-tool-message-incremental-audit.md) 的终态 State 边界；[统一审计读接口](10-audit-debug/2026-09-03-unify-message-audit-read-api.md) 删除增量审计线此前并存的 Model-only 读面（`/model-audits`，源自阶段二衍生的 Model 审计调试读模型，该记录已被吸收删除）。
- [移除内容审查](13-removals/2026-09-03-remove-content-guard.md) 取代 [面向读者的文档写作](01-engineering-trust-and-docs/2026-08-26-human-centered-documentation.md) 中内容审查参考页及其稳定路径；[性能与构建优化](14-frontend-ux/2026-09-08-performance-and-bundle-optimization.md) 部分取代 [依赖漏洞修复](02-dependency-governance/2026-09-05-dependency-vulnerability-remediation.md) 的 PDF 读取路径。
- [并发时延优化](12-concurrency/2026-09-07-agent-concurrency-optimization.md) 对[并发容量基线](12-concurrency/2026-09-04-agent-concurrency-capacity.md)是延伸而非取代：后者仍是容量、SSE 轮询、取消协议与五个持久时间点的 Owner；前者接管并删除的八份更早并发决策已不在仓库。
- [状态面板展示](14-frontend-ux/2026-08-25-state-panel-display.md) 的子任务状态来源被仍在 `implemented/` 的 [SubAgent 独立观察](../../implemented/2026-09-17-subagent-independent-observation.md)取代；[共享智能体资源选择](09-skill-cli/2026-09-05-shared-agent-resource-selection.md) 的完整选择协议由 [统一资源选择决策](../../implemented/2026-09-27-explicit-resource-selection.md)继续拥有。
- 知识库新目录上传的自动实体化提案未随本版本实现，仍由 [proposed 记录](../../proposed/2026-08-24-knowledge-folder-materialization.md)跟踪。
