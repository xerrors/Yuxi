# 降低空闲 worker 探针与开发文件监听开销

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/worker_health.py

## 问题

Issue #1078 中周期性 ARQ 健康检查导入完整 worker 业务依赖，空闲时仍消耗 CPU 并超时；Vite 高频轮询进一步增加开发环境负载。目标是减少这两条周期路径的开销，保留失活检测与跨平台文件监听。其他进程峰值和业务执行性能不在范围内。

## 决策

### 实现方案

将 ARQ 心跳键、间隔和 TTL 上界放入轻量 worker_health 模块，生产者和 readiness 消费同一契约。开发与生产 Compose 直接运行该模块，复用 Redis 连接配置读取心跳值及 PTTL；缺失、空值、无过期时间、超长 TTL 或连接失败返回非零。保持现有检查频率、超时与重试。Vite 保留 polling 并设置 1000 ms 间隔。

## 替代方案

延长检查周期仍保留重依赖导入；关闭探针失去失活检测；关闭 polling 会影响部分 Windows/Docker 挂载的热更新，因此均不采用。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 探针不加载 worker 业务依赖 | 每次检查重复重导入 | worker_health、Compose | 导入隔离 unit、命令检查 | 阻止导入 run_worker、LangGraph、SQLAlchemy | Passed |
| 心跳仍能表达失活 | 永久或过期键误判健康 | worker_health | unit、真实 Redis / ARQ integration | 缺失、过期、永久、超长 TTL、不可达 | Passed |
| 文件变更仍被监听 | polling 配置失效 | vite.config.js | Vite watcher 实测：1000 ms 轮询，813 ms 后发出 HMR update；web lint、380 unit、build | 文件修改后无通知即失败 | Passed |

## 后果

轮询通知延迟增加到约一秒。心跳仍是共享队列级事实，多副本中不能辨认单个进程的失活；维持现有语义。Linux 测试不能替代 Issue 用户机器上的 CPU 和 Windows 热更新复测。

验证使用独占临时 Redis 与 API 镜像，工作树挂载为 `/repo`，`PYTHONPATH=/repo/backend/package`；后端完整 unit 2428 项通过。容器中的 `YUXI_SKILL_PROJECTION_DIR` 指向可写临时目录。针对 Redis 的测试不需要 API 或沙盒，执行 `python -m pytest -p no:cacheprovider --confcutdir=test/integration/services test/integration/services/test_worker_health_redis.py -q`，6 项通过；`--confcutdir` 排除全局 API/沙盒清理 fixture，保留测试自身的随机 Redis 键清理。

导入隔离和 Compose 装配由 `test/unit/services/test_worker_health.py` 与 `test/unit/config/test_docker_compose_checkpointer.py` 覆盖，readiness 回归仍通过。工程契约检查及其 62 项测试、Ruff、文档构建与 `git diff --check` 通过。前端和文档使用 `npm run` 执行 package.json 中相同脚本，避免 pnpm 重装跨工作树复用的依赖目录。

Windows Docker 热更新、浏览器实际消费 HMR、用户机器的整体空闲 CPU 及 402% 峰值来源为 Not run。探针检查的是共享 ARQ 心跳；API readiness 仍单独检查启动、数据库和 reconciliation 租约。
