# Agent 并发容量、流式协议与时延观测

状态：archived
类型：architecture
Owner：docker-compose.yml

## 问题

Agent 并发不是单一的 worker 数量问题。一次真实运行同时占用 ARQ 槽位、PostgreSQL 与 LangGraph 连接、Redis 短操作、SSE 读取、模型供应商额度，以及可选的 Sandbox 容器、网络和清理能力。只调大其中一个容量会把等待或失败转移到下一个边界。

早期实现还存在三类放大器：Run SSE 每秒把 Redis 读取与 PostgreSQL 权限、终态查询绑在一起；每个 Run 为取消长期占用一个 Pub/Sub 连接；所有请求在模型执行前预建 Sandbox，且 Docker 独立网络和串行回收限制了并发。相关结论曾分散在多份 decision 中，配置、证据和替代方案重复，难以判断当前有效设计。

压测客户端的“准备时间”和“首 Token”均从提交请求开始计时，混入 HTTP、调度、SSE 轮询与客户端调度，不能直接成为 Run 的服务端权威阶段事实。历史对话也缺少稳定、可解释的时延展示。

## 决策

Compose 拥有默认部署容量；AgentRun Schema 与 repository 拥有持久时间事实，Run service/worker 拥有 SSE、取消和记录边界，Sandbox provisioner 拥有容器与网络生命周期，压测脚本拥有客户端指标。本文只记录这些 Owner 共同形成的取舍，不成为可独立编辑的运行时事实源。

### 容量预算与压测

开发和生产 Compose 默认使用单 worker，`ARQ_MAX_JOBS=140`，其中 100 个槽位服务目标业务并发，40 个槽位留给健康检查、恢复、清理和短时峰值；共享 worker 中的 Durable Task 另受 PostgreSQL 最多 4 个并发 claim 的约束。API 与 worker 的每个 Redis 客户端池上限分别为 256；API/worker SQLAlchemy 池分别为 120+40；API/worker LangGraph 池分别为 10/120；PostgreSQL `max_connections=600`，合计客户端预算 450 并预留 150；Sandbox 并行删除数为 32。配置入口、完整核算方式和当前实测由 [Agent 并发容量](../../../../../advanced/agent-concurrency-capacity.md)维护。

仓库使用 `backend/test/performance/load.py` 执行真实 HTTP、Request SSE、Run SSE、worker、模型、PostgreSQL 和可选 Sandbox 链路。每个虚拟用户使用独立 Thread，结果必须回读同一 request/run 的权威终态和场景语义。脚本不读取 `.env`、不保存完整模型回复，测试产物放在 Git 忽略目录并精确清理自己创建的资源。

### Redis、SSE 与取消

Run 事件保存在 Redis Stream，SSE 建连时只通过 PostgreSQL 验证一次 Run 对当前用户可见。Redis `XRANGE` 从 100ms 开始自适应轮询；有事件即恢复起始间隔，空闲时指数退避，长空闲上限为 4 秒，并加入正负 20% 抖动。PostgreSQL 每 5 秒独立探测一次权威终态，只在 Redis `end` 缺失且 runtime cleanup fence 已清除时合成终态。Request SSE 保持独立的一秒轮询。

取消入口先提交 PostgreSQL durable 状态，再写入带 TTL 的 Redis key。每个活跃 Run 约每 200ms 读取 key，并以约一秒间隔检查 PostgreSQL durable 状态；流式循环只读取进程内 `asyncio.Event`。取消不使用 Pub/Sub，不为每个 Run 长期占用 Redis 连接。Redis Stream 与取消 key 均是短期投影，不拥有业务终态。

API 与 worker 均可按职责创建多个 Redis 客户端池，同一角色配置限制其中每个池的容量，以吸收 SSE、事件发布、取消、ARQ、健康检查和缓存的短操作峰值。连接池按需建立，不能把 256 误读为进程总预算或常驻连接数，也不能按 Run 数一比一估算。

### Sandbox 生命周期

Sandbox 使用固定 AIO 镜像版本和部署级 runtime profile；默认 `core` 关闭浏览器、VNC、browser MCP、Jupyter、code-server 和 NodeJS REPL 等非必需常驻服务。普通 Run 不预建 Sandbox，只有文件、命令或 Sandbox-backed 工具首次真实访问时才创建；失败在隔离执行边界显式暴露，不回退到宿主文件系统。

