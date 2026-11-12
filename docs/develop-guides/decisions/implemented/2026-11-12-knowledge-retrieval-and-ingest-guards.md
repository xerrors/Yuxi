# 决策：检索入口收口与摄取失败恢复

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/services/retrieval.py

## 问题

知识库 HTTP 查询和评估运行把调用方参数以最高优先级传给检索。超范围的 `final_top_k`、未知键和任意 `file_ids` 可以扩大单次召回。评估数据集下载必须继续按数据集所属知识库的读取权限拒绝无关管理员。

同一知识库里，相同内容、大小和解析器的再次上传会再插入一份原件。处于 `uploaded` 或 `error_parsing` 的文件被入库请求直接拒绝，入库过程中发现没有解析正文时会退回 `uploaded`，这次入库请求就此结束。删除与索引重叠时，已经写入的代次 chunk 要等投影清理，期间不能依赖一次新的清扫任务。

## 决策

检索覆盖只在接受外部参数时按该知识库类型的查询参数声明校验一次：未知键拒绝，数字必须落在声明的最小/最大范围内，`file_ids` 最多 10000 个且必须属于目标知识库。`manager.aquery` 的签名不变。Milvus 把已接受的 `file_ids` 追加为活动代次条件上的 AND 过滤，空列表不命中任何文件。评估的 `answer_llm` 和 `judge_llm` 先从检索覆盖里分开，只校验其余检索键；执行评估时从同一份配置去掉这两个模型键再调用 `aquery`。评估数据集下载继续使用数据集所属知识库的读取校验。

导入在目录事务内按 `content_hash + file_size + ocr_engine` 复用同一知识库里未删除的文件，不再插入第二份。未解析或解析失败的重复文件在调用方要求处理时重新进入摄取队列。可解析状态与可入库状态只有一份。普通解析只领取可解析状态。入库前补解析在同一次条件更新里额外领取可入库、非目录且没有正文的文件，然后只调用一次执行器。执行器在领取和创建集合之前拒绝没有正文或已删除的文件，不改写状态。索引写入过程中如果文件被删除，用现有 `delete_file_chunks_only` 撤销本次代次后直接失败，不再把已删除文件写成索引失败。

分块预览在 `ChunkBrowser` 上提供复制当前分块正文的按钮，`FileDetailModal` 复用该组件。

### 实现方案

| 模块 | 职责 |
|---|---|
| `services/retrieval.py` | 按查询参数声明校验单次覆盖，并核对 `file_ids` 属于目标知识库 |
| `api/routers/knowledge/management.py` | `/query` 与 `/query-test` 只提交校验后的覆盖 |
| `evaluation/service.py` | 把模型键和检索覆盖分开，只校验检索覆盖 |
| `evaluation/evaluator.py` | 调用 `aquery` 前去掉模型键 |
| `file_status.py` | 可解析状态、可入库状态，以及入库前是否要先解析 |
| `implementations/milvus.py` | `file_ids` 只收窄过滤；领取前拒绝缺正文和已删除文件；写入中删除则撤销本次代次 |
| `services/imports.py`、`repositories/files.py` | 指纹复用与未解析重复文件重新入队 |
| `manager.py`、`base.py` | 需要解析的文件先解析；补解析只在同一次领取里放行没有正文的可入库文件，然后只入库一次 |
| `frontend/.../ChunkBrowser.vue` | 复制当前分块正文并提示结果 |

`MAX_FILTER_FILE_IDS` 放在文件仓库，和文件过滤查询在一起。内部活动代次白名单不按这个上限截断。

## 替代方案

1. 在 `manager.aquery` 内统一拒绝越界参数。评估器和基准生成直接依赖现有宽松签名，收紧后会把内部调用和外部调用绑成同一个校验面。
2. 静默把 `top_k` 夹到上限。调用方无法区分请求被拒绝还是被改小，超限请求仍会显示为成功。
3. 为删除与索引重叠新增独立垃圾回收。投影锁、`file_deleted` 和 `delete_file_chunks_only` 已经能删除文件的全部分代，缺的是写入方在锁内发现删除后的当次清理。
4. 给内容哈希加唯一约束并提升知识库 schema 版本。同一次导入事务里的查找已经能阻止第二份插入，当前没有需要迁移的存量约束。

## 后果

- `/query`、`/query-test` 和评估启动对越界或未知检索参数返回参数错误，不再把这些参数交给执行器。
- 合法的 `file_ids` 只会减少 Milvus 命中，不能换成别的知识库或别的代次。
- 相同指纹的再次上传返回已有文件；解析器不同仍保存新文件。未解析的重复文件会进入处理队列。
- 对未解析、解析失败或没有正文的文件发起入库会先解析，然后只入库一次。没有正文的可入库文件只在这次补解析的条件更新里被领取。执行器在领取前拒绝没有正文的文件，原状态保持不变。
- 删除发生在领取前时不会创建集合，也不会把文件改成索引中。删除发生在写入之后时，本次代次会被删掉。投影清理仍负责文件的其余代次。
- 分块预览可以复制当前分块正文。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 超限 top_k、未知键和库外 file_ids 不能进入检索 | 检索入口 | `services/retrieval.py` | `pytest test/unit/knowledge/test_retrieval_guard.py` | 恢复 `aquery(**meta)` 后超限参数被执行 | Passed：3 passed |
| 评估下载拒绝读取范围外的管理员 | 数据集下载依赖 | `api/routers/knowledge/evaluation.py` | `pytest test/unit/routers/test_knowledge_resource_permission.py::test_dataset_download_rejects_admin_outside_read_scope` | 去掉读取校验后返回 200 | Passed：该文件 8 passed，含此负向用例 |
| 相同指纹不插入第二份，不同解析器会插入 | 导入登记 | `services/imports.py` | `pytest test/integration/services/test_knowledge_object_lifecycle.py::test_same_fingerprint_reuses_file_and_other_parser_stores_again` | 去掉指纹查找后同内容产生两行 | Passed：真实 PostgreSQL 与 MinIO，1 passed |
| 未解析或没有正文的入库请求会先解析，执行器只调用一次 | `KnowledgeBaseManager.index_file` | `manager.py` | `pytest test/unit/knowledge/test_index_unparsed_retry.py` | 去掉补解析放行后，`parsed` 且没有正文因 `Cannot parse file with status 'parsed'` 失败 | Passed：5 passed |
| 删除发生在写入之后时删除本次代次 | Milvus 索引 | `implementations/milvus.py` | `pytest test/unit/plugins/test_milvus_kb.py::test_deleted_file_index_removes_chunks_written_before_finish` | 去掉失败路径清理后 `deleted` 为空 | Passed：该文件全部通过，含此用例 |
| 分块复制写入剪贴板 | ChunkBrowser | `ChunkBrowser.vue` | `frontend` `knowledgeChunkBrowser.test.js` | 空正文不写入剪贴板 | Passed：2 passed。浏览器里点击复制写入剪贴板并出现「分块内容已复制」，空分块按钮禁用 |
