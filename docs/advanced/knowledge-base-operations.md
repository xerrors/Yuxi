# 知识库管理与 API 参考

本文面向管理员和集成开发者，集中说明知识库共享权限、文档 API 工作流、知识导图、示例问题与知识图谱运维。第一次通过 Web 创建并使用知识库，请先阅读[知识库教程](../intro/knowledge-base.md)；文件状态、存储 Owner、Tasker 和 Agent 工具链路见[知识库机制详解](../mechanisms/knowledge-base.md)。

## 共享权限

知识库使用 version 2 `share_config`，分别配置 `read_scope` 与可选 `manage_scope`。scope 的 `access_level` 只能是 `global`、`department` 或 `user`；部门范围必须列出部门 ID，指定用户范围必须列出 uid。`manage_scope` 不能超出 `read_scope`。

有效权限由资源归属、scope 命中和角色上限共同决定：

- `superadmin` 无条件获得 manage。
- 创建者获得 manage。
- 其他用户必须命中 `read_scope` 才能读取；同时命中合法的 `manage_scope` 才能管理。
- `admin` 的角色上限允许 manage，但不会自动命中任何部门或指定用户范围。
- `user` 的角色上限是 read，即使命中 `manage_scope` 也不能管理。

后端依赖和 repository/manager 可见性查询执行最终授权。前端显示、路由守卫、Agent 配置和模型 prompt 只能缩小可见范围，不能授予权限。

## 文档 API 工作流

程序化导入先调用 `POST /api/knowledge/files/upload?kb_id=<kb_id>`，取得 MinIO `file_path` 与 `content_hash`。后续只能在下面两条工作流中选择一条；不要先创建记录，再调用一体化入口重复创建。

一体化导入适合让 Durable Task worker 完成“创建记录 → 解析 → 可选索引”：

```http
POST /api/knowledge/databases/{kb_id}/documents
Content-Type: application/json

{
  "items": ["<file_path>"],
  "params": {
    "content_hashes": {"<file_path>": "<content_hash>"},
    "auto_index": true
  }
}
```

分步导入适合在每个阶段检查结果：

1. 调用 `POST /api/knowledge/databases/{kb_id}/documents/add`，请求体包含 `items` 与 `params`，从响应的 `items[].file_id` 读取新记录 ID。
2. 调用 `POST /api/knowledge/databases/{kb_id}/documents/parse`，请求体是待处理 `file_id` 数组；任务完成后回读文件状态，确认进入 `parsed`。
3. 调用 `POST /api/knowledge/databases/{kb_id}/documents/index`，请求体包含 `file_ids` 与可选 `params`；任务完成后回读文件状态，确认进入 `indexed`。

CLI 的 `yuxi kb upload` 封装了上传与导入链路。原文件上传会计算内容哈希，并在写入对象存储前查询当前知识库是否已有相同内容；Web 仅在 URL 模式下额外跳过同一批次的重复内容，普通文件模式没有这项批内保证。该检查发生在入口，数据库没有内容哈希唯一约束。`/documents/add` 与 `/documents` 接收并保存调用方提供的哈希，不会再次查重；并发请求或复用已有对象路径仍可能创建重复记录。文档入口会校验知识库 manage 权限，原文件上传还要求管理员身份。Durable Task 的 `success` 表示编排完成，每个文档仍需通过文件状态和对应存储产物确认结果。

## 知识导图与示例问题

Milvus 知识库可以根据文件元数据生成层次化知识导图。当前生成请求受 [`MINDMAP_GENERATION_FILE_LIMIT`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/knowledge/utils/mindmap_utils.py) 控制，结果保存到知识库 `mindmap` 字段；Agent 可通过 `get_mindmap` 读取。增量更新先比较已追踪文件与当前文件列表：纯删除直接修改现有树，出现新增文件时再调用模型整合分类。成功删除文件也会移除对应导图叶子。

示例问题同样基于文件列表生成并保存到 `sample_questions`，供检索测试选择。两项能力都只使用文件元数据，不证明已经读取或总结全文；内容问答仍需检索 chunk，并按需要打开原文窗口。

## 知识图谱运维

知识图谱只属于 Milvus 知识库。管理员先在详情页确认并锁定 LLM 抽取配置，再提交待处理 chunk 的构建任务；运行中可以查看进度、失败样例、标签与统计，并按明确范围重置或修复图向量索引。图实体、关系和 chunk 关联写入 Neo4j，抽取状态与 chunk 事实写入 PostgreSQL，实体/三元组向量索引写入 Milvus。

Neo4j 的 URI、用户名和密码通过部署环境配置，字段名以 `.env.template` 和 Compose 为准。开发环境通常从宿主机访问 `http://localhost:7474` 管理界面与 `bolt://localhost:7687` 端口，容器间连接使用 Compose 服务名；不要在文档、提交或排障日志中记录实际密码。

图谱任务由 Durable Task worker 从持久 payload 重建，重复投递受数据库 claim/lease 保护。图谱 Handler 当前使用 `fail` 恢复策略：worker 失联后任务明确失败，不在未知 Neo4j/Milvus 副作用上自动重放。失败后先读取图谱状态、chunk 处理状态和外部存储，再决定重试、修复向量索引或重置，不能只根据任务列表覆盖现场。

## 配置与契约入口

- API schema、权限依赖和任务入口：[knowledge_router.py](https://github.com/xerrors/Yuxi/blob/main/backend/server/routers/knowledge_router.py)
- 共享权限解析：[resource_permission.py](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/permissions/resource_permission.py)
- 知识库配置与 executor 调度：[knowledge/manager.py](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/knowledge/manager.py)
- 图谱状态与构建：[milvus_graph_service.py](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/knowledge/graphs/milvus_graph_service.py)
- 部署变量：[.env.template](https://github.com/xerrors/Yuxi/blob/main/.env.template)、[docker-compose.yml](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)、[docker-compose.prod.yml](https://github.com/xerrors/Yuxi/blob/main/docker-compose.prod.yml)

API 调用前以当前 `/docs` OpenAPI 页面核对请求 schema。修改本文涉及的行为时，至少运行知识库权限 unit 与真实 HTTP integration；涉及 MinIO、Milvus 或 Neo4j 时，从对应存储回读结果。
