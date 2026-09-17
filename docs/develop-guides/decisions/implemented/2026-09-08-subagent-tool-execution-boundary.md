# 子智能体禁用工具的执行边界

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/buildin/subagent/graph.py

## 问题

子智能体的默认模式隐藏敏感文件系统工具，但中间件仍可将它们注册到 ToolNode。模型显式返回未展示的工具名时，工具列表过滤不足以阻止执行。

## 决策

文件系统中间件工厂在注册前排除禁用工具；子智能体在同步和异步工具执行入口返回绑定原 tool call 的错误结果。默认模式保留敏感工具禁用策略，`always_trust` 保留文件写入与执行能力。代码是权限事实的 Owner，此修复不增加 Run 或 Thread 审批状态。

## 替代方案

仅隐藏工具或通过提示词约束不能阻止显式调用。让子智能体具备与主智能体一致的审批、暂停和恢复能力，需要闭合子 Run 与父任务之间的执行所有权和恢复链路，作为独立功能处理。

## 后果

默认模式的子智能体仍不能直接写入当前 Project，需将结果交回主智能体处理。拒绝结果不会自动转发工具调用，也不会自动创建审批。

运行系统测试对 Message audit 检查设置三分钟步骤上限、六十秒 Python 堆栈诊断，并在失败或取消时尝试收集服务日志。这只改善挂起的定位；步骤超时后的远端 pytest 由环境清理终止，全局 job 超时仍可能限制日志收集。

## 验证

`backend/test/unit/agents/test_subagent_tool_filter.py` 的同步和异步拒绝用例覆盖工具执行 guard，注册检查覆盖禁用工具未进入文件系统工具集合。

`backend/test/e2e/test_deterministic_agent_path_e2e.py::test_subagent_worker_enforces_inherited_write_policy` 通过真实父 subagent_start/subagent_await、子 Run 和 PostgreSQL checkpoint 验证审批模式继承与 ToolMessage 关联，回读共享 Workdir 证明默认模式未写入、信任模式写入。执行前拒绝保存在 checkpoint ToolMessage 中；信任模式另外核对执行阶段的 Tool audit。该参数化用例由 Runtime System Tests 的确定性 E2E 步骤执行。

本地隔离 Compose 中两种模式 E2E、相关前置 integration 和 Message audit 检查通过，未复现 CI 挂起。该结果不证明 CI 根因已消除，合并前需重新检查远端 CI。修复沿用既定的禁用策略，没有新增待裁决的权限语义，因此直接记录为 implemented。
