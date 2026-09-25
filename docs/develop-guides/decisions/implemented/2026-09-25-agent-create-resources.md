# 创建智能体时导入专属技能和 MCP

状态：implemented
类型：feature
Owner：web/src/components/model-management/AgentEditModal.vue

## 问题

用户创建智能体时只有基本信息表单，必须先创建智能体，再分别上传专属技能和配置 MCP。创建动作完成时，用户预期的能力尚未就绪。

## 决策

创建表单接收一个专属 Skill ZIP 和标准 `mcpServers` JSON 清单。清单限定为远程 `sse` 或 `streamable_http` 服务；`type: "http"` 映射为 `streamable_http`。管理员通过现有系统 MCP 接口创建清单条目，新 MCP 的 slug 同时保存到 Agent 的 `context.mcps`。Agent 建立后，ZIP 通过现有专属 Skill 上传入口保存并绑定。

表单按 MCP、Agent、ZIP 的顺序写入，各自的持久化 Owner 与授权边界保持原有位置。收到明确的 4xx 拒绝时可以修正尚未创建的 MCP 条目并重试剩余步骤；已创建条目的标识和配置保持固定。ZIP 被明确拒绝后可以移除，让已创建的 Agent 完成流程。请求结果不明时停止自动重试，并提示用户回到管理列表核对。未完成草稿在关闭并重新打开创建表单后保留；放弃草稿会刷新 Agent 列表，不会删除已经创建的资源。普通 Agent 创建接口保持原有 JSON 契约。

## 替代方案

- 新增 multipart 一体化创建 API：会要求重构现有 Agent、MCP 与文件系统 Skill 的多个提交点及文件回滚；当前体验复用已有接口并明确展示部分结果。
- 创建后分别配置：保留现有路径，但创建时无法交付已关联的能力。

## 后果

创建不是原子操作，已成功的 MCP、Agent 和 Skill 会在后续步骤失败时保留。管理员可以重试确定未成功的步骤；对于未知提交结果，必须先核对数据库对应的管理列表，避免重复创建 Agent 或 MCP。清单中的 headers 可包含凭据，前端错误与日志不回显清单正文。

## 验证

- `web/src/utils/agentCreateResources.js` 拥有请求顺序与未知结果停止规则；`web/src/utils/mcpManifest.js` 拥有清单边界校验。Node unit 覆盖远程清单映射、拒绝 stdio/无效 URL/过长字段、Agent 和 MCP 丢响应时禁止重复创建、后续 MCP 冲突后的清单修正，以及明确拒绝的 ZIP 上传重试或取消。
- 真实 HTTP integration `test_create_agent_with_remote_mcp_and_uploaded_skill` 回读 PostgreSQL 的 Agent `config_json.context.mcps`、MCP 行、Skill 绑定行和 Skill 文件内容，并证明普通用户创建系统 MCP 返回 403。
- 浏览器中使用可控 HTTP 对端验证创建请求顺序、ZIP 上传 422 后的进度显示与重试计数（MCP 1、Agent 1、ZIP 2）；后续 MCP 返回 400 时，修正未创建条目后只创建新条目；模拟 Agent 创建响应丢失后，关闭再打开表单仍保留草稿并禁用重复 POST。另验证 ZIP 422 后移除文件并点击“完成创建”不会再次写入 Agent 或 ZIP；再次创建并放弃草稿后，已创建 Agent 出现在列表中。宽屏与 390px 视口的创建弹窗经过截图检查。
- Web lint、unit 和 build、工程契约检查与文档构建通过。后端全量 unit 的无同步运行有两项沙盒环境断言失败，差异为 `DISABLE_*` 容器环境值；规定的 `uv run` 命令在编辑态构建时受 `yuxi.egg-info` 写权限阻塞。这两项没有作为本决定的通过证据。
