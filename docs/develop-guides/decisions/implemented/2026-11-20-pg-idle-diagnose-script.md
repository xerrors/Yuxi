# PG idle-in-transaction 归因只读诊断脚本

状态：implemented
类型：feature
Owner：scripts/pg-idle-in-transaction-diagnose.sh

## 问题

部署机上报 `QueuePool limit ... reached` 时，先有四种彼此处置完全不同的解释（长请求/SSE 钉住鉴权事务、worker 侧跨外部调用持有事务、僵尸 Run 拖满长流、数据库容量真的不足）；逐条手写 `pg_stat_activity` SQL 既慢又容易在多 compose 栈的机器上连错库、把在线数误当并发流归因。

## 决策

收录一个只读诊断脚本作为容量文档的执行入口，不新增守护进程或配置面。

### 实现方案

- 使用运行中容器的 Compose 标签确认单一项目；多栈必须显式指定项目。SQL 采集前确认该项目恰有一个 PG 容器，且所选项目的 API/worker 全部指向其标准服务名、端口、用户名、数据库，解析地址只属于该 PG 容器。验证在应用容器内执行，不读取到宿主或输出其连接串。目标不符或不明时不查询，通过 PG 容器本地 Unix socket 读取已验证的默认库。
- 每次采集输出集群空闲事务数、其中最长事务龄、客户端连接数、按事务龄分桶与明细，以及应用库的 Run/lease 对照。需要完整统计权限，否则拒绝采集。持续模式每次重新确认目标，采样间隔必须为正整数；`--whois <IP>` 按完整地址匹配所选项目的运行容器。
- [Agent 并发容量](../../../advanced/agent-concurrency-capacity.md)说明证据边界：单次快照以及低空闲事务数都不能给出根因，需要与应用和数据库的同时间窗观测交叉核对。
- 查询使用 PostgreSQL 14–16 已有的 `pg_stat_activity` 字段，全部为只读。末条 SQL 摘要分享前需要脱敏。

## 替代方案

- 把查询并入 api/worker 的 /ready 或周期任务：给运行面加采集与暴露成本，而这是排障时刻才需要的运维工具；脚本与 bump-version.sh 同款（头部注释块 + awk 打印帮助），沿用 scripts/ 运维工具既有惯例。
- 依赖 pgBadger/外部 APM：部署默认面不含这些组件，结论应能在只用 compose 的机器上取到。
- 自动连接外部数据库：需要额外的凭据与连接约束，不能可靠退回内置库；交由运维在既有受控连接中处理。

## 后果

- 脚本读 `docker` 套接字和 PG 视图，不改变应用状态。只有数据库容器仍在运行、没有 API/worker 可核对，或者采用无法确认的自定义拓扑时，不输出看似正常的内置库统计。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 只有明确且可核验的目标才查询，连接串不出应用容器 | 混采不同 Compose 项目、外部库或同主机上的其他库 | scripts/pg-idle-in-transaction-diagnose.sh | `python3 -m unittest scripts.ci.test_pg_idle_diagnose`，替身 Docker 执行真实的容器内核验代码 | 多项目不指定、外部连接串、额外解析地址、缺应用或双 PG 均不执行 SQL | Passed |
| 摘要只统计空闲事务的最长事务龄，权限不足不打印零；零间隔拒绝 | 把长活跃事务计入空闲年龄、报出伪健康或忙循环 | 同上 | 同一功能回归、`bash -n scripts/pg-idle-in-transaction-diagnose.sh` | 恢复旧 SQL 会输出活跃事务龄；绕过权限检查或允许零间隔时用例失败 | Passed |
| 在真实 Compose 栈确认目标再采集 | 部署差异使内置目标未能解析 | 同上 | 在可用 Docker 环境运行脚本并核对目标及四段输出 | 不适用 | Not run |
