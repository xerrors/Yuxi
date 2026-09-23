# Runtime System Tests 构建缓存与确定性等待缩短

状态：implemented
类型：process
Owner：.github/workflows/system-tests.yml

## 问题

Runtime System Tests 单次运行约 14m38s：`docker compose up --build` 312s（无任何层缓存，apt 125s + `uv sync` 85s 每次全量重跑），确定性 Agent e2e 279s（含固定 31s 沙箱保活等待与 2s 级轮询），Durable Task/Milvus 重建 108s。慢的原因不是真实 LLM——真实模型探针是独立手动工作流 `real-provider-probe.yml`（需 `SILICONFLOW_API_KEY`），CI 只使用 `test/support/openai_replay_server.py` 确定性回放。

## 决策

- 在 CI 中用 `docker/setup-buildx-action` 单独预构建 `yuxi-api` 与 `yuxi-sandbox-provisioner` 镜像，启用 `type=gha,mode=max` 层缓存；`docker compose up` 去掉 `--build`。冷运行与原先等价，热运行命中 apt/uv sync/pip 层。
- 镜像名不硬编码，从 `docker compose config --format json` 解析，跟随 `.env.template` 与 docker-compose.yml。
- CI 环境曾将 `SANDBOX_KEEPALIVE_INTERVAL_SECONDS=5` 写入 .env 并经容器 env 继承 `E2E_RUN_POLL_INTERVAL_SECONDS=1`。引入后 2 次运行在 `test_subagent_worker_enforces_inherited_write_policy[always_trust]` 失败，随即撤回。后续证据证明失败与这两个覆写无因果关系：main 在本变更合入前（ade113ec，run 35818088032，旧 workflow）与合入后（run 35828838121）在完全相同用例以相同签名失败，当日 8 次该文件运行约半数失败。撤回是为消除变量，不是缺陷修复。失败签名：父子 subagent 场景下父 Run 240s 无 `first_model_request_at`，worker job 静默执行 241s 后被取消；根因未定位。
- "Verify Durable Task worker path" 从主 job 拆为独立并行 job。该步骤会 stop api/worker 并强制重建 Milvus，与其余步骤互斥，其中 ~75s 是拓扑热身（真实 pytest 仅 ~25s），串在主链路里全部计入关键路径；它也不依赖 replay server 等主链路前置。两个 job 各自持有完整拓扑，共享层缓存，且都持有覆盖冷缓存构建的 timeout 预算。
- 两个 CI 脚本纳入 workflow 的 pull_request/push paths 与合同脚本 required_paths：只改 gate 实现（env 准备、镜像构建）不得绕过 gate。
- `.env` 准备脚本对 sed 替换文本转义 `&`、`\` 与 `/`，避免含特殊字符的密钥写错值。

## 替代方案

- 保持 `--build` 靠 runner 本地缓存：GitHub runner 每次全新，无效。
- compose 文件加 `cache_from/cache_to`：本地开发构建会被 GHA scope 污染；预构建保持 compose 为本地真相来源。
- 并行化各 pytest 步骤：步骤间共享拓扑且有暂停 worker、强制重建 Milvus 等变更，不能安全拆分；删并步骤会削弱失败归因。
- 缩短 replay 阻塞时间: `time.sleep(60)` 是取消路径的确定性语义，不改。
- Durable Task 留在主链路：可省一套拓扑的 CI 分钟数，但 ~75s 拓扑热身继续占据关键路径。选择拆 job 是因为该步骤与主链路互斥，拆分不降低任何 gate 的归因粒度。
- 缩短 e2e env：引入后 CI 失败，当时以可能相关为由撤回；后续同签名失败出现在未含该覆写的 main 运行上，证明无因果关系，本记录不将其作为否决依据。

## 后果

首个冷运行耗时不降（约 14m23s，与原 `--build` 等价）；热缓存下构建从 312-347s 降到 90s（20 个 CACHED 步骤，uv sync 与 provisioner pip 不再执行），主 job 从 14m38s 降到约 10m15s，Durable Task job 约 4min 并行完成，PR 墙钟约 10m15s。CI 总分钟数因第二套拓扑上升约 4min。缓存回退读取默认分支缓存暂无数据（新 key 首次运行必冷）。`type=gha` 缓存导出在该环境的 job 中静默失效（缓存条目从未生成且无导出日志），层缓存改用 `actions/cache` 持久化 buildkit `type=local` 缓存目录，两个镜像必须使用互相隔离的缓存目录与 key：共用一个目录时后导出者会覆盖前者的 manifest，导致 api 镜像层永不命中。

## 验证

本地 `docker compose config --format json | jq -r '.services.api.image, .services["sandbox-provisioner"].image'` 解析出 `yuxi-api:0.7.3` 与 `yuxi-sandbox-provisioner:0.7.3`；两个 `docker buildx build --check` 均通过；YAML 解析与两个 job 步骤顺序核对无误；`bash -n` 通过。本地全新 buildx builder 验证 type=local 缓存往返：冷构建 2m36s、热构建 7s 且 20 个步骤 CACHED。CI 实测（run 35823445478）：冷运行主 job 14m23s 后按新 key 保存缓存，重跑热缓存构建 90s、主 job 10m15s、Durable job 3m58s，两 job 均 success。合同脚本与发布工作流单测在 paths/预算 fixture 同步后 67 项全部通过。缓存命中依赖 key 涉及文件不变，`backend/uv.lock` 或 Dockerfile 变更会退回冷构建。该 e2e 用例的间歇失败先于本变更存在（合入前的 main run 35818088032 同签名失败），本变更不修复也不引入它；根因排查是独立后续工作。
