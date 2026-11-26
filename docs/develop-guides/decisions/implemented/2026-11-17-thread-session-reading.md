# 会话工作区与 Thread 阅读生命周期

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/ui/SessionWorkspace.vue

## 问题

会话读取依赖侧栏列表、Agent 目录和输入准备。协作成员复用整个根工作区，首读、翻页和指定 Turn 回读共用多模式入口，调用者难以判断读取范围与状态归属。完整草稿持久化、指定子 Run 阅读器与全站 TypeScript 迁移见[前端重构提案](../proposed/2026-09-30-agent-view-frontend-refactor.md)。

## 决策

### 实现方案

路由选择根 Thread，协作标签选择成员 Thread。根工作区装配协作导航和面板，根与成员共用 `SessionThreadView`，各自管理草稿、输入、队列和当前等待点。侧栏维护列表、摘要和返回导航。已确认的路由决定返回偏好，取消导航保留原目标。授权详情由 `sessionRuntime` 按 Thread 保存，重命名、归档和已查看分别消费服务端结果。详情读取和协作观察只更新已有侧栏摘要。

`ThreadTimeline` 仅凭 `threadId` 启动读取和观察，拥有消息展示、分页触发和滚动视口。每个阅读实例注册一个观察者，同一身份与 Thread 共用首读、item 投影和 SSE。runtime 分别提供当前窗口、向前翻页和指定 Turn 回读，指定 Turn 使用独立游标，最终结果取持久 `result_run_id`。阅读器的 `refresh` 重新核对授权和历史，定位完成后发出 `loaded`。输入区合并同一 Thread 的在途准备，独立读取 Agent 配置和附件。路由按 403/404/410 拒绝读取，临时失败保留重试。Agent 配置失败可在编辑区单独重试。

隐藏保活视图保存滚动位置，暂停布局、滚动和标记已读，保留共享观察。分页和滚动代次拒绝旧请求或延迟布局回写，最后一个消费者离开时释放共享状态和连接，身份切换使旧结果失效。`sessionInput` 持有在途提交的观察者直到接收或拒绝，身份变化时旧响应不修改新草稿或新 pending。已接收的 Input 通过持久回执恢复，避免重复发送。

人工操作绑定界面显示的 Thread、Turn、Run 和等待点，提交前重新核对目标等待点。接收前失败保留答案与重试入口，已接收的操作按持久结果和后续运行恢复。保存 Thread 审批模式期间暂停新输入，根会话出现新等待点时刷新状态面板数据。阅读和导出共用消息分组，工作区以事件处理引用、文件和导航。引用按来源 Thread 与 Turn 读取，预览按来源 Thread 与路径缓存。右侧面板支持键盘切换及关闭标签，也支持键盘调宽。文件资源生命周期见[文件预览决定](2026-11-17-file-preview-session.md)。

## 替代方案

只切分模板会保留详情对侧栏的依赖和成员对根页面的重复装配。单独的读取包装层或第二个 runtime 会重复观察与激活生命周期。隐藏后释放历史再重播分页会增加读取，并使超出首屏的阅读位置更难恢复。保留共享 runtime 和视口各自的生命周期，路由等待授权读完，输入区响应同一读取的完成通知。

## 后果

多个视口共享同一 Thread 的持久与实时投影，各自保留滚动位置和编辑状态。根工作区只处理跨视图装配，成员预览沿用来源 Thread 的授权范围。后端拥有权限、排队、执行和最终结果。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 仅凭 Thread ID 可阅读，同 Thread 共用首读与 SSE | 隐含父初始化、重复订阅 | ThreadTimeline / runtime | `threadSession.js` 浏览器回放 | fixture 无预读或 runtime 写入，Agent/目录接口拒绝，双视口并行 | Passed |
| 历史位置、普通游标和指定 Turn 各自独立 | 隐藏后回底、旧结果覆盖、相邻 Turn 或 SSE 尾文本代替最终结果 | ThreadTimeline / runtime | `threadSession.js`、`sessionHistoryLoading.js`、`sessionRuntime.test.js` | 650 条历史隐藏后锚点偏移 <2px、新视口到底、950 条翻页、A/B 乱序、重新登录、指定轮次独立对账、SSE 尾文本与持久输出不同 | Passed |
| 路由复核可见性，输入失败可单独重试 | 侧栏遗漏或缓存跳过授权，Agent 失败阻断历史 | Timeline / SessionThreadView / 导航 | `sessionHistoryLoading.js`、`sessionSidebar.js`、`sessionRouteRestoration.js` | 侧栏外深链接、归档、403/404/410、503、重复刷新、取消导航及过期成员路由 | Passed |
| 等待点、在途输入和文件按来源隔离 | 迟到响应覆盖新草稿、隐藏会话误标已读或同名资源串用 | sessionInput / runtime / 面板 | `sessionInputConsumption.test.js`、`sessionApprovalFailure.js`、`cooperationMemberSync.js`、`attachmentDraftOwnership.js`、`sessionPanelKeyboard.js` | 隐藏最后读者、旧用户迟到成功或拒绝、旧等待点、隐藏根会话完成、延迟上传、相同路径或 Turn ID 属于不同 Thread | Passed |

阅读组件交付时，`pnpm run lint:check`、`pnpm run test:unit`（1104 项）与 `pnpm run build` 通过，`chatReadingPosition.js` 覆盖浅色、深色和 1440/1024/768 宽度。工作区职责调整后，同一组命令通过，单测 1206 项。浏览器使用 Playwright 与固定 HTTP/SSE 响应核对真实组件，面板键盘交互覆盖 1440px 浅色与 768px 深色。真实 API/worker/模型执行、后端权限和真实断网恢复在上述浏览器验证范围之外。

长消息连续正文增量下的手动阅读位置仍缺少复验。阅读组件交付时，同等 Workspace 回放在基线 `3c6ccd27` 和当时源码均出现 scrollTop 从 700 跳到 0，根因未定位。

旧能力不存在：侧栏缓存提供当前详情或预写导航，成员递归装配根工作区，工作区管理首次历史请求和订阅，多模式历史函数作为调用入口。

重新引入条件：有真实消费者无法使用共享 Thread 视图、直接授权读取或现有视口生命周期，并有独立证据支持新增职责。
