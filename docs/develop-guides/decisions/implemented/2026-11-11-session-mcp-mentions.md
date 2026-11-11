# 会话通过提及挂载连接器

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/inputs.py

## 问题

连接器提及候选受 Agent 已配置的 MCP 范围限制，Public 输入也没有显式传递连接器选择的字段。用户无法在当前会话增加可用连接器并在后续轮次继续使用。

## 决策

### 实现方案

Public 创建与消息输入通过 `yuxi.mcps` 携带会话连接器配置增量。Agents 输入 service 在现有 Thread 锁与接收事务中，将选择合入 `Session.config_snapshot.mcps`；Input 继续冻结接收时的配置。保留 `all` 的动态选择语义，列表按首次出现顺序去重。创建空会话也可挂载，省略或空列表不移除既有选择。重试意图包含显式新增选择，未传选择的既有幂等意图保持兼容。

Public schema 的 `SessionConnectorOptions` 拥有会话连接器追加，`InputAttachmentOptions` 只拥有本次输入的附件引用；创建和消息 DTO 组合两类选项，共用传输入口不改变各自归属。附件先绑定 Input，消费时绑定其最后一条用户 Message，不进入会话配置。创建或更新时的模型选择属于会话配置；消息事件的模型覆盖只影响本次 follow-up，steer 不接受该覆盖。

连接器选择在接收事务提交时持久化。后续输入使用会话选择，已经接收的 Input 不被追溯修改。取消 pending Input 不撤销已经提交的会话选择。运行中沿用现有 steer 安全边界；新 Run 保留前一 Run 的模型与审批配置，合入本批输入冻结的 MCP 选择，连续 steer 的选择不丢失。交互恢复继续使用原 Run 快照。

Extensions 拥有连接器目录、个人连接及最终出站权限；复用现有资源解析、可用性查询和执行授权，不将持久选择视为授权。Extensions 目录通过已有可用性服务投影 `available`，覆盖当前身份、部署开关、服务启停和本人连接；前端使用该结果提供候选，从提及 token 提取 slug，随原消息发送。产品名称统一为“连接器”，协议、技术标识和配置示例保留 MCP。

## 替代方案

- 增加 `activated_mcps` checkpoint 字段：接收时可能尚无 checkpoint，并会产生会话配置与图状态的双重来源。
- 仅修改本次 Context：无法满足后续输入和刷新后的持续生效。
- 每次执行读取最新会话选择：会追溯改变已接收 Input 的冻结语义。
- 在接收层另建权限规则：重复现有 Extensions 执行边界，且持久的授权快照不能替代当前权限。
- 将连接器字段并入附件选项：混淆会话配置增量与输入附件的生命周期；通过具体请求 DTO 组合保留各自边界。

## 后果

持久选择只属于当前会话；Agent 定义和其他会话保持原有配置。选择保存在现有 JSON 字段，不涉及数据迁移、其他资源挂载或新增配置项。取消输入不撤销已经接受的会话选择；客户端不能用空列表卸载连接器。

Session 配置拥有后续输入的默认选择；Input 快照拥有已经接受的输入配置；Run 快照拥有本次执行配置；LangGraph checkpoint 拥有图执行状态。核对锁定并安装的 LangGraph 1.2.14 官方包源码 `runtime.py`、`pregel/main.py` 后，沿用现有每次调用的 Runtime context，不增加 checkpoint 激活字段。

选择持久化不保证远端服务持续可用，也不保存授权结论或个人凭据。`all` 仍动态解析当前目录，缺失、停用、未连接及授权失效仍由 Extensions 的当前规则处理。

## 验证

