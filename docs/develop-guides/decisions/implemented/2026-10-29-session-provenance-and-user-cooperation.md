# 会话来源与用户范围协作

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/cooperation.py

## 问题

协作派发的消息缺少可点击的来源，连续创建、等待和查询工具重复占用消息空间。模型只能操作当前协作树，无法创建继承项目的独立对话，也无法查看或操作同一用户已有的其他对话。

## 决策

用户可以追溯非人工输入；Agent 可以在当前用户和 APP 授权范围内协作；连续操作以紧凑事件行展示。保留 FIFO、精确 Turn/Run 结果、运行租约、审批和各树独立的停止与并发语义。非目标包括跨用户或跨 APP 授权、新的 Public API 路由、继承对话历史、任意更换项目以及精确滚动到工具调用。

本决定补充[统一 Session 模型](./2026-10-03-session-cooperation.md)与[任务观察](./2026-10-07-cooperation-task-observation.md)的授权和展示边界。

### 实现方案

协作工具通过 `mode=root` 创建独立顶层会话，默认 `mode=child` 创建直属子会话。新顶层会话使用独立树身份，继承当前项目、配置和审批选择；来源 Run 与父子关系分别保存，避免将顶层创建误作协作后代。创建和投递复用 Input/Receipt 主链路，以来源 Turn 和工具调用身份幂等。

repository 按持久 Run 的用户和 APP 授权全部会话。稳定 ID 可以跨树寻址，路径仅在调用者树内解析；查询结果携带根身份与项目，避免多个根路径混淆。发消息、提交、取消、结果读取与等待采用相同范围，恢复时重新检查授权。事件游标排序在同一用户和 APP 内串行发布，执行名额和整树停止继续由原树拥有。

等待目标授权与事件查询由同一 repository 方法顺序执行，service 只投影更新和游标。会话摘要不加载未使用的配置快照及额外 metadata；模型工具的默认范围、分页和字段由[会话目录决定](./2026-11-05-session-directory-scope.md)收窄，稳定 ID 的跨项目授权保持相同。

非人工输入来源从持久 Input 读取，来源会话信息按当前用户与 APP 过滤后投影。前端把来源根会话放在主页面、成员放在其根会话侧栏；嵌入会话向拥有页面的工作区传递打开意图，不递归创建侧栏。定时任务与外部事件显示实际类型，无会话来源时不伪造跳转。

前端在真实工具展示顺序上合并连续同类协作调用，成员入口分别可点击；错误和等待状态保留。会话查询按[会话目录决定](./2026-11-05-session-directory-scope.md)只显示范围与条数。复用现有头像、图标、主题变量和消息 item reducer。

历史会话目录只从 `list_sessions` 回执提取，普通命令或 MCP 正文中的同名字段不作为会话身份，也不为构建协作视图解析这些正文。

## 替代方案

- 只放宽工具 schema：repository 和等待恢复仍拒绝跨树，无法形成完整链路。
- 将全部会话并为一棵树：改变停止、项目目录与执行容量的含义。
- 只记录可编辑的显示名称：无法可靠导航同名会话，也不能证明来源权限。
- 保留逐条事件：实现少，但连续调用和整树查询继续重复占据消息区。

## 后果

授权与事件提交顺序扩大到同一用户、同一 APP；事件发布使用该范围的事务锁，执行名额和整树停止仍由各树独立拥有。路径只在调用者树内解析，跨树操作使用稳定 ID。跨项目执行使用目标项目的 Workdir，不继承派发方历史。

新增 Web 来源与导航查询复用当前用户授权，不扩展 Public API。缺少可访问来源会话的非人工事件只显示类型，不提供跳转。来源链接不保证滚动到具体 Run 或工具调用。工具目录按项目读取，跨项目历史有固定页大小；完整树形观察继续由状态面板拥有。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 顶层创建继承项目、独立历史且幂等 | 重放重复创建或复制派发历史 | cooperation service、Input/Receipt | PostgreSQL integration、真实 worker E2E 回读 Session/Input/Message | 相同调用更换创建类型、项目不可访问、失效 lease | Passed |
| 全部操作覆盖同用户同 APP，拒绝越权 | 放宽树限制泄漏其他主体 | cooperation repository、等待恢复 | PostgreSQL 范围和恢复测试、跨项目 E2E 文件 oracle | 不同用户、不同 APP、无权目标 | Passed |
| 跨树等待不漏已提交事件，结果绑定精确 Run | 游标越过迟提交事件或读取相邻结果 | 事件 repository、结果查询 | 并发事务阻塞与 commit 顺序测试、真实 worker E2E | 先分配事件的事务迟提交、交错发布 | Passed |
| 来源来自持久事实，导航符合会话层级 | metadata 伪造来源或侧栏递归 | Input repository、Web service、SessionWorkspace | HTTP integration、E2E 精确核对 Input 来源集合、前端与浏览器验证 | 手工消息、无权来源、相同路径、三级来源 | Passed |
| 连续操作紧凑展示且各目标可达 | 丢失调用、跨正文重排或误合并错误 | cooperationToolView、CooperationTool | 前端 unit、真实页面逐个点击创建与等待入口 | 中间正文、失败状态、同名不同 ID | Passed |

隔离 PostgreSQL 上执行 `pytest test/integration/services/test_session_cooperation.py -q`，89 项通过。隔离 Compose API 中执行 `pytest test/integration/api/test_session_cooperation_state_recovery.py -k web_input_origins -q`，1 项通过。执行 `pytest test/e2e/test_session_cooperation_e2e.py -k independent_root_cooperation -q`，2 项通过：一个创建继承项目的独立根，一个操作已有不同项目的根；由真实 HTTP、worker、checkpoint、沙盒文件和数据库回读证明结果。测试结束回读 Session 行不存在，项目目录不存在；模型响应由确定性重放服务提供。

浏览器脚本 `frontend/test/browser/cooperationEvents.js` 在真实 Vue 页面使用确定性 HTTP fixture，核对根、三级与跨根来源、最大化成员页的来源与浏览器前进后退、连续三个创建及等待目标、浅深主题，以及 1440、1024、768px 的布局。它验证 DOM 与路由，不代替真实 worker E2E。浏览器另验证长标题省略、失败详情展开，以及普通工具正文中的 `sessions` 字段不覆盖协作身份；错误分组、同名目标和异步导航失效由前端 unit 覆盖。

测试环境关闭可选 Knowledge，后端 unit 进程显式启用默认 Knowledge 配置，以符合现有全量测试前提。已有 HTTP 文件的另外三个场景依赖全局 Knowledge 清理，未在此隔离环境执行。外部真实模型与 Cube 沙盒的跨树协作未验证；E2E 使用 container 沙盒。

回归命令与结果：`docker compose exec -T -e YUXI_KNOWLEDGE_ENABLED=true api uv run --no-sync --group test pytest test/unit -m "not slow" -q --tb=short`，3369 项通过、125 项跳过；容器外配置补充测试 207 项通过。前端标准 `test:unit` 的等价 Node 命令运行 963 项通过，ESLint、TypeScript 与 Vite 生产构建通过。工程信任检查及其 56 项回归、Ruff、文档构建、相对链接和 diff 检查通过。

补充 API Key HTTP 检查中，`test_apikey_router.py -m self_managed_resources` 的 4 项通过；三个 API Key 文件的完整批次因隔离环境关闭 Knowledge、共享清理访问知识库入口失败而未进入业务断言，不计为通过。
