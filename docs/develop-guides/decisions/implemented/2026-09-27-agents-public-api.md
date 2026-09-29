# Agents Public API 契约

状态：implemented
类型：feature
Owner：backend/server/routers/public_v1/agents.py

## 问题

原有 API Key 可调用绑定用户的全部产品接口，外部 Agent 调用缺少受限入口与可信来源。调用方还需要在同一 APP 内隔离终端用户，从创建 Session 的请求直接接收事件，并在长连接订阅期间保持数据库连接可用。Session/Turn 若另建执行状态，会与已有 Request/Run 生命周期重复。

## 决策

API Key 持久化 `access_level` 和 `app_id`。升级前的 Key 保持 `full`；管理 API 在省略权限时也保持 `full`，以保留既有程序化创建语义。Web 新建表单默认选择 `agents`，并要求填写 APP 来源。`agents` Key 只能通过认证依赖访问 `/api/v1/agents/**`；Public API 还要求 Key 有 `app_id`，JWT 不作为外部来源。Key 绑定用户的 Agent 可见性仍由现有后端边界判定。

Public API 提供可见主 Agent、原生 Thread/Request 与兼容 Session/Turn 入口。创建意图包含权限和来源；公共提交把用户、APP、线程范围与 `Idempotency-Key` 无歧义编码后映射到持久 Request，同键不同输入返回冲突；只在发现同一用户、APP、线程的历史请求时沿用旧 ID。历史幂等摘要编码保持原样，避免既有请求重试变成冲突。Request 和派发后的 Run 分别快照认证 Key 的 `app_id` 与 Key ID；响应 `X-App-Id` 取认证时的标量快照，不读取请求头或客户端 metadata。Conversation 的专用 `app_id` 列只由 Public 用例从认证 Key 写入；产品创建入口拒绝客户端提供 `metadata.app_id`。外部 Key 读取以专用列为准，v9 旧线程升级后该列保持空值，即使旧 metadata 同时带有 APP 和 Public 来源标记也不能跨域读取。稳定 Turn 与跨 Run 生命周期由[后续决定](./2026-09-28-public-session-turn-lifecycle.md)补充。

APIKey schema 与创建意图由 `models_business.py`、`api_key_repository.py` 拥有；Request/Run 来源与结果归属由相应 repository、`agent_request_service.py` 和队列服务拥有。`services/agents/public_api.py` 只协调 Agent 公开用例与既有 Request/Run；原生与兼容响应字段和 URL 由 `server/routers/public_v1/agents.py` 分别组装。Service 按 Agent 领域归组，router 按接口版本组织。

可选 `X-End-User-Id` 由持有 Key 的 APP 声明；未提供时沿用 Key 用户。提供时按 `(Key 绑定用户 ID, app_id, end_user_id)` 在 PostgreSQL 中唯一解析或创建 `role=user`、`user_kind=end_user` 的 User。Header 须非空、无首尾空白且不超过 128 个字符；持久 UID 由无歧义组合编码的摘要生成，不暴露原始外部 ID。Thread、Request、Run、Project 和 Workdir 归该终端用户的真实 UID；查询与 SSE 使用同一外部 ID 才能读取。终端用户没有可登录凭据；密码、OIDC、JWT、模拟登录和 API Key 签发均拒绝其作为产品 API 主体，OIDC 也不恢复已删除用户。Key 绑定用户仍决定 Agent 可调用性，worker 沿该绑定恢复授权；管理员个人 Workdir 和 Skill 不随授权传给终端用户。

Session 创建接受可选布尔 `stream`；省略或 `false` 返回 JSON，`true` 在 Request 提交后从同一 POST 返回 SSE。首个 `session_created` 事件包含 Session、Turn、结果和重连地址，之后跟随该 Turn 的关联 Run。断流不取消已提交的 Request，同一幂等键重放仍指向同一 Request。原生 Thread 创建不接受这个额外字段。

Session `status` 从持久 Turn 关联的 Request 与顶层 Run 投影为 `idle`、`in_progress`、`requires_action`：等待审批优先；未终结 Run 或同线程仍排队的 Request 为 `in_progress`；两者结束后为 `idle`。取消的最新排队 Request 不遮蔽仍在运行的 Run 或更早排队的 Request，审批后 resume Run 终结也不受旧 Request 的 `waiting` 状态影响。完成、失败与取消属于 Turn 结果，不成为 Session 的永久状态。原生 Thread/Request 状态不变。

Request events、Session events 与流式 Session 创建在返回 SSE 前完成鉴权、提交及响应装配，提取标量 UID 并关闭请求会话。事件生成器继续使用独立短会话轮询；流不持有校验事务或 ORM 对象。提交用例拥有持久事务，流式创建关闭的是之后查询 Session 状态开启的事务。

