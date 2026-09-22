# 检索结果携带文件总分片数

状态：implemented
类型：feature
Owner：backend/package/yuxi/repositories/knowledge_file_repository.py

## 问题

query_kb 返回分片索引但没有文件总分片数，模型无法识别单片段文档。

## 决策

现有 PG 批量来源查询一并读取 chunk_count，MilvusKB._hydrate_chunk_sources 写入命中 metadata。知识库技能说明 1 表示单分片，0 或缺失表示未知。知识库过滤、孤儿向量过滤和集合结构保持现状。

## 替代方案

逐个打开文档会增加工具调用；额外逐文件查询会增加数据库往返。扩充已有批量查询保持一次读取。

## 后果

检索结果增加文件级 metadata.chunk_count，原 chunk_index 保留。外部知识库允许没有该字段。计数来自 PG 行，不根据当前检索命中数量推断。

## 验证

- `uv run --frozen --group test pytest test/unit/plugins/test_milvus_kb.py -q`：32 passed，覆盖 repository 返回数据的 hydration；恢复上游实现时新增 3 个场景因缺少字段失败。
- 在设置独立测试 `POSTGRES_URL` 后运行 `uv run --frozen --group test pytest test/integration/services/test_knowledge_chunk_sources.py -q`：真实 PostgreSQL 16 上 4 passed。repository 执行真实 SQL，覆盖 NULL/0/1/4 计数、外库文件排除、PG 中不存在的文件排除和原索引保留；测试使用唯一 schema 并自动清理。
- 完整 Milvus 向量检索与 HTTP E2E 未运行；真实 PostgreSQL 测试调用 hydration，但不证明向量数据库或完整 API 装配。

本地完整 unit 命令 `uv run --frozen --group test pytest test/unit -m "not slow" -q`：2275 passed。
