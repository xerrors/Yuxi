# 前端架构审计报告（原始保存版）

> 本文保存 2026-10-02 收到的前端全量架构审计报告原文，作为后续优化目标的基线。报告中的源码结论已经过抽查；CSS 产物膨胀、bundle 成本、实际内存影响和可利用 XSS 等需要独立构建、运行时或安全探针证据，不能仅凭本快照视为已通过验证。

## 一、总体判断

前端在 `06ebcb19` 刚完成一次大规模迁移：旧的 `components/views/stores/router` 结构被整体搬入新 `app / pages / modules / shared / apis` 五层骨架，**骨架方向是对的，且边界有真实约束力**（ESLint 自定义规则 + 回归测试守护，见 `eslint-boundaries.js`、`test/unit/frontendBoundaries.test.js`）。问题在三件事，不在骨架：

1. **迁移只做了“搬家”没做“收敛”**——143 个旧组件原样搬进新结构，巨型文件一个没拆，旧的坏习惯（双错误体系、样式复制、localStorage 各写各的）全部带进了新家；
2. **依赖方向存在真实破坏**——`apis ↔ identity` 形成两条 import 环，`model → ui` 有一处层级倒挂，这是边界规则管不到的盲区；
3. **会话域（session）是全项目风险中心**——5647 行的 `SessionWorkspace.vue` 承载了约十种职责，既有重构提案（2026-09-30）已识别大半，但本次审计发现了提案未覆盖的机制级问题。

## 二、结构性问题（按严重度排序）

### 1. SessionWorkspace.vue：5647 行的上帝组件（严重，提案已覆盖大半）

`src/modules/session/ui/SessionWorkspace.vue` 同时承担：6 个 store 的装配、按线程分桶的会话运行时（`threadStates` 每桶 17 个字段）、消息投影合并、SSE 流编排回调注入、输入队列、审批、附件、面板布局/拖拽/ResizeObserver、草稿持久化、配置变更提示。脚本约 3090 行 + 样式 1730 行，且在 scoped 块里 `@import '@/assets/css/main.css'` 把整套全局样式复制进组件（`:3918`）。

配套问题：该组件被路由 meta `keepAlive: true` 保活（`app/layouts/AppLayout.vue:525`），意味着这个巨石连同其全部监听器在切页后常驻内存。`pages/AgentView.vue` 已瘦身到 93 行并抽出 `threadRouteCoordinator.ts`（串行路由协调，写得不错），说明重构已在进行，但真正的硬骨头还没动。

### 2. 依赖方向的真实破坏（严重，本次新发现，提案未覆盖）

架构约定“apis 是集中 HTTP 边界、shared 不依赖业务”，但存在两条**反向 import 环**：

- `agents/model/agent.js:3` → `@/apis` → `apis/agent_api.js:2` → `identity/model/user.js:4` → `../../agents/model/agent`（三节点环，且 user store 用**相对路径**跨域引用 agents/projects）；
- `identity/model/user.js:3` → `apis/auth_api.js:3` → `apis/base.js:1` → `identity/model/user.js`（两节点环）。

目前靠“store 在函数体内延迟调用”没有运行时爆栈，属于结构性隐患。正确方向是 apis 不回头 import modules（token 经参数注入），`base.js` 反向依赖 identity 后，“基础设施层”就没法独立测试和复用了。

还有一处**层级倒挂**：`session/model/messageGrouping.js:2` import `ui/tools/toolRegistry` 的 `enrichTaskToolCalls`——投影层依赖展示层。提案定义了 model/data/ui 边界规则但没点名这条既有边，照提案迁移时会把倒挂带进新结构。

### 3. 客户端 superadmin 守卫空转（中，真实缺陷，本次新发现）

`apis/base.js` 中 `apiSuperAdminGet/Post/Put/Delete` 调 `checkSuperAdminPermission()` 做前置守卫，但该函数（`identity/model/user.js:261-264`）**只返回布尔值、从不 throw**，而 `checkAdminPermission` 是 throw 风格——base.js 裸调用不接返回值，所以 4 个 superAdmin 变体的客户端守卫是**空操作**。`DebugComponent.vue:1123` 的 `if (!checkSuperAdminPermission()) return` 说明调用方已经意识到两个函数契约不一致。最终安全由后端兜底（符合架构声明），但客户端守卫名存实亡，且两个同名模式的函数契约不一致是认知陷阱。

### 4. 跨域耦合枢纽 + 模块无公开入口（中）

modules 间跨域 import 共 84 处。耦合中心是 **identity**（被 6 个域引用 18 次）和 **agents**（`ModelSelectorComponent`、`ShareConfigForm` 被 5 个域的 UI 直接消费）；session 是最大消费方（出边 6 个域）。dashboard 是唯一零跨域的干净域。

