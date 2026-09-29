# AgentPanel 承载文件与子智能体线程 Section

状态：archived
类型：feature
Owner：web/src/components/AgentPanel.vue

## 问题

AgentPanel 当前只承载文件树和文件预览，子智能体线程通过独立 Modal 展示。用户无法在主对话侧边持续观察多个子智能体线程，关闭面板也会因为组件卸载而结束内容生命周期。子智能体内容的事实身份是 `thread_id`，页面需要展示该线程的历史消息，并在存在活动 Run 时持续订阅 SSE。

## 决策

AgentPanel 使用扁平一级 Tabs：文件树、每个文件预览和每个子智能体线程都是平级 Tab，文件预览按路径去重，子智能体按 `thread_id` 去重。点击文件新增或激活文件 Tab，文件树快捷按钮保留在面板头部，文件搜索与刷新位于文件树 Tab 的工具栏。`SubagentThreadView` 独立加载线程 state/history，发现非终态 Run 时订阅对应 Run SSE，终态后重新读取历史。AgentPanel 隐藏时保持 Tab 挂载；关闭内容 Tab、切换父线程或销毁页面时卸载组件并停止 SSE。

## 替代方案

- 继续使用 Modal：无法在侧边栏持续观察多个线程，也不符合统一 PanelSection 的交互目标。
- 让父组件传入 Run 和 ongoing 消息：会把子线程内部状态重新耦合到主聊天状态，违背 thread 视图独立边界。
- 文件工作区作为固定一级 Section：会让文件预览和子智能体形成不对等的二级结构，不符合 Tab 直接代表当前打开内容的交互。

## 后果

每个打开的运行中子智能体 Tab 保持一条 SSE；隐藏 AgentPanel 保持组件挂载，关闭内容 Tab 或切换父线程触发卸载与清理。文件树、文件预览和子智能体共享一级 Tab 条，文件树快捷按钮提供固定返回入口。子线程不进入主线程 Store 或路由。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 点击有 `thread_id` 的子智能体在 AgentPanel 打开并按线程去重 | 同一线程产生重复 Tab，或无 thread_id 仍可打开 | `AgentChatComponent.vue` | 前端 unit 与真实页面 | 连续点击同一线程只保留一个 Section | Passed |
| 子智能体 Section 展示历史，并在活动 Run 存在时按游标订阅 SSE | 只显示首次快照、断线从头重放或终态后不回读历史 | `SubagentThreadView.vue` | 真实终态子线程页面；代码路径检查活动 Run SSE | 终态线程不得继续订阅；非终态断线使用最后事件游标重连 | Inspected |
| 隐藏 AgentPanel 不卸载 Section，关闭 Tab 才结束生命周期 | 隐藏面板导致 SSE abort，或异步 state 返回后在卸载组件中重新订阅 | `AgentPanel.vue` 与 `SubagentThreadView.vue` | 真实页面隐藏/重开；组件卸载版本检查 | 隐藏后组件仍存在；卸载后的异步结果不得启动 SSE | Inspected |
| 文件树、文件预览和子智能体为平级 Tab，激活 Tab 保持可见 | 文件预览仍嵌套在文件 Section，或新建 Tab 落在滚动视口外 | `AgentPanel.vue` | 真实页面检查文件树、两个文件和子智能体 Tab | 点击树中文件必须新增、激活并滚动到可见范围 | Passed |
| 文件预览填满 Tab 剩余高度并保持内容可滚动 | Markdown 最后几行被遮挡，或 PDF/HTML iframe 未填满面板 | `AgentPanel.vue` 与 `AgentFilePreview.vue` | Playwright 回读四层容器高度 | `tab-content`、preview shell、内容区底边必须与面板底边一致 | Passed |
| Skill Markdown 在代码高亮能力暂不可用时仍保持结构化渲染 | Shiki 加载失败导致整篇文档退回源码 `<pre>` | `web/src/utils/markdown_preview.js` | 前端 unit；真实 `pptx/SKILL.md` 页面检查 | 无高亮器时标题、frontmatter 和代码块结构仍存在 | Passed |
| lint、unit、build 与工程契约通过 | 新 Section 未接线或 decision 生命周期失效 | `web/` 与工程 gate | 标准前端和工程契约命令 | 删除 Section 分支或矩阵后 gate 失败 | Passed |

## 风险

每个打开的运行中子智能体 Tab 都保持一条 SSE，用户需通过关闭 Tab 释放订阅。首版不展示 Tab 状态徽标，不把子线程写入主线程 Store 或路由，也不支持在子线程视图中发送消息。
