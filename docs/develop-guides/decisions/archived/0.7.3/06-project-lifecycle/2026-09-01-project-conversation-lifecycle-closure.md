# 闭合 Project Conversation 生命周期旁路

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/services/subagent_run_service.py

## 问题

Project 删除与普通显式 Conversation 创建使用同一 Project 行锁，但运行中的父 Agent 可以经 SubAgent 服务在 deleted Project 下新增 Conversation 与 Run。聊天输入区创建 Project 后只更新组件局部列表，侧边栏不会同步；侧边栏“最近”视图还把 `updated_at` 当作主要时间，改变了原有按 `created_at` 排序的契约。

## 决策

SubAgent 服务在读取父 Conversation 后锁定其 active Project，再按相同锁顺序锁定并强制刷新、复核父 Conversation；该锁持有到子 Conversation、关系与 Run 提交。deleted Project、首次读取后被另一事务删除的父 Conversation，以及 deleted 既有子 Conversation 都拒绝创建或继续运行。

Project 共享 store 拥有侧边栏列表与加载版本。Project 选择组件使用服务端创建结果更新 store；列表请求开始后发生的创建、重命名或删除会使旧响应失效，迟到响应不能覆盖较新的页面状态。侧边栏排序保留置顶优先，并在同一置顶层级只按 `created_at` 降序。

## 替代方案

- 为所有 Conversation 创建引入新的统一 service：拒绝。既有 SubAgent 旁路只缺少 Project 生命周期锁，新增抽象会扩大改动与迁移风险。
- 用 provide/inject 只同步创建事件：拒绝。它仍让布局和选择组件维护两份状态，且布局初始化的迟到列表响应可以覆盖刚创建的 Project。
- 将“最近”定义改为最近更新时间：拒绝。当前侧边栏契约保持原有排序，重命名不提升旧 Conversation。

## 后果

SubAgent 写入按父 Run、Project、父 Conversation 的顺序加锁。Project 删除不锁 Run，并按 Project、Conversation 的顺序更新，因此没有形成反向锁顺序。共享 store 只消费 Project API 的服务端结果，不乐观构造 Project。缺少 `created_at` 的异常线程使用零时间，不回退到 `updated_at`。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 负向案例 | 结果 |
|---|---|---|---|---|
| Project 或父 Conversation 删除后 SubAgent 不能新增或继续子 Conversation/Run | SubAgent service、Conversation/Project repository、PostgreSQL | SubAgent unit；Conversation identity-map 刷新 unit；真实 PostgreSQL 并发 integration | deleted 父 Conversation、deleted Project；首次读取后父或子 Conversation 被删除；Project 删除与真实 SubAgent Conversation 创建并发 | Unit passed；integration not run（共享 PostgreSQL 仍为本 PR 前 schema，缺少 `projects.status`） |
| 页面内创建 Project 后侧边栏立即识别该 Project | Project store、AppLayout、ProjectSelectionSection | web store 行为 unit 与 build | 创建后的迟到列表响应不能覆盖新 Project | Passed |
| 最近视图按创建时间保持原排序 | projectConversationGroups utility | web unit 使用创建和更新时间相反的数据 | 重命名旧 Conversation 后顺序不变 | Passed |

后端负向 unit、完整前端 unit、前端 lint/build、ruff、格式、工程契约与文档构建通过。真实 PostgreSQL 并发测试已实现，目标环境升级到当前 business schema 后执行。
