# 数据库 Schema 迁移 Owner 与轻量版本契约

状态：archived
类型：architecture
Owner：backend/package/yuxi/storage_migration.py

`storage-migrator` 执行数据库 Schema 变更，`PostgresManager` 持久化版本并提供只读兼容校验；API 与 worker 只消费已经完成迁移的 Schema。

## 问题

API 与 worker 在启动时执行建表和 `ensure_*_schema`，多个运行进程可能并发执行相同 DDL。破坏性 SQL 会在每次启动时重复检查，数据库也没有可回读的 Yuxi Schema 版本事实。现有 `storage-migrator` 已经是 Compose 启动门禁，但需要独占 Schema 迁移。

## 决策

现有 `storage-migrator` 是 shipping 拓扑唯一的 Yuxi Schema 修改者，不新增迁移服务或框架。迁移器持有 PostgreSQL session advisory lock，并分别记录 `business` 与 `knowledge` 域。正式升级基线是 `v0.7.2` tag 的 business=2、knowledge=1；迁移器一次执行完整业务收敛 SQL，再记录当前 schema 版本，知识域补齐当前结构。未版本化安装保留新库初始化路径；LangGraph checkpoint setup 完成后才记录 business 版本。当前版本重复运行跳过对应域 DDL，未发布中间版本及其他未知版本在领域 DDL 前明确失败。

迁移器始终迁移并要求 business 与 knowledge 两个域。API 与 worker 不执行建表、Schema 收敛或 checkpoint setup，只校验两个域等于当前程序版本；版本表或任一域缺失、过旧或过新时拒绝启动。Compose 继续使用 `service_completed_successfully` 阻止迁移失败后的运行进程启动。

版本表只表达已完成的 Yuxi Schema revision，不承诺自动回滚。开发迭代不形成逐级升级路径；同一未发布版本的 DDL 统一收敛，从已发布 tag 验证缺失字段、数据保留与重复执行。现有内部 revision 编号不重用，也不通过改写版本表冒充兼容。开发槽位若需保留数据，须在停机备份后单独执行完整 DDL、核验结构和数据，再由迁移 Owner 记录目标版本；这种部署处置不进入 shipping 的发布兼容分支。

## 替代方案

- 引入 Alembic 和独立 `db-migrator`：当前没有复杂迁移分支需求，会新增依赖、配置和第二个部署服务；拒绝。
- 只把 DDL 移到迁移器但不记录版本：不能避免每次启动重复执行破坏性收敛，也不能让运行进程校验兼容性；拒绝。
- 保留 API 或 worker 的兜底建表：会重新形成多个 Schema Owner，并让错误部署静默修改数据库；拒绝。
- 要求所有迁移支持 downgrade：数据删除和约束收紧无法形成可信无损回滚；采用发布前备份、幂等升级和明确数据影响。

## 后果

- API 与 worker 启动不再竞争 DDL 锁；迁移错误集中在 `storage-migrator`，失败会阻止运行服务启动。
- 当前版本正常重启不再重复执行 Yuxi Schema 收敛 SQL。
- 裸进程启动前必须先运行迁移器；缺失或不兼容版本形成明确启动错误。
- 首次接入时，没有版本记录的已知 legacy/current 数据库执行现有幂等收敛后建立 baseline；现有 Workdir 中间 Schema 检测继续 fail-closed。
- 版本记录与历史文件迁移不是单一数据库事务。中断时版本不推进，Schema SQL和文件迁移依靠既有幂等边界重跑。

## 验证

- `backend/test/unit/services/test_storage_migration.py` 的入口级负向测试验证未知及未发布版本在 DDL 前拒绝、0.7.2 完整收敛成功后才记录版本，以及失败不提前发布版本。
- `backend/test/integration/services/test_schema_migration_version.py` 在真实 PostgreSQL 验证 advisory lock、0.7.2 缺失字段的完整升级、审计索引与旧游标清理、知识域升级、幂等重放和正确版本回读；用例在升级前移除当前 ORM 预建的新字段，避免把预建结果当作迁移证据。
- `docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/services/test_api_key_schema_migration.py -q`：1 passed，既有破坏性业务 Schema 升级保持幂等和数据约束。
- shipping Compose 迁移后必须回读两个域与当前代码版本一致，并确认既有 Task、定时任务、AgentRun 与用户数据保留；API/worker 只在精确版本匹配时进入 ready。
- 运行进程缺失 business 或 knowledge 任一域时拒绝启动；由 migration unit 与真实 PostgreSQL integration 覆盖。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，61 tests passed；真实 Schema oracle 已接入 `system-tests.yml`。
