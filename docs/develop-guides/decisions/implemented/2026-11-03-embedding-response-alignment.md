# Embedding 响应逐条对应后才写入向量库

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/models/embed.py

## 问题

`OtherEmbedding._extract_embeddings` 只检查响应里有没有 `data`，就把 `data[*].embedding` 拼成列表返回；`batch_encode` / `abatch_encode` 逐批 `extend` 时也不校验该批条数。

`MilvusKB._insert_chunks_to_stores`（`backend/yuxi/modules/knowledge/implementations/milvus.py`）按列组装 `entities`，向量列与 `chunk_id` / `content` 列靠位置一一对应。`pymilvus` 只校验列数等于 schema 字段数，不校验各列长度一致：实测 `[3, 3, 3, 2]` 这样的批次会被 `Prepare.prepare_data` 接受。

因此当 Embedding 端点返回的向量少于输入（批处理截断、单条超长被跳过、兼容实现的部分结果），不会抛错：缺向量的 chunk 与错位的向量会被写入 Milvus，而 PostgreSQL 的 chunk 事实已先写入（该函数注释写明「PG 为权威源，先落 chunk 事实」），属于无法自动发现的静默数据损坏。

`models/rerank.py` 的 `_batch_rerank` 已在 #1016 建立同类契约：条数必须逐条对应、`index` 必须是范围内唯一整数，否则整批失败。Embedding 路径缺少对应校验。

## 决策

### 实现方案

校验点在 `BaseEmbeddingModel`（`backend/yuxi/modules/models/embed.py`），覆盖供应商响应解析与批次累加两处：

- `_extract_embeddings(result, expected)` 是 HTTP 响应进入系统的唯一入口。`OtherEmbedding.encode` / `aencode` 在 `response.json()` 之后调用它，并传入 `_expected_count(message)`（字符串输入计 1 条，列表输入计其长度，与 `build_payload` 的输入形态一致）。条数不符即抛出固定 `ValueError`，不返回任何向量。
- 提供者在条目上声明 `index` 时，`_order_by_reported_index` 按索引归位；索引非整数、负数、超出请求条数、重复，或同一响应混用带与不带 `index` 的条目，均整批失败。
- 未声明 `index` 的响应保持原顺序，兼容既有供应商。
- `batch_encode` / `abatch_encode` 在 `data.extend(...)` 前调用 `_require_one_vector_per_input`，使每批独立满足同一约束，避免子集被拼进更大的列表。

失败边界的语义：任何一批不满足逐条对应，该批抛错，调用方不写入向量；`MilvusKB._insert_chunks_to_stores` 与 PostgreSQL 的写入顺序、事务边界与重试策略均不变。异常文本固定，不回显供应商字段。

不在写入端（`_insert_chunks_to_stores`）补长度校验：那里已丢失请求条数语义，且同一校验会散落到多个调用方。

以下四条规则共同构成契约：

1. `_extract_embeddings` 接收请求条数 `expected`，返回条数不符直接抛出带条数信息的 `ValueError`。
2. 提供者返回 `index` 时按 `index` 归位；缺失、重复、越界（含超出 `expected`）的索引整批失败，混用带与不带 `index` 的条目同样失败。
3. 不返回 `index` 的既有响应继续按响应顺序接受，保持兼容。
4. `batch_encode` / `abatch_encode` 在每批 `extend` 前用 `_require_one_vector_per_input` 校验该批条数，避免子集被拼进更大的列表。

`_expected_count` 让单条字符串输入按 1 条计，与 `build_payload` 的输入形态一致。

## 替代方案

- 缺项填零向量：把缺失伪装成真实向量，检索时仍会命中错误位置，不采用。
- 静默截断到较短长度：同样产生位置错配，且失败不可见，不采用。
- 让调用方（`_insert_chunks_to_stores`）逐批对比长度：校验点分散在多个调用方，且此时 HTTP 响应语义已丢失，不采用。
- 重试整批：重试属于既有 `_prepare_retry` 的职责，本变更只定义「部分结果不满足契约」，不改变重试策略。

本变更直接落为 `implemented` 而不经过 `proposed`：它把同一模块内 rerank 已生效的契约（#1016）**应用到相邻的 Embedding 路径**，没有新增抽象、依赖、配置或状态机，也不改变持久化顺序、权限或兼容承诺；唯一需要裁决的是「部分结果如何处理」，而同类契约已在上游合并并留下决策记录，因此不构成有待裁决的取舍。

## 后果

畸形响应整批抛出固定错误消息，不回显提供者原始字段，也不写入任何向量；既有重试与异常路径不变。返回条数与输入一致、但向量内容错误的情况不在本契约覆盖范围。`index` 归位只在提供者声明索引时生效，未声明时仍信任响应顺序。

## 验证

- `backend/test/unit/models/test_embed_alignment.py`：7 个用例，覆盖条数不足、重复索引、越界索引、乱序归位、无索引兼容、`encode` 边界拒绝部分批次、`abatch_encode` 逐批拒绝部分结果。
- 修复前实测：6 个失败、1 个通过（既有兼容行为）；修复后 7 个全部通过。
- `ruff check`、`ruff format --check`、`ruff check --select I` 在 `backend/` 下对两个改动文件通过。

未执行真实 Embedding 服务调用：本契约验证响应解析与批次累加，不验证模型服务质量。
