# 测试规范

测试的目标是证明用户可观察的结果和工程边界，而不是堆积测试数量。先选最接近风险的最小测试集，再按改动范围扩大；单元测试不能代替真实 HTTP、数据库、worker、文件或浏览器验证。

## 测试分层

| 层级 | 目录 | 适合验证什么 | 环境 |
| --- | --- | --- | --- |
| Unit | `backend/test/unit` | 纯逻辑、边界值、状态转换和失败分支 | 不依赖运行中的 Docker 服务 |
| Integration | `backend/test/integration` | 真实 HTTP、认证、事务、锁、Schema、lease 和服务副作用 | 依赖 Docker Compose |
| E2E | `backend/test/e2e` | Run、SSE、worker、文件落盘和完整用户链路 | 依赖完整 Compose，数量少、速度慢 |
| Web unit | `frontend/test/unit` | 前端状态、组件和交互逻辑 | 通过 `pnpm test:unit` |
| CLI | `packages/yuxi-cli/test` | npm CLI 配置、命令、Public v1 客户端和协议逻辑 | Node.js 22+，静态测试不依赖运行中的服务 |

同一个子项目只保留一个测试根目录，不要同时创建 `test` 和 `tests`。

高风险 Agent 主链路优先使用不依赖外部密钥的 deterministic assembled-path E2E；真实模型、浏览器和外部服务用于手工或周期探针。E2E 需要经过实际 API、worker、SSE 和最终持久化事实，不能用进程内 monkeypatch 代替。

## 如何选择目录

- 只调用纯 Python 逻辑、fake repository 或临时目录：放 `unit`。
- 要验证真实接口、认证、事务或 Redis/PostgreSQL 边界：放 `integration`；API 测试放 `integration/api`。
- 要从入口一路验证到最终 Run、文件或对象：放 `e2e`。
- 前端和 CLI 测试留在各自项目的测试根，不放到 backend。

不要因为测试文件少或执行快就把 integration 降成 unit；测试层级反映它依赖的真实边界。

新增测试时说明它能独立发现什么失败，优先扩展已有参数化用例；既有证据已覆盖目标时可以不新增测试。不同层只补充各自独立的边界，不为相同断言重复堆叠用例。删除测试时说明替代证据或已退役的契约；长期全绿本身不足以证明测试可以删除。覆盖率用于定位未观察区域，测试数量和覆盖率不作为增长目标。

前端行为测试通过现有 `frontend/test/helpers/createTestServer.js` 加载完整模块或 Vue 组件，替身放在 API、浏览器和其他外部依赖边界；需要客户端模板时正常编译完整 SFC。不要截取源码函数、删除 import 或改写编译结果再执行。静态资源、样式和架构规则仍检查对应静态事实；SSR 和自定义 renderer 的断言不冒充真实浏览器验证。多个测试需要相同业务装配时复用领域 fixture，避免建立第二套通用测试框架。

## 命名和结构

文件名使用 `test_<domain>_<target>.py`，一个文件围绕一个清晰主题。测试函数使用 `test_<行为>_<预期结果>`，名称直接表达业务语义：

```text
test_create_agent_run_commits_before_enqueue
test_viewer_download_returns_attachment_response
test_agent_bubble_sort_run_creates_expected_artifacts
```

测试尽量保持 Arrange → Act → Assert 三段结构：

1. 准备数据、fixture 和外部条件；
2. 调用真实被测行为；
3. 断言业务结果、状态和副作用。

不要只断言 `status_code == 200`。根据风险回读数据库行、文件、对象、DOM、SSE 游标或协议 payload。失败信息应指出目标和实际值。

每个新 guard 都要有负向案例：恢复目标缺陷或制造非法状态后，测试必须因正确原因失败。Fixture、snapshot 和 expected output 只能显式更新并进入 diff，CI 不得一边生成 oracle 一边验证它。

关键 guard 应定向注入目标故障并确认正确原因导致失败。偶发失败要定位责任并修复，保留可重放输入或状态；不能用重试掩盖失败，也不能隔离掉核心契约的唯一证据。

## Fixture 和测试数据

