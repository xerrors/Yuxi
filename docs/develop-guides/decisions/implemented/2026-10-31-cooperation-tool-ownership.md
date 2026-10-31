# 协作工具定义归属与助理沟通职责

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/middlewares/cooperation.py

## 问题

普通协作与个人助理重复定义提交、消息、查询和取消工具。助理中间件同时拥有用户沟通和会话协作，公共构图入口又把助理的全部工具当作单个协作中间件接入，工具协议维护分散。

## 决策

### 实现方案

公共协作模块统一构造协作工具，`create_cooperation_middleware` 接受 Backend 显式传入的 `independent_tasks`。普通模式返回原有 `CooperationMiddleware`，保留身份提示、收件箱、等待和游标；独立任务模式返回仅包含工具的基础 `AgentMiddleware`，创建入口仍调用 `create_task`，不引入普通协作钩子、状态或等待工具。资源可用范围继续由既有装配与执行授权维护。

`PersonalAssistantMiddleware` 只注册 `send_to_user`。公共构图入口通过 `middleware` 序列接收 Backend 声明，按既有位置装配；运行图与 checkpoint 清理图使用同一声明。公共模块不依赖具体 Backend 类型，不新增用户配置、模式注册表或策略类。本决定细化[执行图装配](2026-10-30-agent-graph-composition.md)的协作接入方式，保留[个人助理运行机制](2026-10-30-personal-assistant-runtime.md)的沟通与委派协议。

## 替代方案

- 保留两套协作定义：当前协议相同且已有两个消费者，重复维护没有收益。
- 在普通中间件全部模型钩子里增加助理分支：会引入无用节点或状态，并增加运行时判断。
- 删除助理中间件并合并显式沟通到协作模块：混合用户沟通与会话协作职责。
- 收窄助理中间件、共享协作工具定义：采用；仅创建工具和是否等待按既有业务差异选择。

## 后果

两个 Backend 共用六个协作工具包装，创建工具各自保留准确的 schema 和服务路由。助理仍创建普通 Agent 的独立 root 和新 Project；普通协作仍支持 child/root、同项目共享和持久等待。中间件拆分不增加助理的 before_model 节点或游标，不更改服务权限、持久数据或部署配置，也不增加 checkpoint 兼容层。

## 验证

后续清理的完整回归与隔离测试环境说明见[交互与执行边界收敛](2026-10-31-personal-assistant-policy-simplification.md)。

- 独立 Compose API 容器执行 `uv run --no-sync --group test pytest test/unit/agents/test_personal_assistant_runtime.py test/unit/middlewares/test_cooperation_middleware.py test/unit/agents/test_summary_graph_config.py test/unit/agents/test_chatbot_tool_surface.py -q`：53 passed，覆盖工具/schema、身份提示、等待恢复及运行/清理图的一致性。
- 同一容器执行 `uv run --no-sync --group test pytest test/integration/services/test_personal_assistant_cooperation.py -q`：3 passed，回读独立任务的 Project、目标配置、原始 Input 与幂等回执。
- 同一容器启动临时协作模型回放端点后，执行 `test/e2e/test_personal_assistant_e2e.py`、`test/e2e/test_session_cooperation_e2e.py::test_parallel_wait_inputs_keep_both_interrupts_and_resume_exact_results` 和 `test/e2e/test_session_cooperation_e2e.py::test_independent_root_cooperation_uses_real_worker_and_persisted_origins`：6 passed，通过真实 HTTP、worker 和 PostgreSQL 验证独立任务、跨树来源、产物及并行等待恢复。
- 两项负控均在正确断言处失败：向助理中间件重新加入协作工具时，`test_send_to_user_returns_only_validated_communication_content` 拒绝额外工具；把独立任务工厂替换为携带普通协作钩子的中间件时，助理完整构图测试拒绝 `cooperation_cursor`。负控只修改隔离测试进程内的对象，不改写源码或 oracle。
- 独立 Reviewer 已核对完整需求、diff、服务调用链、测试 oracle 和负控日志，未发现本任务范围内需修复问题。
- 最终合并代码在独立 API 容器中设置 `YUXI_KNOWLEDGE_ENABLED=true`，执行 `uv run --no-sync --group test pytest test/unit -m "not slow" -q -p no:cacheprovider`：3524 passed、125 skipped。补挂完整仓库后执行 `python -m pytest test/unit/config -q -p no:cacheprovider`：196 passed、11 skipped，剩余跳过项需要容器内 Docker Compose 命令；跳过项不计为通过。未执行完整 E2E 套件或 CI。
- `ruff check yuxi`、`ruff format yuxi --check`、`ruff check --select I yuxi`、工程契约检查及其 56 项 unittest 均通过。

旧能力不存在：助理模块中的协作函数包装、跨模块 `invoke_cooperation` 调用及公共构图的单个 `cooperation` 参数已移除。`PersonalAssistantMiddleware` 的工具集合只包含 `send_to_user`；共享协作工厂不注册用户沟通工具。

重新引入条件：出现新的实际执行拓扑且当前显式组合无法表达时单独评审，不为假想消费者增加抽象。
