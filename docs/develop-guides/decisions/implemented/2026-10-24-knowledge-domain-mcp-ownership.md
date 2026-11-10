# Knowledge 自有 MCP 与内部认证

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/knowledge/mcp/client.py

## 问题

Knowledge 已通过 MCP 提供工具，但通用 MCP 模块仍拥有 Knowledge 的连接定义、部署可用性和具体集成，Agent 仍解释知识工具结果。为内部认证增加的签发器注册、重复工具加载、额外范围字典和发现模式，使一个服务接入承担了多套框架机制。

## 决策

### 实现方案

Knowledge 拥有 MCP 服务端、短期凭据签验、标准 SDK Auth、内部连接、选择器读取和知识引用适配。API 仅挂载其协议组件与生命周期。内置连接清单（slug、固定容器内地址 `http://api:5050/api/mcp`、默认启停与部署可用性）由通用内置目录 `extensions/mcp/builtin.py` 拥有：Knowledge 是平台自有服务，地址写死在代码中，不提供 `YUXI_KNOWLEDGE_MCP_URL` 环境变量覆盖，`knowledge/mcp` 不再保留独立的连接定义文件。通用 identity 保留用户与通用密钥能力，不拥有 Knowledge 专属凭据契约。

现有 MCP loader 继续负责标准工具发现、metadata、Run 内目录缓存与当前管理员策略。`delegation` 只选择代码提供的认证模块，接收 SDK 原生认证对象；复用既有出站策略，不再维护第二套 loader、issuer registry、专用配置哈希或注册生命周期。`auth_mode` 与 `enabled` 是平台管理元数据，默认值由内置代码装配拥有，普通连接配置 parser 不因此扩展。

Knowledge 从既有可信运行 Context 读取自身的会话选择，不再复制为通用范围字典。Agent 保留产品配置字段，只调用 Knowledge 提供的函数；Agent 和通用 MCP 不实现 Knowledge 的工具识别、Token、选择转换与结果解析。管理发现明确使用当前用户；产品目录及历史引用直接经 SDK 调用 MCP，按照自身读取权限执行，不穿过 Agent 工具启停策略。

## 替代方案

- 将内部认证伪装为共享 headers：不能准确表达按当前用户签发凭据的实际行为。
- 把适配文件搬到 Knowledge，保留注册与重复 loader：路径改变，但机制与认知负担仍在。
- 为内部调用增加 OAuth 授权服务器：超出当前内部信任关系与用户要求。
- 新建可配置 provider/插件平台：当前固定模块装配已满足需求。

## 后果

会话 `all`、显式 IDs 和空列表保持原义。Knowledge 服务端继续复核当前用户权限交集；通用执行边界继续拒绝旧工具绕过启停或连接变化。共享与 Agent-bound Skill 使用当前 MCP 依赖。不处理已有数据，不提供迁移或旧名兼容。

委托凭据只主张 uid 与会话选择，不携带 Run 绑定主张：授权面是当前用户权限与选择交集，服务端每次请求复核，token 内没有也无需 `run_id` 声明。管理页工具清单（GET /tools）与连接测试一样按当前配置建连，停用不再返回空目录；该收窄已被 [管理工具列表建连](2026-11-10-mcp-tools-list-connects-when-disabled.md) 取代。连接测试 `/test` 保持严格建连语义，可对停用服务实际发现并以失败暴露问题。共享 headers 单服务入口拒绝 `delegation` 模式与调用方自带 `auth` 键：委托只能经运行装配持有可信上下文，出站策略由装配层注入。工具引用不再重复跳过错误状态输出——失败工具审计不进入引用输入集，读取侧只解析成功结果；知识 HTTP 客户端超时完全由标准适配器传入。

地址写死的部署后果：曾通过环境变量支持的"宿主直接运行后端并指向本机 API"路径不再可用——不进容器的进程无法访问 `http://api:5050/api/mcp`，知识工具与引用读取需在容器拓扑内执行；存量部署若自定义过该地址，升级后以代码固定值为准。

