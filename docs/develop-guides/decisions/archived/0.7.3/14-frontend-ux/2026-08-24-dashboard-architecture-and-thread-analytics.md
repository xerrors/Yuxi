# Dashboard 架构分层与会话多维分析优化

状态：archived
类型：architecture
Owner：backend/package/yuxi/services/dashboard_service.py

## 问题

当前的 Dashboard 存在若干关键问题：
1. **接口与架构分层不清**：`dashboard_router.py` 直接承载跨表聚合与数据组装逻辑，未遵循 Thin Router 规范；知识库统计接口碎片化。
2. **知识库文件统计查询低效且路径不当**：`knowledge_dashboard_service.py` 之前遍历所有知识库并循环加载全量文件对象（N+1 内存加载），未在 SQL 层直接聚合，且缺乏对虚拟目录的准确过滤。
3. **展示维度单一且未充分利用会话数据**：既有会话查询接口缺乏对应的前端展示与审计入口，管理员无法直观获知全平台会话的增长动态、互动轮数深度、智能体承载与高频用户分布。

## 决策

1. **重构后端分层（Thin Router -> Service -> Repository）**：
   - 新建 `yuxi.services.dashboard_service.DashboardService`，集中承载基础统计、调用时序、智能体分析、会话检索与会话多维分析用例。
   - `dashboard_router.py` 保持轻量，仅负责依赖注入、参数校验与 Pydantic 模型装配。
   - 优化 `knowledge_dashboard_service.py`，由知识库与文件 repository 提供批量 SQL 聚合，service 只做业务展示映射；数据库层完成文件类型分布、节点数与存储容量计算，并将历史 `is_folder = NULL` 记录按普通文件处理。
2. **新增会话多维分析统计与检索能力**：
   - 在 `DashboardRepository` 与 `DashboardService` 中新增 `get_thread_analytics`，提供核心指标汇总（总会话数、活跃会话数、平均对话轮数、平均 Token 消耗）、每日会话增长与活跃时序、对话深度分布（1-2条、3-5条、6-10条、11-20条、20+条）、智能体会话分布及高频用户活跃榜；运营统计统一排除已注销用户、已删除会话与已删除智能体。
   - 增强 `list_conversations`，默认展示全部状态以服务审计场景，支持关键词、用户、Agent、状态过滤与真实总数分页；完整历史保留并标注已注销用户、已删除会话和已删除智能体。
   - 每日趋势按上海日历日使用批量聚合查询，而不是随 7/14/30/90 天范围逐日执行 SQL；`agent_id` 过滤统一约束汇总、趋势、深度、排行与状态分布。
3. **前端重构为多 Tab 布局与会话审计工作台**：
   - 使用全站共享 `PageHeader` 承载“系统概览 / 会话分析”Tab，并将 Tab 状态同步到 URL；首次激活后保留图表实例，避免重复重建与隐藏容器初始化。
   - Tab 1（系统概览）：保留并优化基础 KPI 概览、调用时序监控、用户活跃度、智能体分析、工具调用监控与知识库使用情况。
   - Tab 2（会话分析试点）：呈现 4 张核心指标、2x2 可视化图表网格（增长趋势、深度分布、智能体排行、高频用户榜），以及支持关键词/状态/Agent/用户筛选的全平台会话审计表格与右侧会话交互抽屉；长标识省略展示并保留完整 tooltip。
   - 统一遵循系统设计 Token（`--gray-*`、`--main-*`、语义色），严格适配深浅色与自适应断点。

## 替代方案

- **在原有单页继续追加所有图表**：页面过长且认知负荷极重，无法清晰区分“系统运行时监控”与“业务会话分析与审计”。
- **在应用层遍历或逐日查询会话统计**：数据量或时间范围增大时会造成明显的 IO 与延迟，因此选择在 PostgreSQL/SQLAlchemy 读模型层批量聚合后补齐空日期。

## 后果

- Dashboard 路由与服务实现严格解耦，消除了 N+1 文件查询隐患。
- 超级管理员获得强大的会话深度分析与交互审计能力，可点击任意会话实时查看请求流水与工具执行详情。
- 前后端均通过类型检查、规范测试与构建校验。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Dashboard 路由仅作为薄适配层，业务逻辑下沉至 Service | 路由内出现直接仓库组装或跨表计算 | `backend/server/routers/dashboard_router.py` | `uv run --group test pytest test/unit/services/test_dashboard_service.py` | 路由抛出未捕获内部错误 | Passed |
| 知识库统计使用 SQL 聚合且排除文件夹 | 虚拟目录被计入文件数或 N+1 循环回退 | `backend/package/yuxi/services/knowledge_dashboard_service.py` | `uv run --group test pytest test/integration/api/test_dashboard_router.py` | 文件夹记录计入 file_type 聚合 | Passed |
| 会话多维分析统计与审计抽屉正常加载 | 时序或深度分布维度缺失或无法展开工具调用 | `web/src/components/dashboard/ThreadStatsComponent.vue` | `pnpm run lint:check`；`pnpm run build`；`node --test test/**/*.test.js` | 缺少必要字段或图表销毁泄漏 | Passed |
| 会话趋势与分页不会随范围线性放大 SQL 或伪造总数 | 90 天趋势逐日查询；末页与下一页判断错误 | `backend/package/yuxi/repositories/dashboard_repository.py` | `uv run --group test pytest test/unit/services/test_dashboard_service.py`；真实 HTTP integration | 恢复逐日循环或数组响应后统计/契约测试失败 | Passed |
