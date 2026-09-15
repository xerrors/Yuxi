# Agent 请求接入、派发与执行职责简化

状态：implemented
类型：simplification
Owner：backend/package/yuxi/services/agent_request_queue_service.py

## 问题

普通消息已经通过 Request 队列创建 Run，但直接创建服务仍包含没有生产调用方的普通 chat 分支。执行流同时保留身份生成、会话创建、用户消息保存与配置重新解析，重复承担接入职责。派发函数重复传递已校验对象拥有的身份，提交命令与执行返回值携带没有 consumer 的选项或数据，增加调用方核对成本。manifest 与执行配置还在不同阶段读取工作区提示词和 Skill，形成记录与执行漂移的窗口。

## 决策

### 接入与恢复

`agent_request_service` 与 `agent_request_queue_service` 拥有普通消息接入和 FIFO。Web、Call、Channel、评估与定时入口通过 `AgentRequestInput` 调用 `submit_agent_request` 写入 Message 和 Request，只有符合派发条件的队头创建 Run。普通提交固定查询 main Agent。

`agent_run_service` 的直接创建入口收窄为 `create_resume_run_view`，拥有恢复内容持久化、父 Run 校验、幂等、配置与来源继承以及提交后投递。HTTP resume 中的 query、模型和审批模式字段按已有行为忽略，恢复采用父 Run 快照。历史无 Request 的 Run 兼容读取保留。

### 派发与事务

私有派发函数从已按用户、Agent 和线程过滤并锁定的 Request 读取 Run 身份，从已校验的 WorkdirBinding 读取 conversation_id。ready 队头与暂停队列的人工继续保留各自门禁。接入收尾按提交事务、目录物化、条件投递的顺序执行，不临时转换为 DispatchResult。

### 执行与配置

`chat_service` 只消费已持久化的请求与输入。chat/resume 必须接收非空 thread/request 身份；运行时解析要求 Conversation 存在、未删除且属于当前用户，并保留 Agent 可见性、线程绑定和 Workdir 校验。用户输入由接入和恢复服务保存，流仍提供 init 展示消息，并拥有协议转换、审批、审计与 assistant 结果持久化。

`run_worker` 校验输入与 Workdir 后、开始准备 Context 前启动续租。`agent_run_manifest_service` 使用现有 Context 类型，仅读取持久 Agent 配置中的可配置字段，再应用 Run 模型与审批模式、工作区基础提示词、真实身份、路径和子运行标记。`prepare_agent_runtime_context` 为该对象解析一次资源与 Skill，manifest 从准备结果派生并提交；chat/resume 与 BaseAgent 传递同一对象，构图复用其准备结果。

`prepare_run_execution` 返回 `PreparedRunExecution`，worker 的 `prepare_and_record_run_execution` 负责准备并固化，流入口以 `prepared_execution` 接收同一结果。配置读取和摘要生成复用同一份可配置字段集合。Skill 授权解析得到的 `ResolvedSkill` 保存当时的来源、版本和哈希，运行 scope 连同预加载正文一起保留它们；manifest 纯投影该结果，不再按 slug 查询版本。个人 Skill 的版本与共享内容哈希为空，不借用同名共享记录。

模型与审批模式仍在接入时固定，其余配置在开始执行时确定。worker 与主动压缩显式准备新 Context；状态查询直接读取 checkpoint，不使用 Context 或执行图。执行流继续验证 Conversation、Agent 可见性与 Workdir；准备后 backend 改变时显式失败。资源副作用仍由实际 executor/repository 的权限与路径边界约束。

manifest v2 的 config_digest 覆盖准备后的可配置字段，包括 schema 默认值、模型覆盖与工作区提示词，排除运行身份。预加载 Skill 内容只以摘要进入 manifest；MCP 发现、Memory 与文件动态读取仍在后续边界发生，不承诺完整外部资源重放。历史 manifest 保留，旧版 manifest Run 的重试若与新指纹不一致会显式失败，不覆盖 write-once 事实。

准备期间 manifest 写入因已提交取消而失败时，worker 进入取消收尾，避免误走 failed 后留待 lease 超时。SubAgent 创建与 FIFO 调度、Request/Run 状态模型、数据库 schema 和 HTTP 请求模型保持原契约。

