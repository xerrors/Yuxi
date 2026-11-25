# Agent runtime 的执行后端与沙盒边界

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/runtime/agent_backends/__init__.py

## 问题

智能体执行实现、沙盒能力、文件中间件与资源解析需要不同的维护边界。持久化 `backend_id` 引用执行后端，沙盒实现提供文件与命令能力；以来源命名执行实现、把多类职责聚合到通用后端目录，无法表达这两个层级。

## 决策

### 实现方案

`runtime/agent_backends/` 拥有执行实现与显式注册。`AGENT_BACKENDS` 注册稳定 ID `ChatbotAgent`。协作复用同一执行后端，见[会话协作决策](2026-10-03-session-cooperation.md)。配置和独立实例仍遵循[显式注册决定](2026-09-17-builtin-discovery.md)。

`runtime/sandbox/` 聚合沙盒执行、provider、Cube 适配、下载与 CompositeBackend 装配，虚拟路径由 Workspace 模块拥有。`runtime/middlewares/filesystem.py` 拥有文件工具注册与大结果处理策略，文件和摘要 middleware 仍共用本次构图创建的后端。Context 装配属于 `runtime/context.py`，知识库范围解析与工具执行授权由[知识库可见范围决定](2026-09-30-knowledge-access-resolution.md)部分取代本记录中的资源解析归属。

沙盒装配入口位于 `sandbox/backend.py`，运行作用域必须显式提供，具体取舍与验证由[沙盒环境决策](2026-10-15-sandbox-environment.md)维护。

共享 Skill 投影同步由 `extensions/skills/runtime.py` 调用已有投影发布用例。执行后端在构建运行图时检查 Context 已准备并刷新 Skill 投影，然后装配后端和图。刷新失败会阻止本次构图。

生产代码、测试、性能探针与当前文档使用新入口，旧运行包被删除。沙盒的用户环境、Workdir、runtime scope、路径授权与生命周期仍属于 Agent runtime。模型输入、持久配置、Run 调度、租约与副作用的执行规则保持原有语义。

## 替代方案

- 将两个旧目录整体改为 `agent_backends` 与 `tool_backends`：资源准备与 middleware 仍混杂。
- 将整个沙盒迁入 infrastructure：业务身份、路径与生命周期约束进入技术层，扩大变更范围。
- 将 Skill 刷新移入 Context 准备：准备结果有复用机制，会改变刷新频率和时机，需要独立的行为决定与验证。

## 后果

调用方直接导入职责所属模块，执行后端与沙盒能力可以分别定位。旧 Python 包路径不提供兼容转发，仓库内 consumer 与实验探针同步迁移；公开 backend ID 与既有持久数据保持兼容。非 Agent 模块复用沙盒能力时仍受当前身份和路径授权约束。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 稳定后端 ID、实例独立性与实际构图保持一致 | 路径迁移改变注册或状态复用 | runtime/agent_backends | 后端发现、配置、独立构图 unit | 未注册 ID 失败；独立图不能复用其他运行 Context | Passed |
| 沙盒路径与 middleware 的实际结果保持一致 | 拆分丢失路径授权或工具处理策略 | runtime/sandbox、runtime/middlewares/filesystem.py | sandbox unit；独立 sandbox 的 HTTP 与模型 ToolMessage integration | 越界路径、禁用工具、身份不匹配及输出限额案例 | Passed |
| Skill 每次构图仍在模型执行前刷新 | 准备结果复用跳过刷新；发布失败后继续执行 | extensions/skills、agent_backends | 同一个 prepared Context 两次构图，回读投影文件与实际图输出 | 发布失败阻止构图 | Passed |
| 当前代码与文档使用新入口 | 旧导入、monkeypatch、性能探针或源码链接遗留 | 调用方、当前文档 | 旧引用搜索、工程契约、docs build、独立 Review | 旧运行包不存在；归档记录保留冻结历史 | Passed |
| 真实 API、PostgreSQL 与 worker 主链路保持行为 | 单测掩盖服务装配问题 | Agent services、API/worker 装配 | live API integration 与确定性 E2E | 原有真实状态回读案例 | Not run |

- `docker compose exec -T api uv run --group test pytest test/unit -m "not slow"` 所用依赖同步阶段无法更新时间戳 `yuxi.egg-info`，实际使用 `uv run --no-sync --group test` 执行已有依赖环境。
- 隔离副本执行 `docker compose exec -T -e PYTHONPATH=/tmp/yuxi-runtime-boundaries-root/backend -w /tmp/yuxi-runtime-boundaries-root/backend api uv run --project /app --no-sync --group test pytest test/unit -m 'not slow' -q --tb=short -p no:cacheprovider`：2298 passed、3 failed。同一镜像中的纯 HEAD 副本执行对应全量命令：2296 passed、相同 3 failed。失败为两项 DOCX 解析 fixture 与一项空工具依赖的 Skill middleware fixture，均在未修改基线复现；新增两项构图刷新案例通过。
- 隔离副本的相关集合执行 `pytest test/unit/agents test/unit/sandbox test/unit/services/test_agent_preparation.py test/unit/middlewares/test_summary_middleware.py test/unit/middlewares/test_subagent_task_middleware.py test/unit/middlewares/test_tool_error_guard.py -q --tb=short -p no:cacheprovider`：470 passed。执行环境与全量命令相同。当前工作树执行 `docker compose exec -T api uv run --no-sync --group test` 下的同一集合也为 470 passed。
- 独立、无网络、无用户挂载的 sandbox 1.11.0 中执行 `python -m pytest test/integration/sandbox/test_sandbox_native_grep.py --confcutdir=test/integration/sandbox -q -p no:cacheprovider --tb=short`：1 passed，回读真实文件匹配和模型 ToolMessage。测试进程与临时 sandbox 共用网络命名空间，使用 `TEST_SANDBOX_URL=http://127.0.0.1:8080`，完成后删除临时容器。
- `python3 scripts/verify_engineering_contracts.py` 在本次改动的隔离副本通过；`python3 -m unittest scripts.test_verify_engineering_contracts`：64 passed。`cd docs && pnpm run build` 通过。受影响文件的 `ruff check` 与本次补丁的 `git diff --check` 通过。当前工作树的全局 gate 受其他并行任务的解析器决策与补丁格式问题影响，不能声明全局通过。
- live API integration 执行 `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_builtin_discovery.py test/integration/services/test_project_workdir_provisioner.py test/integration/services/test_user_skill_projection.py test/integration/api/test_agent_config_resource_authorization.py -q --tb=short -p no:cacheprovider` 共 14 项，均因 API 请求超时在 fixture 阶段报错，未产生业务断言证据。`/api/system/ready` 请求同样超时；确定性 worker E2E 未执行。真实服务集成验证仍需在服务恢复后补齐。
- 独立 Reviewer 审查完整需求、diff、测试与规范，并以五种独立进程导入顺序核对入口，未发现源码功能回归；虚拟路径 Owner 文案与决策状态按审查要求收敛。