以下命令均从仓库根执行，另行标明工作目录的除外。测试回读实际数据库、协议和 DOM；没有用模型回答或 HTTP 成功替代业务结果。

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`：3726 passed、7 subtests passed、127 skipped。跳过原因是容器缺少仓库根文件或 Docker Compose；宿主补跑 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`：212 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_input_mcp_selection.py test/unit/routers/test_public_session_contract.py test/integration/services/test_input_attachments.py test/integration/services/test_session_mcp_selection.py -q`：52 passed。生成 OpenAPI 与输入模型保持既有 JSON 形状；真实 PostgreSQL / MinIO 验证同次提交的连接器进入会话配置、附件仅绑定该 Input、单次模型覆盖不影响后续输入，并覆盖合并、幂等冲突、取消、快照冻结、steer、跨身份拒绝与候选可用性。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_public_session_mcps.py test/integration/api/test_mcp_router.py test/integration/api/test_module_failure_boundaries.py -q`：36 passed，验证真实服务的个人 Key 创建、JWT 回读、`all`、原 Agent / 其他会话不变，以及管理权限、配置拒绝与协议结果。
- 使用 `docker compose exec -T api uv run --no-sync --group test python` 在测试进程启动仓库 `test.support.openai_replay_server --port 8765`，设置 `OPENAI_REPLAY_URL=http://api:8765/v1`，调用 `pytest.main(["test/integration/api/test_public_session_mcps.py", "test/integration/api/test_public_agent_files.py", "-q"])`：8 passed。真实 HTTP 验证创建、连接器与附件提交；临时 replay 子进程在 `finally` 关闭。首次执行因缺少该环境变量失败，补齐环境后通过。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_mcp_oauth.py -q`：62 passed，验证本人连接、远端拒绝、断开、配置变化和旧工具对象出站时重新授权。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_session_mcp_selection_e2e.py test/e2e/test_mcp_personal_credentials.py -q`：3 passed。确定性 HTTP 模型与 MCP 服务经过真实 API、worker 和 PostgreSQL，独立核对 8 轮实际工具调用的工具集合、身份与本轮标记，回读目标 Run / Session，并确认独立会话、其他用户、断开和停用行为；隔离测试用户并清理历史、checkpoint、Project 和目录。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_session_mcp_selection_e2e.py -q`：1 passed，复核组合后的请求 DTO 经过真实 worker，8 轮工具调用、会话持久化和身份隔离结果成立。
- 持久化故障注入：在独立 pytest 进程中将输入合并替换为“仅返回旧选择”，上述真实数据库 / HTTP 用例在 `config_snapshot` 断言处失败；没有修改生产源码。
- `docker compose exec -T frontend pnpm run test:unit`：1066 passed；`docker compose exec -T frontend pnpm exec node --test test/unit/agentMentionConfig.test.js test/unit/publicAgentApi.test.js test/unit/sessionCommands.test.js`：22 passed，覆盖最终候选投影、提交与冻结重试。`lint:check`、`build` 通过，构建仍提示既有 CSS / 大 chunk 警告。
- `playwright-cli -s=connectors --raw run-code --filename=frontend/test/browser/connectorMentions.js`：真实页面通过产品名称、创建弹窗、Agent 范围外候选、键盘插入与整体删除、刷新候选、Public 提交载荷，以及浅深色和 768 / 1440 宽度检查。最终截图为 `yuxi-connectors-list.png`、`yuxi-connectors-create.png` 和 `yuxi-connectors-mention-{light,dark}-{1440,768}.png`。浏览器提交用明确的 422 fixture 截获并检查载荷；持久化和实际执行由上述独立 worker E2E 证明。临时用户和会话均定向清理。
- 后端工作目录下 `uv run --no-sync ruff check yuxi`、`uv run --no-sync ruff format yuxi --check`、`uv run --no-sync ruff check --select I yuxi` 通过。
- `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`（56 项）、`git diff --check` 通过。文档目录 `pnpm run build` 通过，工程契约检查同时验证文档相对链接。

独立 Reviewer 使用不继承开发上下文的新 Agent 审查原始需求、方案必要性、完整 diff、规范和直接证据；审查已通过，没有剩余发现。未探测第三方生产连接器或真实供应商 OAuth 兼容性，不将确定性协议 fixture 的通过扩大为这些结论。
