# Agent 并发容量

本页给出 Docker Compose 单 worker 承载 AgentRun 的容量基线、实测结果和调参方法。容量结论只适用于这里记录的拓扑与工作负载；更换模型供应商、Sandbox 规格或宿主机后，应按同一方法重新验证。

## 当前运行模型

- 普通请求先持久化到 PostgreSQL，再由 ARQ 投递给 worker；`ARQ_MAX_JOBS` 是单个 worker 供 AgentRun、Durable Task 与控制面工作共用的执行槽上限。Durable Task 另受 PostgreSQL 最多 4 个并发 claim 的约束。
- Run 事件写入 Redis Stream，SSE 使用自适应 `XRANGE` 轮询；PostgreSQL 低频补偿权威终态。
- 取消请求先提交 PostgreSQL durable 状态，再写入带 TTL 的 Redis key。每个运行中的 Run 约每 200ms 读取该 key，另有约 1 秒的 PostgreSQL durable watcher；模型事件循环只检查进程内 Event。
- 取消链路不使用 Redis Pub/Sub，也不为每个 Run 长期占用一个 Redis 连接。100 个活跃 Run 的取消 key 读取上界约为 500 次/秒。
- Sandbox 只在第一次文件或命令操作时创建。Docker backend 为每个 Sandbox 创建独立容器和网络，因而 Sandbox 工作负载通常先受宿主机内存和 Docker IPAM 限制。

PostgreSQL 是 Run、Request 和终态的事实来源。Redis Stream、取消 key、健康 key 和缓存都是可恢复的短期状态，不能用 Redis 命中代替 PostgreSQL 终态验证。

## 默认推荐配置

当前 Compose 默认值以“单 worker、最多 100 个同时运行的 Sandbox AgentRun”为目标：

| 配置 | 默认值 | 作用与核算方式 |
|---|---:|---|
| `ARQ_MAX_JOBS` | 140 | 100 个业务槽位，另留 40 个槽位给健康检查、恢复、清理和短时峰值 |
| `API_REDIS_MAX_CONNECTIONS` | 256 | API 中每个 Redis 客户端池的上限；不是预先建立 256 个连接 |
| `WORKER_REDIS_MAX_CONNECTIONS` | 256 | worker 中每个 Redis 客户端池的上限；取消轮询不独占连接 |
| `API_POSTGRES_POOL_SIZE` + `API_POSTGRES_MAX_OVERFLOW` | 120 + 40 | API SQLAlchemy 最坏连接预算 160 |
| `API_LANGGRAPH_POSTGRES_POOL_SIZE` | 10 | API thread state、compress 与 channel state 使用的 LangGraph checkpoint 池 |
| `WORKER_POSTGRES_POOL_SIZE` + `WORKER_POSTGRES_MAX_OVERFLOW` | 120 + 40 | worker SQLAlchemy 最坏连接预算 160 |
| `WORKER_LANGGRAPH_POSTGRES_POOL_SIZE` | 120 | worker LangGraph checkpoint 池 |
| `POSTGRES_MAX_CONNECTIONS` | 600 | 上述单 worker 客户端预算共 450，余下 150 给迁移、健康检查和运维 |
| `SANDBOX_DELETE_CONCURRENCY` | 32 | 有界并行回收 Sandbox，避免终态清理串行拖尾 |
| `SANDBOX_DOCKER_ADDRESS_POOL` / `SANDBOX_DOCKER_SUBNET_PREFIX` | `10.253.240.0/20` / `28` | 最多提供 256 个独立 Sandbox 子网；部署前检查路由冲突 |

Redis 256 是经过 100 并发验证的单个客户端池保护上限，并非进程总连接预算或取消协议的最低要求；移除每 Run Pub/Sub 后，100 并发实测只使用 36 个 Redis 客户端连接。暂时保留 256 可以吸收 SSE、事件发布、缓存和控制面峰值，也避免为降低一个不预分配的上限引入新的配置档位。

资源受限部署可以压低该上限，但必须重新跑目标并发并确认没有连接池耗尽或事件缺失。

不新增取消轮询间隔、durable watcher 间隔或取消 channel 配置。200ms/1s 是 worker 内部协议参数；把它们暴露成部署旋钮会产生彼此不兼容且未经验证的组合。现有配置只保留确实需要随 worker 数量和机器容量变化的池、槽位与 Sandbox 资源上限。

## 实测基线（2026-09-04 快照）

