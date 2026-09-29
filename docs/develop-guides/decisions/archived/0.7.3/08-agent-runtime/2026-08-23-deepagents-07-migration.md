# DeepAgents 0.7 迁移与依赖升级

状态：archived
类型：architecture
Owner：backend/package/yuxi/agents/backends/composite.py

## 问题

Yuxi 此前锁定 `deepagents==0.6.7` 与旧版 LangChain 底座（langchain 1.3.10、langchain-core 1.4.8）。升级到 DeepAgents 0.7 存在以下硬性断点：

1. **Backend factory 移除**：`FilesystemMiddleware` 与 `SummarizationMiddleware` 不再接受返回 backend 的 callable，必须传已初始化的 `BackendProtocol` 实例。
2. **`CompositeBackend` 路由补丁**：0.6 时 Yuxi 维护 `CustomCompositeBackend` 修复 route-aware glob 逻辑；0.7 官方已修复此问题并支持 `GlobResult.truncated`。
3. **协议扩展**：0.7 新增 `grep(max_count=...)`、`ReadResult` 结构化分页字段与可选 `delete`。
4. **全仓依赖滞后**：多项直接与间接依赖落后，需要安全批量升级并逐项验证。

## 决策

1. **依赖升级**：
   - backend：`deepagents>=0.7.7,<0.8`、`langchain>=1.3.15`、`langchain-core>=1.6.0`、`langchain-anthropic>=1.6.0`，并执行 `uv lock --upgrade` 升级 26+ 直接依赖。
   - packages/yuxi-cli：升级 lock 至最新。
   - web：升级 pinia (4.0.3)、js-yaml (5.3.0, 适配命名导入)、markdown-it (15.0.0)、katex (0.18.4)、@opencode-ai/models (0.0.51) 等。
2. **Backend 实例装配**：
   - 在每次 `get_graph()` 时基于已准备好的 `context` 调用 `create_agent_composite_backend()` 构造本 Run 独享的 `CompositeBackend` 实例（`artifacts_root` 设为 `{workdir}/outputs`）。
   - `create_agent_filesystem_middleware` 与 `create_summary_middleware` 接收同一 backend 实例，保持 user/thread/workdir 隔离边界。
   - 删除 `CustomCompositeBackend`，直接使用官方 `CompositeBackend`。
3. **文件工具控制**：
   - `FilesystemMiddleware(tools=...)` 显式限定为 `["ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"]`，排除未实现且缺少审批/审计设计的 `delete`。
   - 采纳 0.7 语义：文件工具自身已有截断/分页机制，结果不再做二次 eviction；仅保留 `open_kb_document` 与非文件工具（如 execute）的 eviction。
4. **协议适配**：
   - `ProvisionerSandboxBackend.read()` 补齐 `start_line` / `end_line` / `next_offset` 结构化分页字段，非正 limit 返回 `no_lines_requested`。
   - `ProvisionerSandboxBackend.grep()` 支持 `max_count` 并跨 readable roots 传播 `truncated`。
5. **Summarization 适配**：
   - 适配 0.7 `_offload_to_backend(backend, messages, session_id)` 签名与 `_summarization_session_id` 状态。
   - 采纳 0.7 inline media offload：内联 `data:` 媒体在摘要前落盘为路径引用。

## 替代方案

- **整体切换为 `create_deep_agent`**：放弃 Yuxi 自定义 AgentRun、持久化子智能体、审批、Steer、Skills 链路，改用上游全家桶。被拒绝：风险远超依赖升级本身，破坏 Yuxi 核心调度与持久化事实。
- **保留全局可变 backend factory**：违背 0.7 契约，且跨 Run/跨用户存在竞态风险。被拒绝。

## 后果

- 删除了 Yuxi 自行维护的 `CustomCompositeBackend`。
- 文件搜索与大文件读取获得 0.7 结构化续读提示。
- 依赖漏洞审计 `uv audit --locked` 0 漏洞。
- 1475 项 unit 测试全部通过。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| DeepAgents 0.7+ 正确装配并隔离 | `docker compose exec api uv run --no-sync --group test pytest test/unit/backends/test_sandbox_backends.py -q` | Passed；覆盖 factory 移除、每 Run backend 实例和 outputs 路径派生 |
| 上下文压缩与摘要正确派生 outputs 前缀 | `docker compose exec api uv run --no-sync --group test pytest test/unit/middlewares/test_summary_middleware.py -q` | Passed；覆盖 session 隔离、media offload 与 outputs 前缀 |
| 全仓 unit 门禁全绿 | `docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow" -q` | Passed (1475 passed) |
| 工程契约与依赖安全审计通过 | `python3 scripts/verify_engineering_contracts.py` + `uv audit --locked` | Passed；0 漏洞 |
| 前端构建与测试通过 | `pnpm run build && pnpm run test:unit && pnpm run lint` | Passed；82 unit passed |
| yuxi-cli 构建与测试通过 | `uv run --isolated --no-dev --with pytest pytest tests -q` | Passed；90 unit passed |
