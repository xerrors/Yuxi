# Project 对话分组与生命周期管理

状态：archived
类型：feature
Owner：backend/package/yuxi/services/project_service.py

## 问题

侧边栏只按最近时间展示 Conversation，用户无法从 Project 归属识别和管理相关对话。Project 已拥有名称、Workdir 与 Conversation 外键，但缺少重命名和删除生命周期；硬删除数据库行会破坏历史归属，删除 Workdir 又会越过 Project 只拥有业务绑定、不拥有目录字节的边界。

## 决策

Project 保存 `active/deleted` 状态与删除时间。重命名只更新当前用户的 active selectable Project 名称；删除在一个 PostgreSQL 事务中锁定 Project，把该 Project 及其全部 Conversation 标记为 deleted，并保留 Workdir 目录及其中字节。显式 Project 下创建 Conversation 时取得同一 Project 行锁并持有到 Conversation 提交，使创建与删除按锁顺序线性化，不能留下 active Conversation 绑定 deleted Project。删除后的 Project 不进入选择列表，相关 Conversation 不进入普通历史、搜索或运行入口；同一幂等创建键继续指向已删除记录，不能静默复活。

侧边栏同时提供“项目”和“最近”两个分组，“项目”位于“最近”上方。两个分组复用 `/api/projects` 与线程列表中的不可变 `project_id`：项目分组按 Project 列表顺序展示 selectable Project 及其 Conversation，最近分组只展示由 implicit 或不可见 Project 承载的其他对话，并在 Project 元数据加载完成前不提交分类结果。两个分组与具体 Project 均可折叠；Project 通过行内菜单重命名或删除，删除确认明确说明对话会删除、项目文件夹会保留。最近分组保留置顶、时间排序、线程状态、对话操作和加载更多语义。线程分页的 `limit/offset` 只计非置顶 Conversation，响应每页重复附带全部置顶项；前端以已加载非置顶数量推进 offset，并以当页非置顶数量判断是否还有下一页。

Project 持久化拥有名称与生命周期，Conversation 持久化拥有线程状态，Project Workdir 只保存 UserWorkspace 相对路径。前端不复制 Project 名称到 Conversation，不从路径或名称推断归属。

## 替代方案

- 新增专用分组侧边栏响应：拒绝。它会复制 Project 列表、Conversation 排序、Run 状态与分页契约，产生第二套线程读取面。
- 只在前端隐藏被删除 Project：拒绝。直接 API 调用仍能选择或运行已删除资源，无法闭合持久化事实。
- 硬删除 Project 并级联硬删除 Conversation：拒绝。Conversation 的删除语义是软删除，审计、Run、消息和恢复引用仍需保留数据库归属。
- 删除 Project 时同时删除或清空 Workdir：拒绝。Project 不拥有目录字节，linked 目录还可能被其他 Project 共享。

## 后果

- Project 与 Conversation 的软删除使用同一事务提交；Project 行锁串行化重命名、删除与显式 Project 下的 Conversation 创建。
- 删除不会取消已经投递的 AgentRun；删除前已取得执行 Owner 的 Run 仍可能产生外部副作用和审计记录，但 deleted Conversation 不会重新出现在普通历史。
- 线程分页只组织已经加载的 Conversation；项目视图通过既有“加载更多”继续补齐，不声称首屏包含全部历史。
- Project 生命周期结构通过 0.7.2 发布版到当前版本的完整业务升级创建；当前版本与升级边界由[版本化 Schema 迁移 Owner](../05-storage-workdir/2026-08-24-versioned-schema-migration-owner.md)定义。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 负向案例 | 结果 |
|---|---|---|---|---|
| 当前用户可重命名 active selectable Project | Project repository/service 与 HTTP 依赖 | Project unit；真实 HTTP integration 回读名称 | 空白名称、跨用户、deleted Project 拒绝 | Passed |
| 删除原子软删除 Project 与全部 Conversation | Project repository/service 与 PostgreSQL | 真实 HTTP integration 后重新查询 Project、Conversation 与列表 | 跨用户与重复删除返回 404 | Passed |
| Project 删除不修改 Workdir 字节 | Project service 与 `yuxi.workspace` | integration 删除后回读哨兵目录 | linked Workdir 保持存在 | Passed |
| 项目分组位于最近分组上方且两者同时展示，最近只包含其他对话 | Conversation 导航组件与 Project/Thread API | 前端 unit；Playwright DOM 顺序、分类结果、折叠动画与最终截图 | Project 加载中或失败、空 Project、implicit Conversation、deleted Project、长名称、置顶项跨页、键盘操作菜单 | Passed |
| Project 删除与 Conversation 创建线性化 | Project 行锁与 Conversation 创建事务 | service guard unit；隔离 PostgreSQL 并发事务回读 | 创建持锁时删除等待，最终 Project 与新 Conversation 同为 deleted | Passed |
| Schema 从 0.7.2 收敛到当前版本 | storage-migrator 与 PostgreSQL schema version | storage migration unit；隔离数据库迁移与真实 API 启动 | 发布版结构补齐缺失 DDL；未知版本 fail-closed；迁移失败不提前记录版本 | Passed |

最终页面验证使用真实 Vue 页面、Docker PostgreSQL 与真实 FastAPI；分组并列展示、FolderOpen/FolderClosed 状态和折叠动画在浅色、深色 1440×900 视口完成回读。截图作为 PR 外部素材保存，不进入仓库。
