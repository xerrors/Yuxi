# MCP 仅连接远程服务

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/mcp/service.py

## 问题

原内置图表 MCP 通过 stdio 启动进程，内置例外与直接传入配置使管理接口的远程限制不能覆盖所有执行入口。

## 决策

移除系统 mcp-server-chart，加入固定 Streamable HTTP DeepWiki 定义（slug 为 `deepwiki-official`，展示名称为 DeepWiki），URL 为 `https://mcp.deepwiki.com/mcp`。新内置项默认待管理员启用。使用独立的 `deepwiki-official` 标识避免占用用户既有 `deepwiki` 配置，不新增同步策略。固定定义由 `agents/mcp/builtin.py` 拥有，沿用 [内置内容发现](2026-09-17-builtin-discovery.md) 的显式定义机制。

仅支持 sse 与 streamable_http，在客户端创建与缓存读取前拒绝其他 transport；`MCPServer.to_mcp_config` 不再生成 stdio 配置。启动同步禁用所有历史非远程配置，删除系统创建的退役图表项；用户自己创建的同名远程记录保持用户所有权。保留历史数据库列与详情展示供管理员迁移，取消其运行时用途。MySQL 报表改用 Markdown 表格，不再依赖图表 MCP。

## 替代方案

保留内置 stdio 例外增加执行边界复杂性；删除历史数据库列需要无收益的破坏性 schema 迁移；添加新的绘图依赖超出本次范围。

## 后果

历史 stdio 服务停止使用，管理员需迁移为远程服务；既有自定义角色或 Skill 对退役服务的引用不自动改写。DeepWiki 的可用性依赖外部网络，公开仓库访问无需认证，协议以 [官方文档](https://docs.devin.ai/work-with-devin/deepwiki-mcp) 为准。

## 验证

- `docker compose exec -e UV_CACHE_DIR=/tmp/yuxi-builtin-uv-cache api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：2179 passed、54 skipped。覆盖直接配置、缓存命中、内置 slug 豁免、模型序列化、未知 transport 和固定连接防篡改；跳过项不计为通过。使用容器既有依赖，绕过 uv 默认缓存目录写权限问题。
- `docker compose exec -e UV_CACHE_DIR=/tmp/yuxi-builtin-uv-cache api uv run --no-sync --group test pytest test/integration/api/test_mcp_router.py test/e2e/test_mcp_stdio_security.py -q`：11 passed。包含真实 HTTP MCP 协议发现、拒绝 stdio 命令且无文件副作用、真实 PostgreSQL 重读证明历史 stdio 停用、旧系统图表删除与 DeepWiki 注册、重复同步幂等。
- API 容器直接执行 `inspect_mcp_server_tools` 连接真实 DeepWiki，返回 `ask_question`、`read_wiki_contents`、`read_wiki_structure`。数据库回读确认 DeepWiki 使用固定远程 URL、默认停用，系统图表已删除。仅验证连接和工具发现，没有执行远程工具业务。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts` 通过（62 项）；`pnpm --dir docs run build`、相关 Python Ruff 检查与 `git diff --check` 通过。
- 再次独立审查发现初始 `deepwiki` 标识会覆盖用户同名配置，改为 `deepwiki-official` 后复审通过。相关 MCP unit 34 passed；HTTP integration 与安全 E2E 11 passed；新增 `test_official_builtin_preserves_user_deepwiki` PostgreSQL 回读测试单独运行 1 passed，确认旧连接、请求头、启用状态和归属不变。混合收集同名 unit/integration 模块曾失败，拆分命令后通过，未将收集失败计为验证成功。

旧能力不存在：所有执行入口拒绝 stdio，模型中的 stdio 配置生成分支与图表固定命令已删除，MySQL Skill 没有图表 MCP 依赖。
重新引入条件：需要独立执行隔离设计与显式产品决策，不能仅添加字典条目。