- 同一文件内复用的准备逻辑优先写本地 helper；多个文件需要时再放对应层级的 `conftest.py`。
- 通用 marker 由 `backend/pyproject.toml` 注册；`backend/test/conftest.py` 只装配测试路径和插件，不绑定真实服务。
- integration fixture 负责创建 `test_client`、测试用户和测试资源；不要依赖数据库里碰巧存在的 Agent、模型或知识库。
- integration 默认执行共享 schema 检查和 API/沙盒清理。仅当用例不需要这些共享 session fixture，且自行创建并回收资源时，才可显式声明 `self_managed_resources` 豁免这些依赖；它不承诺独立数据库 schema。混合批次只要包含未标记用例，仍执行共享检查和清理。
- E2E fixture 负责真实入口、账号和资源清理；测试结束后删除自己创建的对话、文件、Run 和外部对象。
- 不在测试或文档中写真实账号、密码、Token、用户数据和本地绝对路径。

### 核心输入链路的前提与等待

输入队列、附件、Skill 授权、取消和恢复用例在准备代码旁明确初始状态、建立入口、回读事实和清理 Owner。公开输入通过 HTTP 建立真实消费前提；需要停在消费前故障窗口的服务测试使用自己的隔离 Schema。普通用户和 Key 使用本例明确授权的 Agent 与 bound Skill；内置发布内容测试以正式 app-init 产物为对象。

复用 `e2e_helpers.py` 的 Input 消费、Turn 终态与 replay 阻塞点等待。Turn 的 `completed`、`failed`、`cancelled` 都是等待结果，由调用方断言本场景预期；删除资源前另用 `live_api_cleanup.py` 的 `validate_test_runs_terminal` 检查历史 Run 的运行时清理。等待总预算覆盖 HTTP 和数据库请求，超时只报告目标 ID 与最后状态。

跨进程文件先写同目录临时文件，完成后原子改名；存在性信号只证明标记已经发布。模型阻塞点使用 replay 的 started/release 协议，解除本例阻塞放在 `finally`。固定 sleep 仅用于轮询间隔或明确验证的缓存 TTL、空闲暂停、取消后持续无写入等时间性质。业务结果继续从 HTTP 投影、数据库、文件或对象字节独立断言。

## skip 规则

只在以下情况使用 `pytest.skip`：

1. 外部可选服务确实未提供，例如 OCR 或真实模型服务；
2. E2E 所需的测试账号或环境变量没有配置。

“系统没有默认数据”不是 skip 理由。用 fixture 显式创建资源，或者让测试失败暴露环境问题。不要用 `print`、日志关键词或 `if __name__ == "__main__"` 判断测试结果。

## 修改 Bug 或既有功能

修复 Bug：

1. 先补一个稳定复现原问题的测试；
2. 再修改实现；
3. 先运行最小相关测试；
4. 再运行受影响层级的回归测试。

修改既有行为时，同时更新正向和负向断言。涉及 API、权限、持久化、队列、SSE、沙盒或恢复时，按风险升级到真实 integration 或 E2E。

## 常用命令

先启动开发环境：

```bash
docker compose --profile container up -d
docker compose --profile container ps
docker compose --profile container logs --tail=100 api
```

默认 Container 需启用 `container` profile；显式选择 Cube 时去掉该参数并先准备执行节点。`make up` 同样默认启用 Container，Cube 可用 `make COMPOSE="docker compose" up`。

后端按 unit、integration/api、其余 integration 子树、E2E 分批运行。各套件的模块和 helper 按当前目录加载；脚本的 `integration` 与 `all` 同样分批执行。确定性 E2E 的用例集合由 `backend/test/run_tests.sh` 唯一维护，CI 复用此入口，避免重复执行相同文件；`e2e-cooperation` 与 `e2e-core` 把同一集合拆成两个并行拓扑的子集，`e2e` 仍等于二者之和。涉及不兼容 Schema 时，先使用[独立槽位](./parallel-worktree-environments.md)装配完整环境，再运行测试；脚本只负责选择和执行套件。

后台作业启动恢复由 `system-tests.yml` 的现有拓扑步骤单独验证：先停止 worker，保存未发布 intent，再启动 worker 并回读领域结果。`test_background_job_worker_path.py` 的这组跨进程阶段不进入普通 integration 批次；通用脚本不会重启正在使用的服务。完整系统验证须同时完成该拓扑步骤。

```bash
docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"
docker compose exec api uv run --no-sync --group test pytest test/integration/api
docker compose exec api uv run --no-sync --group test pytest test/integration --ignore=test/integration/api --ignore=test/integration/services/test_background_job_worker_path.py
backend/test/run_tests.sh e2e
backend/test/run_tests.sh e2e-cooperation  # 只运行会话协作子集（CI 独立拓扑并行）
backend/test/run_tests.sh e2e-core         # 只运行其余生命周期子集（CI 独立拓扑并行）
backend/test/run_tests.sh all
```

