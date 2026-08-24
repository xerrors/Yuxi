# 混合 PI 执行 seam

状态：proposed
类型：architecture
Owner：docs/adr/0004-hybrid-pi-execution-seam.md

## 问题

实现 [ADR 0004](../../../adr/0004-hybrid-pi-execution-seam.md) 前，需要把它的当前代码证据与后续可执行 oracle 固定下来。本记录只拥有实施前验收矩阵，不重复架构方案、替代或后果。

## 提案

按 ADR 0004 的 seam 实现 Local tracer，并让本矩阵中的 `Not run` 项由对应测试变成 `Passed`。当前 mechanism 文档在行为实现前保持不变。

## 替代方案

架构替代及拒绝原因由 ADR 0004 唯一拥有；本记录不重新裁决。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 运行请求、运行与运行尝试仍是唯一状态事实 | PI 另建 Task/attempt 或绕过终态 CAS | `AgentRunRequest`、`AgentRun`、`AgentRunAttempt`、`AgentRunRepository` | `backend/test/integration/services/test_agent_run_manifest_and_attempts.py` 与 `backend/test/integration/api/test_agent_run_result_causality.py` | 重复 claim、旧 attempt final、相邻 Run 输出不能覆盖 | `Inspected` |
| attempt 在 PI 启动前冻结 adapter 与 Runtime Manifest | 运行中换端或实际运行时不匹配 | 待扩展的 `AgentRunAttempt` 与 PI 执行模块 | `backend/test/unit/services/test_pi_execution_service.py` | 篡改 PI/Node/Skill digest 必须在 execute 前失败 | `Not run` |
| 重放 event/final 只产生一个逻辑结果，ACK 后结果仍可读 | Redis 重放、ACK 丢失或旧 attempt 覆盖 | PI Result Sink、`AgentRunRepository`、服务器 artifact Owner | `backend/test/unit/services/test_pi_execution_service.py`、`backend/test/e2e/test_pi_local_tracer.py` | 同一 `event_id`/final 重放两次，旧 attempt 提交 final | `Not run` |
| 取消形成单一终态并停止绑定实例 | 只停 worker stream，sandbox 继续运行 | `request_cancel_agent_run`、PI 执行模块、当前 adapter | `backend/test/unit/services/test_pi_execution_service.py` | Redis 信号丢失、重复取消、stop 重放 | `Not run` |
| Local golden Task 加载锁定 Skill 并在 rootfs 删除后保留结果 | Skill 漂移、结果只留在 sandbox | Runtime Manifest、Skill bundle、Result Sink | `backend/test/e2e/test_pi_local_tracer.py -m e2e` | 缺失/篡改 Skill，ACK 后删除 sandbox 再读取结果 | `Not run` |
| 既有 sandbox、manifest 与 Run seam 可独立验证 | 新模块只能通过完整 UI 或真实云账号测试 | 当前 unit/integration seam | 容器内相关 unit 集合（123 passed） | manifest 固化失败时执行不得开始 | `Passed` |

本票实际验证：`python3 scripts/verify_engineering_contracts.py` 通过；`python3 -m unittest scripts.test_verify_engineering_contracts` 为 62 passed；sandbox、manifest 与 Run worker 的 123 个相关 unit 在只读挂载当前 checkout 的容器内通过；`git diff --check` 通过。`pnpm run build` 进入 VitePress 后被未修改的 `agent-workflow/issue-tracker.md` 与 `agent-workflow/domain.md` 三个既有死链阻断，新文件未出现在死链列表中。

相关 unit 从仓库根目录使用以下实际命令运行：

```bash
docker run --rm --network none \
  -v "$PWD/backend/package:/app/package:ro" \
  -v "$PWD/backend/test:/app/test:ro" \
  -v "$PWD/docker:/app/docker:ro" \
  -w /app yuxi-api:0.7.2.dev0 \
  /usr/local/bin/python -m pytest \
  test/unit/backends/test_sandbox_provisioner_client.py \
  test/unit/backends/test_sandbox_provisioner_config.py \
  test/unit/backends/test_sandbox_backends.py \
  test/unit/services/test_agent_run_manifest_service.py \
  test/unit/services/test_run_worker.py -q
```

## 风险

- 现有 sandbox identity 是用户与文件/Skill 线程作用域，不是 attempt；Local Adapter 必须使用 attempt 独立实例或显式证明复用不会让取消/清理误伤其他 Run。
- Redis Run events 是短期投影且没有稳定业务 `event_id`；Result Sink 不能把一次 `XADD` 当作 durable ACK。
- 线程 outputs 当前依赖宿主 bind mount 或 PVC；Tencent 结果需要服务器上传 Owner，不能把腾讯 rootfs、NodePort 或临时 URL写入最终结果。
- 现有 Skill 投影会按线程重建，个人 Skill 位于共享 workspace；第一期必须只接受审核并锁定的 bundle，项目与个人 Skill 后置。
- 当前正常 Run 只由 idle reaper 回收 sandbox；Local tracer 未证明显式 stop 前，不得把“任务完成”解释为资源已清理。
