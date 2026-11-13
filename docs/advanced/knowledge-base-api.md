# 文档导入与查询 API

本页说明如何通过 HTTP 导入文档，以及如何通过 HTTP 或 CLI 查询已经处理的内容。它面向管理员和集成开发者；知识库运行机制见[知识库机制](../mechanisms/knowledge-base.md)，图谱运维见[知识图谱](./knowledge-base-graph.md)。

## 权限

外部 MCP 客户端可通过[知识库 MCP 接入](./knowledge-mcp.md)调用本页的五个只读查询工具。

文档管理接口要求知识库管理权限；原始文件上传接口还要求管理员身份。读取和外部查询接口要求知识库读取权限。

知识库的 `share_config` 使用 version 2，分别保存 `read_scope` 和 `manage_scope`。范围的 `access_level` 可以是 `global`、`department` 或 `user`；范围关系遵循[资源权限的同类型子集规则](../mechanisms/resource-permissions.md#共享资源)。

| 用户 | 读取 | 管理 |
| --- | --- | --- |
| 管理员所有者 | 有 | 有 |
| `superadmin` | 有 | 有 |
| `admin` | 命中读取范围时有 | 同时命中读取与管理范围时有 |
| `user` | 命中读取范围时有 | 无 |

前端显示和 Agent 配置只会缩小可见范围，最终授权由后端依赖和 repository/manager 查询执行。

## 知识库管理

管理资源使用 `/api/knowledge/knowledge-bases`。创建知识库的请求字段为 `name`、`description`、`kb_type` 与该类型需要的配置。更新请求要求 `name` 和 `description`，`llm_model_spec`、`additional_params`、`share_config` 可选；知识库身份使用 `kb_id`。

更新请求体至少包含：

```json
{
  "name": "<new-name>",
  "description": "<new-description>"
}
```

管理员列表与 `/accessible` 可见列表的响应集合字段为 `knowledge_bases`。创建和详情返回知识库对象；更新返回 `message` 与 `knowledge_base`。文档、图谱构建及检索接口位于该资源下，评估资源位于 `/api/evaluation/knowledge-bases/{kb_id}`。完整字段和路由以部署实例的 OpenAPI 为准。

知识库查询、查询参数、示例问题、文档处理提交，以及文件夹创建、重命名、移动和 URL 抓取的未知异常分支使用稳定错误说明。批量操作保留成功数量、失败文件身份与可公开的领域错误；排查内部故障时由管理员查看服务端日志。

## 一体化导入

一体化入口适合一次提交“添加记录 → 解析 → 可选索引”。先把原文件上传到知识库暂存区，取得 `file_path` 和 `content_hash`，再调用：

```http
POST /api/knowledge/knowledge-bases/{kb_id}/documents
Authorization: Bearer <admin-jwt-or-full-api-key>
Content-Type: application/json

{
  "items": ["<minio-file-url>"],
  "params": {
    "content_hashes": {"<minio-file-url>": "<content-hash>"},
    "auto_index": true
  }
}
```

接口返回 `status=queued` 与 `job_id`，表示后台作业已经登记。管理员通过 `GET /api/background-jobs/{job_id}` 观察作业进度，完成后回读文件记录，确认状态为 `parsed` 或 `indexed`；`auto_index=true` 时还需验证目标文件可检索。

## 分步导入

需要在每个阶段检查结果时，按下面顺序调用：

1. `POST /api/knowledge/files/upload?kb_id=<kb-id>` 上传原文件，保存 `file_path`、`content_hash` 和 `size`。
2. `POST /api/knowledge/knowledge-bases/{kb_id}/documents/add` 创建文件记录，请求体为 `{"items":["<file_path>"],"params":{"content_hashes":{"<file_path>":"<content_hash>"}}}`，其中 `file_path` 与 `content_hash` 来自上一步；这一步不解析、不索引。
3. `POST /api/knowledge/knowledge-bases/{kb_id}/documents/parse`，请求体可以是文件 ID 数组，也可以是 `{"file_ids":[...],"params":{...}}`；回读状态确认 `parsed`。
4. `POST /api/knowledge/knowledge-bases/{kb_id}/documents/index`，请求体包含 `file_ids` 和可选 `params`；回读状态确认 `indexed`。

按状态处理整批文件时，使用 `/documents/parse-pending` 和 `/documents/index-pending`。直接提交的文件 ID 数量有限制，大批量导入应使用按状态入口。

分块配置通过知识库 `additional_params` 或文档请求 `params` 提供。`chunk_preset_id` 接受 `/api/knowledge/chunk-presets` 返回的策略；未知值或非字符串返回 `400`，不会登记处理作业。缺省、`null` 和空字符串表示默认或继承上层策略；内部别名 `naive` 对应 `general`。有效配置按请求、文件、知识库的顺序覆盖；管理读取保留已持久化的非法配置原值；实际处理时明确失败。管理员通过知识库更新接口或文档参数更新提交有效值修复，文件更新中的空策略与 `null` 配置继续保留已有覆盖值。

URL 导入需要管理员身份和目标知识库的管理权限。先调用 `POST /api/knowledge/files/fetch-url`，通过 URL 白名单校验并得到对象地址，再进入导入流程。白名单配置与抓取器的 SSRF 防护、重定向和大小限制由[文档处理与 OCR](./document-processing.md#从-url-导入网页)拥有。不要把 `content_type=url` 直接传给文档导入接口。

上传入口会检查内容哈希，但数据库没有内容哈希唯一约束。并发请求仍可能产生重复记录；`/documents/add` 和一体化入口会保存调用方提供的哈希，不会替调用方再次完成幂等去重。

后台作业由 PostgreSQL 的 `BackgroundJobRecord`（`background_jobs` 表）保存登记、执行租约、进度和终态。作业的 `success` 表示 worker 已完成编排，文件状态与内容仍由知识库保存；最终结论按[知识库机制](../mechanisms/knowledge-base.md#文档状态机)回读文件状态，并在需要时核对 MinIO、chunk 和向量索引。完整请求 Schema 和错误响应以部署实例的 Swagger 页面为准：`<base-url>/docs`。

## 飞书只读连接配置

管理员在新建知识库时选择“飞书”，填写 `feishu_user_token` 与 `feishu_space_id`。空间 ID 是飞书知识库设置页面 URL 中的数字标识。用户 token 需要具备 `wiki:wiki:readonly`、`docx:document:readonly` 权限，且其代表的用户可阅读目标知识库和文档。获取测试 token 可使用飞书开放平台 API 调试台；生产使用者需自行管理凭证有效期。知识库链接和 App Secret 均不能代替搜索所需的用户 token。[飞书 Wiki 搜索](https://open.feishu.cn/document/server-docs/docs/wiki-v2/search_wiki)、[新版文档正文接口](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)

创建请求的 `kb_type` 为 `feishu`，`additional_params` 只需上述两个连接参数，不需要嵌入模型。共享权限决定哪些 Yuxi 用户可以使用此连接。配置响应省略 token；更新时不提交 token 则保留原值，过期后在知识库“配置”中填入新的 token。

搜索关键词长度为 1–50 个字符，查询配置 `final_top_k` 限制返回 1–50 篇文档。结果 `content` 是文档标题，`metadata.result_type` 为 `document`，不含伪造的向量分数。结果 `file_id` 是 Wiki 节点标识，可交给 `open_kb_document` 分页读取实时正文；读取按行返回纯文本，不包含图片和附件。用户可通过 Web 检索结果的文档标题打开正文。

连接器使用飞书中国版固定 API 地址；跨空间、快捷方式和非 Docx 类型读取明确拒绝。搜索候选出现后资源被删除、撤权或 token 失效时，阅读会报错，已有搜索结果不构成访问授权。在线连接的能力与边界见[知识库机制](../mechanisms/knowledge-base.md#能力边界)。

## 外部查询接口

普通登录用户 JWT、`full` Key 或 `knowledge` 级 API Key 通过 `/api/v1/knowledge/tools` 查询绑定用户有读取权限的知识库。Agent 工具、CLI 与这些 HTTP 入口共用 `yuxi.modules.knowledge.services.tools`；API 根据 JWT 或 Key 绑定的用户重新解析知识库读取权限，调用方不能指定别人的用户身份。管理、上传等接口使用 `/api/knowledge/*`。

| 方法 | 路径 | 请求体或结果 |
| --- | --- | --- |
| `GET` | `/api/v1/knowledge/tools/list_kbs` | 返回可见知识库数组 |
| `POST` | `/api/v1/knowledge/tools/query_kb` | `{"kb_id":"ID","query_text":"关键词","file_name":null}` |
| `POST` | `/api/v1/knowledge/tools/open_kb_document` | `{"kb_id":"ID","file_id":"ID","line":1}` |
| `POST` | `/api/v1/knowledge/tools/find_kb_document` | `{"kb_id":"ID","file_id":"ID","patterns":["词"]}` |
| `POST` | `/api/v1/knowledge/tools/search_file` | `{"kb_id":"ID","query":"文件名","offset":0,"limit":300}` |

`search_file` 至少需要 `kb_id`、`kb_name` 或 `query`，查询词只匹配文件名。指定知识库时可省略查询词以列出文件；同时传 ID 与名称时需同时匹配。`open_kb_document` 默认读取 1800 行，`window_size` 最大 2000；`find_kb_document` 返回匹配窗口。不可见资源返回 `404`；业务条件不满足时返回 `400`，请求体字段缺失或数值越界时返回 `422`。原件下载由知识库管理文件接口提供，沿用资源权限校验。

Dify 和 Notion 只提供外部检索能力。它们不支持 Yuxi 的文档上传、解析、索引和全文打开；调用不支持的接口时，服务会明确返回错误。

## Milvus 文本检索参数

`query_kb` 接收 `kb_id`、`query_text` 和可选的 `file_name`，使用知识库保存的默认检索配置。单次公共检索不接受 `options` 覆盖。具有管理权限的调用方可通过 `PUT /api/knowledge/knowledge-bases/{kb_id}/query-params` 保存默认值。检索测试页面默认使用已保存配置，与 Agent 共用 `retrieve`；管理侧 `query-test` 省略 `meta` 或传入 `{}` 时也使用保存配置。显式传入非空 `meta` 表示单次测试覆盖，须通过参数声明和文件所属库校验，不修改保存配置。

管理侧 `/query-test` 返回标准片段列表，`/query` 将同一列表包在 `result` 中。片段的 `kb_id`、`file_id`、`id`、`content` 与 MCP 对应结果一致；分数、高亮和原文行号保存在 `metadata` 中。来源侧栏可以展开文件的检索片段，具备全文读取能力的片段可打开所属库的原文并定位行号；缺少行号时从文档开头读取，仅支持检索的外部库保留片段预览。

保存 Milvus 检索配置的请求体示例：

```json
{
  "search_mode": "hybrid",
  "required_terms": "Milvus",
  "excluded_terms": "legacy",
  "exact_phrase": "vector database",
  "highlight_results": true
}
```

`search_mode` 支持 `vector`、`keyword`、`hybrid`，默认 `vector`。三个文本条件默认为空字符串；必含与排除条件以空白分隔，逐项使用分词匹配，短语使用分词后的连续词序匹配。它们与 `file_name` 一起约束向量、BM25 和图谱候选。中文使用同一中文 analyzer，词项过滤不等同于字面子串搜索。文本条件要求字符串，高亮开关要求布尔值。

`highlight_results` 默认为 `true`。每个片段保留完整 `content`，外部结果在 `results[i].metadata.highlights` 返回文字段数组，段内为 `{"text":"命中词","matched":true}`。消费方按普通文字渲染；BM25 查询词和正向筛选词可以高亮，只有向量语义查询时显示完整正文。

外部结果的 `metadata.score_type` 标识 `cosine`、`bm25`、`hybrid` 或图谱融合的 `fusion`，`metadata.score` 保存该类型的原始分数；`metadata.rerank_score` 是可选重排分数。纯向量检索使用 `similarity_threshold`，BM25 与融合分数不按余弦阈值过滤。检索测试页面传入的 `meta` 使用相同参数，其原始片段响应在顶层返回这些字段；非法参数返回 `400`，执行失败返回 `500`，不会投影为空结果。

Milvus 片段的 `metadata` 包含 `chunk_index`、`start_line` 和 `end_line`。Agent、Public `query_kb` 与检索测试读取同一份入库行号，可结合 `file_id` 定位完整解析文本。行号从 1 开始且包含首尾行；历史未重新索引的片段为 `null`。行号来源与重写内容的范围规则见[知识库存储机制](../mechanisms/knowledge-base.md#各存储负责什么)。Dify、Notion 的定位字段由提供方返回。

## CLI

先按[命令行工具](../intro/cli.md)完成登录：

```bash
yuxi kb list
yuxi kb files --kb-id <kb-id> --query handbook
yuxi kb query --kb-id <kb-id> "如何申请年假？"
yuxi kb open --kb-id <kb-id> --file-id <file-id>
yuxi kb find --kb-id <kb-id> --file-id <file-id> --pattern "年假"
```

npm CLI 的 `kb` 命令使用 Public 工具接口读取知识库，不提供文件上传、解析或向量入库。管理端上传和处理流程见[知识库入门](../intro/knowledge-base.md)。

## 验证入口

- [知识库路由](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/api/routers/knowledge/management.py)
- [Public 工具路由](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/api/routers/public_v1/knowledge.py)
- [知识库权限解析](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/identity/permissions/resource_permission.py)
- [知识库 HTTP integration](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_knowledge_router.py)
- [Public 工具 integration](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_public_knowledge_tools.py)

修改导入、权限或查询接口时，运行真实 HTTP integration，并从 PostgreSQL、MinIO、Milvus 或 Neo4j 回读最终结果。
