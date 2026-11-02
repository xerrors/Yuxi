# 收敛 loguru 日志调用契约

状态：implemented
类型：bug-fix
Owner：backend/yuxi/infrastructure/observability/logging.py

## 问题

项目统一使用 loguru，但部分调用仍按标准 `logging` 的习惯传递 `%s` 占位符和 `exc_info=True`。loguru 使用 `{}` 格式化，`%s` 会原样留在日志中；`exc_info` 不是 loguru 的异常参数，`logger.error(..., exc_info=True)` 不会输出堆栈。受影响日志覆盖 Run 派发、lease 续租、后台作业、取消恢复和 Langfuse 清理，排障时丢失 run_id、job_id、turn_id、phase 与 traceback。

## 决策

所有 loguru 调用改用 `{}` 占位符；需要异常堆栈时使用 `logger.opt(exception=True)` 或 `logger.exception`。标准 `logging` 的 `%s` 用法不在本决定范围内，`sandbox_runner` 与 `tool_error_guard` 继续使用标准 logging 契约。

### 实现方案

`backend/yuxi` 中导入 `yuxi.infrastructure.observability.logging.logger` 或 `loguru.logger` 的模块统一遵守 loguru 格式化规则。新增静态 unit guard，解析源码 AST 并拒绝 loguru 调用中的 `%s`/`%d`/`%i`/`%f`/`%r` 占位符和 `exc_info` 关键字；guard 同时提供负向样例，证明旧写法会被发现，标准 logging 不会被误报。

## 替代方案

- 保持标准 logging 写法并让 loguru 兼容 `%`：loguru 不支持，且会继续静默丢参数。
- 全部改成 f-string：可读但丢失参数化与延迟格式化，且不能统一拦截 `exc_info`。
- 只依赖 code review：同类调用分散在 worker、Agent 服务和后台作业，已多次遗漏，无法防止回归。

## 后果

日志正文会显示真实 run_id、job_id 等标识，异常路径会附带堆栈；业务状态、接口、持久化和事件顺序不变。新增的静态 guard 会阻止后续 loguru 调用重新引入标准 logging 约定。

## 验证

- `cd backend && uv run --python 3.12 --group test pytest test/unit/server/test_loguru_logging_contract.py --noconftest -q`：5 passed。
- `cd backend && uv run ruff check yuxi test/unit/server/test_loguru_logging_contract.py`：通过。
- `cd backend && uv run ruff format yuxi test/unit/server/test_loguru_logging_contract.py --check`：通过。
- `cd backend && uv run ruff check --select I yuxi test/unit/server/test_loguru_logging_contract.py`：通过。
- 未运行完整 unit/integration/E2E：当前 Windows 环境缺少 `os.O_DIRECTORY`，且未启动 Docker Compose；由 CI 的 Linux unit 套件与仓库集成环境补充。
