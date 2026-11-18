# 前端测试缓存耗尽共享宿主磁盘

日期：2026-10-21
Owner：frontend/test/helpers/createTestServer.js
关联决策：[SSR 测试缓存生命周期](../decisions/implemented/2026-10-21-architecture-convergence.md#测试宿主)

## 影响

前端测试产生的 Vite 临时缓存耗尽共享宿主磁盘，并发完整 unit 重跑放大了累积占用。生产 worker 日志报告 Redis AOF 写入空间不足与 `MISCONF`，生产 worker 自动重启。隔离 PostgreSQL 连接中断，使最后两项协作 E2E 和完整后端 unit 失败。恢复后生产健康检查通过，但生产数据与业务完整性未由本轮测试核验，不能据健康恢复声明没有影响。

## 事实时间线

时间使用 Asia/Shanghai（UTC+08:00），来源为本轮缓存目录元数据、运行进程检查、Docker 日志、Redis 持久化状态和隔离 PostgreSQL 恢复记录。

- 17:56 起：本轮前端测试（包括 focused 基线）创建多个独占 `yuxi-vite-test-*` 临时缓存目录。
- 18:05:17.142–18:05:38.214（已保存日志窗口）：生产 worker 日志记录 Redis AOF 写入空间不足的 `MISCONF`，worker 随后自动重启。测试进程也报告 `ENOSPC`，宿主可用容量降至约 2.1 MB。
- 18:06:24.836：Docker 容器 StartedAt 记录生产 worker 容器启动。未手动修改生产配置或删除生产持久化数据。
- 18:06:27.563：应用日志记录 Starting worker，与容器 StartedAt 分属不同观察点。
- 18:06:40：隔离 PostgreSQL 恢复可用。生产服务健康恢复后，业务数据完整性仍属未验证范围。
- 18:10:47：只读检查生产 ready 为 HTTP 200 / ready，Redis 当前 AOF 写入、AOF 重写与 RDB 保存状态均为 ok。过期的 running AgentRun 与 BackgroundJob lease 数均为 0。这些观察不证明业务数据完整性。
- 恢复操作：停止本轮全量前端测试并确认没有活跃测试进程，只清理 17:56–18:05 创建、sourcemap 来源属于本仓库的 113 个临时缓存目录，回收约 5.5 GB。保留业务 volume、state 与环境配置。

## 因果链

直接事实是测试缓存持续占用磁盘、宿主容量耗尽、Redis AOF 与测试写入失败，并出现生产 worker 自动重启。测试和生产虽使用不同 Compose 数据资源，却共享同一宿主文件系统。环境隔离没有隔离磁盘容量。

本地 Vite 源码显示默认客户端 discovery 会创建依赖 optimizer，实际测试宿主消费者只读取 SSR 模块，唯一监听 HTTP 的测试读取 PDF CMap 静态文件。测试关闭期间观察到临时目录删除报 `ENOTEMPTY`，并残留 `deps_temp`。客户端预构建在清理期间仍有写入是与这些事实一致的机制判断。负控只证明旧配置会启动 client optimizer，具体 close/目录重建交错未确定性复现，不能仅凭关闭调用返回认定磁盘责任已释放。

## 安全网为何漏过

既有测试断言业务和 SSR 结果，没有直接断言客户端 optimizer 未启动、服务关闭后缓存目录删除且不再生。独占 cacheDir 只隔离命名，不能限制总容量。并发完整测试放大了累积写入。删除失败最初被视为暂态竞争并增加重试，没有先关闭不需要的后台预构建，未能在生产磁盘写入受阻前拒绝这种测试宿主配置。

## 修正与验证

修复 Owner 和直接 oracle 见[关联决策](../decisions/implemented/2026-10-21-architecture-convergence.md#测试宿主)。修复只修改测试 helper 的客户端 discovery 配置，保留 shipping Vite 配置与真实 SSR/静态资源链路。不增加生产部署配置或自动清理业务数据。

Reproducer 是通过旧测试宿主启动真实 Vite、加载生产模块、关闭服务并回读独占缓存和客户端 optimizer。实际命令 `cd frontend && node --test --test-concurrency=1 test/unit/testServerLifecycle.test.js test/unit/sessionCooperation.test.js test/unit/pdfPreviewAssets.test.js` 通过 11 项，覆盖真实 SSR、生产 Vue 组件编译、PDF CMap HTTP/字节与原协作用例。生命周期用例确认 client depsOptimizer 不存在，并在 close 后立即及 300 ms 后读取 cacheDir 均为 ENOENT。

只在内存恢复 noDiscovery:false 的独立子进程负控因 optimizer 实际存在而失败，Node 测试子进程返回非零。负控父进程等待退出后才清理自己的独占临时目录。

独立 Reviewer 的生命周期与 PDF 用例 2 项通过。修复后的完整 `cd frontend && pnpm run test:unit` 756 passed，0 failed、skipped、cancelled，结束后回读本轮缓存目录残留为 0、剩余容量约 5.4 GB。lint 与冻结后的 build 通过。受影响的 host 后端全 unit 串行重跑 3080 passed。隔离完整生命周期/协作 E2E 串行重跑 30 passed、0 skip，389.33 秒。远程 CI 未运行。磁盘耗尽时的环境失败不计为通过。这些测试验证隔离业务链路，不证明生产业务数据完整性。

## 防复发措施

`createTestServer` 的用途限制为 SSR 与静态资源测试，生命周期 unit 进入既有前端完整测试入口，失败须使 gate 非零退出。本轮完整测试串行执行并检查临时缓存回收。这属于本轮操作，未增加跨 Agent 的全局锁。持续门禁是 optimizer 与缓存生命周期 unit，清理仅作用于测试拥有的目录。实际验证证据见关联决策。

## 未解决风险

宿主磁盘共享仍然存在，没有为本轮引入配额、独立文件系统或部署监控机制。其他构建工具、缓存与业务数据增长仍能耗尽同一容量。生产 Redis 在拒绝写入期间的调用失败、重试与业务完整性未全面核验。健康检查只能证明当前可用，不能补证事故期间的业务结果。

## 后续有限核验

2026-10-21 19:01:39（UTC+08:00）通过生产 API 容器已有数据库连接配置执行聚合查询，事务为 `REPEATABLE READ READ ONLY`，回读 `transaction_read_only=on`；单语句超时 5 秒、锁等待超时 1 秒。未读取业务正文，未修改生产数据或重启生产服务。

事故附近 18:00–18:15 内按 `created_at` 新建的 Input、Run 和 BackgroundJob 记录均为 0。对应窗口的 Input 消费归属、Message 执行归属和 Turn 结果指针检查未发现不一致，但部分集合为空，不能由此声明该窗口没有执行或业务完整性已验证。此前创建的执行、客户端未持久化的请求以及 Redis 拒绝期间的外部调用不由这些查询覆盖。

当前 `running` / `cancel_requested` Run 中租约为空或已过期的数量为 0。`running` BackgroundJob 对应数量为 0。终态完成超过 15 分钟仍待清理的 Run 数量为 0。该快照没有发现需要修复的现存滞留记录。缺少事故前基线，仍不能证明历史请求无丢失或外部副作用无影响。
