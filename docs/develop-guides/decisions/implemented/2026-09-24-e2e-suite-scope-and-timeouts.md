# 收敛常规 Agent E2E 范围与等待预算

状态：implemented
类型：testing
Owner：backend/test/e2e/e2e_helpers.py

## 问题

常规 E2E 入口曾混合确定性 Run 链路、接口集成检查和真实模型探针；部分测试项连续执行多个 Run，每次等待重新获得 240 秒预算。轮询 HTTP 请求本身可等待 300 秒，超出 Run 期限。单项耗时和失败定位因此不清晰。

## 决策

- `backend/test/run_tests.sh e2e` 与 `all` 只选择确定性 Agent E2E；`e2e-all` 明确运行包括外部模型和可选服务在内的完整探针集。测试规范拥有分层说明，外部探针仍保留在测试树中。
- 附件上传、确认、列表的接口断言由 `backend/test/integration/api/test_chat_router.py` 覆盖，删除原 API-only E2E。replay 协议拒绝条件改由 unit 直接检查。
- 重试矩阵缩为两种配置、共三个真实 Run：普通 Agent 在首次调用失败后验证同线程后续派发，子 Run 在工具后失败并由父 Run 消费。附件场景只提交一个 Run，通过显式释放 runtime 验证文件跨实例保留。保留正常 Run、同线程审计因果、执行限制、定时 Run、恢复、取消、工具错误、SubAgent 策略与独立可见性。
- `wait_for_run` 的状态请求同时受剩余 Run deadline 和 10 秒单请求上限约束；确定性 pytest 项有 360 秒整项上限。
- `.github/workflows/system-tests.yml` 顺序运行 smoke、lifecycle、boundaries 三阶段，各有 step timeout；额外收集步骤拒绝未归属的确定性场景。工程契约检查登记三个实际阻断步骤。

## 替代方案

- 只提高 CI job timeout：单项仍可能累计多个 Run 预算。
- 所有 E2E 并行：固定 replay Provider 和共享清理账号会竞争。
- 把全部链路降到 integration：无法证明真实 worker、SSE、checkpoint 和文件边界。

## 后果

常规入口不再隐式调用外部模型，失败步骤能指出主要场景。三阶段仍顺序复用一个 Compose 栈，不共享并行数据库。端到端用例数量减少，但仍保留完整跨进程主链路；产品代码和持久化格式不变。

旧能力不存在：附件接口专用 E2E、replay HTTP 自检 E2E、四组重试矩阵与附件用例中的固定 keepalive 等待已移除。

重新引入条件：现有 integration、unit 和保留的跨进程 E2E 无法检测一项真实新回归，且新增用例提供独立的结果 oracle。

## 验证

- `docker compose exec -T api uv run --no-sync --no-dev pytest test/e2e/test_deterministic_agent_path_e2e.py -q --durations=10`：隔离 Compose 上 12 passed，82.98 秒；回读 Run 终态、数据库、SSE 和文件。
- `docker compose exec -T -e SANDBOX_RUNTIME_PROFILE=core api uv run --no-sync --no-dev pytest test/unit -m 'not slow' -q`：2295 passed、58 skipped。开发配置的 `full` profile 会使两个既有 sandbox unit 断言失败，因此测试进程显式使用仓库默认 `core`。
- `test/integration/api/test_chat_router.py::test_thread_artifact_uses_image_signature_for_content_type`：隔离 Compose 上 1 passed，真实 HTTP 回读附件列表和对象。
- 三阶段 collect-only 结果为 4、4、4 项；未归属 selector 返回 0 项及 pytest 退出码 5。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts` 均通过；`pnpm run build` 在 `docs/` 通过；修改的 Python 文件通过 Ruff。
- PR CI 在创建后单独观察；真实外部模型探针不属于常规 gate，本次未运行。