session 域**没有公开入口（无 index）**，外部 8+ 处深路径直捣其内部：`AppLayout → SessionNavSection/GlobalSearchModal`、`agents/ui/ScheduledAgentEditor → ToolApprovalModeSelector`（跨模块深路径）、`knowledge/FileDetailModal`、`workspace/WorkspacePreviewPane → AgentFilePreview` 等。提案规划了依赖方向但未盘点这些存量消费者，迁移时全是隐性破坏面。另外 `GlobalSearchModal` 同时挂在 AppLayout 和 AgentPanel 两个宿主下，一个组件两套生命周期。

### 5. pages 层残留巨石（中）

AgentView 已瘦身，但其余页面未跟上：`DataBaseInfoView.vue` 1424 行、`WorkspaceView.vue` 1245 行、`EvaluationBenchmarkDetailView.vue` 1077 行、`LoginView.vue` 1066 行、`HomeView.vue` 859 行，普遍带 400-600 行脚本和**非 scoped 全局 style 块**（如 `DataBaseInfoView.vue:1324-1424`），业务逻辑仍住在页面里而不是 modules。`AppLayout.vue` 1034 行，是导航+布局+GitHub star 拉取的混合体。

### 6. 迁移收尾未完成（低，但影响认知）

`src/components/`（12 个空子目录）、`composables/`、`layouts/`、`router/`、`stores/`、`utils/`、`views/` 全部是**零文件的空目录残骸**——文件已在 `06ebcb19` 删除，磁盘目录未清。边界规则已把它们列为“废弃导入目标”，留着只会误导新读者以为还有两套结构。

## 三、横切基建问题

### 7. 持久化与 Token 管理分裂（中高）

- pinia-plugin-persistedstate 已注册但**只有 2/10 个 store 在用**；其余持久化全部手写 storage，key 命名三种风格并存（`yuxi:` / `yuxi_` / 裸 key）。
- **token `user_token` 有三处写入点**：`user.js:36`、`OIDCCallbackView.vue:83`、`DebugComponent.vue:1136`（超管模拟登录直写 localStorage，绕过 user store 的会话状态机）。DebugComponent（2388 行）还内置了一个生产包里的 localStorage 管理器。
- 存 localStorage 意味着 XSS 即会话接管；缓解靠 DOMPurify 渲染管线，但 `AgentFilePreview.vue:245,360` 有 `v-html` 注入 hljs 输出。
- `sessionStorage` 的 OIDC 重定向语义分裂为 `redirect` / `oidc_redirect` 两个 key、三处写入。

### 8. 双错误处理体系（中）

`apis/base.js`（按状态码映射中文文案，日志脱敏做得好）与 `shared/lib/errorHandler.js`（又映射了一遍，401 文案还不一致：“登录已过期” vs “认证失败”）并存。全仓库 `message.error(` 271 处、分布 55 个文件，`ErrorHandler` 使用者仅 7 个文件——实际策略是“401 base 层 toast，其余调用方自理”，存在 401 双重 toast 的可能。另外 `base.js:92` 把运维指令“请使用 docker compose logs api 查看”直接暴露给终端用户。

### 9. 依赖双轨冗余（中，直接影响 bundle）

| 冗余 | 证据 |
|---|---|
| **antd 全量注册**：`main.js:8,18` `import Antd from 'ant-design-vue'` + `app.use(Antd)`，无按需引入，整个组件库进 vendor | 最大单项 bundle 成本 |
| **双语法高亮器**：shiki（重，TextMate/WASM）渲染聊天 markdown（`shared/lib/markdown_preview.js:5`），highlight.js 渲染文件预览（`AgentFilePreview.vue:390`） | 能力完全重叠 |
| **双图标库**：`@lucide/vue` 98 个文件在用，`@ant-design/icons-vue` 仅 8 个文件（迁移残留），6 个文件两家同时 import | |
| **katex 双版本**：直连 `katex@^0.18.4` 只为引一份 CSS（`MarkdownPreview.vue:19`），实际渲染走 `@vscode/markdown-it-katex` 传递的 `^0.16.4` | 双版本共存且漂移 |
| `@opencode-ai/models` 声明为 runtime 依赖但零运行时 import（纯构建期快照，用法聪明，声明位置误导） | |
| echarts + @antv/g6 并存 | 用途不同（统计图 vs 关系图），可接受 |

### 10. 样式体系失控（中高）

- **`extensions.less`（838 行）被 11 个组件的 scoped 块各自 `@import`**，同一份样式带 11 种 scope hash 重复编译进产物（约膨胀 9000 行 CSS）。受影响文件清单已验证：FileTable、SkillCardList、McpDetailView、ExtensionsView、DataBaseInfoView 等 11 处。
- `SessionWorkspace.vue:3918` 在 scoped 块里 import 全局 `main.css`。
- 全 src 共 **298 处 `:deep(`**（56 个文件）、**313 处 `.ant-*` 选择器覆盖**——是“重度重皮”而非主题 token 定制；`main.css:20-28` 还有 `* { position: relative }` 全局通配 reset。
- scoped/全局双 style 块混用无规范（13 个组件双块并存，6 个纯全局）。

## 四、会话域机制细节

