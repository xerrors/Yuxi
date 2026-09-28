# CLI 查看可用 Agent

状态：implemented
类型：feature
Owner：packages/yuxi-cli/src/yuxi_cli/agent.py

## 问题

CLI 登录用户可以按已知 slug 调用 Agent，却无法先发现当前账号可调用的 Agent，也无法在终端核对指定 Agent 的配置。Agent 可见性与角色配置过滤已经由 `GET /api/agent`、`GET /api/agent/{slug}` 和 `AgentRepository` 拥有，CLI 不能复制权限判断或绕过服务端返回内容。

## 决策

新增 `yuxi agent list` 与 `yuxi agent show <slug>`。CLI 通过现有认证 API 读取当前用户可见的主 Agent：列表展示默认标识、名称、slug 和描述；详情展示基础信息，以及 `config_json.context` 中的模型、Skills、系统提示词、工具和其余配置。两个命令支持与知识库查询命令一致的 `--remote` 和无 ANSI 原始 `--json` 输出，并通过 discovery 中的专用能力声明拒绝不支持该契约的旧服务端。

CLI 不推测运行时资源解析结果。`tools`、`knowledges` 和 `skills` 未配置时展示为使用全部可用资源，显式空列表展示为不启用；MCP 未配置或为空列表时展示为未直接选择，Skill 激活后的 MCP 依赖由运行时加载；`subagents` 未配置或为空列表时均展示为使用全部可见子 Agent。详情接口仍允许按既有授权读取可见子 Agent，列表保持服务端现有的主 Agent 范围。

## 替代方案

- 新增 CLI 专用后端路由：会复制现有 Agent 序列化与授权边界，没有新的服务端语义，拒绝。
- CLI 拉取管理接口或本地配置后自行判断可见性：会绕过 repository 权限 Owner，拒绝。
- 只打印完整 JSON：不满足终端用户快速识别默认 Agent 与关键配置的需求；保留为显式选项。

## 后果

Agent 可见性、详情过滤和不存在或无权访问时的 404 继续由现有后端 Owner 决定。CLI 把 slug 编码为单个 URL 路径段，仅展示服务端授权后的响应，并在所有输出模式下对畸形响应、未登录状态和缺失能力声明显式失败。人类可读输出移除服务端文本中的终端控制字符，避免远端字段改变本地终端状态。

人类可读输出只摘要稳定关键字段，其余字段保留在“其他配置”和 `--json` 中，避免维护平行 schema。后端扩展 Agent 查询契约时，需要同步 discovery 能力、CLI 展示与负向测试。

## 验证

- `cd packages/yuxi-cli && UV_PYTHON=3.13 uv run --group test pytest -q`：111 passed。
- `uvx ruff format/check`（Agent 新实现与测试）：通过。
- `cd backend && UV_PYTHON=3.13 uv run --group test pytest test/unit -m "not slow"`：1590 passed。
- API 容器 unit fallback `uv run --no-sync --group test pytest test/unit -m "not slow" -q`：1569 passed，40 skipped；常规同步命令因容器内 editable 文件权限失败。
- 真实 integration 权限过滤测试：1 passed；隐藏 Agent 的真实 HTTP 请求返回 404。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，后者 61 passed。
- `cd docs && pnpm run build`：通过；保留既有 VitePress/Rolldown 警告。
- 独立 Reviewer 已复核功能修复与最新 `main` 的最终 diff，无新增代码问题。
