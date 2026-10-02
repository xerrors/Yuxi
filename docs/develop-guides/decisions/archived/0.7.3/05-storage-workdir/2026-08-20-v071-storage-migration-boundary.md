# 存储迁移只兼容 v0.7.1 发布状态

状态：archived
类型：simplification
Owner：backend/package/yuxi/storage_migration.py

## 问题

当前一次性存储迁移同时识别 v0.7.1 发布状态、当前开发分支曾引入的 `ProjectWorkdir` 中间状态，以及迁移器自己的重试状态。分支中间状态不是已发布兼容承诺，却增加了旧表、旧挂载、旧对象字段和正常启动迁移入口；与此同时，只有数据库 Conversation、没有 uploads/outputs 文件的合法 v0.7.1 实例不会创建目标 Workdir，停机判断也可能因为没有旧文件而漏掉 schema 切换。

## 决策

- 一次性 `storage-migrator` 只接受 v0.7.1 的 Conversation 无 Project schema、当前 Project schema 和全新数据库；检测到 `workdir_id`、`Conversation.workdir_path`、`project_workdirs` 或 `file_storage_materializations` 时明确拒绝未发布中间 schema。
- v0.7.1 的每个顶层 Conversation 都获得由 owner uid + owner thread id 确定性派生的 implicit Project 与 canonical `projects/<uuid>`，即使旧 thread 目录为空也创建 Workdir；子线程继承 owner Project，仅在旧 uploads/outputs 存在时复制文件。迁移不向 Conversation 增加 `workdir_path`。
- 停机条件由 v0.7.1 schema、旧 shared Skill、旧 `base.toml` 或待导入 thread 文件共同触发；schema 切换不依赖旧文件是否存在。
- 附件记录按当前持久字段白名单重建，清除 v0.7.1 的宿主路径、Markdown 和派生 URL；只重写 v0.7.1 使用的 `/home/gem/user-data/...` 虚拟路径。
- Options 迁移只读取 v0.7.1 的 `config/base.toml`，且只由 `storage-migrator` 执行；API 和 worker 正常启动只同步当前配置定义。
- 删除 `legacy-projects` 挂载、MinIO 依赖、Project schema 删除 SQL 和对应分支中间态测试、文档描述。

## 替代方案

- **继续兼容分支中间状态。** 拒绝。该状态没有发布用户，且会让一次性迁移长期承担第二套 schema、文件根和对象协议。
- **只修复空 Conversation，不删除中间态。** 拒绝。功能缺口消失，但迁移边界和后续删除条件仍不清晰。
- **不支持迁移重试。** 拒绝。进程可能在文件导入、数据库切换或旧源清理之间退出；迁移器必须能从自己提交过的状态继续。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| 无文件或含标点 thread ID 的 v0.7.1 Conversation 也直接绑定 Project 并拥有真实目录 | Project 已绑定但目录不存在，验证或后续运行失败 | v0.7.1 Workdir migration、PostgreSQL cutover | `test_workdir_user_workspace.py` 真实 PostgreSQL/文件系统 integration | Conversation 无 uploads/outputs；thread ID 含 `.`/`:` | Not run（当前共享 API fixture 超时） |
| v0.7.1 schema 即使没有旧文件也要求停机并收敛非终态 Run | 旧 runtime 与 schema 切换并发 | storage migrator | `test_storage_migration.py` unit + 真实 PostgreSQL migration integration | 无旧文件但缺少停机证明 | Passed |
| 附件迁移只保留当前持久字段并重写 v0.7.1 虚拟路径 | 旧宿主路径、Markdown 或派生 URL 继续持久化 | v0.7.1 Workdir migration | unit + integration 回读 JSON | 注入 v0.7.1 全量旧字段 | Passed |
| API/worker 不再执行一次性 Options 迁移 | 正常启动继续携带旧版本兼容副作用 | API lifespan、worker startup、storage migrator | 启动 unit 与负向符号搜索 | 启动入口重新导入迁移模块 | Passed |
| 分支中间态兼容和挂载不存在 | 未发布表、路径或依赖继续扩大维护面 | v0.7.1 migrations、schema、Compose、文档 | `rg` 负向搜索 + Compose boundary unit | 恢复 `conversations.workdir_id` 持久列、Project 表或 `legacy-projects` 挂载 | Passed（28 tests） |

旧能力不存在：shipping schema、ORM 与部署不创建、读取、删除或挂载 `conversations.workdir_id`、`project_workdirs`、`file_storage_materializations`、`legacy-projects` 或 `/home/gem/projects/project-*`，也不迁移 `system_runtime_config` 数据库记录。迁移器保留对未发布 schema 名称的拒绝检查；canonical Workdir UUID 的局部变量不构成持久资源或兼容路径。

重新引入条件：只有某个已发布版本或受支持部署被证明持久化了这些状态，并提供真实 fixture、升级承诺和恢复验证时，才重新引入对应兼容路径。

补充验证：迁移相关 unit 共 89 项通过；共享 Skill 的真实 PostgreSQL integration 1 项通过；backend 非 slow unit 共 1381 项通过、35 项跳过。负向案例覆盖标点 thread ID 与斜杠路径别名。

## 后果

- 运行过未发布中间提交的本地数据库会被明确拒绝，需要开发者重建或使用对应历史提交先行处理。
- v0.7.1 SQLite LangGraph checkpoint 继续不迁移；升级会把非终态 Run 收敛为可观察失败，无法恢复暂停中的执行。
- 文件移动和 schema 切换是不可逆升级，仍必须保留停机证明、目标回读和提交后清理顺序。
