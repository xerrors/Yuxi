# 工具审批模式由会话持有

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/context.py

## 问题

工具审批模式由会话或定时任务选择，并随 Input 配置快照固定。智能体 Context 同时把该字段声明为可配置且仅在表单隐藏，导致智能体保存的审批值仍能成为新会话默认值，职责与会话入口重复。

## 决策

### 实现方案

`BaseContext.tool_approval_mode` 仅作为运行字段传递，不参与智能体配置的读写、默认值展开或表单描述。现有配置过滤器负责忽略历史智能体值，现有写入边界拒绝该字段；不迁移或删除历史 JSON 数据。

Input 接收服务显式将请求审批选择或系统默认值写入快照。运行准备、审批恢复、取消清理与压缩从既有会话快照注入运行 Context，协作快照保留派发方审批选择。会话、定时任务和公开 API 的审批入口与协议保持现有语义。

## 替代方案

- 保留：继续允许智能体配置审批默认值，职责重复。
- 收窄：仅隐藏表单，仍允许 API 写入和运行继承，无法移除配置能力。
- 替换：新增独立策略对象或迁移所有运行协议，增加当前需求不需要的维护表面。
- 删除：移除智能体配置能力，保留消费者需要的运行字段，采用此方案。

## 后果

历史智能体审批值不再影响新建会话；已有 Input、Thread 和 Run 快照保持有效。运行快照装载显式恢复审批字段，协作快照和 manifest 的配置摘要继续包含该值。数据库结构与历史 JSON 均无需迁移。

## 验证

- Passed：`docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`，3588 通过、125 跳过；容器未挂载仓库根目录导致的部署配置跳过由 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q -p no:cacheprovider` 补验，207 通过。
- Passed：`docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_agent_lifecycle_services.py test/unit/services/test_session_config_snapshot.py -q -p no:cacheprovider`，12 通过。取消清理恢复会话审批；配置快照忽略智能体旧值；运行准备测试同时断言协作继承和 manifest 摘要保留审批。
- Passed：`docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_agent_config_resource_authorization.py::test_agent_approval_mode_is_rejected_and_hidden_for_all_roles -q -p no:cacheprovider`，3 通过。真实 HTTP 拒绝三种角色的新写入，PostgreSQL 回读证明失败无写入，历史字段不进入配置响应。
- Passed：`docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_session_config_e2e.py::test_session_approval_selection_reaches_worker_without_legacy_agent_default -q -p no:cacheprovider`，1 通过。独占模型重放与真实 worker 验证默认和完全信任两次执行，并回读 Session、Run、manifest、Turn 与输出 Message；该用例进入 `backend/test/run_tests.sh` 的核心 E2E 集合。
- Passed：后端源码 Ruff 检查、格式和导入检查；工程契约检查及其 56 个单测；文档构建；`git diff --check`。

旧能力不存在：`tool_approval_mode` 的 metadata 声明 `configurable=False`，配置读写测试证明该字段不可保存或读取。恢复旧 metadata 时配置读写测试失败；恢复从智能体继承审批值的解析时，新会话默认值测试因得到 `always_trust` 而失败。

重新引入条件：产品明确需要可管理的智能体审批默认值，并定义与用户会话选择的优先级和授权边界。
