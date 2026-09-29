# 测试套件边界与 readiness 探测

状态：archived
类型：testing
Owner：backend/test/run_tests.sh

## 问题

测试审计把不同观察边界的 unit、真实 provider 探针和 E2E 测试按主题名称归为重复项，并发现测试运行器用 liveness 作为集成测试前置条件。前者不足以证明可以删除测试；后者会在 API 进程存活但依赖或 worker 未就绪时错误启动测试。

## 决策

测试运行器在 integration、E2E 和全量测试前检查 `/api/system/ready`。真实 provider 探针放在 `integration/services`，因为它验证模型服务适配与真实 provider 请求，不验证完整 API 链路。

保留具有不同语义 Owner 或观察边界的测试：unit 验证确定性适配逻辑，provider 探针验证真实外部协议，E2E 验证 API、worker、SSE、PostgreSQL 与历史回读。仅凭文件名、主题或 mock 结构相似不删除其中任一层。删除 provider reasoning unit 中 3 个仅重复历史恢复参数的实例，保留覆盖这些输入形态的主测试。

## 替代方案

- 继续使用 `/api/system/health`：拒绝，因为它只表达 API 进程 liveness。
- 以 provider 探针替代 E2E：拒绝，因为探针不经过 API、worker、SSE 或 PostgreSQL。
- 以 E2E 替代 provider 探针：拒绝，因为 E2E 不能覆盖多个模型协议形态且会放大计费与环境成本。
- 按主题批量删除 router、middleware 或 service/worker 测试：拒绝，因为当前证据未证明它们断言同一事实。

## 后果

测试文件数量不因未经证明的“重复”而减少；provider reasoning unit 的参数实例减少 3 个，测试前置条件与目录语义更准确。后续删除测试必须先指出被删除测试的语义 Owner、现存独立 oracle 和负向覆盖。

本轮审计继续删除 5 个仓库内无引用的死数据夹具，合并只含一个标题层级单测的孤立文件，并将两份 AgentRun 集成测试共有的持久化创建链路抽到测试辅助模块；清理不改变测试断言或被测行为。仍被知识库路由集成测试使用的 `A_Dream_of_Red_Mansions_10hui.txt` 保留。

删除只验证测试输入会触发限长和只扫描源码字面量的两个低信息量单测；chatbot prompt 的独立常量断言合并到 `build_prompt_with_context` 的运行时组装结果测试。同一边界的 fence 与 URL 变体改为参数化，保留实际行为覆盖。

对 5 个仍有独立业务断言的内置 Skill 测试，只抽取重复的 registry fixture；没有合并不同 Skill 的契约，也没有降低断言粒度。

逐文件复核后又删除一个名不副实的 laws 分块测试：它声称验证句子边界，实际只验证限长，且与同文件的参数化限长测试重复。对保留的弱 oracle，则补齐实际结果：QA/Book 分块断言完整内容序列，benchmark reorder 断言 drain 后的具体条目，chunk token 默认值使用会跨越错误默认上限的输入，项目路径测试固定断言 400，subagent 取消测试断言共享 runtime 未被释放；同时移除重复的 question id 断言和 provider 数量门槛。

旧能力不存在：测试运行器不再把 liveness 检查当作 integration、E2E 或全量测试的 readiness gate。
重新引入条件：只有当 `/api/system/ready` 不再表达接收真实测试流量所需的 PostgreSQL、Redis 与 worker 条件，并同步更新架构契约与测试证据时，才可重新设计 gate。

## 验证

- `backend/test/unit/config/test_docker_compose_worktree_slots.py::test_host_test_runner_probes_current_compose_slot` 直接拒绝 health-only 探测。
- `backend/test/unit/agents/test_provider_reasoning.py` 的历史恢复矩阵保留标准块、旧扩展字段、畸形值和 literal `<think>` 等输入；删除的 3 个实例没有独立断言事实。
- `backend/test/integration/services/test_provider_reasoning_live.py` 仍仅在显式配置模型时调用计费服务。
- 合并后的 `test_semantic_chunking.py` 保留标题层级推断和空标题行为两组独立断言。
- 两份 AgentRun 集成测试仍各自保留不同的清理逻辑与 schema fixture，只共享完全相同的最小创建链路。
- 已用仓库搜索确认删除的数据夹具没有消费者；保留的数据夹具仍有直接引用。
- QA 限长和 Tasker 的删除项没有独立业务 oracle；`test_chatbot_prompt.py` 在完整组装结果上保留 `html:preview` 不出现的负向断言。
- 内置 Skill registry 测试仍分别断言各 Skill 的名称、依赖、文件和替换关系，仅不再重复构造相同输入。
- `test_ragflow_like_chunking.py` 的保留分块测试现在检查完整内容序列；被删除的 laws 测试没有独立边界 oracle。
- `test_model_request_timing.py`、`test_run_worker.py` 和 `test_runtime_initialization.py` 分别验证“不落库”“不释放共享 runtime”“只创建实际使用类型”，不再仅以不抛异常或模糊包含作为通过条件。
- `test_chunking_token_limit.py` 的零上限与默认上限用精确 chunk 结果和会触发切分的输入验证，避免错误实现仍通过。
- 未执行真实 provider 探针和 E2E；它们需要外部模型凭据与运行中的完整 Compose 拓扑。
