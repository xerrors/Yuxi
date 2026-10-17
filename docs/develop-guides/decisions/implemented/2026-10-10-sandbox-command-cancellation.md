# 沙盒命令取消与有界清理

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/backends/sandbox/backend.py

## 问题

取消 Agent 的异步等待后，同步线程中的 GEM 命令调用仍可等待到原始长超时。provisioner 的删除与发现无限等待操作 pin，清理请求重试积累会占用同步线程资源；前端依据 Run 终态提前显示结束，同一线程的新消息仍被待清理 runtime 阻挡。

## 决策

异步命令使用独立 GEM shell session、异步启动与短周期等待，取消时先收敛启动请求，再显式终止该 session 的进程。重复取消等待有界收尾完成。未指定工具超时时使用现有 `SANDBOX_EXEC_TIMEOUT_SECONDS`；硬超时缺少退出码时返回失败码 124。

provisioner 的操作锁最多等待 5 秒，忙碌返回可重试的 503，失败的删除不能移除另一个删除者的栅栏。代理等待 pin 时取消，晚取得的 pin 仍被归还。健康检查使用异步路由，不依赖同步线程池。

PostgreSQL 的 cleanup fence 与 FIFO 保持原有所有权。当前运行接口返回终态但待清理的 Run；Web 的看门狗、断线和刷新路径等待清理完成后确认终态。worker 的清理异常记录具体 Run 和 traceback。

## 替代方案

- 继续取消同步线程等待：已经开始的远端调用仍运行。
- 取消时直接放行下一条消息：旧命令和旧清理可能影响新 runtime。
- 只增加线程数或缩短所有脚本超时：不能闭合取消传递与清理语义。

## 后果

每条异步命令增加 session 创建、短轮询和最终输出读取请求。忙碌清理仍阻挡同一会话，其他 runtime 与健康检查可继续响应。取消不回滚已经完成的文件和外部副作用。GEM 终止失败会记录异常，实际 runtime 删除仍以 generation-fenced 清理结果为准。

## 验证

- 命令取消 unit 覆盖启动确认前后取消、重复取消与硬超时失败码。
- provisioner unit 覆盖活跃 pin 下删除超时、发现恢复、并发删除所有权和已取消代理的 pin 回收。
- Web unit 覆盖看门狗、自然断流及新标签页恢复 completed/cancelled/interrupted 待清理 Run。
- HTTP/worker/GEM 集成测试回读实际 PID、文件、PostgreSQL 清理标记、Redis end 事件和 generation；使用隔离 Schema，固定模型准备结果，不启动额外共享队列 worker。
- 实际部署中的人工回归观察到两次长脚本停止：终止接口约 0.2 秒响应，generation 删除和终态事件约 3 秒完成，随后新 Run 可执行。该观察不替代当前补丁快照的 PostgreSQL 和完整 assembled-path E2E 验证。

上游补丁快照的实际检查：后端完整 unit 2520 项通过，相关后端 unit 198 项通过，Web 队列 unit 36 项通过；忙碌删除和线程池饱和下健康检查的真实 HTTP/GEM 两项通过。工程契约检查覆盖 184 份文档，其 unit 63 项通过，改动文件 Ruff、Web ESLint/Vite build 和 VitePress build 通过。前端全量未全绿，至少 5 项失败已在未打补丁的官方基线复现，不能将该命令记录为 Passed。

上游 PostgreSQL 集成和完整 API/ARQ/SSE E2E 在当前环境为 Not run；相关测试可在维护者配置的 Compose 环境执行。详见[事故复盘](../../postmortems/2026-10-10-sandbox-command-cancellation.md)。