本节是 2026-09-04 在当时代码与供应商上的实测快照，用于说明默认配置的验证方法和资源量级；它不是长期有效的 SLA。更换模型供应商或升级版本后，时延数字会漂移，应按[应用和验证](#应用和验证)重新执行，容量结论以最新一轮实测为准。

测试使用当前默认配置、真实 HTTP、Request/Run SSE、worker、PostgreSQL、Redis、模型供应商和 core Sandbox。每个虚拟用户创建独立 Thread，模型调用一次 `execute` 执行 20 秒任务；结果必须包含唯一标记、同一 request/run 标识、完整工具事件和 PostgreSQL `completed` 终态。1、10、20、50、100 每档各跑一轮，共 181/181 成功，排队时间 p95 均为 0。

延迟从提交 Agent Run 开始计时：

| 并发 | 成功 | 提交 p95 | 准备时间 p50 / p95 | 首 Token p50 / p95 | 整体时延 p50 / p95 |
|---:|---:|---:|---:|---:|---:|
| 1 | 1/1 | 28ms | 1.52s / 1.52s | 5.03s / 5.03s | 32.43s / 32.43s |
| 10 | 10/10 | 103ms | 0.84s / 0.89s | 5.11s / 7.08s | 33.28s / 34.82s |
| 20 | 20/20 | 247ms | 0.94s / 1.02s | 6.23s / 7.35s | 37.79s / 39.65s |
| 50 | 50/50 | 642ms | 2.05s / 3.19s | 8.36s / 28.25s | 43.55s / 62.52s |
| 100 | 100/100 | 1.78s | 4.36s / 5.43s | 34.32s / 57.88s | 105.76s / 114.13s |

这里的准备时间是压测客户端从提交到首个 Run SSE 事件的延迟，不是 Sandbox 单独创建耗时；首 Token 是客户端从提交到首个非空文本、推理或工具调用数据的延迟，包含模型供应商排队与首轮工具调用决策。50 并发以后首 Token 上升明显，而 Request 队列仍为 0，说明本轮主要时延不在 ARQ 排队。一次试验不能把供应商抖动与本机瓶颈完全分离，因此这里只把成功率和资源峰值作为容量证据，不把首 Token 数字当作稳定 SLA。

## Run 内持久时延口径

AgentRun 另行持久保存服务端权威时间点，用于历史诊断；它们与上面的客户端指标互补，不能互换：

| API timing 字段 | 计算 | 含义 |
|---|---|---|
| `dispatch_latency_ms` | `created_at` → `started_at` | Run 创建后等待 worker 取得 lease |
| `preparation_latency_ms` | `started_at` → `prepared_at` | worker 完成运行清单、Agent 上下文、附件与模型流装配 |
| `model_first_output_latency_ms` | `prepared_at` → `first_output_at` | Agent graph 与事件流创建后，到首次非空文本、推理或工具调用数据 |
| `first_output_latency_ms` | `created_at` → `first_output_at` | 服务端从 Run 创建到首次语义输出 |
| `total_latency_ms` | `created_at` → `finished_at` | 服务端 Run 总时延 |

`prepared_at` 与 `first_output_at` 只由当前 lease owner 写入一次。观测失败不阻断 Run，历史 Run 或缺少阶段的值保持 `null`，API 不用负数或 0 掩盖缺失。`GET /api/agent/runs/{run_id}`、结果接口与对话历史返回同一 `timing` 投影；前端消息底部和折叠过程只使用 `total_latency_ms`，五段明细在消息调试面板的 Run 分组中按需展示，不会为每条历史消息新增请求。 History 将时间放在独立 `runs[].timing`，消息只通过 `run_id` 关联；完整响应边界见[线程阅读数据](../mechanisms/agent-runtime.md#线程阅读数据)。

上述服务端时间点在事务内产生：`created_at` 是 Run 行创建时间，`finished_at` 是终态转换时间，二者分别在 owning transaction 提交后可见并成为权威事实。因此 `total_latency_ms` 不包含终态写入后的 runtime cleanup、SSE 读取或浏览器渲染等待。

同一窗口的资源峰值如下。CPU 为 Docker 各容器百分比之和，`3508%` 约等于 35 个逻辑核满载：

| 并发 | Redis 客户端 / PubSub | PG 会话 / active | Sandbox 容器 | Sandbox 内存 | 容器总内存 | CPU 总峰值 | 宿主最低可用内存 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 / 0 | 23 / 1 | 1 | 0.17GiB | 2.35GiB | 281% | 40.33GiB |
| 10 | 17 / 0 | 57 / 2 | 10 | 1.64GiB | 4.01GiB | 335% | 38.55GiB |
| 20 | 22 / 0 | 73 / 7 | 20 | 3.27GiB | 5.66GiB | 1628% | 36.86GiB |
| 50 | 25 / 0 | 127 / 21 | 50 | 7.72GiB | 10.34GiB | 2596% | 31.91GiB |
| 100 | 36 / 0 | 285 / 83 | 100 | 16.33GiB | 19.61GiB | 3508% | 23.35GiB |

同一默认配置随后使用 `alibaba-cn:qwen3.8-flash` 做跨供应商复测：

| 并发 | 严格成功 | 提交 p95 | 客户端准备 p50 / p95 | 客户端首 Token p50 / p95 | 整体时延 p50 / p95 | Redis / PubSub | PG / active | Sandbox / 网络 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 50/50 | 557ms | 2.02s / 3.05s | 7.53s / 27.78s | 43.67s / 58.86s | 18 / 0 | 167 / 26 | 50 / 50 |
| 100 | 96/100 | 1.51s | 3.62s / 4.74s | 28.48s / 55.43s | 102.79s / 110.13s | 32 / 0 | 285 / 95 | 100 / 100 |

100 档的 4 个失败均由供应商返回账户级 `AllocationQuota.FreeTierOnly`，不是 worker、PostgreSQL、Redis、Sandbox 或 SSE 容量错误；因此该轮证明系统到达了 100 个并存 Sandbox 与网络，但不能把 96% 记为业务容量通过。50 档完整通过，且两档的客户端时延和资源曲线与前一供应商处于同一量级，进一步支持 50 以后主要等待位于模型阶段的判断。该结论仍不替代供应商配额与限流的独立压测。

移除取消 Pub/Sub 前，同一默认配置的 100 并发基线观察到 Redis 136 个客户端连接，其中 100 个是 Pub/Sub；移除后峰值为 36/0。延迟仍落在此前三轮 100 并发的范围内：准备 p95 4.34–5.60 秒、首 Token p95 57.30–61.44 秒、整体 p95 113.32–116.19 秒。专用实时取消探针在 Run 进入 `running` 后发起取消，235ms 收敛到同一 Run 的 PostgreSQL `cancelled` 终态。

测试结束后 `/api/system/ready` 为 200，单 worker healthy，动态 Sandbox 容器和网络均归零。取消相关 unit、真实 HTTP integration、实时取消探针和完整并发链路均已通过；尚未执行 Redis 故障注入或 1000 并发测试。

## 调整容量

1. 先确定每个 worker 的目标业务并发 `N`，为健康检查、恢复和回收保留独立槽位。当前 `N=100` 使用 140，而不是把 100 个槽全部分给业务 Run。
2. 核算 PostgreSQL 最坏预算：所有 API/worker 的 SQLAlchemy 与 LangGraph 池之和，必须低于 `POSTGRES_MAX_CONNECTIONS`，并给 migrator、健康检查、连接抖动和人工运维留余量。增加 API 或 worker 副本会按副本数放大对应的两类池。
3. Redis 池服务短操作，不再按“每 Run 一个订阅连接”计算。仍要覆盖同时进行的 SSE 读取、事件写入、取消 key 读取、缓存和 ARQ 操作；观察 `connected_clients`、连接池错误和 SSE 事件完整性后再收缩。
4. Sandbox 工作负载按每个并发实例约 170MiB 的本轮实测粗估内存，并为镜像、页缓存、Docker daemon、API、worker 和 PostgreSQL 留余量。这个数不是不同 runtime profile 的固定规格。
5. Docker 地址池的可分配子网数必须大于 Sandbox 峰值，并且不能与宿主机、局域网、VPN 或现有 Docker 网络冲突。

若目标只是 10、20 或 50 并发，直接保留默认 100 配置最简单；它不会预先占满 Redis 或 PostgreSQL 连接。只有宿主资源明确受限时才建立较小部署档位，并对该档位重新验证，不根据表格线性猜测池大小。

目标显著超过 100 时，优先评估多 worker、模型供应商配额、PostgreSQL 总预算和 Sandbox 调度。到 1000 个同时 Run 后，200ms key 轮询的理论读取量约为 5000 次/秒；只有实测证明 Redis CPU、网络或取消延迟不可接受时，才评估“每 worker 一个共享订阅器”，不恢复每 Run 一个订阅。

## 应用和验证

修改 `.env` 后，必须重新创建实际读取变量的容器。只执行 `restart` 不会更新容器环境：

```bash
docker compose up -d --force-recreate postgres api
docker compose up -d --force-recreate --scale worker=1 worker
curl --fail http://localhost:5050/api/system/ready
```

随后从小档位渐进到目标并发：

```bash
python -m backend.test.performance load \
  --base-url http://localhost:5050 \
  --scenario sandbox \
  --concurrency 1,10,20,50,100 \
  --task-seconds 20 \
  --collect-local-resources
```

通过条件包括所有 Request/Run 因果标识一致、SSE 工具生命周期完整、PostgreSQL 权威终态正确、readiness 最终恢复，并且 ARQ、Sandbox 容器和动态网络均已清理。脚本退出码或 HTTP 200 只能作为辅助信号。压测工具的协议和输出字段见 `backend/test/performance/load.py`。
