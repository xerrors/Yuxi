# 集成测试 Schema 检查的连接池生命周期

状态：implemented
类型：bug-fix
Owner：backend/test/integration/conftest.py

## 问题

维护集成测试时，`ensure_live_api_schema` 在 `anyio.run` 的临时事件循环内初始化 PostgreSQL manager。Schema 查询结束后，未关闭的 LangGraph 连接池仍有后台任务；退出循环的取消阶段可持续等待，Runtime job 表现为收集测试后挂起，而非业务断言失败。

## 决策

Schema 检查以 `try/finally` 在同一事件循环内关闭 manager。成功与版本不匹配均执行清理，原版本错误继续传播。后续测试通过 `get_async_session` 或 `get_async_session_context` 在自己的循环内按需重新初始化，不共享已关闭循环的池。HTTP API 进程持有独立 manager，不受测试进程关闭影响。

主动压缩 HTTP 测试直接调用 checkpointer 初始化方法，改由函数级 `postgres_checkpointer` fixture 在测试循环内显式初始化、完成测试后关闭，不依赖 session Schema fixture 的初始化副作用。

## 替代方案

不延长 workflow 超时，不跳过 Schema 校验，也不更改运行时连接池实现。把连接池保留到 session teardown 仍跨越临时事件循环边界，不能消除问题。

## 后果

Schema fixture 仅拥有一次检查所需资源，不再隐式提供跨测试循环的已初始化 manager。此次修复只恢复资源生命周期，不改变 Schema 版本、业务行为或测试断言；因此与实现一同记录，无需另设提案阶段。

## 验证

`test/unit/services/test_live_api_schema_fixture.py` 检查成功及失败路径均在同一循环内关闭池，并保留原错误。恢复旧实现时，两项均因缺少关闭事件失败。

独立 PostgreSQL 和临时文件系统下，保留原 session autouse fixtures、仅将 HTTP 清理端点替换为空资源测试服务，旧实现栈停在 `ensure_live_api_schema → anyio.run → _cancel_all_tasks`，数据库连接均 idle；修复后 Workdir/Skill 两文件五项测试完成。该隔离复验不代替真实 Skill artifact HTTP 授权链路或完整 Runtime workflow，后者以 CI 结果为准。

上述五项与主动压缩测试连续运行六项通过；压缩测试经 ASGI HTTP 回读真实 PostgreSQL checkpoint，证明 session 检查关闭后，直接 checkpointer consumer 可在自己的循环内重新初始化并正确清理。
