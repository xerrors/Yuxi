# 知识库管理用例收敛到领域服务

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/knowledge/services/bases.py

## 问题

创建、删除、列表、权限筛选和目录操作经由执行器管理器进入既有服务或 repository。读写 PostgreSQL 元数据不需要初始化检索后端，却暴露在相同对象上，修改一个管理行为需要判断 manager 与 service 哪个拥有事实。

## 决策

### 实现方案

库元数据创建、更新、软删除、摘要、详情和查询参数保存由 `services/bases.py` 拥有；用户可见性由已有 `services/access.py` 拥有；文件列表、搜索、元数据和正文读取由已有 `services/documents.py` 拥有；目录创建、移动、改名由已有 `services/directories.py` 拥有。HTTP 负责认证依赖、请求适配和响应，不经 manager 读取管理事实。管理路由、工作区、图谱提交、知识工具与批处理消费同一组领域入口。

目录创建的统计收尾和文档类型检查在目录 service 内完成；删除、批量删除的类型检查只在 administration 用例入口执行，HTTP 不再提前重复检查。类型拒绝使用 `ValueError` 的特定子类，使 HTTP 能保留原能力拒绝的 400 状态与消息，同时继续区分文件缺失。数据库树锁、事务提交、tombstone/outbox 和异步外部清理不变，HTTP 成功不表示外部清理完成。操作失败或取消后仍尝试刷新统计，刷新失败不能覆盖原异常；成功操作后的统计失败继续向上传播。批量逐项提交并返回部分成功，失败响应不能用来推断删除未提交。

manager 保留运行配置缓存与回源、执行器装配、解析索引、检索，以及需要执行器的外部清理和正文读取。查询参数定义通过运行配置与类型类合并，不读取管理详情。文档全文查找不需要执行器，直接进入 documents。工作区下载保留入口的文档类型校验；内部 `documents.get_file_download` 继续只读取已定位的文件内容，不给 Markdown 重置等已有调用者新增类型查询前提。无消费者的 accessible-info 方法和只有一次使用的名称查询转发删除，不添加兼容层。

固定执行器类型注册由 `factory.py` 拥有，`runtime.py` 只创建 Manager 单例。管理 service 冷导入可查询完整类型能力，不依赖其他消费者先导入 runtime；注册类型不创建连接或加载文档 parser。

documents 以一个 find_in_document 用例完成能力检查、正文读取和结果组装，纯窗口计算仍由 build_find_file_windows 提供，不保留迁移后同模块的全量参数转发。分块数量和近似 token 汇总由已有 dispatcher 提供，Milvus 不直接引用 ragflow_like 内部计数器；统计算法与持久化时机保持不变。

RAGFlow 移植的 general、book、laws 只传递 Markdown 文本片段，删除空布局元组与无输入来源的位置附加、布局标题分支。原文位置仍由 SourceText 与来源定位器维护；用户正文中的 PDF 标签清理、编号标题和 token 上限保留。书籍层级合并的深度按实际正文与编号层计数，不把已经删除的空布局层算入预算，分块内容与来源范围保持等价。

## 替代方案

保留 manager 兼容转发会保留两种入口和测试替身分歧；把全部管理操作移入一个新 facade 仍混合权限、目录与库元数据，均不采用。把类型注册继续留在 runtime 会让独立服务导入取决于先前的装配顺序，因此一起迁移其明确的 Owner，而不增加懒初始化标志。

统计刷新包装保留既有异常优先级，展开到每个用例会复制规则。部分局部导入用于避免 base、documents 与 repository 的循环；不为排版另设错误模块或启动机制。删除、管理与分块取舍集中在本记录，避免按迁移步骤重复维护决定。

## 后果

管理行为的入口由业务对象和生命周期确定，manager 不再兼任管理 API。库元数据的事务和目录树事务仍分别由原 repository/service 拥有。知识库禁用时原路由、能力发现和 worker 装配约束保持不变。

本决定不重写上传暂存、图谱、评估或后台作业状态机；HTTP 中仍有上传协议和错误映射代码。完整知识管理 router 的所有历史编排清理不包含在本次验收中，不把 manager 边界收敛表述为整个知识域所有入口均已简化完毕。

## 验证

以下命令在 backend 目录执行；集成测试使用一次性 PostgreSQL/Redis 拓扑和每例独立 schema，身份注入保留正式权限依赖，模型目录使用受控 fixture。

