# 收敛模块审计中的结果、故障与持久化缺陷

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/repositories/bases.py

## 问题

模型缓存与 MCP 建连失败被转换为空结果，导致基础设施故障表现为用户配置错误或能力缺失。知识库并发创建没有数据库名称约束，后台失败后的统计快照没有随文件终态收敛。内置 Skill 的列表与管理判断采用不同角色规则，图谱删除可能在事件循环内初始化网络客户端，未配置网页搜索时没有可观察的降级记录。用户独立 save 新建的会话不能跟踪传入的 detached 实体，可能静默忽略其修改。

## 决策

### 实现方案

知识库 ORM 与 Schema-init 用部分唯一索引约束未删除记录的 lower(name)，保留名称不区分大小写、已删除名称可复用的规则。repository 把该索引的创建或更新冲突转换为既有名称冲突异常，HTTP 返回 409；其他完整性错误继续传播。名称唯一索引由当前 ORM 在空库初始化时直接建立；历史升级方案由[首个 Schema 基线决策](2026-10-19-schema-baseline-one.md)取代，部署步骤由[部署参考](../../../advanced/deployment.md#_3-数据库与文件目录)拥有。

知识文件失败 hook 在 owning Job 终态事务内按 KB → File 顺序加锁，更新仍由该 Job 拥有的文件错误态，再用既有 PostgreSQL 聚合写回受影响知识库统计。Job、文件与持久统计共同提交或回滚；知识库运行配置缓存不包含统计，详情与文件列表的统计读取按[GUI 缺陷修复决策](2026-10-12-gui-report-remaining-defects.md)从 PostgreSQL 查询当前聚合。

Skill permission resolver 拥有内置权限上限，有效 superadmin 可以管理内置 Skill，admin 只读取已启用且可读的内置项。列表、响应和服务消费同一解析结果，已删除身份不能通过管理判断。规则与[Skill 管理](../../../agents/skills-management.md#权限和选择)的权限表一致。

模型缓存读取失败、快照缺失或非法时抛出明确的基础设施错误；有效空快照仍表示零个模型。MCP 建连与工具发现失败经配置工具装配和 Skill 中间件传播为运行失败，正常零工具结果保持合法。日志记录组件、错误类型及不可用状态；网页搜索未配置凭据或注册失败时也保留结构化降级记录。图谱文件删除通过已有异步初始化入口取得向量客户端，将同步构造移到线程。

用户 repository 的写操作要求调用方注入会话，只 flush、不拥有 commit；独立会话继续支持读取。现有生产写调用都已注入事务，调用契约在 repository 会话边界执行。

## 替代方案

给模型缓存增加同步 PostgreSQL 回源会引入新的数据库读取和缓存更新路径；明确暴露基础设施失败直接修复错误归因。内置 Skill 向 admin 放开管理会改变现有权限约定；统一到 superadmin 维护规则闭合列表与操作语义。自动重命名存量知识库改变用户数据，采用原子拒绝知识域升级。为独立 save 自动合并 detached 实体会创建平行事务 Owner，采用明确要求调用方事务。

## 后果

名称冲突可在并发创建和重命名中观察为 HTTP 409。升级需要备份数据库并停止旧版 API/worker；遇到存量重名由维护者修正，初始化器不删除或重写业务数据。MCP 与模型缓存故障明确中断受影响操作。用户无参写调用明确拒绝，当前请求事务的提交与回滚行为保持原值。清理提交保持独立，本阶段以一个修复提交交付。

审计 D1 的“引用恒为未知来源”不成立：向量、关键词、混合及图谱结果返回前均由 PostgreSQL 文件名补齐来源；真实 PostgreSQL 参与的检索装配到引用标题测试保留该实现。D9 的“自建会话不 commit”也不成立，PostgresManager 上下文会自动提交；修复的是独立 save 的实体与会话不匹配，并明确所有用户写入的事务 Owner。

## 验证

测试使用独立 PostgreSQL 与 Redis 容器、每用例隔离 Schema、当前代码挂载和已有 API 镜像依赖；无业务数据挂载或对外端口。HTTP 用例经真实 TCP 执行路由并回读数据库。worker 验证直接调用 shipping process_background_job 入口，执行真实 Handler、租约和领域收尾后回读 Job、文件和统计；不声称验证独立 ARQ worker 进程或完整 Agent journey。

- `docker compose -f docs/vibe/audit-defects.compose.yml exec -T api uv run --group test pytest test/unit -m 'not slow' -q`：2676 passed、60 skipped；其中三个依赖故障用例调用实际日志初始化器并回读文件，验证故障字段进入 shipping 输出。
- `docker compose -f docs/vibe/audit-defects.compose.yml exec -T api uv run --group test pytest test/integration/api/test_module_audit_conflicts.py -q`：3 passed。
- `docker compose -f docs/vibe/audit-defects.compose.yml exec -T api uv run --group test pytest test/integration/services/test_knowledge_audit_persistence.py test/integration/services/test_optional_dependency_failures.py test/integration/services/test_schema_migration_version.py test/integration/services/test_knowledge_stats_refresh.py -q`：35 passed。
- 原实现使 Redis/缓存故障、缺失搜索凭据、MCP 建连及两层装配吞异常、事件循环内客户端初始化、并发重名、重名迁移、失败统计、内置角色漂移、无参用户写入的回归断言分别失败；修复后相应断言通过。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，后者 64 项。
- 修改的生产代码与其余测试 Ruff 检查通过；既有 Schema 集成测试的 9 项 E501 与基线逐项一致，未新增诊断。`git diff --check`：通过。
- `pnpm --dir docs run build`：通过，文档相对链接随构建校验。

本地 Compose 装配被忽略，不参与提交。实时搜索供应商、实时模型、Milvus/Neo4j 网络和独立 API/ARQ worker 的完整 E2E 未执行；外部投影删除以线程初始化和清理调用 unit 验证，未声称验证真实外部存储结果。
