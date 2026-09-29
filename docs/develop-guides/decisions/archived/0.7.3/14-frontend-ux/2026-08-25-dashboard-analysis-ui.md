# Dashboard 统计分析共享组件与口径

状态：archived
类型：feature
Owner：web/src/components/dashboard/DashboardMetricCard.vue

## 问题

系统概览与会话分析各自实现指标卡、图表容器和统计数字，导致样式、数字呈现、头像 fallback、筛选默认值和响应式行为不一致。会话分析还无法切换是否纳入子智能体，审计列表默认展示已删除记录，用户活跃趋势只有近 7 天折线图，不能直接识别两个月内的活跃分布。

## 决策

- 抽取 `DashboardMetricCard` 与 `DashboardMetricGrid`，系统概览与会话分析统一使用普通 HTML 数字和响应式网格；Dashboard 统计模块不再使用 `a-statistic`。紧凑卡片仍保持图标在左、数字在右，不因可用宽度较窄改为纵向堆叠。
- 保留 ECharts 作为连续趋势和排行图表的 Owner；`DashboardActivityHeatmap` 使用 16px 方格与均匀周间距铺满可用宽度，展示后端返回的近 120 天活跃数据。月份按连续周分段，隐藏不足两周的首尾残月标签，避免相邻月份重叠；同时保留空状态、tooltip、窄屏横向滚动和深色模式。
- 会话统计 API 增加 `include_subagents` 查询参数；默认统计仍排除子智能体和已删除会话，开关打开后只纳入未删除的子智能体。
- 会话审计的 `all` 语义改为默认排除已删除会话，`deleted` 仍可显式检索；用户和智能体展示统一经过现有 `FallbackAvatar`。
- Dashboard 的 `FallbackAvatar` 同时传入按实体 ID 生成的像素头像作为 `defaultSrc`，与用户管理和智能体列表保持一致。
- 会话摘要只保留累计会话、活跃会话、平均轮数和消息总数四个有效指标；宽屏四列、中屏两列、窄屏单列。
- 智能体承载排行按条目索引使用共享图表色板，避免单一颜色削弱条目辨识。
- 会话统计的子智能体范围与统计周期并列，使用带“包含/不含”状态的原生 switch 按钮；只有最新请求成功后才提交范围状态，失败时继续显示已有数据对应的旧口径。刷新按钮不投影请求 loading。
- 存储容量使用 B 到 YB 的标准二进制单位自动换算，数值最多保留四位有效数字，单位放入指标标签行。普通 Dashboard 卡片 hover 只改变浅背景，不增加黑色边框、outline 或阴影。

## 替代方案

- 只调整会话分析页面的 CSS：无法消除系统概览与会话分析的重复组件和数字呈现差异，未采用。
- 只在前端过滤子智能体与已删除记录：会造成统计口径与后端分页/汇总不一致，未采用。
- 引入新的图表库：现有 ECharts 已覆盖连续图表，新增依赖没有当前 Owner 或必要性，未采用。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| 两个 Dashboard Tab 共享指标卡、网格和普通数字结构 | `rg -n "<a-statistic|ant-statistic" web/src/components/dashboard web/src/assets/css/dashboard.css` 无匹配；`DashboardMetricCard` 与 `DashboardMetricGrid` 同时由系统概览和会话分析导入 | Passed |
| 会话统计可切换是否包含子智能体，且失败请求不提交新口径 | API、服务、仓储单元与 HTTP integration；`dashboard_thread_stats.test.js` 检查范围只在最新响应后提交 | Passed |
| 审计列表默认排除删除项且可显式检索 | Dashboard service unit 与 `test_dashboard_router.py` HTTP integration | Passed |
| 用户/智能体缺少头像时使用统一 fallback | `dashboard_thread_stats.test.js` 检查用户排行、智能体排行和审计列表的 `FallbackAvatar` 装配 | Passed |
| 刷新与子智能体控件语义、摘要和排行结构成立 | `dashboard_thread_stats.test.js` 检查无刷新 spinner、switch、四项非 Token 摘要和共享色板 | Passed |
| 用户活跃度返回 120 天并按连续月份分段 | 后端 unit；`buildHeatmapMonthSegments` unit | Passed |
| 存储容量最多四位有效数字且单位分离 | `dashboard_thread_stats.test.js` 覆盖 KB、GB、PB 与零值 | Passed |
| 前端工程检查 | `cd web && pnpm run lint:check && pnpm run test:unit && pnpm run build` | Passed；构建仅保留既有第三方注释和大 chunk warning |
| 1440px 浅色/深色与 1024px 响应式布局成立 | 真实 Compose 页面检查摘要为 4/2 列、图表为 2/1 列，1024px 文档无横向溢出；子智能体开关成功后更新为“包含” | Inspected |
| 375px 窄屏摘要切为单列 | 真实 Compose 页面为单列；应用壳仍受既有 `--min-width: 450px` 约束而产生横向裁切 | Inspected；全局窄屏限制不由本决定拥有 |

## 后果

- Dashboard API 是超级管理员读模型；删除排除规则由仓储执行，前端开关只改变统计读模型纳入范围。
- 方格图按接口返回的数据渲染，仓储补齐 120 个连续自然日；旧响应或空数组不会把缺失天数伪装成活跃记录。
- 现有 ECharts 连续图表和运行能力分支保持不变。
- Compose 内 `uv run --no-sync` 执行 Dashboard、Chat HTTP integration；Office 转换、状态面板超限滚动和纯触屏等未构造状态仍保留为未验证范围。
