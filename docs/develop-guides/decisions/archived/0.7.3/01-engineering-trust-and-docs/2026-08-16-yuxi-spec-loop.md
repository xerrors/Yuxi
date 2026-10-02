# Yuxi Spec Loop 工程闭环

状态：archived
类型：process
Owner：docs/develop-guides/spec-loop.md

## 问题

Yuxi 已有语义 Owner、decision lifecycle、测试分层、负向案例和独立 Review 约束，但非平凡变更仍可能在实现后才补决定；验收证据散落在散文中；高风险 assembled path 缺少稳定的无密钥 E2E 门禁；简化和高影响逃逸事故也缺少可直接执行的收敛载体。单独存在的规则不能保证从问题到证据、从失败到学习形成闭环。

## 决策

Yuxi 使用 [Spec Loop](../../../../spec-loop.md) 统一 Scope/classify、权威重建、Propose、assembled-path 实现、Verify、独立 Review、Converge 和 Learn。任务类型固定为 `feature`、`bug-fix`、`simplification`、`architecture`、`process`、`testing`；非平凡变更在实现前创建 tracked proposed decision，证据收敛后改写为 implemented。

Proposed decision 和 PR 使用同一组验收字段——验收主张、失败面、语义 Owner、直接 oracle/命令、负向案例和 `Passed` / `Inspected` / `Not run` / `Inferred` 结果；决策记录以证据矩阵表呈现，PR 模板以逐条分组呈现。Verifier 只检查确定性结构、结果词、Owner 和 workflow 接线，不代替语义 Review。

Agent 创建 PR 时使用默认严格模板；人工或其他非 Agent 提交可以显式选择简化模板，只保留变更、验证、风险和关联事项。模板分层不免除非 trivial / 高风险变更在贡献指南中的 decision、直接证据和未验证范围要求。

`system-tests.yml` 在相关 PR 上运行无外部密钥的 OpenAI-compatible deterministic replay，真实经过 Compose API、worker、SSE 和 PostgreSQL 因果回读；`real-provider-probe.yml` 由 `workflow_dispatch` 使用仓库 secret 手工校准真实 provider。`simplification` 记录包含旧能力不存在的负向搜索和重新引入条件。达到门槛的高影响逃逸事故使用 `docs/develop-guides/postmortems/` 的 Owner 与模板。

本决定不改变 AgentRun、lease、retry、manifest、attempt 或外部副作用的运行时语义；相关演进分别由 xhome 待办承接。

## 替代方案

- 复制 ds-spec-loop 的 `.agents/notes`：与现有 decision lifecycle 重复并形成第二套组织记忆。
- 建立中央 claim/risk registry：复制代码、数据约束、测试和 workflow 已拥有的事实，产生可独立漂移的第二真相。
- 只扩充文档、不接 gate 与真实 E2E：无法给违规产生稳定后果。
- 用 diff heuristic 自动判定 substantial/trivial：无法可靠判断语义风险，保留为 Reviewer 责任。

## 后果

非平凡工作增加一个实现前决策点和逐主张证据记录，但小而完整、没有待裁决替代或风险的同变更修复仍可解释后直接 implemented。Deterministic replay 提供低噪声 PR 阻断，却不证明外部 provider 自然语言行为；真实 provider 探针缺少 secret 时明确失败，未执行时记录 `Not run`。

Decision 类型、proposed 矩阵、simplification 删除标签、postmortem 模板和两个 E2E workflow 都成为 verifier 的派生契约并各有负向测试。业务语义仍由最接近风险的代码、数据库、协议结果和 Reviewer 拥有。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| Decision 类型、证据矩阵、simplification、postmortem 与 workflow 漂移会被拒绝 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts` | Passed：5 workflows；48 tests |
| 无密钥 Agent 主链路经过 API、worker、SSE 并回读同一 Run 的 PostgreSQL output | `docker compose exec -T api uv run --no-sync --no-dev pytest test/e2e/test_deterministic_agent_path_e2e.py -q` | Passed：2 tests |
| 新增 E2E/replay Python 代码满足静态检查 | `docker compose exec -T api uv run ruff check test/e2e/test_deterministic_agent_path_e2e.py test/support/openai_replay_server.py` | Passed |
| 正式文档与导航可构建 | `cd docs && pnpm run build` | Passed；仅有既有 Rolldown、env lexer 与 chunk warnings |
| Workflow YAML 可解析 | Ruby stdlib YAML 逐文件读取 `.github/workflows/*.yml` | Passed；`actionlint` Not run（本机未安装） |
| 全量 backend unit inventory | `docker compose exec -T api uv run --group test pytest test/unit -m 'not slow'` | Not run：收集 1279 项后约 14% 时进程 exit 137，不能给出全套结论 |
| 真实 SiliconFlow shipping probe | GitHub Actions `Real Provider Agent Probe` | Not run：本地不使用仓库 secret；workflow 接线与凭证负控已 Inspected |

全新上下文 Reviewer 审查了需求、完整 diff、测试和规范，没有 P0/P1；其 5 个 P2（手工 probe 契约、replay 请求校验、失败清理、空/非法证据矩阵、decision Owner）均已修复并由新增负控或 E2E 覆盖。
