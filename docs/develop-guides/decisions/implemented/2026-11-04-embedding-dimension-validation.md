# Embedding 向量维度必须与配置一致

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/models/embed.py

## 问题

`_extract_embeddings` 在 #1143 建立了「响应与输入逐条对应」的契约（条数、索引唯一性、拒绝 `null` 向量），但不校验每条向量的**长度**。`test_connection` 只比对 `embeddings[0]` 的维度，且只用于连接探针。

`MilvusKB._create_new_collection` 用 `embedding_info.dimension or 1024` 固定集合 schema 的向量维度，写入时不再比对。`pymilvus` 只校验列数，不校验每条向量的维度：实测 `Prepare.prepare_data` 接受 `[4, 2, 4]` 对 schema `dim=4` 的批次。

供应商切换模型版本、对超长输入返回截断向量、或聚合网关路由到维度不同的后端时，维度混杂的批次会写入集合。PostgreSQL 的 chunk 事实已先行写入，问题无法从业务侧自动发现，只能重建集合。

## 决策

### 实现方案

维度校验放在与条数、索引相同的响应边界，即 `BaseEmbeddingModel._extract_embeddings`：

- 新增 `dimension` 参数，`OtherEmbedding.encode` / `aencode` 传入 `self.dimension`（来自模型配置 `ModelInfo.dimension`）。
- 逐条校验 `len(item["embedding"]) == dimension`，不符抛出固定 `ValueError`，整批不返回任何向量。
- `dimension` 为 `None` 时跳过校验，保持未配置模型的既有行为。
- `_require_one_vector_per_input` 同时承担维度校验，`batch_encode` / `abatch_encode` 在每批累加前复用，覆盖「首批正常、后续批维度不符」的场景。

校验在解析阶段完成，`MilvusKB` 的写入顺序、事务边界与重试策略均不变。

规则总述：响应条数等于请求条数、`index` 为范围内唯一整数、每条向量非 `null` 且长度等于配置维度；任一不满足即整批失败。

## 替代方案

- 只在 `test_connection` 校验：探针与入库路径判定不一致，且只覆盖第一个向量。
- 在 `MilvusKB` 写入前校验：校验点分散到每个调用方，且此时已丢失「模型配置维度」的语义来源。
- 自动截断或补零到配置维度：掩盖供应商错误并产生无效向量，不采用。
- 依赖 Milvus 服务端拒绝：实测客户端已放行，服务端行为不受本项目控制。

## 后果

维度不符的响应整批抛出固定错误，不回显供应商原始字段，也不写入任何向量。已写入的历史数据不会被修复。维度正确但向量内容质量、归一化方式不在本契约范围内。

## 验证

`backend/test/unit/models/test_embed_dimension.py` 在 HTTP 边界替换供应商响应，覆盖：

- 同步 / 异步直接调用的短维度、过长维度、仅第二项维度错误；
- `_extract_embeddings` 直接拒绝维度不符、接受维度一致、未配置维度时跳过；
- 批处理首批成功后，后续批次维度不符仍整批失败；
- 与 `test_embed_alignment.py` 合并运行，两个文件共 49 项通过。

`cd backend && uv run --no-sync --group test pytest test/unit/models/test_embed_dimension.py test/unit/models/test_embed_alignment.py -q`。真实供应商调用与 Milvus 写入链路不由这些单元测试证明。
