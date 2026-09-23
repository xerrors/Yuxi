# Runtime System Tests 构建缓存与确定性等待缩短

状态：implemented
类型：process
Owner：.github/workflows/system-tests.yml

## 问题

Runtime System Tests 单次运行约 14m38s：`docker compose up --build` 312s（无任何层缓存，apt 125s + `uv sync` 85s 每次全量重跑），确定性 Agent e2e 279s（含固定 31s 沙箱保活等待与 2s 级轮询），Durable Task/Milvus 重建 108s。慢的原因不是真实 LLM——真实模型探针是独立手动工作流 `real-provider-probe.yml`（需 `SILICONFLOW_API_KEY`），CI 只使用 `test/support/openai_replay_server.py` 确定性回放。

## 决策

- 在 CI 中用 `docker/setup-buildx-action` 单独预构建 `yuxi-api` 与 `yuxi-sandbox-provisioner` 镜像，启用 `type=gha,mode=max` 层缓存；`docker compose up` 去掉 `--build`。冷运行与原先等价，热运行命中 apt/uv sync/pip 层。
- 镜像名不硬编码，从 `docker compose config --format json` 解析，跟随 `.env.template` 与 docker-compose.yml。
- CI 环境将 `SANDBOX_KEEPALIVE_INTERVAL_SECONDS=5` 写入 .env（仅影响保活 touch 节奏，不改变回收语义），e2e 测试进程经容器 env 继承 `E2E_RUN_POLL_INTERVAL_SECONDS=1`。二者都是既有 env 开关，不引入新机制。
- "Verify Durable Task worker path" 从主 job 拆为独立并行 job。该步骤会 stop api/worker 并强制重建 Milvus，与其余步骤互斥，其中 ~75s 是拓扑热身（真实 pytest 仅 ~25s），串在主链路里全部计入关键路径；它也不依赖 replay server 等主链路前置。两个 job 各自持有完整拓扑，共享层缓存。

## 替代方案

- 保持 `--build` 靠 runner 本地缓存：GitHub runner 每次全新，无效。
- compose 文件加 `cache_from/cache_to`：本地开发构建会被 GHA scope 污染；预构建保持 compose 为本地真相来源。
- 并行化各 pytest 步骤：步骤间共享拓扑且有暂停 worker、强制重建 Milvus 等变更，不能安全拆分；删并步骤会削弱失败归因。
- 缩短 replay 阻塞时间: `time.sleep(60)` 是取消路径的确定性语义，不改。
- Durable Task 留在主链路：可省一套拓扑的 CI 分钟数，但 ~75s 拓扑热身继续占据关键路径。选择拆 job 是因为该步骤与主链路互斥，拆分不降低任何 gate 的归因粒度。

## 后果

首个冷运行耗时不降；后续运行预计主链路从约 14m38s 降到约 8min（热缓存 + 并行拆分）。CI 依赖 GitHub Actions 缓存服务（分支可回退读取默认分支缓存）。`SANDBOX_KEEPALIVE_INTERVAL_SECONDS=5` 与 `E2E_RUN_POLL_INTERVAL_SECONDS=1` 是 CI-only 覆写，不改变生产与本地默认。拆分后 CI 总分钟数上升约一套拓扑的启动成本，PR 墙钟下降。

## 验证

本地 `docker compose config --format json | jq -r '.services.api.image, .services["sandbox-provisioner"].image'` 解析出 `yuxi-api:0.7.3` 与 `yuxi-sandbox-provisioner:0.7.3`；两个 `docker buildx build --check` 均通过；YAML 解析与两个 job 的步骤顺序核对无误；`bash -n` 与临时目录 env 脚本烟测通过（密钥注入与 Langfuse 覆写符合预期）；`git diff --check` 无告警。收益证据取自最近一次真实 run（35809726195）的 step 计时与本地同拓扑 e2e `--durations` 复现；CI 上的热/冷缓存收益与并行拆分后的墙钟需按本 PR 的真实 run 计时复核。
