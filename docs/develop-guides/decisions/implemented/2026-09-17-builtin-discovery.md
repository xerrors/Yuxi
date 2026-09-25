# 内置内容定义与发现

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/buildin/__init__.py

## 问题

预置角色的提示词与初始化方法混在 repository 中，新增角色需要跨层注册。内置 Skill 的元数据分散在 Python 清单与 SKILL.md；MCP 固定定义嵌入连接服务。执行后端的目录扫描与实例管理又把能力声明、缓存和重载耦合起来，而实际 Graph 每次运行都独立构建，不使用实例上的 graph 缓存。

## 决策

执行后端由 `BUILTIN_BACKENDS` 显式字典声明，稳定后端 ID 映射到 Python 类。`get_agent_backend` 按需创建轻量对象，`list_agent_backend_info` 返回以字典键为 ID 的基础信息。后端不使用单例、实例缓存、全量初始化或重载，BaseAgent 不持有 Graph 缓存，也不根据类名生成对外 ID。每次运行的 Context、Graph 和 middleware 持有本次状态，知识库增删无需重载后端。

未知后端抛出 `AgentBackendNotFoundError`。HTTP 接入与配置边界转换为 404；角色更新在任何字段修改或提交前解析后端，未知后端不会产生部分更新。worker 中的配置错误仍进入既有失败收敛流程。

预置 Agent 与子智能体各用一个 Python 模块导出 `AgentPreset`，递归按文件发现，子智能体定义放在 `subagents/`；现有 `agent_config_service.py` 在任何落库前确认全部后端存在，再交给 repository 初始化。已存在的角色保留定制，默认智能助手继续维护既有默认与共享约束。角色类型由 `backend_id` 推导，无需重复声明子智能体标志。初始化用例复用配置 service，不单独创建仅承载一个函数的 `builtin_agent_service.py`。

Skill 通过目录发现，SKILL.md frontmatter 唯一拥有名称、描述、版本和依赖；同步逻辑保留启停状态。发行包包含整个内置 Skill 目录，新增脚本与资源无需逐项登记打包规则。MCP 固定定义由独立 `mcp/builtin.py` 拥有，同步和执行逻辑直接消费同一字典；远程连接的权威在代码中。远程传输限制与 DeepWiki 定义见 [MCP 仅连接远程服务](2026-09-17-remote-only-mcp.md)。

## 替代方案

- keep：保留逐角色初始化、手工清单和后端管理器，继续承担重复登记及无效缓存的维护成本。
- narrow：只替换后端注册表或移动常量，不能消除失效重载与重复 Skill 元数据。
- replace：内容采用文件发现，执行能力采用显式定义，后端按需创建；不引入依赖或通用插件框架。
- remove：删除内置能力会破坏默认知识能力与现有用户配置；仅删除无真实运行消费者的缓存、初始化和重载机制。

## 后果

新增角色只需新增定义模块，新增 Skill 只需新增目录；执行后端和 MCP 的新增能力需要显式修改注册或固定定义。角色模块属于受审查的仓库代码，不执行外部操作。源码更新保留领域差异：角色首次创建，Skill/MCP 按原有规则同步，不能用统一覆盖策略替代用户定制。

既有角色 slug、后端 ID、权限与角色提示词保持不变。Skill frontmatter 合并使用原有效元数据，文件哈希发生变化；MySQL 报表和 MCP 的后续调整由远程 MCP 决策记录拥有。后端类名重命名不改变接口 ID；接口不再把未知后端误报为未捕获 KeyError。页面流程、Schema、执行图装配和配置迁移不在范围内。

## 验证

最终提交前验证：完整 unit 2180 passed、54 skipped；角色/压缩/队列/MCP HTTP integration 19 passed；MCP 安全 E2E 5 passed；主对话与子智能体 worker E2E 3 passed。完整 unit 首轮因旧预设导入路径收集失败，修正后一次运行在异步测试停滞而中断；相关 worker unit 独立运行 52 passed，完整重跑 30 秒全部通过。MCP E2E 首轮与 worker E2E 并行触发共享环境非终态 Run 检查，串行重跑通过。独立 Reviewer 对完整变更复核通过；Ruff、工程契约、62 项验证器单测、文档构建与 diff 检查通过。

- 初始化用例合并到 `agent_config_service.py` 后，相关配置/发现 unit 17 passed，`test/integration/services/test_builtin_discovery.py` 3 passed；验证未知后端阻止写入及重复初始化保留定制。

- `docker compose exec -e UV_CACHE_DIR=/tmp/yuxi-builtin-uv-cache api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：2172 passed、54 skipped。标准 uv 同步受容器缓存目录权限限制，使用已安装依赖执行，不将跳过项计为通过。测试覆盖独立后端对象、两个实际编译并执行的 Graph、稳定注册 ID、无导入期扫描或实例化，以及未知后端的领域错误。
- `docker compose exec -e UV_CACHE_DIR=/tmp/yuxi-builtin-uv-cache api uv run --no-sync --group test pytest test/integration/services/test_builtin_discovery.py test/integration/api/test_context_compression_router.py test/integration/services/test_agent_request_queue_concurrency.py -q`：12 passed。真实 PostgreSQL 与 HTTP 证明新增文件落库可见、重复初始化保留定制和停用状态、未知后端阻止任何预置角色写入；已有失效角色的查询和更新返回 404，独立数据库回读确认更新未生效。压缩测试显式初始化并关闭所需 PostgreSQL manager。
- `docker compose exec -e UV_CACHE_DIR=/tmp/yuxi-builtin-uv-cache api uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k 'test_deterministic_agent_path_reaches_persisted_result or test_subagent_worker_enforces_inherited_write_policy' -q`：两项子智能体用例通过；普通对话第二次请求的 SSE 被并行打包触发的 API 热重载中断。停止打包后以完整节点名 `test/e2e/test_deterministic_agent_path_e2e.py::test_deterministic_agent_path_reaches_persisted_result` 单独重跑通过。验证使用加载新代码的 worker，回读最终输出、权限结果和 Run 审计归属。源码目录打包与 E2E 必须串行，避免临时 Python 文件触发 WatchFiles。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：通过，验证器单测 62 项。
- `cd docs && pnpm run build`：通过；本次修改的 Python 文件通过 Ruff lint 与 format 检查，`git diff --check` 通过。
- 独立 Reviewer 从 `/tmp` 源码副本执行 `uv build`：sdist 与 wheel 构建成功，24 个最新角色、Skill、MCP、后端工厂、BaseAgent 与配置 service 文件逐字节一致，无 Python 字节码或已删除的初始化 service；包含 `subagents/` 子目录。
- 发现机制重构阶段对照 HEAD 核对 6 个角色、5 个 Skill 和 MCP 定义一致；随后 MySQL 报表与 MCP 按远程 MCP 决策调整。MCP 管理 HTTP 集成测试 7 项通过。

旧能力不存在：源码中已删除 AgentManager、执行后端目录扫描、隐式类名注册、实例缓存与 reload_graph；知识库接口不再调用后端重载。逐角色 ensure 方法、BuiltinSkillSpec、Skill 手工清单和服务内 MCP 定义字典均已移除，符号搜索无旧入口残留。

重新引入条件：只有出现拥有明确昂贵资源、隔离和释放需求的真实后端消费者，才重新讨论生命周期管理；只有不同落库规则的真实角色消费者才增加专属初始化。跨领域统一插件协议需要实际共享生命周期支撑。

未执行真实外部模型；模型验证使用确定性协议服务。MCP 远程验证由远程 MCP 决策记录说明。
