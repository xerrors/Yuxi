# 机制详解

本组页面面向已经了解基本部署和使用方式的开发者、运维人员，解释运行状态、事实归属、权限及失败恢复。首次完成产品任务从[快速开始](../intro/quick-start.md)进入；部署和参数查找使用[部署参考](../advanced/deployment.md)与[配置参考](../advanced/configuration.md)。

## 从一个任务开始

接手代码时，先沿用户动作定位“身份 → 决策 → 持久事实 → 失败恢复 → 验证”，再进入对应机制页。下面只解释容易混淆的名称，完整状态和约束由链接页面维护。

| 名称 | 对应事实与修改入口 |
| --- | --- |
| Thread / Session | 同一长期会话；`Session` 映射 `sessions` 表，`id` 是内部行主键，`thread_id` 是业务身份。接收与会话用例在 Agent services，见[输入队列](./agent-request-queue.md) |
| `agent_id` / Agent slug | 会话与现有协议中的 `agent_id` 保存 `Agent.slug`；按所属契约使用原字段，不把它当作 Agent 数字主键，见[Public API](../advanced/agents-public-api.md) |
| `modules/models` / 各域 `models.py` | 前者拥有 chat/embed/rerank 调用和 providers 配置，不是 ORM 汇总；后者拥有对应业务域的持久映射，见[模型域入口](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/models/__init__.py) |
| Skill 管理 / 运行 | 共享、个人、安装草稿、Agent 绑定分别从 shared、personal、install_draft、bound 进入；授权、内容提交、投影和运行快照是共同边界，见[Skill 代码入口](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/__init__.py) |
| Input / Receipt | 一次提交的原始输入，以及幂等接收事实；由输入接收事务保存，见[输入队列](./agent-request-queue.md) |
| Turn / Run | 一轮工作与其中的一段执行；运行租约属于 Run，Turn 从指定结果 Run 取最终输出，见[运行上下文](./agent-runtime.md) |
| Workdir | Project 绑定的工作目录，由 workspace 边界校验归属和访问，见[沙盒与文件系统](./sandbox.md) |
| `workdir_path` / 界面相对路径 / 沙盒路径 | 持久绑定、界面寻址与运行环境寻址；由 workspace 显式映射，不能直接拼接互换，见[路径边界](./sandbox.md) |
| Agent Run / schedule occurrence / BackgroundJob | 对话执行段、定时触发记录、知识处理等后台作业；各自登记与领取，失败写回各自持久记录。定时触发会调用 Agent 输入用例，不能因此合并生命周期，见[架构后台任务](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md#后台任务) |

新增名称与既有协议的映射遵循[局部可理解性规则](../develop-guides/spec-loop.md#局部可理解性)。

例如，排查一条聊天消息，从输入队列找到接收、消费和投递，再读运行上下文中的执行与结算；排查删除知识文件，从知识库机制找到删除事实与异步清理，区分 HTTP 返回和外部清理完成。需要解释设计取舍时，才沿机制页进入对应决策；[决策状态](../develop-guides/decisions/README.md)区分当前决定、待实现方向和历史。

## 怎么读

- 排查资源不可见、共享授权或运行中撤权：看[资源权限](./resource-permissions.md)。
- 理解配置怎样进入一次运行：看[Agent 运行时上下文](./agent-runtime.md)。
- 理解 MCP 共享配置、个人授权及调用身份：看 [MCP 授权与运行身份](./mcp.md)。
- 核对排队、引导、取消或恢复：看[输入队列与调度](./agent-request-queue.md)。
- 排查文件路径、执行环境和清理：看[沙盒与文件系统](./sandbox.md)。
- 理解长对话怎样压缩：看[上下文压缩](./context-compression.md)。
- 排查文档解析、入库和检索：看[知识库](./knowledge-base.md)。

每页都按“入口 → 装配或派发 → 执行 Owner → 持久化/文件 → 可观察结果”展开。排查问题时先看“失败、恢复与观察边界”，修改实现时从“源码定位与验证”进入 owning 模块和测试。

## 专题地图

| 专题 | 回答的问题 | 配置/操作入口 |
| --- | --- | --- |
| [资源权限](./resource-permissions.md) | 角色、私有/共享范围和运行中撤权在哪里执行？ | [配置智能体](../agents/agents-config.md#私有与共享定义)、[API Key 接入](../advanced/api-key-integration.md) |
| [Agent 运行时上下文](./agent-runtime.md) | 配置、权限、文件和 checkpoint 怎样组成一次运行？ | [配置和开发智能体](../agents/agents-config.md) |
| [MCP 授权与运行身份](./mcp.md) | 管理员配置、用户连接、刷新和工具调用怎样隔离？ | [MCP 集成](../agents/mcp-integration.md) |
| [输入队列与调度](./agent-request-queue.md) | Input 怎样消费成 Turn/Run，steer、取消和失败后怎样恢复？ | [Agents Public API](../advanced/agents-public-api.md)、[会话协作](../agents/session-cooperation.md) |
| [沙盒与文件系统](./sandbox.md) | runtime identity、挂载、路径权限和回收怎样协作？ | [沙盒配置与运维](../advanced/sandbox-operations.md) |
| [上下文压缩](./context-compression.md) | 单一阈值怎样控制工具结果压缩、摘要和主动压缩？ | [中间件](../agents/middleware.md)、[智能体配置](../agents/agents-config.md) |
| [知识库](./knowledge-base.md) | 文件状态、存储、权限和 Agent 检索怎样连接？ | [知识库教程](../intro/knowledge-base.md)、[文档处理](../advanced/document-processing.md) |

只有当主题有稳定的 Owner、真实 consumer 和可验证链路时，才新增机制页。未来设计放入 roadmap 或 proposed decision，不画进当前运行图。
