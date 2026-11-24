# 0.8.1 从正式 0.8.0 数据库直接升级

状态：implemented
类型：simplification
Owner：backend/yuxi/migrations/agent_plugins.py

## 问题

正式标签 `v0.8.0`（`0fa68b050`）发布 business 2 / knowledge 2。拆分 Skill 和插件的 `7f879457f` 引入 business 4，专属 MCP 的 `e26b4492a` 将其推进到 5。两个提交之间没有应用发行标签。原迁移只检查业务域，缺少知识域的源库可进入转换，混合版本的目标库也可提前返回成功。

## 决策

### 实现方案

0.8.1 的离线插件迁移在部署锁内核对完整的域版本。export 接受正式版的 2/2，apply 接受 2/2，或在已完成的 5/2 上幂等返回。部署者排空并停写，导出并审阅 Skill 包、Agent 和会话配置以及旧连接来源；apply 重新核对计划，在同一数据库事务中发布 DDL、包引用、配置和新版本。同步器与迁移共享内置 Skill 的归属校验，避免旧上传包占用目标标识时，发布了新版本才在初始化阶段遇到冲突。前置元数据重写保留正文字节，包括 CRLF 换行。新包在提交前失败时回收，提交结果未知时保留以供重试。见[操作说明](../../../agents/skills-management.md#从运行依赖升级)。

## 替代方案

为开发主线的 v4 保留第二套转换，需维护不同的 Skill 布局及设备连接归属判断。通过 app-init 自动追赶版本，会跳过部署者对自定义能力和连接归属的审阅。保留独立离线命令使已有数据升级与空库初始化各自有明确入口。

## 后果

0.8.0 正式库已具备个人 MCP 凭证列与真实知识库目录。对应早期部署的补列、目录审计和转换保存在 [0.8.0 标签](https://github.com/xerrors/Yuxi/tree/v0.8.0/backend/yuxi/migrations)。旧部署可按匹配标签的规程处理。本决定更新[目录实体化](./2026-08-24-knowledge-folder-materialization.md)、[个人凭证](./2026-11-06-mcp-personal-credentials.md)、[插件运行能力](./2026-11-17-agent-plugins.md)及[专属 MCP](./2026-11-24-plugin-bundles.md)的升级范围。

版本表记录结构契约。运行过未发布源码的站点需自行核对来源与备份。跨版本回退须恢复相配套的数据库及文件；新 worker 的 checkpoint 留存与旧消息 JSON NUL 修复各自有独立的触发条件。见[候选更新说明](../../releases/0.8.1.md)。

## 验证

`test_agent_plugin_migration.py` 在 PostgreSQL 中验证缺失域、过渡版本及初始化中的标记在写入前被拒绝，回读版本、配置和列结构。正常升级回读转换后的插件、MCP 与文件，重复应用保留结果。

使用 `git archive v0.8.0` 的源码、该标签的依赖锁和正式 app-init 在独立 PostgreSQL 16.14 建库，写入合成的会话、Input/Receipt/Message/Turn/Run、知识数据以及 Skill 文件。用候选版执行 `agent_plugins export`、`apply`、app-init，并分别重复 apply 和初始化。升级后的 53 张表、34 个序列、231 个约束、253 个索引及自定义触发器与候选空库一致。为消除 PostgreSQL 备份恢复对数组类型转换表达式的等价重排，两库经过相同的 PostgreSQL 16.14 dump/restore，再对整份 Schema dump 规范化会话随机标记和新增列及其注释的位置，结果逐字节相同。

48 张未参与转换的表逐行相同。6 处配置按预期转换，5 个新 Skill 包保留正文和辅助文件，旧文件、用户工作区与历史版本回读一致。重复 apply 前后数据库与文件快照相同；重复 app-init 只刷新内置 Skill 的同步时间。遇到同名上传包、未确认 DeepWiki 来源、仍有活动 Run 或审阅后配置发生变化时，迁移拒绝继续。CR-only 前置头可解析，CRLF 正文的 SHA256 保持相同。演练使用合成数据，实际部署的数据量、停写时间和自定义清单需在其备份副本上核对。

在关闭 Knowledge 的隔离拓扑上，以升级后的库启动候选 API、ARQ worker 和 Container runner。worker 发布健康状态前，readiness 返回 503，发布后返回 200。经真实 HTTP 回读历史会话、Turn、最终回答、Input/Receipt 与工作区文件；另一用户读取同一会话返回 404，未认证请求返回 401。用 PostgreSQL saver 回读原 checkpoint。该场景未调用真实模型或新建沙盒执行。

旧能力不存在：0.8.1 的发行代码移除 knowledge v1 目录转换、business v1 个人凭证补列和插件过渡 v4 的升级分支。

重新引入条件：需要支持另一个明确的已发布起点，且能用对应发行代码建立数据库并验证升级结果。
