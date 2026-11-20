# PG idle-in-transaction 归因只读诊断脚本

状态：implemented
类型：feature
Owner：scripts/pg-idle-in-transaction-diagnose.sh

## 问题

部署机上报 `QueuePool limit ... reached` 时，先有四种彼此处置完全不同的解释（长请求/SSE 钉住鉴权事务、worker 侧跨外部调用持有事务、僵尸 Run 拖满长流、数据库容量真的不足）；逐条手写 `pg_stat_activity` SQL 既慢又容易在多 compose 栈的机器上连错库、把在线数误当并发流归因。

## 决策

收录一个只读诊断脚本作为容量文档的执行入口，不新增守护进程或配置面。

### 实现方案

- 按 `com.docker.compose.service=postgres` 标签定位容器，凭据取容器环境变量（`POSTGRES_USER`/`POSTGRES_DB`），不给 `-p` 时用容器 project 标签兜底——多栈机器自洽、无需手填 DSN。
- 一次采集输出四段：idle-in-transaction 计数与最长事务、按事务龄分桶（5 分钟粒度到 30 分钟）、明细 top N（backend/client_addr/事务龄/空闲时长/当前查询截断）、非终态 running 与 lease 过期对照；`-w <秒>` 连续采样盯复发时刻（`-w`/`--top` 必须紧跟数值，缺失或非法即显式报错退出），`--whois <IP>` 把 client_addr 映射回本项目容器名。
- 判读口径写入[Agent 并发容量](../../../advanced/agent-concurrency-capacity.md)：api 聚集且事务龄数十秒以上=长请求钉鉴权事务；worker 聚集=外部调用跨事务持有；idle 很少但总量高=按数据库容量处理。
- 兼容 PostgreSQL 14-16：读 `query` 列（17 才新增 `last_query`，不混用）；全部语句只读。

## 替代方案

- 把查询并入 api/worker 的 /ready 或周期任务：给运行面加采集与暴露成本，而这是排障时刻才需要的运维工具；脚本与 bump-version.sh 同款（头部注释块 + awk 打印帮助），沿用 scripts/ 运维工具既有惯例。
- 依赖 pgBadger/外部 APM：部署默认面不含这些组件，结论应能在只用 compose 的机器上取到。

## 后果

- 脚本读 `docker` 套接字与 PG 视图，属部署机运维工具；无应用侧改动。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 单次采集在真实 compose 栈输出四段并只读 | 归因工具本身连错库/写状态 | scripts/pg-idle-in-transaction-diagnose.sh | `bash -n`；`./scripts/pg-idle-in-transaction-diagnose.sh -p <project> --top 3` 实跑输出 idleInTxn=0 与空桶/明细/对照行 | — | Passed |
| 人工 idle-in-transaction 会话确实出列 | 漏报使归因不可信 | 同上 | 事务内挂起 sleep 后实跑：`idleInTxn=1 最长事务=3s`，分桶 `0-300s=1` | — | Passed |
| `-h` 输出用法、参数可组合 | 运维误用 | 同上头部说明 | `./scripts/pg-idle-in-transaction-diagnose.sh -h` 打印目的/用法/判读 | 不适用 | Passed |
