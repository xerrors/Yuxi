# 决策：检索侧过滤已删除文件的孤儿向量

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/implementations/milvus.py

## 问题

删除知识库文件时，`delete_file_chunks_only` 先删除 Milvus 向量再删除 PostgreSQL chunks（由 commit `313d1d7a` 修复），但如果删除流程在两者之间崩溃或超时，Milvus 中仍会残留孤儿向量。检索链路 `aquery` 直接从 Milvus 取回 chunk，不校验 `file_id` 是否仍存在于 PostgreSQL，导致已删除文件的内容仍出现在检索结果中。

## 决策

在 `_hydrate_chunk_sources` 中利用已有的 `KnowledgeFileRepository().get_filenames_by_file_ids` 查询结果，过滤掉 `file_id` 不在返回字典中的 chunk，而非将其标记为"未知来源"。`aquery` 使用过滤后的列表继续后续流程。

该方案在检索侧实现防御性过滤（defense-in-depth），确保即使删除流程意外中断，已删除文件的内容也不会被返回。

## 替代方案

1. 定期对账任务（比对 PG file_id 与 Milvus 残留向量并批量清理）——增加后台任务复杂度，且无法实时阻止已删除内容被返回；留作后续改进。
2. 删除时使用软删除/墓碑标记——引入新的数据模型，改动范围大。
3. 仅依赖删除顺序和错误传播修复——不够健壮，进程崩溃仍可产生孤儿数据。

## 后果

- `file_id` 不在 PG 中的 chunk 不会出现在检索结果中（之前显示为"未知来源"）。
- 正常文件的行为不变（所有 file_id 均在 PG 中时过滤无效果）。
- 不主动清理 Milvus 中的孤儿向量，仅防止其被返回；孤儿数据通过正常删除流程或后续对账任务清理。
- 不影响 GraphRAG 检索路径（graph chunks 从 PG 读取，天然不含孤儿数据）。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 孤儿 chunk 被过滤，正常 chunk 保留 | `_hydrate_chunk_sources` 过滤逻辑 | `_hydrate_chunk_sources` | `pytest test/unit/plugins/test_milvus_kb.py::test_hydrate_chunk_sources_filters_orphaned_file_chunks` | 恢复旧行为（"未知来源"）后孤儿 chunk 仍返回 | Passed |
| 无孤儿时行为不变 | `_hydrate_chunk_sources` 过滤逻辑 | `_hydrate_chunk_sources` | `pytest test/unit/plugins/test_milvus_kb.py::test_hydrate_chunk_sources_returns_all_chunks_when_no_orphans` | 误过滤正常 chunk | Passed |
| Milvus 返回孤儿向量时 aquery 最终结果不含已删除内容 | `aquery` 完整检索链路 | `aquery` → `_hydrate_chunk_sources` | `pytest test/unit/plugins/test_milvus_kb.py::test_query_filters_orphaned_chunks_from_search_results` | 移除过滤后孤儿 chunk 出现在最终结果 | Passed |
| 既有 Milvus 测试不回归 | 全部 Milvus 行为 | `MilvusKB` | Docker Compose: `pytest test/unit/plugins/ test/unit/knowledge/` 241 passed | 修改 `_hydrate_chunk_sources` 返回类型签名后既有 26 项测试仍通过 | Passed |
