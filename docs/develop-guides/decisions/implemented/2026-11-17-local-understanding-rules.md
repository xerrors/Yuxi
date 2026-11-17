# 按任务定位事实并审查局部可理解性

状态：implemented
类型：process
Owner：docs/develop-guides/spec-loop.md

## 问题

接手者容易把 Session 与 Thread 当作两个实体，或把文件数量当作职责复杂度。既有机制文档和 Review 流程已存在，需要更明确的阅读起点与拆合判断。

## 决策

### 实现方案

[机制入口](../../../mechanisms/index.md)只补关键身份、路径和任务类型映射，链接当前解释 Owner。新内部会话名称使用 Thread，既有协议与存储字段保留适配映射；域内约定文件名按完整路径定位，不要求统一加域前缀。Session ORM 注释明确行主键和业务身份；runner 的模块说明串起接收、消费、投递、租约、执行和结算，不复制完整状态机。

[Spec Loop](../../spec-loop.md#局部可理解性)用真实修改任务审查职责拆合、抽象、命名、阅读材料及简化成果。现有 Reviewer 执行规则，不增加行数阈值、规则登记表或新的架构层。

Skill 保留 shared、personal、install_draft、bound 四类管理入口，删除 shared 无用途的权限与内置同步重导出，消费者直接引用 access 或 builtin.sync。跨域内容提交、投影和运行快照仍由各自模块负责。模型域同时包含调用适配和供应商管理，保留 models 名称并在包入口解释与 ORM 的区别；OAuth 按实际安全边界保留分层。

前端导航 store 不保存无消费者的消息加载状态；消息归属读取当前 Input/Run 字段，不保留旧乐观消息拼接的回填工具。无消费者的前端请求包装和耗时摘要移除，后端公开协议保留。Agent 内部只保留实际事件写入与订阅查询入口，解码失败显式抛错，不保留不可达的空结果过滤。

测试装配可以直接预热 ORM 缓存或使用生产查询；锁后刷新、执行序号、事件游标与非法数据拒绝仍验证原有业务结果，不用死接口维持测试。数据库存量状态、旧作业 payload、Workdir 绑定和部署配置兼容未被判定为无消费者，不在删除范围内。

## 替代方案

另写完整入门架构会重复现有事实；批量重命名和按文件长度合并缺少语义依据。采用已有入口与规则位置的定向补充，整体方向见[认知成本提案](../proposed/2026-11-17-cognitive-cost-simplification.md)。

保留仅由测试消费的内部入口会让已经退役的生产路径看似仍受支持；添加弃用别名又保留同一维护面。因此删除这些入口并调整测试装配。判断依据是原生产消费者消失、当前职责已有明确承接者且无外部或持久兼容承诺，不能仅凭搜索引用次数删除。

## 后果

规则改变 Review 的判断依据，不改变运行契约。接手耗时仍需真实任务测量；文档构建不能证明效率收益。

## 验证

术语与职责已经对照 ORM、调用点和机制文档核对（Inspected）。`python3 scripts/ci/verify_engineering_contracts.py`、其 56 项 unittest、相对链接核对、`cd docs && pnpm run build` 和 `git diff --check` 通过。独立审查确认两处 Python 修改仅改变 docstring；新人计时实验未执行（Not run）。

Skill 入口清理的相关测试已通过：`cd backend && uv run --no-sync --group test pytest test/unit/services/skills test/unit/agents/skills test/unit/agents/test_builtin_discovery.py test/unit/permissions/test_resource_permission.py -q`，247 项。授权算法、持久结构和公开 HTTP 不变。

内部入口退役验证：在 frontend 执行 `pnpm run lint:check`、`pnpm run test:unit`（1,104 项）和 `pnpm run build` 通过；两项仅覆盖退役函数的测试删除，消息身份、导航隔离和迟到 Input 回归保留。在 backend 执行 `uv run --no-sync --group test pytest test/unit/services/test_agent_transport.py test/unit/services/test_agent_event_writer.py test/unit/storage/test_session_repository.py -q`，30 项通过；隔离 PostgreSQL/Redis 上执行 `test/integration/services/test_agent_input_schema.py` 与 `test/integration/services/test_run_stream_redis.py`，5 项通过。缓存刷新、执行序号、事件内容顺序、游标和 TTL 均继续直接验证，新增 Run 归属拒绝案例与既有版本拒绝共同覆盖读取边界。

隔离 Compose api 运行 `uv run --no-sync --group test pytest test/unit -m "not slow" -ra -o cache_dir=/tmp/pytest-cache`，3,805 项及 7 个 subtest 通过，127 项因容器没有仓库根或 Docker CLI 跳过；宿主运行 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`，212 项通过。

删除没有改变 UI、协议、事务或执行生命周期，未新增浏览器装配或重跑完整 worker E2E；上述 unit/integration 不替代完整执行链路证据。
