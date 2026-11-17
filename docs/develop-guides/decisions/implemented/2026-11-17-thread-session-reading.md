# Thread 独立阅读与视口生命周期

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/ui/ThreadTimeline.vue

## 问题

消息视口与输入工作区共用页面装载流程，读取一个已有 Thread 需要先理解 Agent 目录和输入配置。目标是仅传 threadId 即可读取、恢复与观察已有 Thread，同 Thread 多实例共享事实并独立阅读。后端继续拥有权限、最终结果与输入队列事实。

完整输入组件、完整草稿持久化、指定子 Run 阅读器与全站 TypeScript 迁移仍属于[前端重构提案](../proposed/2026-09-30-agent-view-frontend-refactor.md)。本决定不把整个 SessionWorkspace 标为纯装配。

## 决策

### 实现方案

ThreadTimeline 接收 threadId，复用 sessionRuntime 的 loadThread 完成历史读取、持久 Turn 恢复和队列观察，同时拥有消息展示、分页与滚动视口。每个阅读实例只注册一个观察者；同一身份、同一 Thread 的首读和 SSE 由 runtime 共享。无需先初始化 Agent 目录、输入配置或侧栏。

阅读器仅通过 refresh 显式复核持久事实；可选 onLoaded 回调由工作区提供，等待 Agent 配置与附件准备后再完成读取。selectChat 只等待 refresh，不重复初始化输入。一次在途读取覆盖读取、回调和定位；不另设 ready 事件或页面初始化去重状态。403/404/410 与临时失败保留原路由返回分类，Agent 加载失败通过工作区重试入口再次调用 refresh。

隐藏保活视图保留历史、分页和共享观察，只暂停布局与滚动。停用前直接保存 DOM.scrollTop，恢复本视口位置；新实例自行决定初次定位。换 Thread、销毁或身份变化释放观察；最后一个消费者离开清理共享状态和连接。身份变化使旧状态失效，迟到历史和事件不能覆盖新身份。分页代次与 ScrollController 取消代次分别阻止旧请求、nextTick、两帧布局和延迟重试触碰新视口。

主工作区和递归协作侧面工作区复用同一阅读组件。消息分组由 threadMessageProjection 派生，阅读器与导出消费同一转换；页面通过事件处理引用、文件和导航，通过插槽组合输入与队列 UI。页面不操作消息内部 DOM，只消费阅读器的滚动动作和根视口几何。

sessionInput 的在途提交也是 runtime 消费者，持有到接收或拒绝收敛。身份变化后旧响应不修改新草稿、不删除新 pending，也不重新 POST 已接收输入。文件资源归属见[文件预览决定](2026-11-17-file-preview-session.md)。

## 替代方案

把读取包装层和视口层分成两个仅相互消费的组件，会重复观察与激活生命周期；再用 ready 事件和方法同时驱动页面，需要额外协调输入初始化。采用一个阅读组件和一条可等待的装载路径，删除包装层与重复入口。

保留页面装载不能满足独立阅读。把页面全部搬入 composable 仍携带 Agent 和输入前提。增加第二套 runtime 会分裂订阅和终态事实，因此复用既有领域 Owner。

隐藏即丢弃共享历史会破坏分页范围之外的阅读位置；为此增加分页重播没有业务必要性。保留既有保活语义，最终离开再清理。

## 后果

阅读维护者从 ThreadTimeline → sessionRuntime → 历史/流 Owner 即可理解主路径。后端权限和 Input/Turn/Run 事实不变。SessionWorkspace 仍拥有输入编辑组合、等待点和队列 UI、Agent 配置、混合面板导航与路由导出；这部分不因独立读取完成而宣称已简化完毕。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 仅 threadId 自行读取与观察，同 Thread 单连接 | 隐含父初始化、重复首读/SSE | ThreadTimeline / runtime | `playwright-cli -s=thread-session run-code --filename=frontend/test/browser/threadSession.js` | fixture 无预读或 runtime 写入，Agent/目录接口拒绝；双实例 | Passed |
| 保留历史位置，隔离不同 Thread 和身份 | 隐藏后回底、旧响应污染 | runtime / Timeline | 同上；真实 SFC + 独立 HTTP/SSE 服务 | 650 条历史分页至第 100 条，隐藏最后视图再开同消息偏移 <2px；新视图到底、A/B 乱序、登出换身份 | Passed |
| 失败重试闭合输入，缓存路由复核持久可见性 | 输入永久 loading、缓存跳过鉴权请求 | Workspace / ThreadTimeline | `playwright-cli -s=thread-session run-code --filename=frontend/test/browser/sessionHistoryLoading.js` | 首读 503 → reader 重试；Agent 503 →重试可发送；已读 Thread 再次 HTTP 403/404/410 返回 false，503/network 返回 null | Passed |
| 终态只取持久最终结果 | 事件尾文本或相邻 Run 替代结果 | 原有 Turn 投影 | 独立浏览器 SSE 到最终 DOM | 完成事件文本与持久 result_run 输出不同 | Passed |
| 提交在隐藏期间收敛，身份变化不修改新草稿 | 观察释放丢接收结果、旧响应覆盖 | sessionInput / runtime | `test/unit/sessionInputConsumption.test.js`、`sessionRuntime.test.js` | 隐藏最后读者、换 Thread、旧身份迟到成功/拒绝 | Passed |
| 原有前端行为与构建 | 组装入口遗漏 | 前端各 Owner | `pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`、阅读位置/双视口浏览器回归 | 对应既有负例 | Passed |

最终前端检查：`pnpm run lint:check`、`pnpm run test:unit`（1104/1104）、`pnpm run build` 均通过。`chatReadingPosition.js` 覆盖浅/深色与 1440/1024/768 宽度；双视口的独立滚动与增量展示由 `threadSession.js` 的 HTTP/SSE fixture 验证，不保留直接写 runtime 的重复 fixture。截图保留普通输入及最终回答、窄屏深色、历史加载与错误状态。

长消息正文 delta 下的手动位置保持尚有缺口：同等 Workspace 回放在基线 `3c6ccd27` 和当前源码均出现 scrollTop 从 700 跳到 0，根因未定位；上述新消息追加与分页锚点通过不代表该场景通过。

浏览器固定 HTTP/SSE 回放证明前端装配、协议处理与 DOM，不证明真实 API/worker/模型执行、后端权限或断网恢复。真实 worker E2E 未执行。

旧能力不存在：SessionWorkspace 不再拥有首次 history 请求和 Run 订阅注册；未新增平行运行状态、协议或分页重播。

重新引入条件：有真实消费者不能复用既有 runtime、输入事实或视口边界，且独立审查证明需要新的 Owner；不能仅为缩短页面行数新增编排层。
