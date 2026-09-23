# Runtime System Tests 构建缓存与确定性等待缩短

状态：implemented
类型：process
Owner：.github/workflows/system-tests.yml

## 问题

Runtime System Tests 单次运行约 14m38s：`docker compose up --build` 312s（无任何层缓存，apt 125s + `uv sync` 85s 每次全量重跑），确定性 Agent e2e 279s（含固定 31s 沙箱保活等待与 2s 级轮询），Durable Task/Milvus 重建 108s。慢的原因不是真实 LLM——真实模型探针是独立手动工作流 `real-provider-probe.yml`（需 `SILICONFLOW_API_KEY`），CI 只使用 `test/support/openai_replay_server.py` 确定性回放。

## 决策

- 在 CI 中用 `docker/setup-buildx-action` 单独预构建 `yuxi-api` 与 `yuxi-sandbox-provisioner` 镜像，启用 `type=gha,mode=max` 层缓存；`docker compose up` 去掉 `--build`。冷运行与原先等价，热运行命中 apt/uv sync/pip 层。
- 镜像名不硬编码，从 `docker compose config --format json` 解析，跟随 `.env.template` 与 docker-compose.yml。
- CI 环境曾将 `SANDBOX_KEEPALIVE_INTERVAL_SECONDS=5` 写入 .env 并经容器 env 继承 `E2E_RUN_POLL_INTERVAL_SECONDS=1`。首轮 PR CI 中 3 次运行有 2 次在 `test_subagent_worker_enforces_inherited_write_policy[always_trust]` 以相同签名失败（worker 租约心跳仍在、Run 执行停滞 240s 超时），失败点在沙箱相关等待上与保活节奏变化相容但无直接证据。已撤回这两个覆写，e2e 回到默认轮询与保活；重新引入需先有失败根因证据。
- "Verify Durable Task worker path" 从主 job 拆为独立并行 job。该步骤会 stop api/worker 并强制重建 Milvus，与其余步骤互斥，其中 ~75s 是拓扑热身（真实 pytest 仅 ~25s），串在主链路里全部计入关键路径；它也不依赖 replay server 等主链路前置。两个 job 各自持有完整拓扑，共享层缓存。

## 替代方案

- 保持 `--build` 靠 runner 本地缓存：GitHub runner 每次全新，无效。
- compose 文件加 `cache_from/cache_to`：本地开发构建会被 GHA scope 污染；预构建保持 compose 为本地真相来源。
- 并行化各 pytest 步骤：步骤间共享拓扑且有暂停 worker、强制重建 Milvus 等变更，不能安全拆分；删并步骤会削弱失败归因。
- 缩短 replay 阻塞时间: `time.sleep(60)` 是取消路径的确定性语义，不改。
- Durable Task 留在主链路：可省一套拓扑的 CI 分钟数，但 ~75s 拓扑热身继续占据关键路径。选择拆 job 是因为该步骤与主链路互斥，拆分不降低任何 gate 的归因粒度。
- 缩短 e2e env：被 CI 失败证据否决（见决策），撤回而非降级保留。

## 后果

首个冷运行耗时不降；后续运行预计主链路从约 14m38s 降到约 9min（热缓存 + 并行拆分，e2e 保持默认 env 的 ~282s）。`type=gha` 缓存导出在该环境的 job 中静默失效（`ACTIONS_CACHE_URL` 等未注入，缓存条目从未生成），层缓存改用 `actions/cache` 持久化 buildkit `type=local` 缓存目录（实测生成 969MB 缓存条目）。CI 依赖 GitHub Actions 缓存服务。拆分后 CI 总分钟数上升约一套拓扑的启动成本，PR 墙钟下降。

## 验证

本地 `docker compose config --format json | jq -r '.services.api.image, .services["sandbox-provisioner"].image'` 解析出 `yuxi-api:0.7.3` 与 `yuxi-sandbox-provisioner:0.7.3`；两个 `docker buildx build --check` 均通过；YAML 解析与两个 job 的步骤顺序核对无误；`bash -n` 与临时目录 env 脚本烟测通过；`git diff --check` 无告警。CI 实测：run 35818751748 生成 `buildkit-Linux` 969MB 缓存条目，证明 actions/cache 路线在该 workflow 的 job 中可用；`type=gha` 同一 job 零缓存条目且无导出日志。热缓存收益、并行拆分后的墙钟与 e2e 默认 env 下的稳定性需按本 PR 下一轮真实 run 计时复核。
