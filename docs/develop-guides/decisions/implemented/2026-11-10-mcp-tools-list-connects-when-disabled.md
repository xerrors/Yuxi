# 管理工具列表对停用服务建连

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/extensions/mcp/service.py

## 问题

管理页刷新工具列表只读取已启用服务。停用服务的连接测试能发现工具，同一配置的工具列表却返回成功空目录，管理员无法在启用前查看或调整工具开关。

## 决策

### 实现方案

`get_all_mcp_tools` 按 slug 读取当前服务记录，再调用已有的管理端严格发现入口。停用不再挡住这次建连。个人凭证的目录读取不使用运行时启停门禁，仍只读取调用者自己的已连接凭证；工具调用继续拒绝停用服务。部署不可用、连接失败和个人凭证缺失沿用该入口的失败语义，不把失败记成零工具。

运行装配仍只使用已启用且连接可用的服务。工具列表读取不写入启停、禁用列表或运行缓存；单个工具的启用标记仍由当前 `disabled_tools` 投影。

## 替代方案

- 保持空目录，只在页面说明需要先启用：管理员仍要先把未验证的服务放进运行集合，才能看到工具。
- 刷新时临时启用再恢复：会打开并发运行窗口，并改变持久启停。

## 后果

停用的 DeepWiki 等内置服务可以在管理页看到远端工具。启用状态仍决定服务是否进入 Agent 运行。当前部署不可用的内置服务会失败，而不是显示空目录。本决定取代 [Knowledge 自有 MCP](2026-10-24-knowledge-domain-mcp-ownership.md) 中“停用工具清单返回空目录”的收窄。

## 验证

`test_get_all_mcp_tools_connects_disabled_server` 证明停用服务进入严格发现，且运行目录查询仍排除它。恢复启用过滤会使该测试在发现前失败。`test_management_http_lists_and_tests_delegated_server` 用真实 HTTP 与隔离 PostgreSQL 核对停用服务的工具目录，并回读确认查看没有改启停。`test_personal_static_credentials_use_own_identity_and_revoke_old_tools` 证明停用后的个人连接测试仍发现本人工具，回读启停未变，运行装配继续排除该服务；把这次读取重新接回运行启停门禁会得到 502。
