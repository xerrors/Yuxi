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

复用 `test/unit/plugins/test_milvus_kb.py` 的孤儿分片过滤测试，覆盖 0/1/4 分片数、来源文件名和原分片索引保留。运行命令：`uv run --frozen --group test pytest test/unit/plugins/test_milvus_kb.py -q`。

测试使用现有 fake repository，不证明真实 PostgreSQL SQL、Milvus 向量检索或 HTTP 装配。独立 schema、连接池和公共 fixture 覆盖带来的维护成本高于本次字段透传所需，因此不保留专用数据库测试脚本；真实数据库查询证据与完整链路的未验证范围在 PR 中记录。
