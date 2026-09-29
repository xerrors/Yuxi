# Sandbox 原生异步文件读取

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/agents/backends/sandbox/backend.py

## 问题

DeepAgents 0.7 的异步 `read_file` 调用 `BackendProtocol.aread()`。`ProvisionerSandboxBackend` 曾只覆盖同步 `read()`，使异步调用落入上游基于 shell stdout JSON 的默认实现。该路径绕过 Yuxi 的可读路径、文件类型和图片传输规则，并会因命令回显或截断输出产生 `unexpected server response`。

## 决策

`ProvisionerSandboxBackend.aread()` 使用 `agent-sandbox` 的原生异步文件 API，并与同步 `read()` 共享路径分类、类型错误、内容规范化和 `ReadResult` 分页组装规则。

文本通过 `AsyncSandbox.file.read_file()` 按行读取。图片通过 `AsyncSandbox.file.download_file()` 流式读取原始字节，在 Yuxi 内执行 `MAX_BINARY_BYTES` 限制和 base64 编码；PDF、Office、音频、视频及未知二进制保持同步读取的拒绝语义。授权和参数检查先于任何远端 client 构造。

每次需要访问 sandbox 的异步读取都创建一个 owning `httpx.AsyncClient`，通过 `async with` 关闭，并将其显式传入 `AsyncSandbox`。同步 provisioner 连接发现通过 `asyncio.to_thread()` 执行；当前不缓存没有进程级关闭 Owner 的异步 client。

## 替代方案

- `asyncio.to_thread(self.read, ...)`：能恢复结果正确性，但取消不能中止底层同步 HTTP，并会继续占用线程池，未采用。
- 保留 DeepAgents 默认 shell 读取：依赖纯 stdout JSON、输出上限和 shell 传输，无法闭合 Yuxi 授权与文件类型契约，未采用。
- 立即增加进程级共享 `AsyncClient`：需要新增 API/worker 生命周期和关闭接线，超出本次缺陷修复范围，未采用。

## 后果

- 异步 `read_file` 不再依赖 shell stdout 或 sandbox 临时 base64 文件，图片字节在下载过程中即受大小限制。
- provider 发现仍会短暂占用线程；文件数据面和 HTTP 等待保持原生异步。
- 每次异步读取建立独立 HTTP client，牺牲连接复用以换取明确关闭；只有运行数据证明必要且建立进程级生命周期 Owner 后才引入共享 client。
- 同步 `read()` 的传输实现保持不变。

## 验证

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/backends/test_sandbox_backends.py -q`：83 passed；覆盖越界路径不构造 client、文本 offset/limit 与 `next_offset`、HTTP client 关闭、图片原生流读取、流式超限以及文档和其他二进制拒绝。
- Compose 临时 sandbox 探针：通过原生异步 endpoint 回读文本分页窗口和 PNG 原始字节，核对 `ReadResult` 与 base64 后释放 runtime。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：1502 passed，39 skipped。
- `backend/.venv/bin/ruff check backend/package` 与 `backend/.venv/bin/ruff format backend/package --check`：通过。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，后者 61 tests。
- `cd docs && pnpm run build`：因仓库 `docs/node_modules` 权限不可写，使用同一锁文件和源码在临时目录构建通过。
- 独立 Codex Reviewer 完整检查需求、提案、diff、测试与 `agent-sandbox 0.0.30` API 契约，未发现阻塞问题。