Compose API 容器只挂载后端目录时，部署配置测试会因缺少仓库根文件而跳过。这些用例由包含根目录的 CI 单测环境执行；本地须从宿主仓库根目录补跑，不能把容器的 skip 当作部署契约通过：

```bash
uv run --project backend --no-sync --group test pytest backend/test/unit/config -q
```

应用共享初始化使用真实 PostgreSQL 隔离 Schema 和临时 Skill 目录验证首次部署、当前版本重跑、管理员值保留、阶段失败重试与并发锁。独立子进程覆盖已有 Schema 的冷启动，并用缺少 ORM 注册的负控复现失败；这些用例接入 `service-integration.yml`：

```bash
docker compose exec api uv run --no-sync --group test pytest test/integration/services/test_app_initialization.py test/integration/services/test_schema_migration_version.py -q
```

模型重放服务由测试环境启动；依赖共享重放的场景显式读取 `OPENAI_REPLAY_URL` 与 `COOPERATION_REPLAY_URL`。需要独占状态的场景复用 `e2e/conftest.py` 已有的重放 fixture，避免在测试文件内重复启动和回收服务器。

默认 Compose 测试环境分别启动 `test/support/openai_replay_server.py` 与 `test.support.cooperation_replay_server` 后，在宿主导出 worker 可访问的地址；测试脚本把它们和测试账号转发给容器。CI 在现有环境生成步骤写入同样的值：

```bash
export OPENAI_REPLAY_URL=http://api:8765/v1
export COOPERATION_REPLAY_URL=http://api:8766/v1
```

也可以从仓库根目录使用脚本：

```bash
backend/test/run_tests.sh unit
backend/test/run_tests.sh integration
backend/test/run_tests.sh e2e
backend/test/run_tests.sh e2e-all  # 显式运行真实模型与外部服务探针
backend/test/run_tests.sh all
```

前端：

```bash
docker compose exec frontend pnpm run lint:check
docker compose exec frontend pnpm run test:unit
docker compose exec frontend pnpm run build
```

CLI：

```bash
cd packages/yuxi-cli
npm ci
npm run typecheck
npm test
npm run pack:check
```

工程契约、文档构建和补丁检查：

```bash
python3 scripts/ci/verify_engineering_contracts.py
python3 -m unittest scripts.ci.test_verify_engineering_contracts
cd docs && pnpm run build
git diff --check
```

依赖供应链检查：

```bash
make audit-dependencies
make audit-licenses
```

Docker 镜像与挂载权限回归在仓库宿主执行，需要可用的 Docker Engine。测试使用临时合成文件与独立 Compose project，不挂载生产数据；受限构建输入、历史文件保留、只读存储失败与负向镜像均由真实容器结果验证。API 容器无需访问 Docker daemon。

```bash
python3 -m unittest scripts.ci.test_docker_build_context scripts.ci.test_container_permissions
```

Windows 初始化脚本的安全行为需要在 Windows 或 PowerShell 7 环境中验证：

```powershell
pwsh -NoProfile -File scripts/ci/test_init_security.ps1
```

这些命令的实际 workflow 和 selector 由仓库 `.github/workflows` 与 `Makefile` 维护；文档不复制一份会漂移的 CI 配置。

## CI 环境与依赖

常规质量检查和独立服务集成测试使用 GitHub 托管执行器，无需维护公网服务器。CI 准备、检查、探针与测试集中在 `scripts/ci/`；用户初始化、拉镜像和版本入口留在 `scripts/`，评估脚本位于 `scripts/eval/`。测试选择器由对应 workflow 和脚本拥有：

PR 由无路径过滤的 `.github/workflows/ci.yml` 调用可复用质量工作流，粗粒度选择由 `scripts/ci/gate.py` 拥有；共享或无法分类的变更保守扩大检查。各工作流通过 `workflow_call` 复用既有测试主体，保留原有 push、tag 和手动入口。汇总检查名为 `CI required`，选择失败、必需结果缺失、失败、取消或意外跳过均不能通过。仓库接线本身不启用 GitHub required 规则：须先部署 workflow 并产生真实检查，再配置规则并验证远端行为。

