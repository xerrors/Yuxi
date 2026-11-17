# 中间件

中间件把文件、Skills、会话协作、上下文压缩、审批和用量统计接到 LangGraph Agent。它们在模型调用、工具调用或 state 更新的边界运行，让不同 Agent 复用同一套能力。

内置 `ChatbotAgent` 在 `get_graph()` 中组装中间件。Graph 创建前，系统完成用户资源和权限的归一化；运行中的最新授权由执行边界复查，具体规则见[Agent 运行机制](../mechanisms/agent-runtime.md#资源权限)。

## Graph 创建前的准备

`prepare_agent_runtime_context` 会根据当前用户和 Agent 配置：

- 过滤内置工具、知识库、MCP 和 Skills；
- 展开 Skill 依赖，生成 `skill_runtime_snapshot`；
- 使用系统默认模型补齐空的模型配置。

随后，工具解析器记录工具来源和可执行注册集合，`build_prompt_with_context` 生成系统提示词，Agent 再创建 Graph。工具执行时仍需检查具体目标，准备阶段的资源快照不是授权替代品。

## 内置中间件顺序

`ChatbotAgent` 的常见顺序如下（[graph.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/agent_backends/chatbot/graph.py) 装配）。可选项只在对应能力启用时加入：

| 顺序 | 中间件 | 作用 |
| --- | --- | --- |
| 1 | `ToolErrorGuardMiddleware` | 最外层隔离普通工具异常，保留取消与 interrupt 的传播 |
| 2 | `SteerMiddleware` | 在安全边界发现待接替请求 |
| 3 | `create_agent_filesystem_middleware` | 提供 Workdir、User Data、Skills 文件后端，并卸载过大的工具结果 |
| 4 | `SkillsMiddleware` | 注入 Skill 说明，按激活状态开放依赖 |
| 5 | `YuxiMemoryMiddleware` | Memory 开关开启时，注入用户记忆并提供受限工具 |
| 6 | `CooperationMiddleware` | 所有持久 Run 提供统一 Session 协作工具 |
| 7 | `YuxiSummarizationMiddleware` | 先确定性压缩工具结果，仍达到同一阈值时生成摘要 |
| 8 | `PatchToolCallsMiddleware` | 修正部分工具调用消息形态 |
| 9 | `NetworkRetryMiddleware` | 网络错误按预算、其他可重试模型错误按次数重试，耗尽后抛出异常 |
| 10 | 工具审批 middleware | 默认模式下审批 Project 外的文件写入和命令执行；当前 Project 内的写/编辑自动放行 |
| 11 | `RuntimeAuthorizationMiddleware` | 准备模型输入时刷新资源，实际请求前同步投影；返回时先于审批复查身份 |
| 12 | `ImageInputCompatibilityMiddleware` | 桥接工具读取图片与模型输入格式；必要时回退 OCR |
| 13 | `TokenUsageMiddleware` | 记录授权和图片转换后的近似上下文及主模型实际用量 |

模型重试耗尽后，异常进入 Run 失败通道，持久化 `failed` 状态与错误原因；已有部分输出保留错误元数据。

协作成员的失败进入持久状态通知，等待方读取目标 Turn 的结果，由模型决定后续处理。最终正常回答仍须满足同 Run 的 model lifecycle 审计关联。

## Skills 和知识库

Skills middleware 将 Skill 说明按模型请求注入：预加载 Skill 从首轮开放工具和 MCP 依赖，普通 Skill 在模型读取对应 `SKILL.md` 后激活，再开放声明的依赖。Agent 显式选择的 MCP 服务器从运行开始提供工具。

知识库能力由内置 `knowledge-base` Skill 提供。它的工具是否注册、模型是否可见、参数是否能访问目标知识库分别由工具组装、Skill 激活和知识库权限检查负责。完整链路见[工具系统](./tools-system.md)和[知识库机制详解](../mechanisms/knowledge-base.md)。

## 文件和附件

上传先保存隔离的附件草稿。提交消息时，接收事务把附件绑定到 Input，再由附件准备流程写入当前 Project Workdir。文件就绪后，消费事务创建 Message 并绑定最终附件，提交后才投递 Run。模型输入使用本次 Input 及此前已消费输入的附件上下文，按需提供文件名和当前路径；持久 Message 保留原始文本。状态与恢复边界见[输入队列与调度](../mechanisms/agent-request-queue.md#状态与故障恢复)。

协作成员各自拥有 Thread、Input、Turn、Run、消息历史和 PostgreSQL checkpoint，共享同一 Project Workdir。同用户所有会话共用持久 Cube VM。共享文件的修改对其他成员可见，消息与执行归属仍由各自持久记录隔离。Viewer、附件和 artifact API 直接读取授权的 Workspace/Workdir 文件，文件访问与执行环境边界见[沙盒机制](../mechanisms/sandbox.md)。

## 会话协作

所有 Session 都有创建、提交、消息、取消、查询与持久等待工具。协作消息在模型调用前消费，消费位置随 checkpoint 保存。等待释放 worker 和树内名额，恢复创建同 Turn 的下一 Run。

接口、授权、结果与环境边界见[会话协作](./session-cooperation.md)。

## Summary 上下文压缩

Summary 在文件和 Skills 等中间件之后运行。请求达到唯一压力阈值后，它先为当前模型请求精简工具结果并重新计量；仍达到同一阈值时才写入历史文件、生成摘要并更新 checkpoint。它不会删除 PostgreSQL 聊天消息，也不会把内部摘要模型调用当作用户可见回复。支持主动压缩的 Agent 复用同一摘要器，并通过 service 在空闲线程的 canonical graph state 上更新 checkpoint。

参数和状态细节见[上下文压缩机制](../mechanisms/context-compression.md)。

## Token 用量

`TokenUsageMiddleware` 同时记录近似上下文 token（用于摘要阈值和状态面板）和主 Agent 模型返回的 `usage_metadata`（按最近调用、当前 Run、线程和模型分桶保存）。

`siliconflow-cn` 和 `siliconflow` 当前位于 Provider 用量黑名单：这两个供应商仍可提供近似上下文统计，但不会写入最近调用、Run 或线程的 Provider 实际用量聚合。其他能返回兼容 `usage_metadata` 的供应商才会进入这组实际用量统计；Summary 内部摘要模型调用不计入。

Run 终态时，worker 把与当前 `run_id` 匹配的 state 快照写入 `AgentRun.token_usage`；父 Run 和子 Run 分开保存。

## 新增中间件时

先说明它要改变哪一条边界：Prompt、模型调用、工具调用、文件访问、state 或观测。资源筛选和权限收敛放在 Graph 创建前。文件读写和工具结果卸载优先复用现有 filesystem middleware。新增模型可见输入或副作用时补充对应测试和失败案例。

实现入口：[ChatbotAgent graph](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/agent_backends/chatbot/graph.py)、[中间件目录](https://github.com/xerrors/Yuxi/tree/main/backend/yuxi/modules/agents/runtime/middlewares)。
