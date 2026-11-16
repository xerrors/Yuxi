# v0.7.3 Schema 离线迁移

状态：implemented  
类型：bug-fix  
Owner：backend/yuxi/migrations/legacy_v073.py

## 问题

v0.7.3 本地定制库使用 business `9`、knowledge `4`，而 v0.8.0 运行进程只接受 `3/3`。`app-init` 必须拒绝不兼容结构，不能通过修改版本行绕过约束；历史数据需要在停写窗口迁移到新的 Session、Turn、Input、Run、Message 和知识版本模型。

## 决策

### 实现方案

迁移入口默认只读审计，只有显式 `--apply` 才执行。入口验证所有 Run、请求、后台任务和知识版本已收敛，在事务内获得 advisory/schema 锁并锁定受管理旧表，然后将完整旧表移至 `<schema>_v073_archive`，按当前 ORM 约束转换数据，最后写入 `3/3`。失败时整笔事务回滚；已是 `3/3` 或重复执行不产生副作用。对象存储、向量库、Neo4j、checkpoint 和 `.env` 不由该入口修改。

历史输入保留独立 Input 与 Receipt；原始消息内容块、附件 ID、WPS 加密字段和旧表列都保留。旧工具审计正文迁入新 Message metadata。旧 SubAgentBackend 映射为 ChatbotAgent，权限范围转换为当前共享配置，不凭空声明内置 Agent。

Session 配置快照由归档 Agent 的可配置字段与 Schema 默认值生成，模型按本会话最后一次 Run、旧会话元数据、旧 Agent 配置、归档系统默认值的顺序恢复；审批模式优先使用最后一次 Run，其次是旧会话设置和默认审批。旧资源选择的 `null` 转换为 `all`，显式空列表保持为空。没有可追溯模型时整笔拒绝，不用当前系统配置冒充历史设置。该快照用于后续会话，不能证明历史 Run 的执行清单。

已经完成 `3/3` 迁移但缺少快照的部署，显式运行 `--repair-session-config` 审计；附加 `--apply` 时在独立事务中锁定目标 Session，只补齐与归档 ID、线程、用户和 Agent 一致且快照为空的记录。已有快照、新会话、消息、Run 与归档不变，重复执行不覆盖用户选择。

## 替代方案

手工更新版本号会让当前约束和数据事实不一致，拒绝。直接删除旧表或只导入用户与知识文件会丢失执行、幂等和审计事实，拒绝。继续让 app-init 自动猜测旧结构会把不可逆迁移隐藏在启动副作用中，拒绝。

## 后果

迁移需要完整备份、停写和维护窗口；旧数据会在同库保留归档，增加存储占用。stdio MCP、旧 LangGraph checkpoint 和 Milvus 集合字段的兼容性不由 PostgreSQL 迁移证明，部署者必须单独验证或重建相关投影。未知表保持原 schema，避免删除部署者定制对象。

## 验证

- `backend/test/integration/services/test_legacy_v073_schema.py` 在真实 PostgreSQL 上使用冻结旧 DDL 验证数据归属、多模态输入、权限、WPS 密文、知识父子版本、序列写入、幂等、在途拒绝、锁后复核和整事务回滚。
- `docker compose exec -T api python -m yuxi.migrations.legacy_v073 --repair-session-config --apply` 在现有 `3/3` 数据库中补齐 21 个历史 Session 快照；回读确认 21 个 Session、215 条 Message 与归档 ID 均保留，空快照数为 0。
- 历史 Session 的真实 PostgreSQL 与 HTTP 协议测试覆盖列表、详情、模型与审批选择、资源空值转换、显式修复幂等、已有配置保留及来源不足时整批拒绝。
- 未验证：现有 Milvus 集合重索引、stdio MCP 和旧 checkpoint 的图状态兼容性。