User 的 Schema 与唯一性由 `models_business.py` 和 `user_repository.py` 拥有；认证依赖和产品登录入口拥有可达性边界；`services/agents/public_api.py` 解析 Public 用户，Agent repository 拥有 Agent 可见性。Session 状态由该 Public service 投影，路由拥有响应和 SSE 生命周期。

## 替代方案

- 只在新路由检查 Key：旧产品路由仍可用同一 Key，无法形成权限边界。
- 一次注册全部 Agents 参考资源：缺少对应持久化和可见性事实，容易产生占位端点。
- 只暴露 Session/Turn：兼容名称会渗入内部用例，调用方也无法直接使用 Yuxi 的 Thread/Request 概念。
- 建立独立 APP 注册与成员系统：当前只需可追溯来源，尚无消费方要求第二套权限模型。
- 仅在 Request/Run 加终端用户标签：Thread、Workdir 和 worker 仍归 Key 用户，无法形成隔离。
- 允许终端用户登录或继承 Key 用户的全部资源权限：会扩大产品 API 可达面，或暴露管理员个人 Skill 与 Workdir。
- 创建后由客户端立即 GET SSE：增加往返并留下创建与订阅之间的衔接负担。
- 持久化第二套 Session 状态或把最新 Turn 状态重命名：与 Request/Run 生命周期重复，且不能正确表示等待审批、完成和排队。
- 扩大连接池或全局改变数据库依赖作用域：前者只能延缓 SSE 耗尽连接，后者影响无关路由；三个流式入口在响应前关闭会话即可闭合问题。

## 后果

存量 Key 与省略权限的程序化创建继续拥有原有范围；管理员需要显式创建 `agents` Key 才能收窄访问，Web 默认选择该范围并提示 `full` 的影响。持有同一 APP Key 的调用方可声明该 APP 下任意终端用户，因此 Key 是可信 APP 凭据，终端用户 Header 不是独立认证。首次访问新外部 ID 会增加 User；其身份约束始于 business Schema v10。Key 撤销不会改写已经接收的 Request/Run 来源快照。

SSE 响应头发送后的故障由现有错误事件及持久结果查询表达；流生成器仅接收标量与响应字典，数据库依赖的最终清理可以再次关闭会话。当前事件 envelope 仍是 Yuxi 格式；完整 OpenAI Agents API、官方工具结果和等待中的取消不属于已实现契约。部署公开入口时由网关控制速率和并发，并监测 FIFO 队列与成本。

## 验证

- 真实 PostgreSQL 升级测试先删除新增列再执行两次迁移，验证历史 Key 为 `full`，Request/Run 来源列存在。
- 真实 HTTP 集成覆盖受限 Key 对旧接口返回 `403`、服务端 `X-App-Id`、JWT 不可冒充 Public API、创建重放与变更冲突，以及原生和兼容消息入口的跨源预检。
- 确定性 provider 的真实 API + worker E2E 回读 Request/Run 和最终输出，覆盖跨入口同键重放、原始内容块变化冲突、两种排队 SSE 交接、终态与跨 APP `404`。
- 带分隔符的 APP 与幂等键构造旧编码碰撞，验证新 Thread/Request ID 仍相互独立；Web unit 验证永久 `4xx` 会终止 Request SSE 重连并反馈界面。
- Web lint、构建、组件定向测试以及浅色、深色、窄屏真实页面截图验证管理界面。
- 真实 PostgreSQL 迁移测试从 v9 形态重复升级，回读旧用户与终端用户唯一约束；真实 HTTP 并发解析同一外部 ID，验证跨 APP UID 分离、非法 Header、软删除用户及所有产品登录和 Key 签发入口拒绝终端用户。
- 确定性 provider E2E 从 PostgreSQL 回读终端用户的 Thread、Project、Request、Run；缺失或换用 Header 的资源查询返回 `404`。Session 创建流在模型阻塞时断开并重放，核对同一 Request/Run、终态输出和 Session 状态；非布尔 `stream` 返回 `422`。Session 状态 unit 覆盖审批、排队、取消与 resume 的投影。
- `test_public_sse_releases_validation_transaction` 在真实 HTTP、worker 与 PostgreSQL 下订阅三个 SSE 入口，要求模型等待时不存在贯穿 500ms 观察窗口的 Request/Run 空闲事务，随后回读同一 Run 的完成状态与输出。恢复旧路由行为时三个参数均因持续事务失败；修复后三个参数通过。该测试标记 `e2e_lifecycle`，由现有 CI 阶段执行；连接池上限并发压测不在此观察范围内。
