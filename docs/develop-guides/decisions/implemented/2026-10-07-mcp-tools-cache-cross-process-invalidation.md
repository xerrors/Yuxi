# MCP 工具快照跨进程失效

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/mcp/service.py

## 问题

MCP tools 快照缓存（`_mcp_tools_cache`）是纯进程内 dict，键为 `server_slug:config_hash`，只覆盖数据库连接字段。远端 server 新增 tool 时缓存键不变，持有旧快照的 ARQ worker 每次命中都返回旧 tool 列表，没有 TTL 也没有跨进程失效通道。实际故障：server 已有 7 个 tool，智能体只读到 6 个，最新 tool 缺失，只有重启 worker 才恢复。

## 决策

### 实现方案

- 事实与键：Redis 全局代数键 `yuxi:mcp:tools-version`（INCR），每进程持模块级镜像 `_mcp_tools_cache_generation`；缓存值类型与 config_hash 键不变，落后比对发生在整体镜像层。
- 读路径：`get_mcp_tools` 命中进程内快照前调用 `_sync_mcp_tools_cache_generation()`，GET 值与镜像不一致即整体丢弃本进程快照，按既有 config_hash 逻辑重建。
- 写路径（commit point 之后才广播）：CRUD（create/update/delete/set_enabled/toggle/内置同步清理）与 `get_all_mcp_tools`（管理端查看/刷新工具列表共同入口，以 `cache=False, force_refresh=True` 真实建连）调用 `invalidate_mcp_tools_cache(slug)` 清本进程该 slug 快照并 INCR 全局代数、镜像同步为新值（自失效不误伤其他 slug）。
- 已付出的代价：读侧每次命中路径多一次 Redis GET（worker 每个 Run 每配置 server 一次，走共享池化客户端，亚毫秒级）；代数比对与缓存命中非事务，失效恰落在两者之间时至多一次请求返回失效前快照、下一次请求纠正。
- 降级边界：Redis 不可读/值非整数时读侧保留现有快照、写侧仅 warning（fail-open，沿用 system options 同款取舍），不引入新失败模式。
- 连带收敛：代数失效使进程内 `_mcp_tools_stats` 计数失去可信读者，refresh 端点计数改为新鲜 tool 列表 + DB `disabled_tools` 现场计算，stats 状态删除。

## 替代方案

给快照加 TTL（如 60s）：无变更时也周期性重建全部 server 连接，且失效延迟不可控。缓存条目携带构建时版本号逐条比对：语义等价但改动扩散到所有直接读写 `_mcp_tools_cache` 的测试和 refresh 统计回读。逐 slug 版本键加 Lua 原子读写：为低频失效引入不必要的精度与复杂度。把 refresh 端点接入 UI 刷新按钮（前端改调用 `POST /tools/refresh`）：多改一个前端面，且列表查看本身同样是管理端真实取数，在 `get_all_mcp_tools` 闭合覆盖两个入口。

## 后果

远端新增 tool 后，管理员在扩展页打开或刷新该 server 的工具列表即可使 worker 在下一次取 tools 时重建，无需重启服务；无人触达管理端时快照仍保持（失效由管理动作或配置变更驱动，无自动探测）。任何一次失效会使各进程丢弃全部 server 快照（含无关 slug），下一个 Run 并发重建，代价为每 server 一次建连（实测单 server 33-300ms）。本机制要求 worker 先加载含失效逻辑的代码一次；此前部署的 worker 仍需重启。Redis 长时间不可用时行为回到旧缺陷（快照滞留），但不会出现新的失败模式。CRUD 失效大多与 config_hash 自然失效重叠，作为对未预见变化（如禁用列表形态迁移）的保守闭合保留。管理端查看工具列表时即使远端建连失败（返回空列表）也会广播失效——空列表既可能是真实状态也可能是失败，条件广播会漏掉前者，接受一次失败查看引发各进程一轮重建作为保守取舍。

## 验证

`test/unit/services/test_mcp_service.py`（全部经内存版代数客户端隔离真实 Redis）：其他进程仅递增代数即令本进程重建（远端 1→2 tool，失效前旧快照保持、失效后重建为 2，建连次数可数）；`invalidate_mcp_tools_cache` 清本进程该 slug、写全局键、镜像同步为新值且不误清其他 slug；Redis 抛错时命中保留、失效不抛异常（降级用例）；内置同步的失效通知晚于事务 commit point。`test/integration/api/test_mcp_router.py::test_management_refresh_rebuilds_other_process_tool_cache`：真实 HTTP、Redis、PostgreSQL 与本地 Streamable HTTP MCP 协议对端，测试进程模拟 worker——建连得 1 tool，远端增至 2 后仍命中旧快照（改造前缺陷的再现断言），经 API refresh（200，tool_count=2）后 worker 侧不重启重建为 2；用例首尾 `clear_mcp_cache` 并删除服务行。
