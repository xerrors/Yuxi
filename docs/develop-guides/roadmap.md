# 开发路线图

路线图可能会经常变更，如果有强烈的建议，可以在 [issue](https://github.com/xerrors/Yuxi/issues) 中提。

日志添加规范（For Agent）:

- 同一版本的多次功能更新时，应以功能为单位进行更新，比如之前添加了 A 功能的更新，在后续的更新中修复了因 A 功能引入的 bug，那么这个修复说明应该和 A 功能描述放在一起，而不是新增一条修复记录，功能更新同理。


### 看板

- Langfuse 增加 self-host 模式支持，补齐私有化部署与配置说明（已支持 cloud，待调试）
- 检索测试中，添加问答
- 集成 Memory，基于 deepagents 的文件后端实现，需要考虑定位
- Yuxi-cli 相关的功能，放在后续版本中实现（不是类似于编程助手，而是管理平台的工，等各个 router 接口优化之后）
- 完善测试基准自动生成功能，目前的实现过于简单，无法覆盖实际需求
- 完善 Skills 的环境变量注入
- 拓宽检索的知识源，统一多知识源（channel），目前已知知识库/知识图谱/网页，可拓展：个人知识库、数据库、历史对话等
    - 前置任务，多知识库并行检索（扩展 query_kb）
    - 新增 query_keywords 工具，专门用于基于关键词命中的排序，也结合词频（和 BM25 的区别？）
- 参考 AgenticRAG 方案扩展当前 Search 工具：基于知识库工具返回的 resource_id/file_id 改进 Search 返回递增文件序列 ID，完善 Find 与 Open 能力；Summary 暂缓
- 评估，基于 Agent 的评估，这里应该是结合 Langfuse 实现

### Bugs
- 目前的知识库的图片存在公开访问风险

### BREAKING CHANGE（不兼容变更，0.7 版本再实现）
- 将自定义provider 的实现逻辑，从文件移动到数据库中，并将相关处理代码，移出 config 文件，放到 provider 模块中
- 已补充方案文档：`docs/vibe/2026-04-18-custom-provider-db-refactor-plan.md`，明确采用“provider 一行、models 放 JSON、移除 provider 默认模型”的落地方案
- 优化知识库的 API 接口设计，使用 /{db_id}/xxx 的形式，整合 mindmap / eval 接口
- 移除 v1 版本的 provider 统一接口，改为 v2 版本的 provider 模块接口



## 版本记录

### 0.6.3 开发记录

- 修复 DeepAgent 未绑定 `DeepContext`，导致深度分析专用系统提示词和子智能体默认模型配置未生效的问题；同时避免运行时重复注入默认提示词。
- **MCP 多鉴权编排与内部代理链路**：为 MCP 接入新增 `auth_config_json` 与 `mcp_connections` 绑定模型，支持 `bound_secret`、`custom_http_token`、`client_credentials`、`stdio_env`、`authorization_code` 等鉴权编排基础；后端补齐基于 Redis 的 access token 缓存、预刷新与 401 后自动删缓存重试能力，并新增 `/api/internal/mcp-proxy/{server_name}` 内部代理路由，将动态 HTTP MCP 的鉴权、续期和重试逻辑统一收敛到服务端；补齐用户/部门绑定连接缺失时的内部代理拒绝逻辑，避免个人级 MCP 连接被其他用户通过代理入口串用；同时让管理端 `/api/system/mcp-servers/{name}/tools` 与 `/tools/refresh` 也按当前管理员的 `user_id/department_id` 解析绑定连接，避免跨部门管理员在未授权情况下探测到 MCP 工具列表；新增 Redis 版次 + manifest 分级缓存，让 API/Worker 多进程场景下的 MCP 工具清单按 `server` / `connection` 分区同步失效，并避免旧 graph 中预加载的 managed tool 覆盖本轮实时鉴权加载结果；修复动态 HTTP 内部代理短期 JWT 被工具对象缓存固化、停用 MCP 仍可通过内部代理访问、更新 `auth_config` 后 runtime token 未立即清理的问题；统一 Agent 运行态与连接管理页的个人 MCP scope 语义，避免运行态使用数据库主键查找 `mcp_connections.scope_id` 导致个人连接不可用；补齐运行时鉴权 MCP 工具的执行阶段映射，避免模型已绑定 `getTicket` 等动态工具但 ToolNode 静态注册表无法执行的问题；审计并修复该链路隐患：通过 DynamicMCPTokenAuth 引入 15 秒 TTL 在内存缓存（含联动清除机制）解决 httpx 请求对 DB 的高频重复查询问题；修复 `_normalize_token_payload` 处理 naive datetime 的时区偏差问题以消除 token 无限自动刷新的 Bug；改进 `_calculate_config_hash` 哈希计算逻辑，对 json.dumps 增加 default=str 降级保护防止无法序列化而崩溃的问题。

---

历史版本发布记录已迁移到 [版本变更记录](./changelog.md)。

维护说明：
- roadmap 仅保留未来规划（看板/Bugs/里程碑方向）。
- 具体版本发布内容统一维护在 changelog。