标准 MCP 传输、工具与结果契约和内部自有认证分别说明；自有 Bearer 凭据不宣称实现完整 MCP OAuth 授权发现。

## 验证

- Passed：后端非慢速 unit 3223 项，容器中 122 项 skip；宿主配置测试补跑 197 项，覆盖容器没有仓库根文件的部署检查。固定文件边界测试包含恢复旧 scope、Token 和工具解析实现的负向案例。
- Passed：真实 PostgreSQL、HTTP MCP 与 Skill 发布集成 22 项，Knowledge HTTP API 18 项。覆盖 `all`、显式 IDs、空列表、越权指定、撤权、停用、缺少运行上下文、旧工具对象与同 Run 恢复；原生 MCP structuredContent 与标准适配器 artifact、记录和引用结果交叉核对。
- Passed：真实 worker E2E 4 项，验证内置、共享和 Agent-bound Skill 的五个 MCP 工具、正文、manifest 与执行审计。独立 PostgreSQL 回读核对当前 Turn/result Run、成功和失败审计、引用文件及原文行范围。模型与嵌入使用确定性协议重放，MCP、worker、数据库、对象存储和索引为真实链路。
- Passed：Knowledge 关闭拓扑的 worker 用例，确认会话选择保留且普通回答完成。未装配工具的拒绝由当前 Run 的 checkpoint 和 ToolMessage 证明，不伪造工具执行审计。独立回读确认路由、候选能力与知识后台任务关闭，管理员设置保留；恢复后原生 MCP 目录和后台任务恢复，同组 4 项 worker E2E 再次通过。
- Passed：前端 lint、830 项 unit 和 build；真实浏览器详情页显示“内部服务认证”。
- Passed：工程契约及其 66 项测试、Ruff 三项检查、文档相对链接和 build、补丁空白检查。全新 Reviewer 独立检查完整需求、源码与证据；领域异常不再依赖 API 类型，产品 SDK 客户端不依赖 Agent 工具异常。

本轮架构审查后的收窄（管理清单停用语义、委托 token 去除 `run_id`、单服务入口拒绝 delegation、共享入口拒绝自带 auth、引用与超时的冗余防御删除）以相同批次复验：非慢速 unit 3254 项通过，委托与访问回收、OAuth、委托生命周期集成共 80 项通过（含停用服务 GET /tools 返回空目录与 `/test` 严格建连的正反断言），新增四个负向单测覆盖上述拒绝路径。地址写死与内置清单并入 `builtin.py` 后再次全量复验：unit 3254 项通过，宿主 config 批次 204 项通过，知识/MCP 相关集成 94 项通过（含修复 `test_knowledge_audit_persistence` 对已迁移函数的 import）；integration 全量两批 469+561 项通过，其余失败项逐一归因为本分支前已存在的测试期望过期、sandbox 运行时环境问题与开发机多项目共享网络的 `api` 别名冲突，未在本轮修复。knowledge E2E 4 项因共享网络别名冲突无法稳定复现（worker 请求被并行项目旧版 api 拦截；本机直连委托链路以真实 200 响应验证）。两轮全新 Reviewer 独立审查完成并修复其发现的格式与文档问题。

旧能力不存在：`extensions/tools/knowledge`、`extensions/mcp/integrations/knowledge.py`、通用 issuer 注册、委托专用 loader、重复运行 scope 与 discovery 特殊模式。

重新引入条件：出现当前具体消费者和不能由标准 SDK/现有模块边界解决的需求，经独立审查重新决定；不预留配置化平台。

验证边界：没有外部付费模型探针，不以协议重放证明供应商可用性；没有已有数据迁移或兼容测试。跨层依赖仍由原有架构 guard 拒绝，Knowledge 领域不导入 API 异常类型。
