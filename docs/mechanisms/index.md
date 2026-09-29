# 机制详解

本组页面解释 Yuxi 在运行时为什么这样工作，适合已经完成[快速开始](../intro/quick-start.md)的开发者和运维人员。这里重点回答“谁创建、谁保存、谁能访问、失败后怎么办”，不重复配置手册里的完整变量清单。

## 怎么读

- 想知道配置怎样进入一次 Agent 运行：先看[Agent 运行时上下文](./agent-runtime.md)。
- 想知道 Agent 文件和命令在哪里执行：再看[沙盒与文件系统](./sandbox.md)。
- 想知道长对话怎样压缩：继续看[上下文压缩](./context-compression.md)。
- 想知道文档怎样从上传变成可检索：最后看[知识库](./knowledge-base.md)。

每页都按“入口 → 装配或派发 → 执行 Owner → 持久化/文件 → 可观察结果”展开。排查问题时先看“失败、恢复与观察边界”，修改实现时从“源码定位与验证”进入 owning 模块和测试。

## 专题地图

| 专题 | 回答的问题 | 配置/操作入口 |
| --- | --- | --- |
| [Agent 运行时上下文](./agent-runtime.md) | 配置、权限、文件和 checkpoint 怎样组成一次运行？ | [配置和开发智能体](../agents/agents-config.md) |
| [沙盒与文件系统](./sandbox.md) | runtime identity、挂载、路径权限和回收怎样协作？ | [沙盒配置与运维](../advanced/sandbox-operations.md) |
| [上下文压缩](./context-compression.md) | 单一阈值怎样控制工具结果压缩、摘要和主动压缩？ | [中间件](../agents/middleware.md)、[智能体配置](../agents/agents-config.md) |
| [知识库](./knowledge-base.md) | 文件状态、存储、权限和 Agent 检索怎样连接？ | [知识库教程](../intro/knowledge-base.md)、[文档处理](../advanced/document-processing.md) |

只有当主题有稳定的 Owner、真实 consumer 和可验证链路时，才新增机制页。未来设计放入 roadmap 或 proposed decision，不画进当前运行图。