顶层选择系统测试时，五个系统 job 执行完整测试，并由 `check-jobs` 汇总；缺失、失败、取消或意外跳过均不能通过。四个拓扑 job（后台作业与检索、会话协作 E2E、生命周期与 worker E2E、运行时契约）各自构建镜像并持有独立 Compose 拓扑，共享镜像层缓存；沙盒容器隔离 job 在宿主机只复用 sandbox 镜像，不启动拓扑。push、tag 和手动入口沿用各自触发条件。

测试运行脚本在显式设置 `CI_TEST_REPORT_DIR` 时输出独立 JUnit 报告和慢测试信息；目录相对于 pytest 进程的工作目录，不设置时原有命令行为不变。workflow 直接调用 pytest 时显式传入独立报告路径。CI 测试报告 artifact 保留 7 天，失败与 skip 仍须分别审查。

| 检查范围 | 实际依赖 | 执行入口 |
|---|---|---|
| 工程契约、Ruff、后端 unit、前端、文档、依赖审计 | 执行器上的语言工具链 | `.github/workflows` 中对应质量工作流 |
| 初始化、Schema/关系约束、Input/FIFO、Run lease、反馈、协作持久化、OAuth 和局部 HTTP | 临时 PostgreSQL、Redis、文件与测试自建 HTTP 服务 | `service-integration.yml` → `scripts/ci/run_service_integration.sh` |
| 附件服务持久化与对象读写 | PostgreSQL、MinIO 与文件系统；测试主体无需 VM | `system-tests.yml` 中附件服务步骤，随完整拓扑执行 |
| API readiness、正式 worker 启动恢复、Agent E2E 和沙盒隔离 | 完整 Compose 拓扑、container runner 与 sandbox 镜像 | `system-tests.yml` 的四个拓扑 ubuntu-24.04 job 与沙盒隔离 job，使用 `container` profile，执行完整测试 |
| 真实供应商 Agent SSE 与持久结果 | 临时托管执行器、完整 Compose 拓扑和 `SILICONFLOW_API_KEY` 仓库 Secret | `real-provider-probe.yml`，仅手动运行 |
| GitHub 托管机器的 KVM/Cube 兼容性探针 | KVM、临时 XFS、Cube 安装、正式模板和共享挂载 | `cube-hosted-probe.yml`，仅手动运行 |

真实供应商探针在明确的外部配额或限流错误时会跳过用例；必须核对 pytest 结果，不能把跳过当作真实模型通过。

独立服务 job 使用一次性数据库，运行应用初始化并创建基线测试管理员后执行已有集成测试；显式选择 container 配置，共享 API、container runner 与 Cube 地址固定为不可达，用真实数据库结果证明独立性。该入口会写入数据库及 Skill 目录，本地复用时必须提供独立的 `POSTGRES_URL`、`REDIS_URL` 与 `YUXI_SKILL_DATA_DIR`，并使用没有 `backend/.env` 的干净 checkout，禁止指向运行中的业务环境。基线管理员保留在临时库中，测试账号的创建、修改和清理继续受数据库的最后管理员约束保护。

公开附件 HTTP 用例中的待消费快照、取消与删除边界使用真实 worker 和阻塞模型重放，随完整 container 拓扑执行；模型重放服务在这些用例之前启动。它们与仅验证持久化和对象读写的附件服务测试具有不同依赖。

`self_managed_resources` 只描述 fixture 清理责任，不绕过 API/worker 的沙盒启动检查。常规系统 job 通过环境准备脚本显式选择 container 后端、启用对应 Compose profile 并生成临时 runner 令牌；API、worker 和 runner 使用同一物理 Workspace 与 Skill projection，只有 runner 持有 Docker socket。正式 sandbox 镜像参与独立层缓存，状态位于执行器临时目录。生产默认后端仍由 Compose 配置决定。

container 专项包括 API/worker 的真实 execute 与 steer 后容器清理，以及宿主进程通过真实 Docker 验证文件、进程、资源和认证隔离。宿主专项显式启用 `SANDBOX_CONTAINER_TEST=1`，使用本次 Compose 配置选择的镜像；它的临时文件根与业务 runner 分离，不给 API 容器挂载 Docker socket。协作 E2E 对 container 使用共享 Project 文件作跨 Run 探针，对 Cube 保留共享 VM 临时文件与执行身份断言；全部协作、取消、压缩和持久结果检查仍执行。

