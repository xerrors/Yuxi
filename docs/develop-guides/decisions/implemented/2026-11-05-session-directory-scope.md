# 会话工具的项目范围与紧凑摘要

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/cooperation.py

## 问题

Agent 查询会话时一次获得当前用户的全部历史及面板内部状态，聊天记录还能展开长列表。会话查询的自定义事件行与通用工具摘要颜色和布局不同。用户需要默认查看当前项目，并在主动查看历史时分批获取；执行记录只需说明查询范围与条数，并沿用通用工具样式。

## 决策

`list_sessions` 默认 `scope=project`，返回当前项目的授权会话；显式 `scope=all` 查看同用户、同 APP 的历史目录，每页固定最多 10 条，支持非负 `offset`。当前项目目录保持完整，历史按更新时间及稳定身份倒序分页。查询不改变会话操作权限。

### 实现方案

协作 middleware 拥有工具参数和模型说明，service 从持久 Run 确认调用者，repository 在用户、APP 和项目过滤后查询目录与最新 Turn。工具回执携带 scope，只保留会话标识、标题、路径、所属根和项目、当前 Turn 标识与状态、等待类型。完整协作树与运行、队列投影继续由 HTTP 状态面板接口拥有。

前端会话查询复用 `BaseToolCall` 的 `summary-only` 展示，与“查看智能体”使用相同的标题和计数布局；颜色、间距、图标与运行状态由通用组件维护。完成状态显示“查看了本项目会话”或“查看了历史会话”与本次返回条数，不提供列表展开入口。含会话列表的旧回执缺少 scope 时按其全部历史契约展示；尚无列表与范围的调用按当前默认项目范围展示，其他协作动作保留导航及失败状态。

## 替代方案

- 只隐藏前端列表：模型仍收到全部历史及内部状态，不满足查询范围和信息收敛。
- 修改共享树摘要的字段与分页：会破坏状态面板完整树形、队列与执行观察契约。
- 所有查询都限制 10 条：会截断当前项目的协作目录；用户仅要求历史查询分页。
- 单独调整会话行的颜色和间距：复制通用组件样式，仍存在独立的状态和布局维护面。

## 后果

模型需要主动选择历史范围并翻页；offset 反映当前目录顺序，不承诺并发更新期间的快照。当前项目目录仍可较大。没有数据库迁移、权限变更、Public API 扩展或新的前端配置。

本决定收窄[用户范围协作](./2026-10-29-session-provenance-and-user-cooperation.md)的目录返回与查询展示；稳定 ID 的跨项目操作权限、完整树摘要与[先序顺序](./2026-10-24-cooperation-tree-preorder-summary.md)保持原有职责。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 默认只查本项目，历史每页最多 10 条且 offset 生效 | 全账户泄漏到默认结果或页间重复 | cooperation repository/service | 真实 PostgreSQL integration：项目 12 条，历史按 10/10/5/0 分页并核对 SQL | 其他项目、用户、APP、删除会话及相同更新时间 | Passed |
| 模型仅接收协作所需字段，状态面板保留完整摘要 | 共用 DTO 导致内部字段暴露或面板缺字段 | cooperation service、middleware | schema unit、PostgreSQL 回执字段集合及完整树断言 | 非法 scope、负 offset、内部队列及 Run 字段 | Passed |
| 查询范围可见且复用通用摘要，不展开列表 | 自定义行样式不一致、默认展开参数恢复长列表或旧历史被误标本项目 | CooperationTool、BaseToolCall | 前端 unit、真实工具分组与单独入口的 DOM、计算样式和截图 | defaultExpanded=true、旧回执、空列表、失败和中断 | Passed |

定向后端 unit 42 项通过；完整协作 PostgreSQL 集成 92 项通过；前端工具投影与渲染 unit 33 项通过。新增目录和渲染回归在旧实现上分别失败，覆盖默认范围、分页参数、展开列表以及单独和分组入口缺少通用摘要的缺陷。

目录排序的负向验证将更新时间改为升序，独立准备的最近会话顺序断言失败；恢复降序后执行 `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_session_cooperation.py -k session_directory -q -p no:cacheprovider`，3 项通过。

回归命令与结果：

- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_session_cooperation.py -q -p no:cacheprovider`：92 项通过。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q --tb=short`：3618 项通过、127 项跳过；宿主补跑 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q --tb=short`，209 项通过。容器中的跳过不作为对应配置契约通过的证据。
- `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run test:unit`、`docker compose exec -T frontend pnpm run build`：均通过。
- 后端 `uv run --no-sync ruff check yuxi`、`uv run --no-sync ruff format yuxi --check`、`uv run --no-sync ruff check --select I yuxi`，以及工程契约检查、56 项 verifier 回归、文档构建与 `git diff --check`：通过。

真实 Vite 页面执行 `frontend/test/browser/toolRendering.js` 与 `cooperationEvents.js`，核对 21 种工具摘要、本项目与历史查询、其他协作操作和来源导航。浅深主题以及 1440、1024、768px 均通过。每种主题和宽度下，单独入口的 4 行和真实工具分组的 3 行会话摘要，与“查看智能体”的计算颜色、字体、行高及间距一致；加载和失败使用通用状态图标。浏览器使用合成工具回执及确定性 HTTP fixture，只证明 DOM、样式与导航。

完整 worker E2E 未执行：运行环境存在活动 Run，未重启 API/worker，且未配置 `COOPERATION_REPLAY_URL`。服务读取变化由真实 PostgreSQL 集成验证；浏览器证据不替代 worker 执行链。
