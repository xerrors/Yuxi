# 语义切分要求每条句子都有对应向量

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/chunking/ragflow_like/utils/semantic_utils.py

## 问题

`semantic_chunking_with_auto_clusters` 在聚类后按位置配对句子、簇标签与 token 数：

```python
labels = AgglomerativeClustering(...).fit_predict(embeddings)
for sentence, label, token_count in zip(sentences, labels, sentence_token_counts):
```

`labels` 的长度等于 `embeddings` 的行数，而不是 `sentences` 的长度。`zip` 未加 `strict`，条数不等时按较短方截断，后面的句子被静默丢弃：既不进任何 chunk，也不产生日志或异常。

sklearn 不会因条数不匹配报错——实测 4 句配 2 个向量时返回 2 个标签，`zip` 得到 2 对，另外 2 句消失。

进入该函数的 `embed_fn` 来自 `semantic.py:56` 的 `select_embedding_model(...).encode`，是单次调用、不经 `batch_encode`；调用方注入自定义 `embed_fn`（单测、外部集成）时完全绕过模型层校验。

## 决策

### 实现方案

在聚类之前校验 `len(embeddings) == len(sentences)`，不符抛出固定 `ValueError`。同一函数的另一条按预算合并路径（第二个 `zip`）与聚类路径的 `zip` 均加 `strict=True`，使后续任何条数偏差都以明确异常暴露，而不是截断。

该校验位于切分工具内部，不依赖具体 embedding 实现：无论 `embed_fn` 来自模型层还是调用方注入，契约都在此成立。`#1143` / `#1145` 处理的是模型响应边界（条数、索引、维度），本变更处理的是切分算法自身的输入契约，两者互补。

不选择「不足时降级为按预算顺序合并」：那会把部分失败的聚类伪装成成功结果，且用户无法得知有句子未参与切分。

## 替代方案

- 只加 `strict=True` 的 zip：异常消息不说明是向量条数问题，且发生在聚类之后，失败位置离原因较远。
- 截断 `sentences` 到 `len(embeddings)`：与当前缺陷行为相同，只是显式化。
- 在模型层校验：覆盖不到调用方注入的 `embed_fn`。

## 后果

向量条数与句子数不符时切分整批失败，不产出任何 chunk；调用方得到明确异常而不是残缺的分块结果。条数一致但聚类质量、向量语义正确性不在本契约范围内。

## 验证

`backend/test/unit/knowledge/chunking/test_semantic_embedding_alignment.py`：

- 向量少于句子数时抛出 `ValueError`；
- 向量多于句子数时抛出 `ValueError`；
- 条数对齐时输出包含全部原文句子（负向对照，确认正常路径不受影响）。

与既有 `test_semantic_chunking.py`、`test_semantic_tables.py` 合并运行共 24 项通过。修复前新增用例 2 项失败、1 项通过。

`cd backend && pytest test/unit/knowledge/chunking/ -q`。真实 embedding 服务与聚类质量不由这些单元测试证明。