### 配置与投影的单一来源

持久配置统一通过 `filter_declared_config` 筛选 Schema 可配置字段，Context 的 `update_config` 和资源归一化复用该规则；接入、执行和主动压缩都排除持久配置中的运行身份与子运行标记。角色修改权限仍在写入边界单独处理。

SubagentRunService 从当前子 Agent 配置、已校验父 Run 的输入模型、系统默认中依次解析模型。middleware 不读取模型配置或传入覆盖值，避免两次配置读取决定不同优先级。普通请求保留显式请求、会话保存值、Agent 配置、系统默认的顺序。

Skill middleware、依赖工具与 manifest 读取同一个 `_skill_runtime_snapshot`，Context 不维护各项私有别名。worker 从已准备 Context 投影模型、审批模式、runtime scope 和 Workdir 元数据；执行流直接使用 Context 处理产物路径，事件字段保持兼容。

### Request 接入与幂等

`AgentRequestInput` 是各来源构建的入口输入，`AgentRunRequest` 是持久请求状态。`submit_agent_request` 拥有授权、线程绑定、持久化、commit、目录物化和投递的完整用例；内部 `_persist_request` 在同一事务内保存消息并尝试派发，返回 ORM 请求与实际 DispatchResult。提交入口在 commit 后投递实际队头 Run，即使它属于此前排队的请求；当前请求的响应继续投影自身状态。幂等返回不携带本事务派发结果。新提交、重发与 steer 操作共用 `request_view`，不维护平行结果类型。队列服务保留派发、引导、取消与恢复，依赖方向由提交服务指向队列服务。

幂等作用域只包含用户、Agent、线程及来源标识，queue_policy 为可变调度策略。enqueue 升级 steer 后重发原输入仍返回同一个 Request 的当前策略。相同身份的正文和配置以第一次接收为准，重发不改写。

既有 Request 在 Agent 可见性、Conversation 归属/删除状态/Agent 绑定以及 Project 访问检查后直接返回。返回不依赖当前后端、不物化目录、不再次投递；pending Run 的已有周期恢复继续拥有补发。新请求仍在 Conversation 锁前、锁后和唯一约束冲突后检查幂等，事务提交后才投递。历史无 Request 的 Run 保持原兼容路径。

Request.input_payload 保存接入时解析的模型与审批配置；Message 保存输入内容。这里只修正源码与机制说明，不改变持久字段或迁移历史数据。

### Context 生命周期与状态读取

Context 按 Schema 默认值、持久配置、身份与单次覆盖、工作区提示词、资源授权的顺序构造。`prepare_agent_runtime_context` 在同一对象上追加工作区提示词并准备资源；worker 和主动压缩显式调用，内置 `get_graph` 拒绝未准备对象。BaseAgent 的执行方法只接收 Context 和实际使用的观测选项，删除字典双输入、`update_from_dict` 及无消费者的 get_config、stream_values、check_checkpointer、get_history。模型 invoke 与 message stream 入口保留显式 Context 接口。

状态读取纳入 checkpoint-state 工作树的 `d77e64ea` 方案，在 Conversation 与 Project Workdir 授权后从 PostgreSQL checkpointer 一次读取根 namespace。业务值来自最近完整 checkpoint，中断来自同一 tuple 的 pending writes，且仅在最新 Run 为 interrupted 时展示。未合并的业务 pending writes 不进入面板；无 checkpoint 返回空视图，存储错误显式传播。当前 `files` 已是 DeltaChannel，但 shipping Sandbox backend 不写该状态字段；启用该 channel 的写入前须重新验证还原规则。执行与 resume 仍由真实图拥有。状态读取不依赖当前 Agent、模型或 MCP 可用性。

## 替代方案

- keep：保留双用途入口、执行层接入分支和重复参数，继续维护无生产 consumer 的表面。
- narrow：按现有职责收窄入口，执行层消费准备好的数据，派发从事实来源读取身份，manifest 从唯一执行 Context 派生；采用此方案。
- replace：新增平行配置类型或持久完整 Context 会扩大接口迁移与敏感数据范围；把 resume 纳入普通 FIFO 还需重新裁决中断恢复与队列关系。
- remove：删除直接恢复服务或整个流服务会破坏审批恢复、协议转换与结果持久化；删除派发分层会混淆 ready 派发和人工继续。

