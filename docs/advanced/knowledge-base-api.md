# 文档导入与查询 API

本页说明如何通过 HTTP 或 CLI 把文档加入知识库，以及如何查询已经处理的内容。它面向管理员和集成开发者；知识库运行机制见[知识库机制](../mechanisms/knowledge-base.md)，导图和图谱见[知识导图与知识图谱](./knowledge-base-graph.md)。

## 权限

文档管理接口要求知识库管理权限；原始文件上传接口还要求管理员身份。读取和外部查询接口要求知识库读取权限。

知识库的 `share_config` 使用 version 2，分别保存 `read_scope` 和 `manage_scope`。范围的 `access_level` 可以是 `global`、`department` 或 `user`；管理范围必须包含在读取范围内。

| 用户 | 读取 | 管理 |
| --- | --- | --- |
| 创建者 | 有 | 有 |
| `superadmin` | 有 | 有 |
| `admin` | 命中读取范围时有 | 命中管理范围时有 |
| `user` | 命中读取范围时有 | 无 |

前端显示和 Agent 配置只会缩小可见范围，最终授权由后端依赖和 repository/manager 查询执行。

## 一体化导入

一体化入口适合一次提交“添加记录 → 解析 → 可选索引”。先把原文件上传到知识库暂存区，取得 `file_path` 和 `content_hash`，再调用：

```http
POST /api/knowledge/databases/{kb_id}/documents
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "items": ["<minio-file-url>"],
  "params": {
    "content_hashes": {"<minio-file-url>": "<content-hash>"},
    "auto_index": true
  }
}
```

接口返回任务已提交。完成后回读文件记录，确认状态为 `parsed` 或 `indexed`。

## 分步导入

需要在每个阶段检查结果时，按下面顺序调用：

1. `POST /api/knowledge/files/upload?kb_id=<kb-id>` 上传原文件，保存 `file_path`、`content_hash` 和 `size`。
2. `POST /api/knowledge/databases/{kb_id}/documents/add` 创建文件记录；这一步不解析、不索引。
3. `POST /api/knowledge/databases/{kb_id}/documents/parse`，请求体可以是文件 ID 数组，也可以是 `{"file_ids":[...],"params":{...}}`；回读状态确认 `parsed`。
4. `POST /api/knowledge/databases/{kb_id}/documents/index`，请求体包含 `file_ids` 和可选 `params`；回读状态确认 `indexed`。

按状态处理整批文件时，使用 `/documents/parse-pending` 和 `/documents/index-pending`。直接提交的文件 ID 数量有限制，大批量导入应使用按状态入口。

URL 导入需要管理员身份和目标知识库的管理权限。先调用 `POST /api/knowledge/files/fetch-url`，通过 URL 白名单校验并得到对象地址，再进入导入流程。白名单配置与抓取器的 SSRF 防护、重定向和大小限制由[文档处理与 OCR](./document-processing.md#从-url-导入网页)拥有。不要把 `content_type=url` 直接传给文档导入接口。

上传入口会检查内容哈希，但数据库没有内容哈希唯一约束。并发请求仍可能产生重复记录；`/documents/add` 和一体化入口会保存调用方提供的哈希，不会替调用方再次完成幂等去重。

Durable Task 的 `success` 只代表 worker 已完成编排，任务状态不拥有文件事实；最终结论要按[知识库机制](../mechanisms/knowledge-base.md#文档状态机)回读文件状态，并在需要时核对 MinIO、chunk 和向量索引。完整请求 Schema 和错误响应以部署实例的 Swagger 页面为准：`<base-url>/docs`。

## 外部查询接口

登录用户可以调用自己有权限的知识库：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/knowledge/databases/external` | 列出可见知识库 |
| `GET` | `/api/knowledge/databases/external/{kb_id}/files` | 列出或按文件名搜索文件 |
| `POST` | `/api/knowledge/databases/external/{kb_id}/retrieve` | 检索片段 |
| `GET` | `/api/knowledge/databases/external/{kb_id}/files/{file_id}/open` | 按行打开解析后的 Markdown |
| `POST` | `/api/knowledge/databases/external/{kb_id}/files/{file_id}/find` | 在文件内按关键词或正则查找 |

`files` 的查询参数只匹配文件名，不搜索正文。`open` 默认从第 0 行开始读取，单次最多 1800 行；`find` 返回匹配窗口。

Dify 和 Notion 只提供外部检索能力。它们不支持 Yuxi 的文档上传、解析、索引和全文打开；调用不支持的接口时，服务会明确返回错误。

## CLI

先按[命令行工具](../intro/cli.md)完成登录：

```bash
yuxi kb list
yuxi kb upload ./docs --kb-id <kb-id>
yuxi kb files --kb-id <kb-id> --query handbook
yuxi kb query --kb-id <kb-id> "如何申请年假？"
yuxi kb open --kb-id <kb-id> --file-id <file-id>
yuxi kb find --kb-id <kb-id> --file-id <file-id> --pattern "年假"
```

`kb upload` 只上传原文件并添加文件记录，不自动完成 OCR、解析或向量入库。完成后回到知识库页面继续处理并确认 `indexed`。

## 验证入口

- [知识库路由](https://github.com/xerrors/Yuxi/blob/main/backend/server/routers/knowledge_router.py)
- [外部查询路由](https://github.com/xerrors/Yuxi/blob/main/backend/server/routers/external_kb_router.py)
- [知识库权限解析](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/permissions/resource_permission.py)
- [知识库 HTTP integration](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_knowledge_router.py)
- [外部知识库 integration](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_knowledge_external_router.py)

修改导入、权限或查询接口时，运行真实 HTTP integration，并从 PostgreSQL、MinIO、Milvus 或 Neo4j 回读最终结果。
