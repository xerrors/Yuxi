# Agent 并发容量

本页供部署管理员核算 API、worker、数据库连接和执行节点的容量，并说明如何重新压测。当前参数以 Compose 和运行时约束为准；资源规格以目标 Cube 节点和实际压测为准。

## 当前运行模型

- 普通输入先把原始消息保存在 PostgreSQL Input，并提交 Receipt。调度器消费就绪输入时创建正式 Message、Turn/Run 并绑定附件，事务提交后才通过 ARQ 投递 worker。输入优先级与合批规则见[输入队列机制](../mechanisms/agent-request-queue.md)。
- `ARQ_MAX_JOBS` 是单个 worker 的执行上限。AgentRun、后台作业与已登记控制工作共用这个执行上限。后台作业还受 PostgreSQL 全局最多 4 个 `running` claim 的约束。每棵协作树的 Run 上限由管理员系统配置 `max_cooperation_concurrency` 控制，默认 4、最小 1，在领取下一次执行时读取当前值；降低上限不取消已领取的 Run，增加上限也不能突破 worker 或模型供应商容量。取消收尾同样占位，协作等待释放执行名额。协作树限额不会限制其他树的总并发，也不等于用户 VM 的命令或资源配额。
- Run 事件写入 Redis Stream，SSE 使用自适应 `XRANGE` 轮询；PostgreSQL 低频补偿权威终态。
- 取消请求先提交 PostgreSQL durable 状态，再写入带 TTL 的 Redis key。每个运行中的 Run 约每 200ms 读取该 key，另有约 1 秒的 PostgreSQL durable watcher；模型事件循环只检查进程内 Event。
- 取消链路不使用 Redis Pub/Sub，也不为每个 Run 长期占用一个 Redis 连接。100 个活跃 Run 的取消 key 读取上界约为 500 次/秒。
- Cube 按用户创建并复用持久 VM，首次需要执行环境时创建或唤醒，空闲后按 `SANDBOX_IDLE_SECONDS` 休眠。同一用户的不同 Run 共用该 VM，协作树成员共用 Project Workdir；运行数、活跃用户 VM 数和命令并发数需要分别观测。节点、模板和共享文件挂载要求见[沙盒配置与运维](./sandbox-operations.md)。

PostgreSQL 是 Input、Turn、Run 和终态的事实来源。Redis Stream、取消 key、健康 key 和缓存都是可恢复的短期状态，不能用 Redis 命中代替 PostgreSQL 终态验证。

## 默认推荐配置

以下是开发与生产 Compose 的默认值，按一个 API 和一个 worker 核算。执行槽与连接池上限用于限制资源使用，实际可承载并发还取决于用户 VM、模型配额、工作负载及执行节点：

| 配置 | 默认值 | 作用与核算方式 |
|---|---:|---|
| `ARQ_MAX_JOBS` | 140 | 所有已登记作业共用的执行上限，没有按业务类型预留独立槽位 |
| `API_REDIS_MAX_CONNECTIONS` | 256 | API 中每个 Redis 客户端池的上限；不是预先建立 256 个连接 |
| `WORKER_REDIS_MAX_CONNECTIONS` | 256 | worker 中每个 Redis 客户端池的上限；取消轮询不独占连接 |
| `API_POSTGRES_POOL_SIZE` + `API_POSTGRES_MAX_OVERFLOW` | 120 + 40 | API SQLAlchemy 最坏连接预算 160 |
| `API_LANGGRAPH_POSTGRES_POOL_SIZE` | 10 | API thread state、compress 与 channel state 使用的 LangGraph checkpoint 池 |
| `WORKER_POSTGRES_POOL_SIZE` + `WORKER_POSTGRES_MAX_OVERFLOW` | 120 + 40 | worker SQLAlchemy 最坏连接预算 160 |
| `WORKER_LANGGRAPH_POSTGRES_POOL_SIZE` | 120 | worker LangGraph checkpoint 池 |
| `POSTGRES_MAX_CONNECTIONS` | 600 | 上述 API/worker 池上限合计 450，剩余 150 还需覆盖初始化、健康检查、其他连接及运维 |

单 API、单 worker 的 PostgreSQL 池预算为 `(120 + 40) + 10 + (120 + 40) + 120 = 450`。增加副本时，API 每份增加最多 170，worker 每份增加最多 280。例如一 API、两 worker 已达到 730，超过数据库默认 600，需要先调整池或数据库容量。这是配置上限的求和。它不是已建立连接数或实际峰值。

Redis 256 是单个客户端池的上限，进程可拥有多个客户端池，不能据此推导进程连接总数。当前部署应同时观察 SSE、事件发布、缓存、ARQ 和取消轮询的实际连接与等待。

资源受限部署可以压低该上限，但必须重新跑目标并发并确认没有连接池耗尽或事件缺失。

取消 key 的 200ms 轮询和 PostgreSQL 的约 1 秒补偿检查是 worker 内部协议参数，没有对应的部署开关。容量调整使用已有执行槽和连接池配置，并通过取消终态、事件完整性与连接等待验证。

## Run 内持久时延口径

AgentRun 另行持久保存服务端权威时间点，用于历史诊断；它们与客户端指标互补，不能互换：