Docker backend 为每个 Sandbox 保持独立网络。默认从 `10.253.240.0/20` 按 `/28` 分配，可提供 256 个子网；Docker 网络是分配事实，冲突时重新扫描，不建立第二份租约表。部署者必须检查地址池与宿主、VPN 和现有网络是否冲突。

同一 Sandbox generation 的创建和删除由 keyed lock 串行化，不同 Sandbox 以默认 32 个槽位有界并行删除。容器停止宽限默认为 2 秒，provisioner 删除请求超时为 120 秒。删除仍位于 PostgreSQL runtime cleanup fence 内，避免新旧 execution runtime 交错。

### Run 时延事实与前端展示

AgentRun 持久保存五个可空 UTC 时间点：

- `created_at`：owning transaction 内创建 Run 行的时间，事务提交后才对其他事务可见；
- `started_at`：worker 取得有效 lease；
- `prepared_at`：运行清单、Agent 上下文、附件和模型流装配完成，即将进入模型执行；
- `first_output_at`：父线程首次产生非空模型文本、推理文本或工具调用数据；
- `finished_at`：owning transaction 内写入终态的时间，随该事务提交成为权威事实。

新增时间点只由当前有效 lease owner 写入且 write-once。观测写入使用短事务；失败记录 warning，但不能覆盖模型输出或 Run 终态。历史 Run 的缺失值保持 NULL，不补造时间。

API 使用同一 serializer 派生 `dispatch_latency_ms`（创建到开工）、`preparation_latency_ms`（开工到准备完成）、`model_first_output_latency_ms`（准备完成到首次输出）、`first_output_latency_ms`（创建到首次输出）和 `total_latency_ms`（创建到终态），不冗余持久化毫秒值，也不产生负耗时。结果接口、Run 接口与对话历史复用同一投影；历史查询继续批量读取 Run，不增加逐消息请求。前端消息底部与折叠过程只读取同一 `total_latency_ms`，五段明细归入调试面板的 Run 分组；具体展示归属由 [前端优化](./2026-09-05-frontend-optimization.md) 记录。

这些时间点记录的是事务内状态转换，不包含随后提交本身、runtime cleanup 或前端等待与渲染耗时；`total_latency_ms` 因而是 Run 状态机耗时，不是完整的用户端请求时延。

压测客户端指标继续保留用户观察口径：“准备时间”是提交到首个 Run SSE 事件，“首 Token”是提交到首个非空模型语义输出。两者适合评估端到端体验，但不得与 Run 的服务端准备耗时、模型首响混用。

## 替代方案

- 多 worker 作为默认：能改善故障隔离和横向吞吐，但会按副本放大数据库连接预算；当前默认目标明确为单 worker 100 业务并发。
- 把 100 个槽位全部用于业务：既有试验中控制面约 80 秒得不到调度并导致 readiness 短暂失败，因此保留 40 个槽位。
- SSE 使用 `XREAD BLOCK`：低延迟但每条 SSE 长期占用 Redis 连接；当前无状态自适应轮询更符合 100 并发目标。
- SSE 或取消使用 Pub/Sub/共享 Broker：共享订阅器可服务更大规模，但增加注册竞态、重连、背压、丢通知补读和多实例生命周期；100 并发没有证明这份复杂度必要，禁止恢复每 Run 一个订阅。
- 只用 PostgreSQL 做取消或每事件查询：前者放大数据库轮询，后者把查询量绑定到模型输出频率；保留 Redis key 快速提示和 PostgreSQL durable 兜底。
- 预建或按 TTL 保留 Sandbox：预建让纯文本请求支付冷启动，TTL 会改变资源 Owner 与残留进程语义；当前只做首次访问惰性创建和终态清理。
- 共用一个 Docker bridge 或修改 daemon 全局地址池：前者破坏 Sandbox 网络隔离，后者影响同机其他项目并要求重启 Docker。
- 保存派生毫秒值、回放 Redis Stream 或逐 chunk 写 PostgreSQL：分别引入重复事实、受 TTL 限制或高频持久化；保存少量绝对时间点足以支持当前诊断。
- 使用 Locust 取代单文件压测：适合未来分布式发压，但仍需实现 Yuxi 的 Request→Run SSE 和同 Run 因果校验；当前脚本满足本机容量验证。

