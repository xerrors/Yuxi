# 管理连接检查区分协议失败与零工具结果

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/mcp/service.py

## 问题

管理端连接检查使用运行时工具发现包装器时，停用服务返回空列表，连接异常也可能降级为空列表。路由将空列表报告为成功，用户无法区分真实的零工具 MCP 服务与未建立连接的配置。

## 决策

管理连接检查使用独立的严格发现入口，从已加载的服务记录构造实际连接配置并调用 MCP 适配器的工具发现。启用状态和单工具停用列表不阻止管理员显式检查；代码内置配置及自定义 stdio 迁移限制继续执行。该入口不写数据库或 Agent 运行缓存。

管理路由将连接异常映射为固定 HTTP 502，不回显对端正文、地址或凭据。真实 MCP 初始化及工具列表协议成功后，零工具列表仍是合法成功结果。HTTP 502 的语义 Owner 是路由，协议异常传播的 Owner 是严格服务入口。

## 替代方案

把所有空列表视为错误会误报真实零工具服务。临时启用服务再调用运行时入口会修改配置并引入并发运行窗口。改变 Agent 运行时的整体发现降级契约超出管理连接检查范围；保留该契约，仅将显式管理检查与它分开。

## 后果

管理员可在保持服务停用的情况下验证实际协议连接，检查不会启用服务、修改工具停用列表或刷新运行缓存。连接检查仍只证明工具发现协议，不执行工具业务或验证业务调用质量。旧自定义 stdio 配置继续在连接前要求迁移；不存在的服务及管理员权限错误沿用既有行为。

## 验证

`backend/test/unit/services/test_mcp_management_inspection.py` 验证停用服务真实调用适配器、异常保留、空列表成功、完整工具元数据和运行缓存不变，以及旧 stdio 在连接前拒绝。

`backend/test/integration/services/test_mcp_management_inspection.py` 使用独立 PostgreSQL schema、真实 HTTP API 和本地 Streamable HTTP MCP 协议对端，覆盖启用/停用零工具、停用有工具、协议服务错误和连接拒绝，重新读取全部服务列并核对运行缓存不变。身份依赖使用测试管理员，SQL、路由和 MCP 适配器均保留真实实现。

独立测试仅接受 `TEST_POSTGRES_URL` 指向名为 `fixture` 的数据库，且要求 `TEST_ALLOW_MCP_INSPECTION_DB=1`；缺少显式配置时跳过。运行命令在 backend 目录执行：

```bash
python -m pytest test/unit/services/test_mcp_management_inspection.py test/unit/services/test_mcp_service.py test/unit/routers/test_mcp_router.py -q
python -m pytest --confcutdir=test/integration/services test/integration/services/test_mcp_management_inspection.py -q
```

测试文件定义可执行证据；外部服务可用性不由本地协议测试证明。