既有提案（`docs/develop-guides/decisions/proposed/2026-09-30-agent-view-frontend-refactor.md`）对结构性问题覆盖完整且与代码现状吻合（巨石组件、深链接依赖侧栏分页、草稿三处分裂、steer 携带 model 配置的契约不一致、主/子两套阅读投影、随 Turn 停止观察等，均已逐一实锤）。但以下机制级问题提案未点名：

1. **审批弹窗是全局单例**：`useApproval.js` 的 `approvalState` 是单个 reactive，跨线程靠 `getVisibleThread()` 门控互斥——多线程同时 waiting 时只能显示一个。
2. **输入领取靠每条 1 秒轮询**：`useAgentInputQueue.js:80` 每个 input 一个独立定时器，N 条排队 = N 个轮询器，无退避。提案说“前端只投影服务端 FIFO”却没给出替代轮询的领取通知机制。
3. **重连无退避**：主流固定 1s `setTimeout`（`useAgentRunStream.js:211-217`），子流固定 2s（`useSubagentRuns.js:97`），服务端故障时是固定频率重压。
4. **JSON 深拷贝热点**：`agentItems.js:4` 每个事件 delta/快照都用 `JSON.parse(JSON.stringify)` 克隆，长流式 run 下的 CPU 成本未被列入提案性能清单。
5. **草稿 store 双实例**：`chatThreads.js:8` 模块级与 `SessionWorkspace.vue:960` 组件级各持一个 `createThreadDraftStore()` 实例操作同一批 localStorage 键，两个 Owner 写同一存储。
6. **主聊天内联渲染行，子线程用 `ThreadMessageList`**——两套投影消费者并存（提案点名了现象，但未给出统一时的这条具体路径）。
7. `tools/ToolCallRenderer.vue` 静态 import 全部 24 个渲染器，低频 renderer 无懒加载。

## 五、工程化短板

- **TS 迁移几乎未开始**：src 下仅 1 个 `.ts`（`threadRouteCoordinator.ts`），`tsconfig.json` 只 include `src/**/*.ts`——"strict 类型检查"的覆盖面目前约等于零，“build 先 typecheck”实际只查一个文件。
- **ESLint 用最弱档**：`eslint.config.js` 采用 `pluginVue.configs['flat/essential']`（非 strongly-recommended/recommended），大量模板级问题不在检查范围。
- **router meta 双轨**：`public: true`（仅 OIDC 回调用，守卫根本不读它）与 `requiresAuth: false` 并存，`public` 是死元数据；路由 import 相对路径（`../../pages/`）与别名（`@/pages/`）混用。
- **硬编码环境地址**：`BasicSettingsSection.vue:68-113` 四处 `http://localhost:7474/5050/9001/9091` 外链（非本机部署全失效）；`AppLayout.vue:89` 硬编码 GitHub API；`pixelAvatar.js:1` 硬编码 dicebear.com 外联。
- **无 i18n 框架**，单语硬编码中文（这算已知取舍，不算缺陷，但 104 个含中文的 vue 文件将来是要还的账）。
- 测试体系本身是亮点：89 个 unit 测试文件用 `node --test` + Vite `ssrLoadModule` 直接加载真源码（含 .vue），另有 Playwright browser 测试。但全部测试只覆盖 JS/model 层与少量组件行为，5647 行的编排组件只能靠 browser 测试兜底。

## 六、健康面（这些是对的，重构时别误伤）

- 五层骨架 + ESLint 边界规则 + 边界回归测试的组合，约束真实有效；shared 零反向依赖、modules 不依赖 app/pages 均已验证；
- `apis/` 各文件是干净的薄包装，`base.js` 的日志脱敏、422 白名单质量高；
- session 的 SSE 字节解析（`processRunSseResponse`）、事件 reducer（`agentItems.applyAgentEvent`）、流平滑器（`useStreamSmoother`，rAF + 字素级 + reduced-motion）都是可独立测试的纯函数，设计良好；
- dashboard 域完全自包含，是 modules 里分层最干净的样本；
- `AgentView.vue` 93 行 + `threadRouteCoordinator.ts` 串行队列说明重构方法论已经跑通一次。

## 七、优先级建议

1. **P0**：修 `apiSuperAdmin*` 守卫空转（一行契约统一）；收敛 token 三处写入到 user store 单点。
2. **P1**：打断 `apis ↔ identity` 双环（token 注入化）；消除 `messageGrouping → toolRegistry` 倒挂；按提案推进 SessionWorkspace 拆分，并把本次新发现的 7 个机制问题补进提案的阶段清单（尤其审批单例与轮询领取，它们影响提案的目标架构形状）。
3. **P2**：antd 按需引入；shiki/hljs 收敛为一个；extensions.less 改为真正全局引入一次；清理 7 组空目录。
4. **P3**：pages 层大页面按 AgentView 模式逐个搬入 modules；TS 覆盖面扩大到 model 层；router meta 统一。

## 保存说明

本报告不是运行时事实源，也不替代源码、测试、构建产物或决策记录。后续优化按上述目标推进；每项完成状态以对应语义 Owner、负向测试、实际命令和独立 Review 为准。
