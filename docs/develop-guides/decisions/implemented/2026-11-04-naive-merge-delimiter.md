# 分块合并使用配置的分隔符

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/chunking/ragflow_like/nlp.py

## 问题

`naive_merge` 接收 `delimiter` 参数，但常规合并路径从不使用它：`chunk_token_num <= 0` 时硬编码 `"\n"` 拼接，预算路径每段也以 `"\n" + sec` 追加。

后果有两层：

- `general` 是默认分块器，调用方配置了分隔符却不生效，切分粒度与预期不符；
- 分隔符本身被替换成换行，chunk 内容与原文不一致。对空格敏感的文本（代码、表格、英文段落）语义会被改变，向量索引也随之偏移。

同一份输入在 `separator` 分块器下行为正确，可作为对照：

| 分块器 | 输入 40 个空格分隔的词，`delimiter=" "` | 结果 |
| --- | --- | --- |
| `separator` | | 40 个 chunk，每个一个词 |
| `general`（修复前） | | 4 个 chunk，内部空格全变为 `\n` |

此外预算路径的输出带一个前导分隔符（`'\nalpha\nbeta'`），调用方 `.strip()` 掩盖了它，但说明该路径的拼接未经验证。`naive_merge` 此前没有任何单元测试。

## 决策

### 实现方案

在 `naive_merge` 入口按 `separator = delimiter or "\n"` 归一，两条路径共用：

- 无预算路径把 `separator` 传给 `join_source_text`，替换硬编码换行；
- 预算路径追加片段时使用 `separator`，且首片段不加分隔符，消除前导分隔符。

`delimiter` 为空时回退换行，与切段阶段 `_iter_sections` 的默认语义一致。字符合并仍经 `join_source_text`，源位置映射（`source_positions`）不变，入库行号定位不受影响。

不改 `separator` 分块器：它按分隔符切段后不再合并，行为已正确。

## 替代方案

- 只在 `general` 层替换：`naive_merge` 还是其余分块器的兜底，缺陷会留在更底层。
- 继续硬编码换行并忽略配置：配置项失去意义，且原文被改写。
- 由调用方在合并后 `replace("\n", delimiter)`：无法区分原文换行与拼接产生的换行。

## 后果

配置分隔符后 chunk 内容保留原文形态，切分粒度仍由 token 预算决定（`general` 的设计如此，本变更不改变预算逻辑）。未配置分隔符时行为与修复前一致，仅去掉前导分隔符。

## 验证

`backend/test/unit/knowledge/chunking/test_naive_merge_delimiter.py` 覆盖：

- `chunk_token_num <= 0` 时按分隔符拼接；
- 预算充足时按分隔符拼接且无前导分隔符；
- 默认换行语义保持；
- 分隔符非空白时，原文空格不被改写。

与 `test_semantic_chunking.py`、`test_chunk_source_lines.py` 等合并运行共 23 项通过。修复前新增用例 4 项全部失败。

`cd backend && pytest test/unit/knowledge/chunking/ test/unit/plugins/test_chunk_source_lines.py -q`。
