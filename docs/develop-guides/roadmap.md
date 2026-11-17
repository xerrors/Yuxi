# 开发路线图

这里记录尚未完成的方向，不代表已经实现或一定会在某个版本发布。需求和优先级会根据 Issue、讨论和维护资源调整。欢迎在 [GitHub Issues](https://github.com/xerrors/Yuxi/issues) 提交建议。

## 计划中的不兼容方向

后续版本仍需评估：

- 进一步解除 API/worker 对共享文件系统的依赖，支持更清晰的分布式部署边界。
- 为 PDF 分块补充可验证的来源页码。

涉及部署和数据的变化会在确定方案后说明兼容范围。Milvus 3、后台作业职责收敛与权限调整的当前行为见[0.8.0 更新说明](./releases/0.8.0.md)。

## 知识库

- 增加按关键词排序的 `query_keywords` 工具。
- 丰富检索结果中的 metadata 和标签。
- 为个人工作区增加检索能力，但不做向量化。

## 其他

- 继续完善基于 DeepAgents 文件后端的 Memory 方案，并明确它与现有用户级 Memory 的边界。

协作会话、成员干预和会话间通信的当前用法见[会话协作](../agents/session-cooperation.md)。已发布的功能和修复见[版本变更记录](./changelog.md)。
