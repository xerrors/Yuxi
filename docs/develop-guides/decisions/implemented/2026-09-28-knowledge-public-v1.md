# Knowledge 查询与工具的 Public v1 边界

状态：implemented
类型：architecture
Owner：backend/server/routers/public_v1/knowledge.py

## 问题

external 查询仅在未版本化的 `/api/knowledge/databases/external*` 提供。CLI 依赖旧路径，API Key 只有 full 与 agents，无法向只需查询知识库的调用方发放受限凭据。Agent 的七个知识库工具原先封装在 toolkit 中，外部 API 无法复用其可见性和查询语义。

## 决策

Public v1 注册现有五个 external 查询操作，路径为 `/api/v1/knowledge/databases/external*`；另注册六个与 Agent 工具同名的只读操作，路径为 `/api/v1/knowledge/tools/{name}`。查询逻辑归 `yuxi.services.knowledge.tools`，Agent toolkit 保留 LangGraph 上下文与输出适配，Public 路由只组装 HTTP 输入与响应。CLI external 调用使用新路径；前端 API 层提供相同的五个 external 调用，管理与上传调用仍使用原路径。旧 external 路径暂保留并标记弃用。

普通用户 JWT、full Key 和 knowledge Key 均可访问这些 Public 查询。`knowledge` Key 只可进入 external 查询子树和明确列出的六个工具路径；`auth_middleware.py` 拥有 API 面限制，service 以绑定用户查询可见知识库，Agent 会话还受其启用范围约束。Knowledge 不处理 `End-User-Id`。`download_kb_file` 依赖 Agent 会话沙盒，因此只留在 Agent 工具中。`models_business.py`、`manager.py` 与 `storage_migration.py` 拥有持久化约束，从当前 main 的 business Schema v9 一次升级到 v10。前端管理路由没有迁入 Public v1。

## 替代方案

- 同时迁移管理与上传：扩大本次契约与权限面，还需处理现有前端响应和写入语义，故不采用。
- 立即删除旧 external 路径：仓库外调用方尚无迁移完成证据，故保留弃用窗口。
- 允许 `knowledge` Key 进入整个 `/api/v1/knowledge/*`：会使未来新增的管理路由意外获权，故限制到 external 子树与六条只读工具路径。
- 对外转发 `download_kb_file` 到任意沙盒：独立查询请求没有受信任的会话沙盒 Owner，故不采用。

## 后果

迁移期有两组 external 路径，旧路径不接受 `knowledge` Key。前端新增的 external 客户端目前没有产品页面消费者；现有管理页面继续使用原接口。CLI 的受限 Key 导入在 `/auth/me` 返回 403 时，改用 external 列库验证该 Key。工具接口使用与 Agent 同名的输入字段和结果；HTTP 返回 400/404，而 Agent 工具把错误转为可读字符串。

## 验证

真实 HTTP integration 覆盖 JWT 与受限 Key 的工具调用、下载和其他 API 面拒绝、管理路由未迁入、跨用户知识库不可见。Agent toolkit 单测核对共享 service 的结果。隔离 PostgreSQL migration 测试覆盖 v9→v10 约束升级和重复执行。CLI 与前端单测核对 external 请求路径、方法与参数；前端 lint、build 和浏览器组件渲染核对新增权限选项。