## 后果

维护者在接入与恢复服务追踪输入保存，在 Request 与 WorkdirBinding 追踪派发身份，在 worker 追踪唯一执行 Context 的准备与审计生成。执行流不再提供 save_user_message 开关、身份补建、会话创建和配置 fallback；恢复入口不再接收被忽略的普通消息参数。测试直接构造已准备的输入与配置，不提供测试专用兼容入口。

## 验证

旧能力不存在：`rg -n 'create_agent_run_view|_prepare_run_input_message|_resolve_agent_run_request_id|save_user_message|_ensure_thread_bound_agent' backend --glob '*.py'` 无匹配。私有 `_dispatch_locked_head` 不接收 uid、agent_slug、thread_id、conversation_id；`dispatch_ready_head` 不透传 conversation_id。AgentRequestInput 无 agent_kind；流服务无模型、审批和子运行参数注入 helper；manifest 无临时 Context limits 解析与独立 Skill 解析。worker 返回实际 Context 与 manifest，不返回 normalized_context 字典快照。Skill 解析结果保存在该 Context 内，由 manifest 和执行共同读取。协议 Message ID、工具 question ID、Skill 解析与指纹输入保留。旧准备类型、函数和参数名已删除；旧性能 span 名仅作为已保存样本的读取兼容，不提供旧函数别名。

重新引入条件：出现明确的独立生产 consumer，且现有接入和数据来源不能满足其契约；须先裁决持久化、FIFO、幂等与 execution ownership，优先复用现有接入服务。

### Context 一次准备的验证

