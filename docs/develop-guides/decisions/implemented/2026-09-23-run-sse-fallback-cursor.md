# Run SSE 补发终态与事件游标

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/agent_run_service.py

## 问题

数据库补发的 end 复用普通事件 ID 时，Web 的游标去重会丢弃该通知，需再依赖断流后的状态查询收尾。该缺陷本身不能证明页面永久等待。连续读取流尾不能证明写入结束；最大数字游标引入额外重连状态。本记录拥有 [工具异常与 SSE 决策](./2026-09-23-agent-tool-errors-and-sse-terminal.md) 中 SSE 部分的详细规则。

## 决策

补发条件是空批次、数据库终态和 runtime cleanup 完成。真实 Redis 事件使用原 ID；数据库补发的 end 不携带 ID，客户端按终态通知处理，重连仍使用最后实际消费的 Redis 游标。worker 发布顺序、工具错误策略与前端普通事件去重保持原契约。

## 替代方案

- 最大数字游标：需要额外协议分支，不能解决迟到写入竞态。
- 各消费者豁免重复 end：让数据库通知继续冒用 Redis 事件 ID，增加客户端维护成本。
- 连续读取流尾：只能改变竞态窗口，不能形成写入完成边界。

## 后果

补发通知允许重连后重发；PostgreSQL 终态与结果是最终事实。Redis 发布和数据库事务没有原子性，不承诺终态后的任意迟到事件全部投递。普通事件继续按实际 ID 去重。SSE 心跳属于注释行，不会刷新 Web 的业务事件看门狗。

## 验证

后端 unit 覆盖有限尾部事件消费、流尾重连、compact request_id 与清理完成前不补发。Web 单测调用真实消费函数，对照复用 ID 和无 ID 两种通知：前者被丢弃并触发断流查询，后者直接结束生成；重复普通消息只处理一次。

执行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_agent_run_service.py -q`，58 项通过；Web 完整 unit 354 项通过，lint 通过。真实异步 Agent E2E 的业务断言通过，清理阶段因共享 Workdir 保护拒绝删除而报错，不能记为整套 E2E 通过。对该已完成测试 Run 的只读 HTTP 探针回读 PostgreSQL 终态和输出指针、重放 Redis 事件，并连续两次从流尾重连，均收到一个不带 ID 且绑定同 Run/request 的 completed 通知；同等重连断言纳入 E2E 文件。

全量后端 unit 使用现有依赖运行时为 2282 passed、58 skipped、2 failed，失败位于未修改的 sandbox provisioner 配置测试。常规 uv 同步命令因容器依赖目录权限失败。工程契约检查及其 62 项测试通过。