| API timing 字段 | 计算 | 含义 |
|---|---|---|
| `dispatch_latency_ms` | `created_at` → `started_at` | Run 创建后等待 worker 取得 lease |
| `preparation_latency_ms` | `started_at` → `prepared_at` | worker 完成运行清单、Agent 上下文、附件与模型流装配 |
| `model_first_output_latency_ms` | `prepared_at` → `first_output_at` | Agent graph 与事件流创建后，到首次非空文本、推理或工具调用数据 |
| `first_output_latency_ms` | `created_at` → `first_output_at` | 服务端从 Run 创建到首次语义输出 |
| `total_latency_ms` | `created_at` → `finished_at` | 服务端 Run 总时延 |

`prepared_at` 与 `first_output_at` 只由当前 lease owner 写入一次。观测失败不阻断 Run，缺少阶段的值保持 `null`，API 不用负数或 0 掩盖缺失。`GET /api/v1/agents/sessions/{thread_id}/runs/{run_id}` 和管理员按需读取的 Thread 审计返回 Run 的 `timing` 投影；普通 History 只返回 Run 归属，不附带完整时延。前端调试面板按需读取审计，按 Turn 汇总记录，详情保留各 Run 阶段明细。消息通过持久 `run_id`、`turn_id` 关联。完整响应边界见[线程阅读数据](../mechanisms/agent-runtime.md#线程阅读数据)。

上述服务端时间点在事务内产生：`created_at` 是 Run 行创建时间，`finished_at` 是终态转换时间，二者分别在 owning transaction 提交后可见并成为权威事实。因此 `total_latency_ms` 不包含终态写入后的 runtime cleanup、SSE 读取或浏览器渲染等待。

## 调整容量

1. 分别确定 worker 作业并发、活跃用户 VM 数、每棵协作树的 Run 数和模型请求速率。`ARQ_MAX_JOBS` 由所有作业共用，调低上限也会影响恢复与控制工作的执行机会。协作树的独立上限通过系统设置 `max_cooperation_concurrency` 调整。
2. 核算 PostgreSQL 最坏预算：所有 API/worker 的 SQLAlchemy 与 LangGraph 池之和，必须低于 `POSTGRES_MAX_CONNECTIONS`，并给 migrator、健康检查、连接抖动和人工运维留余量。增加 API 或 worker 副本会按副本数放大对应的两类池。
3. Redis 池服务短操作，不再按“每 Run 一个订阅连接”计算。仍要覆盖同时进行的 SSE 读取、事件写入、取消 key 读取、缓存和 ARQ 操作；观察 `connected_clients`、连接池错误和 SSE 事件完整性后再收缩。
4. 按实际 Cube 模板和活跃用户 VM 测量执行节点的 CPU、内存、磁盘与网络，并为宿主、Cube 控制服务和共享文件系统留余量。仓库的模板构建脚本传入 `--cpu 1000 --memory 1024`；已有实例沿用创建时的模板，模板参数不能替代节点实测。
5. 同一用户的不同 Thread 可共用一个 VM，独立用户会增加 VM 数。压测账号分布需要接近真实负载，验证并发命令的输出归属、取消清理与共享文件正确性；网络和挂载容量由 Cube 节点配置决定。

从低并发逐档增加负载，记录池等待、节点资源和业务结果，再选择执行槽与池大小。连接上限不等于即时连接占用，也不能线性推导较小部署的配置。

扩大并发前，同时评估 worker 数量、模型配额、PostgreSQL 总预算和 Cube 节点资源。按 200ms 间隔计算，1000 个运行中 Run 的取消 key 理论读取量约为 5000 次/秒；该数只估算轮询请求，不证明 Redis、模型或执行节点支持这一负载。

## 应用和验证

压测会产生真实模型费用、创建会话并使用用户 VM。先在独立测试环境准备凭据、目标 Agent、Cube 节点和共享目录，确认无生产任务。修改实际使用的环境文件（开发 `.env`、生产 `.env.prod`）后，重新创建读取该变量的容器；数据库重建连接前需安排停机窗口。生产命令显式使用 `.env.prod` 与 `docker-compose.prod.yml`：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate postgres api
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate --scale worker=1 worker
curl --fail http://localhost/api/system/ready
```

就绪地址使用 Web 入口，默认 80 端口；修改 `YUXI_FRONTEND_PORT` 后使用对应端口。

随后从小档位渐进到目标并发。以下示例使用开发环境入口；生产环境把 `--base-url` 改为 Web 入口（默认 `http://localhost`）：

```bash
python -m backend.test.performance load \
  --base-url http://localhost:5050 \
  --scenario sandbox \
  --concurrency 1,10,20,50,100 \
  --task-seconds 20 \
  --collect-local-resources
```

通过条件包括 Input/Turn/Run 因果标识一致、SSE 工具生命周期完整、PostgreSQL 权威终态正确、readiness 最终恢复，以及 Run 租约和命令句柄完成清理。Cube 用户 VM 按空闲策略保留并休眠，不要求容器或网络归零。`--collect-local-resources` 只采样当前 Compose 与本机资源，远端 Cube 节点需要单独采样。脚本退出码或 HTTP 200 只作为辅助信号，协议、凭据和输出边界见[性能与容量测试](../develop-guides/testing-guidelines.md#性能评测)。

配置由[开发 Compose](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)与[生产 Compose](https://github.com/xerrors/Yuxi/blob/main/docker-compose.prod.yml)拥有。连接池实现见 [PostgresManager](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/infrastructure/postgres/manager.py)，协作限额见 [CooperationRepository](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/repositories/cooperation.py)，节点模板规格见[模板构建脚本](https://github.com/xerrors/Yuxi/blob/main/docker/cube/build-template.sh)。