## 后果

- 默认配置能在一个 worker 内承载 100 个同时 Sandbox Run，并为控制面留出调度余量；它是经过当前机器与工作负载验证的容量基线，不是所有部署的 SLA。
- 高并发首先受外部模型配额、宿主内存、Docker IPAM 和数据库总预算共同约束。增加 worker、修改模型或 Sandbox profile 后必须重新测试，不能按表格线性外推。
- 活跃 SSE 通常在百毫秒级读取事件；长空闲单次等待叠加抖动最多 4.8 秒，Redis `end` 缺失时 PostgreSQL 兜底最多等待下一次 5 秒探测。
- 100 个活跃 Run 的取消 key 读取上界约 500 次/秒；目标接近 1000 时约 5000 次/秒。只有实测 Redis CPU、网络、池等待或取消延迟不可接受时，才评估每 worker 一个共享订阅器。
- 每个 Run 最多新增两次短 PostgreSQL 观测写入，并在首次输出写入前先把已观察 chunk 刷入 Redis，避免观测拖慢用户可见输出。观测故障会留下 NULL，而不是让业务执行失败。
- 默认 core Sandbox 不提供浏览器与 IDE 服务；使用这些能力的部署必须显式选择相应 profile。默认专用地址池可能与个别网络环境冲突。

## 验证

- 当前单 worker 默认配置完成 1、10、20、50、100 阶梯，共 181/181 个 Sandbox Run 严格成功。100 档客户端准备 p95 为 5.43 秒、首 Token p95 为 57.88 秒、整体 p95 为 114.13 秒；Redis 峰值 36 个客户端且 Pub/Sub 为 0，PostgreSQL 峰值 285 个会话（83 active），100 个 Sandbox 内存峰值 16.33GiB，测试后 readiness、ARQ、cleanup、容器和网络均恢复正常。
- 使用 `alibaba-cn:qwen3.8-flash` 的交叉复测中，50 档为 50/50 严格成功；100 档为 96/100，四个失败均是供应商账户级 `AllocationQuota.FreeTierOnly`。100 档仍达到 100 个 Sandbox 与网络，客户端准备、首 Token、整体 p95 分别为 4.74、55.43、110.13 秒；该轮用于区分系统容量与供应商配额，不作为 100 档业务全通过证据。
- 移除取消 Pub/Sub 前的同配置基线为 Redis 136 个客户端，其中 100 个 Pub/Sub；移除后时延仍在原三轮范围内。实时取消探针在进入 `running` 后 235ms 收敛到同一 Run 的 PostgreSQL `cancelled`。
- Docker 专用 IPAM、有界并行清理和 core profile 均由 unit 与真实 provisioner 代理验证；50/70/100 并发分别达到同等数量的隔离容器和网络，结束后均归零。
- Run timing 的 serializer、有效 owner/write-once repository、模型输出识别、API/对话投影和历史 NULL 由 unit 覆盖；真实 PostgreSQL 与 HTTP integration 9/9 通过。Web 全量 unit、lint 和 production build 通过。
- AgentRun 阶段时间字段随 0.7.2 发布版到当前版本的完整业务升级引入；全部 DDL 成功后才能记录当前版本，迁移来源由[Schema 迁移 Owner](../05-storage-workdir/2026-08-24-versioned-schema-migration-owner.md)定义。
- 旧能力不存在：运行代码与配置中没有取消 Pub/Sub channel、每 Run `subscribe`/`unsubscribe`，上述分散的并发 decision 已删除，当前取舍只由本记录和容量参考页维护。
- 重新引入条件：目标显著超过 100、长时间 soak 或故障注入证明当前单 worker、轮询或 Sandbox 生命周期成为真实瓶颈时，基于相同权威终态和因果门禁另行提案；不以供应商抖动或单次客户端时延直接改写协议。
- 尚未执行 1000 并发、Redis 故障注入、多主机和长期 soak；外部模型供应商的时延与配额不由本决定承诺。
