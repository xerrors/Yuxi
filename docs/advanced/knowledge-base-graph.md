# 知识图谱

本页说明 Milvus 知识库示例问题和图谱运维。它面向管理员和需要查看结果的用户。文档导入和 API 见[文档导入与查询 API](./knowledge-base-api.md)，状态和存储边界见[知识库机制](../mechanisms/knowledge-base.md)。

## 示例问题

知识库详情页可以根据文件列表生成 `sample_questions`，供检索测试选择。示例问题适合快速检查入口是否可用，但不等于经过人工审核的评估基准。需要可比较的检索分数时，请使用[知识库评估](../intro/evaluation.md)并检查参考 chunk 和答案。

## 构建知识图谱

知识图谱只支持 Milvus 知识库。开始前确认：

- 文档已经解析并完成向量索引；
- 已在图谱配置中选择 LLM 抽取模型；
- 当前账号有知识库管理权限；
- Neo4j 和 Milvus 服务可访问。

在知识库详情页配置抽取器并提交构建任务。任务会从已索引的 chunk 中抽取实体和关系，随后写入结构数据和向量索引。详情页可以查看抽取、结构写入、向量索引进度和失败 chunk。

三类存储分别拥有不同事实：

| 存储 | 负责的事实 |
| --- | --- |
| PostgreSQL | chunk、抽取状态、图谱处理状态和任务信息 |
| Neo4j | 实体、关系和 chunk 关联 |
| Milvus | 实体与三元组的语义向量索引 |

检索时，系统可以把图谱实体/关系结果与文本 chunk 结果融合。图谱查看权限只要求知识库读取权限；配置抽取器、开始构建、重置和修复索引要求管理权限。

## 失败和修复

图谱后台作业把可序列化 payload 保存在 PostgreSQL 的 `BackgroundJobRecord`（`background_jobs` 表），提交后交给 ARQ worker。`BackgroundJobRepository` 使用 attempt owner 与 lease 拒绝重复执行和迟到写入。worker 中断或租约过期时，作业收敛为 `failed`，保留错误供管理员检查外部副作用。任务失败或取消后：

1. 先保留任务错误和失败 chunk；
2. 检查 PostgreSQL 的 chunk 与处理状态；
3. 检查 Neo4j 的实体、关系和引用；
4. 检查 Milvus 的图向量索引；
5. 再决定重试、修复向量索引或重置图谱。

后台作业的 `success` 表示 worker 已完成编排，三类存储的一致性需分别回读。图谱状态接口把最近成功作业投影为 `build_job_status=completed`，失败或取消作业投影为 `failed`。同时检查抽取、结构和向量计数以及失败 chunk，不能只根据该概览判断所有 chunk 已成功。

## API 入口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/knowledge/knowledge-bases/{kb_id}/graph-build/status` | 查看图谱构建状态 |
| `POST` | `/api/knowledge/knowledge-bases/{kb_id}/graph-build/config` | 保存抽取配置 |
| `POST` | `/api/knowledge/knowledge-bases/{kb_id}/graph-build/index` | 提交图谱构建 |
| `POST` | `/api/knowledge/knowledge-bases/{kb_id}/graph-build/reset` | 重置图谱状态 |
| `POST` | `/api/knowledge/knowledge-bases/{kb_id}/graph-build/reconcile` | 修复图向量索引 |

接口字段和权限以部署实例的 Swagger 页面为准。只读用户可以调用读取接口，不能通过改 URL 绕过管理权限。

## 源码和测试

- [知识库路由](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/api/routers/knowledge/management.py)
- [图谱服务](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/graphs/service.py)
- [后台作业执行与故障收敛](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/workers/background_jobs.py)
- [知识库 unit tests](https://github.com/xerrors/Yuxi/tree/main/backend/test/unit/knowledge)
- [图谱和知识库 integration](https://github.com/xerrors/Yuxi/tree/main/backend/test/integration/api)
