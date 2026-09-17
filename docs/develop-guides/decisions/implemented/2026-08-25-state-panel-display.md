# 状态面板显示与悬浮布局

状态：implemented
类型：bug-fix
Owner：web/src/components/AgentChatComponent.vue

## 问题

状态面板内容超过可视高度时滚动不稳定，悬浮面板也可能覆盖高度会变化的消息输入区。刷新按钮长期占据标题区；同一子线程的多个运行记录重复显示，任务描述缺失时还会回退到不稳定的运行或工具调用 ID。待办状态使用多套图标和颜色，产物卡片包含不必要的第二行元信息。

交付物卡片虽然能打开侧栏，但 artifact 路由只返回 Office 原始字节，没有经过文件树使用的统一预览渲染，因此 DOCX/PPTX 会被前端判断为不支持预览。

## 决策

- 固定面板和悬浮面板都保持内容自然高度，并通过 `ResizeObserver` 计算各自的 `max-height`：固定模式使用容器高度扣除上下边距和卡片边框，悬浮模式跟随输入区真实顶边；超过上限后由面板 body 独占纵向滚动。
- 刷新按钮默认隐藏，只在面板悬浮、键盘焦点进入面板或设备不支持 hover 时显示；刷新中的 disabled 状态不绕过隐藏规则。
- 展示层先按真实 run 或工具调用合并流式占位，再按 `child_thread_id` 收敛为每个子线程一项并保留最新状态。描述依次使用状态字段、父对话中 `task` 或 `subagent_start` 的 `description`、`child_thread_id`。
- 待办统一使用中性的空心圆和浅灰实心圆；进行中为空心圆内辅助色闪烁圆点，并在 reduced-motion 下停止动画；已取消项使用中性虚线圆并保留“已取消”的无障碍文案。附件与产物卡片使用较小文件图标，只保留单行名称。
- artifact 下载继续返回原始文件；预览请求显式携带 `preview=true`，在既有授权检查后复用 Workspace 文件预览适配器，使 DOCX/PPTX 与文件树一样转换为 PDF，不建立第二套格式判断。

其中状态面板展示由 `web/src/components/AgentChatComponent.vue` 拥有，交付物预览授权与响应由 `backend/package/yuxi/services/artifact_service.py` 拥有。Artifact 只把已授权字节交给预览适配器，runtime Office 缓存仍由 [`yuxi.workspace.preview`](./2026-08-21-preview-owner-separation.md) 拥有。

## 替代方案

- 修改后端持久化结构或新增描述接口字段：任务描述已经由父对话子智能体启动调用的入参拥有，重复写入运行状态会形成第二份可漂移事实。
- 为悬浮面板预留固定底部高度：排队请求、审批和多行输入会改变输入区实际高度，固定值仍可能重叠。
- 在 LangGraph reducer 按 `child_thread_id` 合并运行：该 reducer 拥有逐次 run 历史，UI 才拥有“每个子线程只显示一项”的展示需求，因此不改变持久状态语义。

## 后果

继续同一子线程时，状态面板只显示最新一项，旧运行不会单独占行；最新调用没有任务描述时会保留同线程已有描述，全部缺失时显示 `child_thread_id`。悬浮面板自然贴合内容，可用最大高度随输入区变化，极低可用高度下优先保证不覆盖输入区。刷新操作在桌面端成为渐进显现的次要操作，在触屏设备上仍保持可见。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| 固定与悬浮面板按可用高度设置上限且不产生负值 | `statePanelLayout.test.js` 覆盖固定、悬浮、缺少测量和极低可用高度 | Passed |
| 同一子线程只显示最新状态并有稳定描述 | `subagentRuns.test.js` 覆盖同步 `task`、异步 `subagent_start`、继续同一 `child_thread_id`、描述回填与无线程 ID 流式占位 | Passed |
| 已完成、进行中、待处理与已取消状态保持独立语义 | `agentPanelSections.test.js` 检查 cancelled 文案和中性虚线状态 | Passed |
| 交付物预览保留原始下载语义 | artifact service/router unit 覆盖预览 renderer、超限和查询参数；`test_chat_router.py` 通过真实 HTTP 验证 Markdown JSON preview 与原始字节下载 | Passed |
| DOCX/PPTX artifact 在真实 HTTP 中转换为 PDF 并由浏览器展示 | 未执行真实 Office 转换和页面验证 | Not run |
| 前端 lint、unit 与生产构建成立 | `cd web && pnpm run lint:check && pnpm run test:unit && pnpm run build` | Passed；构建仅有既有第三方注释和大 chunk warning |
| 决策记录可由文档站点完整构建 | `cd docs && pnpm run build` | Passed；保留既有语法高亮、Rolldown 插件和大 chunk warning |
| 工程契约与 staged diff 格式成立 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts`；`git diff --cached --check` | Passed |
| 状态面板在真实历史对话中按内容收缩 | 真实 Compose 页面打开含一个附件的状态面板，面板为 340×131px，body 的 clientHeight 与 scrollHeight 均为 89px，不占满聊天区 | Inspected |
| hover、超限滚动、键盘焦点、纯触屏、暗色与更多响应式视觉完整 | 未构造全部交互状态 | Not run |

子任务状态来源由[独立子 Run 观察](./2026-09-17-subagent-independent-observation.md)取代；父 state 仍提供子任务身份，历史 task 描述继续展示。
