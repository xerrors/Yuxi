# 现代 Web CSS 基线：滚动条标准化、color-scheme 同步与 textarea 自适应

状态：implemented
类型：simplification
Owner：frontend/src/assets/css/base.css

全局滚动条与浅色 `color-scheme` 由 base.css 拥有；深色覆盖由 frontend/src/assets/css/base.dark.css 拥有；组件内滚动条与 textarea 自适应分别由 frontend/src/modules/session/ui/workspace/AgentFilePreview.vue 和 frontend/src/modules/session/ui/HumanApprovalModal.vue 拥有。

## 问题

modern-web-guidance legacy-scan 全量体检（含 docs/tests，9 处发现、5 类）暴露三个真实问题：

1. 全局与组件的 `::-webkit-scrollbar` 规则与标准 `scrollbar-width`/`scrollbar-color` 并存：现代浏览器（Chrome/Edge 121+、Safari 18.2+）按规范压制前缀规则，组件内声明的滚动条样式在多数用户浏览器上实际失效，两套机制冲突。
2. 主题切换只切 `html.dark` 类，未同步 `color-scheme`：深色模式下原生表单控件、自动填充、UA 滚动条等仍按亮色渲染。
3. HumanApprovalModal 的 textarea 用 JS 逐帧 `scrollHeight` autosize；`field-sizing: content` 已 Baseline（2026-06），浏览器原生能力可承担该职责。

## 决策

### 实现方案

- 滚动条：标准属性为主（全局 `*` 与 `.file-content`），`::-webkit-scrollbar` 兜底按指南包进 `@supports not (scrollbar-color: auto)`，仅在标准属性不可用的旧 WebKit 生效，消除双机制冲突。
- color-scheme：以 `.dark` 类为唯一事实源——`base.css :root { color-scheme: light }`、`base.dark.css :root.dark { color-scheme: dark }`，类切换点（theme.js `updateDocumentTheme`）不变；`index.html` 增加静态 `<meta name="color-scheme" content="light">` 仅覆盖 CSS 加载前窗口，CSS 声明按规范覆盖 meta，JS 不更新 meta。不引入 `light-dark()`，调色板仍由既有 CSS 变量承载。
- textarea：HumanApprovalModal 两个 textarea 由 `field-sizing: content` + CSS 限高承担自适应（`calc(4lh + 10px)` 精确复刻原 JS 的 4 行 × 行高 + 10px 内边距：free-text 94px、other 88px），原 JS 保留为旧浏览器兜底，以 `typeof CSS !== 'undefined' && CSS.supports('field-sizing', 'content')` 守卫（node:test 环境无 `CSS` 全局，走 JS 路径）。

## 替代方案

- meta + JS 双写同步 color-scheme、`:has()` 映射、`light-dark()` 全量迁移调色板：状态双写增加漂移面，调色板迁移是大规模重写且无对应验收主张，拒绝。
- 删除 JS autosize 只留 `field-sizing`：旧浏览器从自动撑高退化为固定高度，属无守卫的静默回归，拒绝。
- `sibling-index()` 改写 loading.css stagger：不支持的浏览器中声明失效、延迟回退 0s，加载条同步脉动，可感知回归换零用户价值，拒绝；`interpolate-size` 对 tool-args 面板（0↔300px 定长过渡、合法滚动上限）不适用，跳过。
- MessageInputComponent 的 scrollHeight autosize：其 inputRef 是 contenteditable div，`field-sizing` 只适用表单控件，属扫描器误报，跳过。

## 后果

- 深色模式原生控件/自动填充/UA 渲染跟随主题；浅色行为不变。
- AgentFilePreview 滚动条在现代浏览器从"被全局标准属性压制的失效声明"变为组件声明的 gray-300/gray-50——恢复其设计意图，是用户可见变化。
- JS autosize 兜底路径短期双轨：`field-sizing` 在目标浏览器矩阵普及后可删除（当前旧浏览器仍依赖）。
- 源码另有 7 处裸 `::-webkit-scrollbar`（AgentEditModal、AgentPanel、SessionWorkspace 等）不在本次扫描报告内，现代浏览器下同样被全局标准属性压制、旧 WebKit 生效；未纳入本次范围。

## 验证

- `pnpm run lint:check`、`pnpm run test:unit`（1095 通过，含 humanApprovalModal 单测）、`pnpm run build`、`verify_engineering_contracts.py` 全部通过；构建产物在容器内 grep 确认 `@supports not (scrollbar-color:auto)` 正确包裹两处前缀规则、`field-sizing:content`、`calc(4lh + 10px)`、`color-scheme:dark` 均在 dist（宿主 dist 为旧产物，复核须在容器内执行）。
- 真实页面（localhost:5173）：浅色 `colorScheme=light`、meta=light、无 dark 类；`theme=dark` 后 `html.dark` 且 `colorScheme=dark`，深色下原生输入框跟随深色（截图）。
- 真实浏览器隔离挂载 HumanApprovalModal 实测：free-text 空态 54px/上限 94px、other 空态 30px/上限 88px，超限 `scrollable=true`，与原 JS 数值逐项相等；浅/深两态渲染截图正常。审批弹窗未走真实后端审批流（构造成本高；组件行为已由真实组件 + 真实浏览器覆盖）。
- 旧能力不存在：现代浏览器上 JS 逐帧 autosize 与被内联覆盖的 `max-height: 112px` 死值不再生效，行为由 CSS 声明拥有。
- 重新引入条件：仅当 `field-sizing` 从目标浏览器矩阵移除（或出现其缺陷）时，才需要恢复 JS 主路径；JS 兜底删除条件为该属性在支持矩阵全量普及。
