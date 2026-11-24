# Milvus 活动代次过滤不再列举每个文件

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/implementations/milvus.py

## 问题

每次向量、BM25、混合检索和图检索的可见范围，都会把知识库里每个未删除文件的 `file_id` 放进 Milvus 过滤表达式。文件一多，表达式长度会超过 Milvus 限制，检索直接失败。旧代次又不能放行，否则会占满 top-k。

## 决策

可见范围仍是「文件当前 `active_generation`」。表达式改为：全体文件同一代次时只写 `generation == N`；否则以文件数最多的代次为多数，用 `file_id not in` 排除例外，少数代次才列出自己的文件。空知识库仍是 `file_id in []`。排序和召回集合不变。

### 实现方案

`_build_active_generation_expr` 继续读取 `KnowledgeFileRepository.list_active_generations`。文件数相同时取较小代次，文件 ID 排序后写入 JSON 数组，结果不随查询顺序变化。向量、关键词、混合和图检索都复用这条表达式，不改各通道的打分。

不给现有集合新增 `active` 字段。集合是否可检索只核对描述、向量维度和文本检索 schema；旧集合没有该字段，查询时写 `active == true` 会失败，除非先做全库迁移。

## 替代方案

- 继续列出每个文件：大知识库的过滤式会超长。
- 新增 `active` 字段并在切换代次时翻转：现有集合没有该列，未迁移时检索失败。
- 只按 `generation in [...]` 过滤：同一文件的旧代次仍会进入 top-k。

## 后果

常见的单代次知识库，以及只有少数文件刚完成重建的知识库，过滤式不再随文件数线性增长。每个文件各有独立代次时，例外列表仍然很长。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 单代次表达式不包含每个 file_id | 重新拼接全部 file_id | _build_active_generation_expr | test_single_generation_does_not_list_every_file | 表达式出现 file-4 | Passed |
| 少数代次才被点名，旧代次不进入多数条件 | 旧代次仍匹配 | _build_active_generation_expr | test_minority_generation_is_the_only_explicit_file_list | 去掉 not in | Passed |
| 空知识库不命中任何文件 | 空表达式变成全库 | _build_active_generation_expr | test_empty_knowledge_base_matches_no_file | 返回空字符串 | Passed |
