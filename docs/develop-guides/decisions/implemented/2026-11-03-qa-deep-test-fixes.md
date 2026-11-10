# 修复 QA 深测发现的终态投影、归档只读与错误可见性问题

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/ui/SessionWorkspace.vue

## 问题

连续深测(浏览器 + 代码复核)在既有功能上发现 10 处可修复缺陷，集中在两类：

1. **终态与错误不可见**：用户主动停止的 Turn 有工具摘要时不显示"已取消"；仪表盘基础统计加载失败时卡片显示 0（与同页 VM 统计的正确做法不一致）；连接型知识库检索失败只有 3 秒 toast，错过即无从得知原因。
2. **事实与界面不一致**：归档会话经 URL 打开后会重新出现在"最近"列表且输入框仍可发送（后端返回 409，但文案是"请刷新后重试"）；切到"对话目录"沿用运行前渲染的空树；审批模式选择器没有说明"当前 Project 内写入自动放行"这一既定设计，容易被读成安全缺陷。

## 决策

### 实现方案

- **终态**：复用 `formatEmptyRunStatus` 既有文案，仅放宽渲染条件——`cancelled/interrupted/cancel_requested` 即使存在过程记录也显示，并加 `.is-terminal` 强调。不新增状态机。
- **仪表盘**：新增 `basicStatsError`，卡片经 `basicValue()` 在加载中显示 `…`、失败显示 `—`，错误条带重试。完全对齐同组件已有的 `vmValue`/`vmStatsError` 模式，不引入新抽象。
- **归档会话**：`upsertThread` 不把归档线程作为新条目插入（当前打开线程除外，用于承载只读提示）；侧栏与搜索统一由 `visibleThreads` 过滤；`threadArchived` 并入 `agentExecutionAvailable`，输入区显示只读说明。后端语义不动，权限仍在后端 fail-closed。
- **409 文案**：字符串 `detail` 仅在 409/410 透传。**401/403/422/5xx 继续使用脱敏兜底**，避免把权限或服务端内部信息展示给用户（该收紧由两个既有单测的失败直接驱动，见"后果"）。
- **检索错误**：结果区新增持久错误块（标题 + 原因 + 重试），toast 降级为即时提醒。
- **对话目录**：`setTreeScope('thread')` 切换时立即静默拉取一次，与切到个人空间的既有行为对称。
- **审批说明**：两个模式各加一行范围说明，下拉宽度 140→280px。
- **MCP 401 文案**：把内部术语改为可操作指引，并同步唯一断言该文案的单测。

## 替代方案

- 只在会话组件内新增"配置版本"对比：需要后端在 session 资源暴露配置快照指纹，属新契约，本轮不引入，改为先纠正误导性文案。
- 无差别透传所有字符串 `detail`：会让 403 权限文案与 5xx 内部信息直接上屏，已否决（实测导致两个既有单测失败）。
- 图例改用 `a-tooltip` 包裹：会插入额外节点破坏 `li` 的 grid 列布局，改为两列时名称独占一行 + 原生 title。

## 后果

- `base.js` 的字符串 `detail` 透传范围是本次最容易回归的点：它影响全局错误投影。任何放宽都必须同步检查 `api_boundary` 的脱敏用例。
- 归档只读提示依赖 `public_resources.session_resource` 返回的 `yuxi.archived`；该字段若被移除，提示会静默消失（不会误拦截发送，因为后端仍拒绝）。
- `upsertThread` 允许"当前打开线程"进入列表，意味着归档会话在被访问期间仍存在于 store，只是侧栏不渲染。列表刷新时 `cachedDetails` 已按 `currentThreadId` 保留同类行为。
- Q-8(协作视图异步组件超时)与 Q-10(快照指纹提示)未改：前者缺少可稳定复现的对照实验，后者需要新契约。
- 图例的"两列时名称独占一行"布局已由 [2026-11-10 单行响应式图例](./2026-11-10-dashboard-legend-single-line.md) 取代；本记录其他修复继续有效。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 停止的 Turn 显示明确终态 | 有工具摘要时被渲染条件跳过 | SessionWorkspace 渲染条件 | 浏览器 DOM：停止后 `.chat-inline-notice.is-terminal` 文本"本次运行已取消" | 正常完成的 Turn 不出现该提示 | Passed |
| 统计失败不投影为 0 | 双端点失败后 `{}` 被 `formatNumber` 渲染成 0 | DashboardView / StatsOverviewComponent | 注入 500：四卡显示 `—` + 错误条；移除注入点重试恢复真实值 | 成功路径仍显示真实数值，不出现错误条 | Passed |
| 归档会话不再出现在最近列表 | `upsertThread` 无条件插入 | chatThreads store + SessionNavigation | 归档后 URL 直达：侧栏匹配数 0，页面显示只读提示 | 未归档会话仍正常出现在侧栏 | Passed |
| 409 业务文案优先且不泄露其��信息 | 无差别透传字符串 detail | `frontend/src/apis/base.js` | 新增单测"409 冲突优先展示后端业务文案"；`api_boundary` 全量 1013 通过 | 403/500 仍返回脱敏文案（既有 2 个用例） | Passed |
| 检索失败原因可回看 | 仅 toast，3 秒后消失 | QuerySection | 飞书 401 后 `.query-error` 常驻并含重试 | 成功检索仍展示片段列表 | Passed |
| 切到对话目录立即有内容 | 沿用运行前空树 | AgentPanel.setTreeScope | 切入后树节点出现、无"当前对话目录为空" | 个人空间切换行为不变 | Passed |
| 审批范围可发现 | 选择器无说明 | ToolApprovalModeSelector | 菜单文本含"当前 Project 内写入自动放行" | 原有模式选择与写回不变 | Passed |
| 图例名称可读 | 名称列被压缩到 26px | KnowledgeStatsComponent | 两列布局下截断元素 8/8 → 0/8 | 单列（≤4 类型）布局不变 | Passed |
| MCP 401 文案可操作 | 内部术语 | knowledge MCP server | curl 无凭据返回新文案 401；该模块 29 项单测通过 | 有效 Key 的正常调用不受影响 | Passed |

实际执行：

- `docker compose exec -T frontend pnpm run lint:check`：通过（`--max-warnings=0`）。
- `docker compose exec -T frontend pnpm run test:unit`：1013 passed / 0 fail。
- `docker compose exec -T frontend pnpm run build`：成功。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：3525 passed / 125 skipped；skip 不计入通过。
- `python3 scripts/ci/verify_engineering_contracts.py`：通过；`python3 -m unittest scripts.ci.test_verify_engineering_contracts`：56 OK。
- 浏览器复测：逐项在真实页面核对上文表格的负向案例；截图属于本地测试产物，不纳入仓库。
