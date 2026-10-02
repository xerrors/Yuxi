# 取消 LITE 运行模式

状态：archived
类型：simplification
Owner：docker-compose.yml

## 问题

Yuxi 同时维护完整模式与 `LITE_MODE`。这个部署选择贯穿 Compose、Schema 迁移、API/worker 启动、路由注册、Durable Task、Agent Skill 与工具、能力发现、前端导航和 CI，使同一版本存在两套装配路径。产品决定只交付完整知识能力路径，需要删除轻量模式及其兼容表面，同时保持知识库、图谱、评估、聊天、Agent、工作区和既有权限语义。

## 决策

删除 `LITE_MODE` 环境变量、`make up-lite` 和所有模式判断。shipping Compose 始终声明完整知识拓扑；`storage-migrator` 始终迁移 business 与 knowledge schema，API 与 worker 始终校验两个域。知识路由、内置 `knowledge-base` Skill、知识工具和 Agent 知识资源始终按既有权限规则装配，知识运行时初始化失败继续阻止 readiness。

Durable Task worker 统一消费 shipping registry 中的全部任务，并只发布一套通用健康租约。系统 discovery 保留版本与 CLI 协议字段，但知识能力固定为可用。Web 删除运行模式能力 store、知识路由门控和条件渲染。既有轻量部署升级时，由已有未版本化 knowledge baseline 创建当前 schema。

正式文档只描述统一知识拓扑；历史 changelog 保留当时事实。实现不引入替代开关、自动降级或新的部署模式。CI 可以按测试边界只启动所需依赖，但应用装配不因此分叉。

## 替代方案

- 保留现状：继续提供低资源的 Agent-only 部署，但每次知识、Task、Schema、前端导航和 CI 变更都必须维护双模式；不采用。
- 仅在 Compose 中少启动外部服务：API 仍需在服务缺失时定义路由、启动、discovery 和失败语义，不能删除主要条件矩阵；不采用。
- 拆分不同镜像或可选依赖组：可以缩小安装包，但会增加构建产物、依赖解析和发布矩阵，属于独立功能；不采用。
- 删除知识库、图谱和评估：会改变产品核心能力，不属于本次简化。

## 后果

- shipping 代码、配置、测试和当前文档只保留完整能力路径，净删除超过 900 行条件装配与专属测试。
- 既有轻量安装升级前必须补齐 Milvus、etcd、Neo4j 及完整知识运行所需资源；这是有意的部署兼容变化。
- API 导入知识路由并初始化知识运行时；附件 parser 和外部 provider 仍保持真实动作发生时的惰性边界。
- `tasks` 位于 business schema，旧数据库遗留的知识任务由统一 worker 按持久 Handler、owner 和 lease 规则继续处理或显式失败。
- Durable Task 健康租约改为统一 key；升级时应在迁移后协调重启 API 与 worker，混跑新旧版本只会安全地暂时 not ready，不会把旧租约误认成新 worker 能力。

## 验证

旧能力不存在：shipping 代码、配置、测试和当前用户文档中不存在可启用 LITE 的入口、条件分支或能力投影；历史 changelog 仅保留不可执行的历史描述。

重新引入条件：只有产品重新承诺无知识基础设施的受支持部署，并同时提供独立装配、Schema、worker/readiness、前端 discovery、迁移和风险相称的双路径证据矩阵时，才重新引入轻量模式。

- 全仓负向搜索确认除本记录和历史 changelog 外，不再存在 `LITE_MODE`、`lite_mode`、`up-lite`、模式专属能力判断或前端状态。
- `docker compose config --quiet` 通过；默认拓扑包含 PostgreSQL、Redis、MinIO、Milvus、etcd、Neo4j、API、worker 与迁移器。
- 后端相关 unit：125 passed；完整非 slow unit：1678 passed、44 skipped。
- 真实 HTTP/数据库 integration：Schema migration 8 passed，system readiness/discovery/OpenAPI 3 passed，Dashboard/Task 15 passed；readiness 实际回读 knowledge schema 为 required 且 healthy，discovery 回读知识与 CLI 知识能力全部为 true。
- `ruff check package server test` 与 `ruff format package --check` 通过。
- Web `lint:check`、189 项 unit 和生产 build 通过。
- `python3 scripts/verify_engineering_contracts.py` 与其 61 项 verifier unit 通过。
- 扩展执行 `test_system_router_api.py` 与 schema migration integration 时有 20 项通过、2 项失败；失败分别来自测试进程无权创建既有 `legacy-saves` 目录，以及此前系统配置变更新增 `system_runtime_config` 后旧断言未同步，均不涉及本决策路径。
- docs build 被 `docs/vibe/2026-08-23-readme-capability-screenshots.md` 引用的两个既有缺失 WebP 阻断；该忽略目录及资源不在本次变更中，当前文档的工程信任与链接检查已通过。
