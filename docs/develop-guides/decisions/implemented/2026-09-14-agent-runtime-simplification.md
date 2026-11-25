# Agent 运行时 Context 与 manifest 只准备一次

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/services/preparation.py

## 问题

同一 Run 的执行配置、工作区提示词与 Skill 内容若在 manifest 和 LangGraph 构图阶段分别读取，会出现记录与实际执行不一致。运行身份若混入持久 Agent 配置，又会扩大客户端可控制的执行范围。

## 决策

### 实现方案

worker 取得 Run lease 后，使用持久 Agent 配置、该 Run 已冻结的模型与审批模式、用户与线程身份准备一个 Context。`prepare_agent_runtime_context` 在同一对象上解析资源和 Skill，manifest 从准备结果派生并提交；chat/resume 执行和 `BaseAgent.get_graph(context)` 消费该对象。状态查询无需准备 Context 或模型。面板投影和 PostgreSQL checkpoint 的读取条件见[运行入口](../../../mechanisms/agent-runtime.md#运行入口)。协作成员通过[独立会话](2026-10-03-session-cooperation.md)进入相同的执行准备流程。

`filter_declared_config` 只装载 Schema 可配置字段，worker 注入身份与运行标记。manifest 的摘要包含准备后的可配置字段和工作区提示词；预加载 Skill 正文只保存实际读取字节的摘要。MCP 发现、Memory 和动态文件读取在各自执行边界生效，manifest 不声明冻结这些外部事实。执行处仍校验 Agent 可见性、Project Workdir、资源权限和用户路径。

## 替代方案

- manifest 与构图各自重新读取配置和 Skill：同一 Run 可记录两份不同事实。
- 持久化完整 Context：会扩大敏感内容范围，也无法冻结后续 MCP、Memory 和文件副作用。
- 状态读取重新构图：使只读查询依赖当前模型和外部资源可用性。

## 后果

每个执行段明确携带一份已准备 Context；配置摘要是该准备结果的审计事实，不代表所有外部资源的完整重放。动态资源授权在实际读取或副作用边界再次执行。历史 manifest 保留 write-once 指纹，内容不被新的准备结果覆盖。

## 验证

运行时 Context 单测和 worker E2E 核对 manifest、实际 Skill 内容、权限与 Run 结果；PostgreSQL checkpoint 查询测试确认只读状态不初始化模型。命令与当前结果以交付 PR 的实际记录为准。

旧能力不存在：执行流不创建 Thread 或用户输入、不从字典补建身份、不为 manifest 再次解析 Skill；状态读取不准备 Context。

重新引入条件：存在明确的新 consumer，且能证明另一份配置快照不会与当前 Run 的执行事实分叉。
