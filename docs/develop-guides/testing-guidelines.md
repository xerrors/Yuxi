# 测试规范

测试的目标是证明用户可观察的结果和工程边界，而不是堆积测试数量。先选最接近风险的最小测试集，再按改动范围扩大；单元测试不能代替真实 HTTP、数据库、worker、文件或浏览器验证。

## 测试分层

| 层级 | 目录 | 适合验证什么 | 环境 |
| --- | --- | --- | --- |
| Unit | `backend/test/unit` | 纯逻辑、边界值、状态转换和失败分支 | 不依赖运行中的 Docker 服务 |
| Integration | `backend/test/integration` | 真实 HTTP、认证、事务、锁、Schema、lease 和服务副作用 | 依赖 Docker Compose |
| E2E | `backend/test/e2e` | Run、SSE、worker、文件落盘和完整用户链路 | 依赖完整 Compose，数量少、速度慢 |
| Web unit | `web/test/unit` | 前端状态、组件和交互逻辑 | 通过 `pnpm test:unit` |
| CLI | `packages/yuxi-cli/tests` | CLI 配置、命令和客户端行为 | 独立 Python 包 |

同一个子项目只保留一个测试根目录，不要同时创建 `test` 和 `tests`。

高风险 Agent 主链路优先使用不依赖外部密钥的 deterministic assembled-path E2E；真实模型、浏览器和外部服务用于手工或周期探针。E2E 需要经过实际 API、worker、SSE 和最终持久化事实，不能用进程内 monkeypatch 代替。

## 如何选择目录

- 只调用纯 Python 逻辑、fake repository 或临时目录：放 `unit`。
- 要验证真实接口、认证、事务或 Redis/PostgreSQL 边界：放 `integration`；API 测试放 `integration/api`。
- 要从入口一路验证到最终 Run、文件或对象：放 `e2e`。
- 前端和 CLI 测试留在各自项目的测试根，不放到 backend。

不要因为测试文件少或执行快就把 integration 降成 unit；测试层级反映它依赖的真实边界。

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

## Fixture 和测试数据

- 同一文件内复用的准备逻辑优先写本地 helper；多个文件需要时再放对应层级的 `conftest.py`。
- `backend/test/conftest.py` 只保留通用 marker，不绑定真实服务。
- integration fixture 负责创建 `test_client`、测试用户和测试资源；不要依赖数据库里碰巧存在的 Agent、模型或知识库。
- E2E fixture 负责真实入口、账号和资源清理；测试结束后删除自己创建的对话、文件、Run 和外部对象。
- 不在测试或文档中写真实账号、密码、Token、用户数据和本地绝对路径。

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
docker compose up -d
docker compose ps
docker compose logs --tail=100 api
```

后端：

```bash
docker compose exec api uv run --group test pytest test/unit -m "not slow"
docker compose exec api uv run --group test pytest test/integration
docker compose exec api uv run --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -m e2e
docker compose exec api uv run --group test pytest test
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
docker compose exec web pnpm run lint:check
docker compose exec web pnpm run test:unit
docker compose exec web pnpm run build
```

CLI：

```bash
cd packages/yuxi-cli
uv run pytest
```

工程契约、文档构建和补丁检查：

```bash
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
cd docs && pnpm run build
git diff --check
```

依赖供应链检查：

```bash
make audit-dependencies
make audit-licenses
```

Windows 初始化脚本的安全行为需要在 Windows 或 PowerShell 7 环境中验证：

```powershell
pwsh -NoProfile -File scripts/test_init_security.ps1
```

这些命令的实际 workflow 和 selector 由仓库 `.github/workflows` 与 `Makefile` 维护；文档不复制一份会漂移的 CI 配置。

## 性能评测

性能工具位于 `backend/test/performance/`；参数、采样和探针的单测位于 `backend/test/unit/performance/`，由常规后端 unit 命令执行。仓库根目录使用同一个模块入口，Python 环境需要后端依赖：

```bash
python -m backend.test.performance --help
python -m backend.test.performance matrix --help
python -m backend.test.performance load --help
python -m backend.test.performance report tmp/load-tests/continuous/final-20260907/matrix.json
docker compose exec api uv run --group test pytest test/unit/performance -q
```

`matrix` 测不同用户、固定 Thread、完成后立即补位的闭环调度；`load` 保留通用对话与沙盒容量场景，协议不同，不能混算。两者执行采样会产生真实模型费用。默认矩阵为 3150 请求，小实验通过 `--workers`、`--concurrency`、`--rounds-per-thread` 显式缩减，不自动预热。认证变量、独立槽位与结果边界见[并发优化决策](./decisions/implemented/2026-09-07-agent-concurrency-optimization.md)。

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
