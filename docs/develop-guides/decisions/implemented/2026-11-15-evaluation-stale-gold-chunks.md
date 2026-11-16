# 决策：评估参考块跨代次对齐与失效提示

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/evaluation/service.py

## 问题

Milvus 入库时把 chunk_id 改写为 `{chunk_id[:80]}-g{generation}`，文件每重新入库一次代次加一。评估基准保存的 `gold_chunk_ids` 是建集时的 id，检索结果里的 id 是当前代次，召回指标按字符串求交集。文件用同样的分块参数重新入库后，内容相同的 chunk 也对不上，recall/f1 变成 0。分块参数变化或文件删除后，参考块确实已不在知识库，这类题同样按 0 分混进平均值，前端和日志都没有提示。

## 决策

检索指标比较 chunk_id 前，先去掉检索结果和参考块末尾的 `-g\d+` 代次后缀。同一文件、同一分块参数生成的 `{file_id}_chunk_{idx}` 稳定，因此重新入库前后、以及代次后缀引入前建的基准都能对齐。

评估作业开始时读取知识库当前可见代次的全部 chunk_id，同样去掉代次后缀，逐题核对参考块。题目里有任一参考块不在当前知识库时，这道题不计入检索平均值。逐题结果去掉检索指标，写入失效数量和提示。Run 的 `metrics` 写入失效参考块总数和提示，并记 warning 日志。前端在运行列表、运行详情和逐题检索指标处显示提示。参考块仍存在但没被检索到时照常计 0 分。

### 实现方案

| 模块 | 职责 |
|---|---|
| `evaluation/metrics.py` | `normalize_chunk_id` 去掉代次后缀，召回/F1 比较前统一归一 |
| `repositories/chunks.py` | `list_chunk_ids_by_kb_id` 只查询当前可见代次的 chunk_id |
| `evaluation/service.py` | `_run_evaluation_job` 核对参考块，失效题排除出检索平均值，写逐题与 Run 级提示 |
| `frontend/.../KnowledgeEvaluationWorkspace.vue`、`EvaluationBenchmarkDetailView.vue` | 展示 `metrics.warning` |

## 替代方案

1. 按 metadata 里的 `(file_id, chunk_index)` 匹配。基准只保存 chunk_id 字符串，检索结果的 metadata 字段在不同实现间不保证齐全；去后缀只依赖 chunk_id 本身。
2. 失效参考块仍按 0 分计入。分数会和"检索没命中"混在一起，用户无法区分基准过期与检索退化。
3. 发起评估时直接拒绝。部分题目仍然有效，整体拒绝会让基准在任何文件变化后都不可用。

## 后果

分块参数变化后参考块本身已经不同，只能提示用户重建基准，不能自动迁移。核对需要一次读取知识库全部 chunk_id；评估入口只对 Milvus 知识库开放，PG chunk 投影是这类知识库的当前事实。代次后缀格式与 `implementations/milvus.py` 的入库改写保持一致，后者变化时需要同步。

## 验证

- `backend/test/unit/knowledge/eval/test_metrics.py`：新旧代次后缀、无后缀基准对齐，不同 chunk 去后缀后仍不命中。
- `backend/test/unit/knowledge/eval/test_evaluation_stale_gold_chunks.py`：失效参考块的数量与提示文案、排除出平均值；参考块存在但未命中时仍计 0 分且无提示。