- Passed：`docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit -m 'not slow' -q --disable-warnings`，2005 passed、53 skipped。覆盖持久配置不能伪造运行身份、Run 覆盖、工作区提示词改变摘要、运行身份不影响摘要、同一 Context 不再次解析 Skill、后端变化拒绝执行及准备期间取消。Skill 元数据负向案例在首次解析后修改源版本、哈希和正文，manifest 仍保留解析结果。
- Passed：`docker compose exec api uv run --no-sync --group test pytest test/unit/services/test_skill_service.py -k resolved_shared_skill_captures_original_version_and_hash -q --tb=short --disable-warnings`，1 passed、71 deselected。补充验证真实 ORM Skill 适配后，原行更新不改写 ResolvedSkill 的版本与哈希。
- Passed：`docker compose exec api uv run --no-sync --group test pytest test/integration/services/test_agent_run_lease.py test/integration/services/test_agent_request_queue_concurrency.py test/integration/api/test_agent_request_queue_router.py test/integration/services/test_agent_run_manifest_and_attempts.py -q --tb=short --disable-warnings`，51 passed，包含之前失败的 Agent Call 取消与测试清理。
- Passed：`docker compose exec api timeout --signal=INT --kill-after=15s 900s uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k 'subagent_worker_enforces_inherited_write_policy or deterministic_agent_path_reaches_persisted_result or resume_with_offloaded' -q --tb=short --disable-warnings`，4 passed、6 deselected。验证真实 API、worker、SSE、manifest v2、普通输入与恢复正文、父子配置、两种 SubAgent 审批策略、工具审计和共享文件。
- Passed：`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（62 项）、改动 Python 文件 Ruff check/format、`pnpm --dir docs run build` 与 `git diff --check`。

### 配置来源收敛的验证

- Passed：`docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit -m 'not slow' -q --disable-warnings`，2010 passed、53 skipped。覆盖持久配置排除运行身份、状态与压缩归一化排除子运行标记、服务层子模型优先与父模型继承、Skill 提示词与工具依赖，以及 Context 与原输入故意不同时的 worker 元数据来源。
- Passed：`docker compose exec api uv run --no-sync --group test pytest test/unit/agents/test_context_auth.py test/unit/services/test_run_worker.py test/unit/services/test_context_compression_service.py test/unit/services/test_chat_service_sync.py -q --tb=short --disable-warnings`，103 passed。
- Not run（业务断言未执行）：`docker compose exec api timeout --signal=INT --kill-after=5s 180s uv run --no-sync --group test pytest test/integration/services/test_agent_run_lease.py test/integration/services/test_agent_request_queue_concurrency.py test/integration/api/test_agent_request_queue_router.py test/integration/services/test_agent_run_manifest_and_attempts.py -q --tb=short --disable-warnings`，51 setup errors。首次相同命令未加 timeout，同样 51 setup errors；两次均在前置知识库评估资源清理的 HTTP 读取中超时。API readiness 为 ready，数据库活动查询显示 DataFileRead 且无阻塞 PID；未跳过清理或修改现有数据。历史 integration 通过不能替代该配置收敛的真实链路验证。
- Not run（完整结果断言未完成）：`docker compose exec api timeout --signal=INT --kill-after=15s 300s uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k 'subagent_worker_enforces_inherited_write_policy or deterministic_agent_path_reaches_persisted_result or resume_with_offloaded' -q --tb=short --disable-warnings`，选择 4 项，300 秒超时退出 124，无通过结果。数据库回读确认父子 Run 已创建，输入与 manifest 的模型均为 replay 模型；中断清理最初报告两条 running，随后再次回读均为 cancelled。继承已有部分真实证据，终态与文件产物完整断言未完成，慢执行的完整原因未定位。
- Passed：全部 36 个改动 Python 文件 Ruff check/format、`pnpm --dir docs run build`、工程信任检查及其 62 项单测、`git diff --check`。

旧能力不存在：生产与测试 Python 中 `_effective_skill_slugs`、旧 Skill 数据别名和 `_subagent_model_override` 无引用；SubagentRunService.start 不接收模型覆盖参数，流执行层不重写 runtime/Workdir 元数据。独立 Review 的配置消费者遗漏和元数据负向证据问题均已修复。

### Context 全流程收敛的验证

- Passed：`docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit -m 'not slow' -q --disable-warnings`，2017 passed、53 skipped。包含未准备 Context 构图拒绝、旧字典输入拒绝、默认提示词保留、同对象准备幂等、真实 LangGraph 完整快照与 pending 中断对照、状态权限，以及压缩/模型观测配置。
- Passed：`docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit/agents test/unit/services/test_context_compression_service.py test/unit/services/test_agent_run_manifest_service.py test/unit/services/test_base_agent_langfuse_config.py test/unit/services/test_chat_service_sync.py test/unit/services/test_checkpoint_state_reader.py test/unit/services/test_chat_stream_interrupt.py -q --tb=short --disable-warnings`，271 passed。
- Not run（业务断言未执行）：`docker compose exec api timeout --signal=INT --kill-after=5s 180s uv run --no-sync --group test pytest test/integration/api/test_checkpoint_state_view.py test/integration/api/test_context_compression_router.py test/integration/services/test_agent_request_queue_concurrency.py -q --tb=short --disable-warnings`，10 setup errors，前置清理登录 HTTP 超时。
- Not run（未取得通过结果）：`docker compose exec api timeout --signal=INT --kill-after=15s 900s uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k 'subagent_worker_enforces_inherited_write_policy or deterministic_agent_path_reaches_persisted_result or resume_with_offloaded' -q --tb=short --disable-warnings`，确认 API 启动失败后主动中断，50.66 秒、6 deselected，无通过结果。API required knowledge_base 初始化因 Milvus 不可用失败；Milvus 到 etcd 超时，etcd 出现 slow fdatasync，宿主 I/O pressure full avg10 约 80%。已尝试重启开发 etcd/Milvus，未修改数据或跳过清理。来源工作树的 HTTP/E2E 记录不替代本地最终 diff 的验证。
- Passed：改动 Python 文件 Ruff check/format、工程信任检查及其 62 项单测、docs build 与 `git diff --check`。完整独立 Review 发现的两处 integration fixture 旧入口引用已迁移；状态读取另经过独立专项 Review。

旧能力不存在：生产代码不再提供 `build_agent_input_context`、`update_from_dict`、`_build_agent_context`；内置构图无资源准备，状态查询无 Context 初始化。性能探针跟随显式准备 Owner，压缩集成 fixture 使用实际 Context。

### Request 接入收敛的验证

- Passed：`docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit -m 'not slow' -q --disable-warnings`，2022 passed、53 skipped。
- Passed：补充早返回访问与已派发分支后执行 `docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit/services/test_agent_request_queue_service.py test/unit/services/test_run_submission_service.py -q --tb=short --disable-warnings`，69 passed。断言升级 steer 后原请求重发保留 Message，模型不重解析；早返回不初始化后端、不创建请求或投递，且 Agent 不可见、线程不存在/越权/删除/绑定不符、Project 不存在或删除均拒绝。
- Not run：本轮真实 HTTP integration、PostgreSQL 并发与 E2E。API/etcd 处于 unhealthy，readiness 请求超时，沿用已定位的共享开发环境 I/O 与依赖服务故障；未跳过前置清理或复用历史通过。已迁移并发测试到命令接口，HTTP 用例新增升级 steer 后重发原命令并回读消息指针的断言，待环境恢复执行。
- Passed：Ruff check/format、工程信任检查及其 62 项单测、docs build 和 `git diff --check`。独立 Review 的早返回分支/负向访问证据缺口已补齐。

旧能力不存在：内部持久化不再接收独立 request_id、agent_slug、thread_id、source、channel、model_spec、input_message 散参；既有请求幂等 scope 无 queue_policy。不新增内容指纹、DTO 或数据库迁移。

### Request 提交事务 Owner 的验证

旧能力不存在：生产代码删除 `RunSubmissionCommand`、`submit_run_command`、`IntakeResult`、`intake_request` 和 `finalize_intake`，删除原 run_submission_service 模块。所有普通来源使用 agent_request_service，内部持久化直接返回 ORM 请求。

重新引入条件：存在独立业务消费者且拥有明确事务边界；仅为拆短函数或重命名不引入中间协议。

- Passed：`docker compose exec api timeout --signal=INT --kill-after=5s 90s uv run --no-sync --group test pytest test/unit -m 'not slow' -q --disable-warnings`，2027 passed、53 skipped。完整提交入口覆盖 dispatched、queued、rejected 的持久化结果与幂等视图，commit 失败时目录和投递不发生。内部持久化测试直接断言 Request/Message，旧 finalize 测试迁入真实提交入口。
- Inspected：独立 Reviewer 检查完整变更及本轮事务职责，未发现新增功能、权限或提交顺序问题；架构入口说明与残留空分支已修正。
- Not run（业务断言未执行）：`docker compose exec api timeout --signal=INT --kill-after=5s 240s uv run --no-sync --group test pytest test/integration/services/test_agent_request_queue_concurrency.py test/integration/api/test_agent_request_queue_router.py test/integration/services/test_scheduled_agent_repository.py test/integration/api/test_checkpoint_state_view.py test/integration/api/test_context_compression_router.py -q --tb=short --disable-warnings`，25 setup errors、82.83 秒。API、etcd、Milvus 已 healthy，readiness 返回 200；前置知识库清理 HTTP GET 仍 ReadTimeout，业务并发与接口断言未执行，未跳过清理。
- Not run（Run 主链路未执行）：`docker compose exec api timeout --signal=INT --kill-after=5s 180s uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k 'deterministic_agent_path_reaches_persisted_result' -q --tb=short --disable-warnings`，1 failed、9 deselected、67.24 秒。前置 `_create_provider` 返回 400：共享测试供应商 `ci-replay` 已存在，尚未提交普通请求；未删除可能由其他测试使用的共享供应商。
- Passed：57 个改动 Python 文件 Ruff check/format、工程信任检查及其 62 项单测、docs build、`git diff --check`。

### 风险与验证边界

- manifest v2 与旧版指纹不同；历史记录只读保留，已有旧 manifest 的 Run 重试会按 write-once 契约拒绝不一致的配置。实时外部模型 provider 校准未执行。
- Skill 元数据读取与文件内容读取不构成跨 PostgreSQL/文件系统的原子事务；预加载摘要代表实际读取的字节。
- manifest 记录准备后的配置与预加载 Skill 内容摘要，不代表 MCP 实际工具可用性、Memory 或动态文件字节的完整快照。
- 取消竞态的历史集成结果为 25 passed、1 failed、1 teardown error；失败 Run 停留 cancel_requested 并最终由 lease 恢复为 worker_lease_expired。原因是 manifest 写入拒绝 cancel_requested，异常分支尝试 failed 又被终态保护拒绝。准备取消分流的负向单测与同一集成集合的 26 项通过共同验证此修复。
- 较早 `docker compose exec api uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k resume_with_offloaded -q --tb=short` 曾遇到 resume Run 为 cancelled，原因未定位；此前该场景通过不抹除这次历史失败。
- Inspected：独立 Review 发现的运行身份注入回归已修复，持久配置中的伪造父线程、子运行标记和 uid/worker_id 由负向案例覆盖。最终代码复查无未解决阻断问题。

### 旧队头投递修复

提交新请求 B 可以派发此前排队的 A。内部持久化返回请求及已有 DispatchResult，提交入口按实际派发结果投递；不从 B 的响应推测要投递的 Run。仅返回请求会遗漏旧队头的即时投递，依赖周期恢复造成延迟。复用现有 DispatchResult，未新增中间类型。

- Passed：`docker compose exec api uv run --no-sync --group test pytest test/unit/services/test_agent_request_service.py test/unit/services/test_agent_request_queue_service.py -q --tb=short --disable-warnings`，70 passed。旧队头场景在投递时确认事务已提交，回读 A 的 Request/Message 与实际 Run ID 一致；B 保持 queued，重发不增加投递。原有 commit 失败负向测试保留。
- Not run：真实 PostgreSQL/HTTP 与 E2E 沿用本记录最近的验证缺口：集成清理 HTTP 超时、E2E 的共享 ci-replay 供应商冲突。本次未重复执行相同受阻前置流程。

### 2026-09-15 集成与 E2E 验证

验证环境为默认开发 Compose。知识库清理超时定位到 `knowledge_files` 统计聚合；该表缺少分析统计，执行 `ANALYZE knowledge_files` 后清理恢复。确定性回放服务健康，残留 `ci-replay` 配置指向测试地址且无活跃 Run，清理该测试配置后由 E2E fixture 重新创建。未跳过清理、改写既有知识库内容或替换持久化断言。

首轮相关集成集合为 47 passed、3 failed。主动压缩 fixture 显式配置 `summary_threshold=200`，保持 checkpoint 中 `200 * 1024` 的独立数值断言；审批 flush/heartbeat fixture 返回 `PreparedRunExecution`，保持真实 PostgreSQL 终态、attempt、清理和事件发布断言。生产实现无需修改。

- Passed：`docker compose exec -T api timeout --signal=INT --kill-after=10s 300s uv run --no-sync --group test pytest test/integration/api/test_context_compression_router.py test/integration/services/test_agent_run_lease.py -k 'compress_thread_persists or approval_flush_overlap' -q --tb=short --disable-warnings`，3 passed、23 deselected，14.45 秒。
- Passed：`docker compose exec -T api timeout --signal=INT --kill-after=10s 600s uv run --no-sync --group test pytest test/integration/api/test_checkpoint_state_view.py test/integration/api/test_agent_request_queue_router.py test/integration/api/test_context_compression_router.py test/integration/services/test_agent_request_queue_concurrency.py test/integration/services/test_agent_run_lease.py test/integration/services/test_scheduled_agent_repository.py -q --tb=short --disable-warnings`，修复后完整集合 50 passed，84.65 秒。
- Passed：`docker compose exec -T api timeout --signal=INT --kill-after=15s 900s uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -q --tb=short --disable-warnings`，10 passed，273.90 秒。覆盖普通请求、SubAgent 模型/审批继承、定时任务、审批恢复、取消、工具审计和附件持久化；回读 PostgreSQL、checkpoint、SSE 与沙盒重建后的文件字节。
- Passed：`docker compose exec -T api timeout --signal=INT --kill-after=10s 180s uv run --no-sync --group test pytest test/unit -m 'not slow' -q --tb=short --disable-warnings`，2028 passed、53 skipped，30.48 秒。
- Passed：`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（62 项）、`pnpm --dir docs run build`、两个修改测试文件的 Ruff check/format 与 `git diff --check`。容器缺少 Ruff 可执行文件，使用后端本地虚拟环境中的 Ruff 检查。
- Inspected：全新独立 Reviewer 核对两个 fixture 的完整 diff、生产契约和 oracle，未发现放宽断言或掩盖生产回归的问题。确定性回放结果不替代真实模型 provider 校准。