```bash
.venv/bin/pytest test/integration/services/test_knowledge_management_boundaries.py test/integration/services/test_knowledge_projection_integrity.py test/integration/services/test_knowledge_stats_refresh.py test/integration/services/test_chunk_config_boundary.py test/integration/api/test_error_information_boundary.py -q --tb=short
.venv/bin/pytest test/unit/knowledge test/unit/plugins test/unit/services/test_knowledge_access.py test/unit/services/test_knowledge_tool_queries.py test/unit/routers/test_workspace_knowledge_tree.py test/unit/routers/test_http_exception_passthrough.py test/unit/config/test_knowledge_deployment.py -q --tb=short
.venv/bin/pytest test/unit/routers/test_knowledge_documents_router.py test/unit/routers/test_knowledge_resource_permission.py -q --tb=short
.venv/bin/pytest test/integration/services/test_knowledge_management_boundaries.py::test_workspace_download_keeps_read_only_type_boundary -q --tb=short
.venv/bin/pytest test/unit/services/test_background_jobs.py test/unit/services/test_knowledge_job_handlers.py test/unit/utils/test_mime_consumers.py test/unit/routers/test_workspace_knowledge_tree.py -q --tb=short
.venv/bin/ruff check yuxi
.venv/bin/ruff format yuxi --check
.venv/bin/ruff check --select I yuxi
```

- Passed：91 项 integration，包含真实 HTTP 工作区下载类型边界。新管理场景从 HTTP 创建/更新/删除库、改名/移动目录、筛选列表和搜索，再回读持久行、统计与 outbox；无权限、只读类型、名称冲突、目录循环、跨库父节点均明确拒绝。统计故障返回失败，创建事实已经提交；删除既有用例继续覆盖事务回滚和并发树锁。
- Passed：847 项相关 unit，1 项 OCR 模型未提供而跳过。包含独立子进程的服务冷导入、未导入 runtime/parser 断言，以及关闭 Knowledge 后的 API/worker 装配。额外 28 项路由权限测试、28 项清理后的边界测试和 93 项后台作业/下载消费者测试通过；原 mock 已迁到领域 Owner。
- Passed：合并全文查找后的 55 项直接相关 unit 通过；测试从固定正文计算实际窗口与匹配数，不再替换已删除的转发函数。
- Passed：全量 Ruff 检查与 `git diff --check`。隔离 Compose 的 api 容器执行 `uv run --no-sync --group test pytest test/unit -m "not slow" -ra -o cache_dir=/tmp/pytest-cache`，3,805 项及 7 个 subtest 通过；127 项因容器缺少仓库根或 Docker CLI 跳过。宿主仓库根执行 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`，212 项通过，补齐配置测试的环境缺口。
- Passed：`cd backend && uv run --no-sync --group test pytest test/unit/plugins/test_milvus_kb.py test/unit/knowledge/test_chunking_token_limit.py test/unit/knowledge/chunking -q`，113 项通过；固定期望覆盖空输入、空白、标点与中英文，索引测试回读写入统计。token_count 仍为原有近似计数，不代表供应商计费用量。
- Passed：`uv run --project backend --no-sync --group test pytest backend/test/integration/api/test_module_audit_conflicts.py -q --tb=short`，3 项通过；名称并发屏障直接替换 repository 预检，HTTP 结果与数据库赢家、改名回滚仍由独立回读验证。
- Not run：`test_module_failure_boundaries.py` 的共享部署权限场景未重跑；删除了对无消费者旧接口的重复内部断言，保留原 HTTP 列表过滤和详情 403 断言。隔离拓扑没有运行该文件要求的认证 API 与清理 worker。
- Passed：文本形态清理后 `test_ragflow_like_chunking.py`、`test_chunk_source_lines.py`、`test_chunking_token_limit.py`、`test_naive_merge_delimiter.py` 共 87 项 unit，另有 `test/integration/services/test_chunk_config_boundary.py` 的 16 项真实 PostgreSQL integration。对照删除前源码，以 20 份固定文本和 seed 20261117 的 120 份随机 Markdown，组合 11 种配置与 5 个入口完成 7,700 组差分；2,198,724 个分块的内容、顺序、逐字符来源、字符范围和行号范围完全一致。separator 同步消费文本列表，既有 expected output 未改变。
- Not run：真实 Milvus/MinIO/Neo4j 与独立 worker 的完整 E2E。本改动不改变外部执行器、outbox 消费及发布实现，当前证据不能替代完整拓扑验证。

旧能力不存在：manager 不再提供元数据创建更新删除、权限筛选、目录、文件列表搜索和本地文件读取转发；真实消费者迁到各服务，未保留兼容别名。

重新引入条件：出现必须与执行器实例生命周期共同维护的新状态；普通元数据和权限读取不构成理由。
