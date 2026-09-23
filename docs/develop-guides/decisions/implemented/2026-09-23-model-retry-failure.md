# 模型重试耗尽保留失败语义

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/middlewares/network_retry.py

## 问题

模型重试中间件默认把耗尽异常合成为 AIMessage。该消息没有 model lifecycle 审计，终态持久化无法关联当前 Run；跳过一致性检查又会使无输出的失败 Run 成为 completed。子任务失败还需要通过真实 worker 链路证明父任务可读取失败并完成清理。

## 决策

本决定部分取代[网络重试预算](./2026-09-10-network-retry-budget-ownership.md)中的次数重试耗尽策略；该记录拥有的网络预算、退避计时与异常分类规则继续有效。

统一中间件使用上游的 on_failure="error"，保留次数重试和网络预算，耗尽后抛出原异常。Run service/worker 拥有失败终态、错误与清理，chat_service 保留输出关联检查。已有部分输出由失败通道保存，带 is_error 和当前错误元数据。范围不含限流调度、并发配额或历史 checkpoint 迁移。

## 替代方案

- 为合成消息打标记并豁免审计：仍需另建失败到 Run 的映射，直接完成会丢失错误语义。
- 关闭重试：失去瞬时故障恢复能力。
- 在重试边界抛出异常：复用已有失败通道，改动最小。

## 后果

父智能体通过子 Run 结果读取真实模型失败原因并决定后续操作；默认继续策略的合成错误回答不进入 checkpoint。无需新增消息标记、持久化豁免或 Run 状态。真实 provider 漂移仍需外部探针校准。网络异常的既有分类、预算与取消行为保持原有实现。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 重试耗尽抛出原异常，恢复后正常返回 | 合成错误回答或关闭重试 | network_retry.py | 同步/异步 unit；相关集合 70 passed | 修改前四个耗尽断言均因 DID NOT RAISE 失败 | Passed |
| 429 Run 失败且父任务可处理、清理、继续请求 | 空成功或级联失败 | run_worker.py / Run repository | deterministic E2E 四种场景、HTTP / SSE / PG 回读 | 恢复 continue 后父任务读取的错误为持久化失败，原始 429 断言失败 | Passed |

最小回归命令：`docker compose exec -T api uv run --no-sync --no-dev pytest test/unit/agents/test_network_retry.py test/unit/services/test_chat_service_sync.py -q`；真实链路：`docker compose exec -T api uv run --no-sync --no-dev pytest test/e2e/test_deterministic_agent_path_e2e.py -k model_retry_exhaustion -q`。E2E 位于现有 `system-tests.yml` 整文件 gate 中。

真实豆包限流、五子任务并发与历史 execution tree 清理崩溃未复现；验证覆盖正常装配的首次/工具调用后失败、普通/子 Run 和相同线程重复请求。