Cube 实现的 unit 继续运行；真实 KVM、Cube 安装、正式模板与共享挂载由手动 hosted probe 和 Cube 专属测试验证。常规 container CI 的成功不能解释为 Cube VM 兼容性通过。托管探针仅允许在 GitHub 临时托管执行器运行，其成功只证明该次执行器的兼容性。CI 取舍见[container 运行链路决定](decisions/implemented/2026-10-23-container-runtime-ci.md)。

## 网络搜索与正文工具

设置持久化与权限由 `integration/api/test_web_tools_settings.py` 在真实 HTTP、独立 PostgreSQL Schema 上验证；`integration/services/test_web_provider_protocol.py` 启动独立 TLS 重放进程验证固定官方端点上的协议传输。供应商重放属于确定性证据，不能代替带真实凭据的外部探针。

worker 全链路在独立测试槽位叠加协议重放拓扑：

```bash
docker compose -f docker-compose.yml -f backend/test/e2e/web-tools.compose.yml up -d web-provider-replay api worker
docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api uv run --no-sync --no-dev pytest test/e2e/test_web_tools_e2e.py -q
```

该拓扑仅用于没有已保存搜索 Key 的隔离测试环境。它生成短期测试 CA，API 与 worker 的 HTTPS 请求由重放服务提供三家 Search 和两家 Fetch 固定结果；测试通过真实 Session SSE、worker 与持久消息确认配置切换，结束后清除自己保存的 Key。`system-tests.yml` 显式运行此测试，生产拓扑不使用该 CA 或代理。

## 性能评测

性能工具位于 `backend/test/performance/`；参数、采样和探针的单测位于 `backend/test/unit/performance/`，由常规后端 unit 命令执行。仓库根目录使用同一个模块入口，Python 环境需要后端依赖：

```bash
python -m backend.test.performance --help
python -m backend.test.performance matrix --help
python -m backend.test.performance load --help
python -m backend.test.performance report tmp/load-tests/continuous/final-20260907/matrix.json
docker compose exec api uv run --no-sync --group test pytest test/unit/performance -q
```

`matrix` 测不同用户、固定 Thread、完成后立即补位的闭环调度；`load` 保留通用对话与沙盒容量场景，协议不同，不能混算。两者执行采样会产生真实模型费用。默认矩阵为 3150 请求，小实验通过 `--workers`、`--concurrency`、`--rounds-per-thread` 显式缩减，不自动预热。认证变量、独立槽位与结果边界见[并发优化决策](decisions/archived/0.7.3/12-concurrency/2026-09-07-agent-concurrency-optimization.md)。

矩阵只在[隔离槽位](./parallel-worktree-environments.md)运行。先导出槽位变量、测试认证变量和 `MATRIX_FINE_TIMING`，用 `docker compose -f docker-compose.yml -f backend/test/performance/compose.yml up -d --no-deps api` 装配实验 API；矩阵命令按 `--workers` 重建实验 Worker。采样结束或中断后，用普通 Compose 的 `up -d --no-deps --force-recreate --scale worker=1 api worker` 恢复普通入口。探针属于实验装配，不进入 shipping 启动。

`report` 默认只读已有样本，在相同目录生成 `stages.json` 与 `report.md`，不访问容器或模型，不改原始样本；仅在实验容器仍运行时显式使用 `--refresh` 补齐日志，并另外保存 `complete.json`。本地派生报告不替代决策记录中的可审阅结果。

## 证据和报告

测试结果必须说明：

- 实际执行的完整命令；
- 通过、失败或未执行；
- 失败时的环境和影响；
- 外部服务、凭证或浏览器未覆盖的范围；
- 需要回读确认的最终状态、文件或协议结果。

`Passed` 只表示命令成功且结果已核对；`Not run` 必须说明原因。HTTP 200、任务完成提示、日志关键词、mock 调用次数和 Agent 自述都不能单独形成完成证据。

## 提交前检查

- 测试位于正确层级，且名称表达行为。
- 断言了业务结果和关键副作用，而不是只断言 HTTP 状态码。
- 新增 guard 有能恢复目标缺陷的负向案例。
- fixture 不依赖共享默认数据，测试数据会清理。
- skip 有明确的可选外部依赖或缺失环境变量原因。
- 真实 API、数据库、worker、文件、对象或浏览器语义已经按风险验证。
- expected output、fixture 和 snapshot 的更新经过人工审阅。
- PR 如实记录命令、结果和未验证范围。

相关规范：[参与贡献](./contributing.md)、[工程信任系统](./engineering-trust.md)、[Yuxi Spec Loop](./spec-loop.md)。
