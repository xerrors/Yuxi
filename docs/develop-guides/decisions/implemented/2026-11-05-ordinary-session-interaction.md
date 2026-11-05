# 普通会话草稿、阅读位置与路由恢复

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/ui/SessionWorkspace.vue

## 问题

普通 `/agent` 的纯图片草稿不能在空闲时发送，运行中还显示停止；引导入口与发送忙碌条件不一致。输入编辑器在换行删除和输入法组合时干预原生编辑，运行中的粘贴和拖拽也被停止按钮状态拦截。聊天宽度变化会丢失正在阅读的文字位置，成员 URL 恢复的迟到响应能够重新打开已关闭侧栏。已读取的智能体详情依赖目录非空，知识库关闭时布局仍请求管理列表。路由协调器等待读取后才启动的旧成员恢复，还会抢占同一页面上较新的来源点击。

## 决策

### 实现方案

`SessionWorkspace` 用当前文本与图片判断草稿，普通发送继续使用 follow-up，引导继续显式选择 steer，并共同遵守当前忙碌条件与 `sendDisabled`。`MessageInputComponent` 用 BR 表示正文换行，尾占位不进入正文和偏移；输入法确认期间保留原生按键，上传由能力开关和输入禁用状态决定。

`ScrollController` 保存可见字符 Range 与视窗坐标，由工作区的 ResizeObserver 在尺寸变化时补偿；贴底用户继续跟随底部，分页期间沿用分页补偿。成员路由恢复复用现有最新请求裁决，保留 `member_session_id` 和树根归属校验；关侧栏、清除成员或离页使旧读取失效。未完成的显式点击拥有同一路由上的导航优先级，迟到恢复不能使它失效；真正路由变化、关闭或离页继续废弃旧请求，点击失败后恢复仍可继续。智能体详情先于目录读取。

`AppLayout` 仅在 discovery 明确开启 Knowledge 时请求 `/api/knowledge/knowledge-bases` 管理列表，失败不阻塞普通导航；导航链接保留折叠后的可访问名称。Agent 目录通过 `/api/knowledge/knowledge-bases/accessible` 初始化来源名称的现有请求仍保留，该路径属于 agents Store，此范围只门控布局拥有的管理列表。配置保存只补已有服务端回写语义的测试。

## 替代方案

- 继续用按钮 loading 禁用上传会阻止运行中准备后续草稿；当前普通会话已支持 FIFO 与显式引导。
- 按滚动百分比或消息顶部恢复位置无法保留长段落内部同一行文字；字符坐标只由会话阅读控制器维护。
- 在路由协调器 await 之后检查过期仍允许工作区先产生侧栏副作用；现有导航裁决在副作用前检查请求身份。仅让恢复与点击竞争相同的最新修订号，也无法区分迟到恢复与用户新意图，因此由导航器维护当前显式点击的局部身份。

## 后果

字符 Range 会因 Markdown 替换失效，零尺寸容器或折叠 Range 保留原滚动位置。布局补偿和主动滚动分别更新阅读意图；分页加载期间由分页流程补偿。前端可用性仍是体验约束，后端继续拥有授权与输入接收事实。

此范围保留普通会话的 follow-up、显式 steer、停止、Session 工具审批和 `sendDisabled`。配置 Store 的生产实现保持当前行为，测试只使用已有的 `default_model` 字段。Assistant、compact、只读模式及公开 API 扩展留在各自功能范围。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 纯图片可发送，忙碌状态拒绝引导 | 按钮与提交分叉 | SessionWorkspace | `sessionWorkspaceDrafts.test.js`、`ordinaryDraftSubmission.js` | 空文本带图、pending/cancel/等待/缺 Run、sendDisabled | Passed：unit 与浏览器均符合预期 |
| 换行、提及与输入法原文一致 | DOM 占位污染正文、组合按键误发 | MessageInputComponent | `inputEditing.test.js`、`inputEditing.js` | 换行和提及删除；IME 组件、事件和 229 信号 | Passed：真实光标、剪贴板与原文断言通过 |
| 运行中可准备上传，禁用时拒绝 | loading 被误当草稿禁用 | MessageInputComponent | `inputEditing.test.js`、`inputEditing.js` | isLoading=true、enableUploads=false、disabled=true | Passed：unit 与真实 DOM 上传事件通过 |
| 重排保留阅读字符和贴底意图 | scroll 先到导致意图丢失 | ScrollController | `scroll_controller.test.js`、`chatReadingPosition.js` | 视窗与左右侧栏重排、隐藏容器、折叠 Range | Passed：同一字符坐标、贴底和主动上滚通过 |
| 成员恢复采用最新请求与当前根 | 迟到响应重开侧栏或恢复抢占点击 | sessionLinks 与 SessionWorkspace | `sessionLinks.test.js`、`sessionWorkspaceDrafts.test.js`、`sessionRouteRestoration.js`、`cooperationEvents.js` | 乱序、迟启动恢复、点击失败后恢复、真实路由变化、关闭、清路由、离页、外根 | Passed：unit 与浏览器最终路由和侧栏通过 |
| 详情与布局管理列表采用各自事实 | 空目录丢详情、关闭服务仍请求管理列表 | SessionWorkspace 与 AppLayout | `sessionWorkspaceDrafts.test.js`、`layout_startup.test.js`、`ordinaryDraftSubmission.js` | 空目录有详情；discovery关闭/失败管理列表为零、开启一次 | Passed：管理列表请求分别为 0、0、1 |

前端目录内的 `pnpm run test:unit` 全量单测和直接受影响的导航单测通过；`docker compose exec -T frontend pnpm run lint:check` 与 `docker compose exec -T frontend pnpm run build` 均通过。有效行为负控包括隔离源码副本的 25 组定向故障注入，每组均核对预定业务断言失败；导航测试在未修复代码上另行复现旧恢复抢占新点击，URL 停留旧成员，修复后完整浏览器循环进入所有预定成员。配置保存测试覆盖默认模型清空、重新选择以及服务端返回的其他字段。

四个普通浏览器脚本通过固定公开协议响应验证完整 SFC 和真实 DOM，覆盖 1440、1024、768px 及浅深主题；截图已核对。布局管理列表门控通过，Agent 目录的既有 accessible 初始化请求仍可观察到。真实 Input 持久化、HTTP 后端与 worker 行为由对应后端测试证明，本记录的浏览器 fixture 不覆盖这些边界。
