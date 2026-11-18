# 前端 UI 与交互审查报告

审查日期：2026-11-17。本文面向产品、设计和前端维护者，合并保存两轮审查的 16 项发现，用于安排优化和复验。编号与审查反馈保持一致；优先级不由编号决定。

本报告是当日运行页面与工作区源码的审查快照，不是修复完成记录或持续维护的系统契约。实施前应重新核对相关源码；文中的公开源码链接用于定位模块，不保证远端主分支与审查时的未提交工作区完全一致。

实施范围：第 1、2、3、9–16 项；第 4–8 项按用户“（不对）”反馈排除。实现与验证见[交互恢复与输入保护决策](../decisions/implemented/2026-11-17-frontend-interaction-recovery.md)，下文保留原始审查发现。

## 范围与判断依据

审查使用 [Hallmark](https://github.com/Nutlope/hallmark) 的应用界面检查方法，结合 Playwright 浏览器操作、DOM 检查、axe 可访问性扫描和源码核对。视觉判断遵循项目[设计规范](../design.md)，保持现有工具型产品定位，不套用营销落地页的装饰要求。

检查覆盖首页、登录页，以及登录后的会话、工作区、知识库、Skills、工具、MCP、智能体、模型供应商、定时任务、集成、反馈等入口；深入检查部分设置、创建弹窗、资源详情和 3 个已有会话。响应式检查采用 1440、1024、768 CSS 像素，768 为项目规定的最低支持宽度。

证据分为三类：

- **浏览器复现**：实际操作或读取渲染 DOM 后观察到结果。请求拦截产生的故障会单独标明。
- **源码核对（Inspected）**：确认实现方式，尚未完成对应运行态或保存链路验证。
- **设计建议**：基于布局和任务流程提出改进方向，不等同于功能故障。

审查没有执行真实资源创建、删除、凭据保存、外部工具测试或新会话生成。表单检查只使用未保存的临时输入。报告不包含账号、凭据、会话内容或用户资源名称。

## 优先级总览

| 编号 | 优先级 | 发现 | 证据性质 |
| --- | --- | --- | --- |
| 1 | 高 | 设置导航与资源卡片无法完整使用键盘操作 | 浏览器 DOM、源码 |
| 2 | 高 | MCP 加载失败显示为无资源 | 浏览器复现，模拟网络失败 |
| 3 | 高 | 知识库搜索无结果时列表区域空白 | 浏览器复现 |
| 4 | 高 | 登录页在 768px 下横向溢出 | 浏览器尺寸测量 |
| 5 | 中 | 辅助文案和部分功能文字对比度不足 | 浏览器计算样式、axe |
| 6 | 中 | 登录表单缺少就地错误说明 | 浏览器复现、源码 |
| 7 | 中 | 推荐技能占据个人技能的首屏位置 | 设计建议 |
| 8 | 中 | 工作区在最低支持宽度下内容空间不足 | 布局测量、设计建议 |
| 9 | 低 | 智能体列表过度突出内部 ID | 设计建议、源码 |
| 10 | 高 | 用户信息请求返回 502 后清除登录态 | 实际故障、浏览器 DOM 与存储检查 |
| 11 | 高 | 创建弹窗关闭后丢失未保存草稿 | 浏览器复现 |
| 12 | 中 | 搜索弹窗的 Enter 和焦点约束不完整 | 浏览器复现 |
| 13 | 中 | 返回知识库列表后搜索条件丢失 | 浏览器复现 |
| 14 | 中 | 知识库与工具筛选框缺少可访问名称 | 渲染 DOM、axe、源码 |
| 15 | 中 | 凭据编辑的留空含义不一致 | 源码核对、设计建议 |
| 16 | 低 | 保留优雅动效，减少不必要的动画位移 | 源码核对、设计建议；运行态未验证 |

## 发现详情

### 1 设置导航与资源卡片无法完整使用键盘操作

**高优先级 · 浏览器 DOM 与源码核对。** 设置侧栏多数入口使用绑定点击事件的 `div`，没有按钮语义和可聚焦属性。审查时 10 个入口中仅“设备管理”使用原生按钮，其余 9 个的 `tabIndex` 为 -1。资源列表使用的 `InfoCard` 根节点也无法通过 Tab 聚焦打开。

建议使用原生按钮或链接表达入口，保留清晰的焦点样式；卡片内若有独立操作，需避免嵌套交互元素。复验时仅使用 Tab、Shift+Tab、Enter 和 Space，应能进入各设置分组和资源详情，并明确感知当前位置。

源码：[SettingsModal.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/settings/ui/SettingsModal.vue)、[InfoCard.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/ui/InfoCard.vue)。

### 2 MCP 加载失败显示为无资源

**高优先级 · 浏览器复现，故障由请求拦截模拟。** 中断 MCP 列表请求后，页面出现短暂错误提示，但主体显示“暂无连接器，点击上方按钮添加”，没有持续错误区域。用户可能将读取失败理解为资源不存在。

建议区分加载中、成功但为空、加载失败三种状态；失败时提供明确错误说明和重试入口。复验需分别覆盖真实空列表、请求失败及恢复成功，确保失败状态不会引导用户重复创建资源。

源码：[McpCardList.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/extensions/ui/McpCardList.vue)，重点检查列表读取异常与空状态分支。

### 3 知识库搜索无结果时列表区域空白

**高优先级 · 浏览器复现。** 已有知识库时输入不匹配的关键词，卡片数量变为 0，列表区域没有“无搜索结果”或清除筛选提示。源码中的空状态判断使用原始列表长度，卡片循环使用过滤后的列表，两者判断对象不同。

建议单独呈现搜索无结果状态，保留关键词并提供清除搜索或重置筛选入口。复验需区分“系统尚无知识库”和“已有知识库但搜索未匹配”，两者的提示与行动应不同。

源码：[KnowledgeCatalog.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/knowledge/ui/KnowledgeCatalog.vue)，重点检查 `filteredKnowledgeBases` 对应的渲染分支与空状态条件。

### 4 登录页在 768px 下横向溢出（不对）

**高优先级 · 浏览器尺寸测量。** 登录卡片宽度为 900px；在 768px 视口中，测得卡片左边界约为 -66px，文档滚动宽度约为 834px，内容超出可视范围。这发生在项目明确支持的最低宽度。

建议为卡片设置随视口收缩的最大宽度，并在窄屏调整两栏布局和留白。复验 768、1024、1440px 下的登录表单、错误提示和主要操作，确认没有整页横向滚动或不可见控件。

源码：[LoginView.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/pages/LoginView.vue)，重点检查登录容器的 `width: 900px` 及响应式样式。

### 5 辅助文案和部分功能文字对比度不足（不对）

**中优先级 · 浏览器计算样式与 axe 检查。** 浅色模式下，登录副标题对比度约为 2.86:1，替代登录分隔文案约为 1.85:1；会话输入提示使用较浅灰色。补充检查中，项目按钮、智能体选择和“完全信任”等功能文字也出现约 2.7–2.9:1 的对比度。

建议按文字用途调整颜色 token，优先提高功能选项、状态文字与输入提示的可读性。常规小字号文字以至少 4.5:1 为复验目标；禁用控件与纯装饰元素应单独判断，不将扫描器输出直接计为缺陷。浅色和深色模式需要分别检查。

源码：[LoginView.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/pages/LoginView.vue)、[MessageInputComponent.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/MessageInputComponent.vue)。

### 6 登录表单缺少就地错误说明（不对）

**中优先级 · 浏览器复现与源码核对。** 空表单提交后主要依赖短暂提示，输入框附近没有持续的校验说明，焦点仍停留在提交按钮。源码通过 `display: none !important` 隐藏 `.ant-form-item-explain`。

建议恢复字段级错误信息，并将错误与输入框关联；提交失败后聚焦第一个需要修正的字段。复验空值和格式错误时，应能直接看出需要修改哪一项，且不会发出不必要的登录请求。

源码：[LoginView.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/pages/LoginView.vue)，重点检查表单校验与 `.ant-form-item-explain` 样式。

### 7 推荐技能占据个人技能的首屏位置（不对）

**中优先级 · 设计建议。** Skills 页面把推荐分组排在个人技能之前。审查布局中，3 张较大的推荐卡片在 768px 宽度下将个人技能推到首屏之外，用户需要先浏览推荐才能管理自己的资源。

建议按管理任务调整信息顺序：优先显示已安装或个人技能，将推荐作为紧凑区块或可折叠内容。复验应覆盖已有个人技能与首次使用两种场景，比较“找到并打开自己的技能”所需的滚动和操作。

源码：[SkillCardList.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/extensions/ui/SkillCardList.vue)，重点检查推荐和个人技能分组顺序。

### 8 工作区在最低支持宽度下内容空间不足（不对）

**中优先级 · 布局测量与设计建议。** 768px 下，全局侧栏和工作区侧栏合计占用约 446px，主内容剩余约 322px。虽然没有整页横向溢出，文件阅读和预览仍显拥挤。

建议在该宽度下折叠其中一层导航，保留明确的展开入口，把主要空间交给文件内容。复验文件列表、预览和搜索流程，确认导航收起后仍容易恢复，且不会以隐藏关键操作换取空间。

源码：[WorkspaceView.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/pages/WorkspaceView.vue)。

### 9 智能体列表过度突出内部 ID

**低优先级 · 设计建议与源码核对。** 智能体卡片直接以 `agent_id` 作为副标题。内部标识对配置和排查有用，但占据了普通用户选择智能体时更需要的用途描述位置。

建议在列表中优先显示用途或能力描述，将 ID 放到详情、复制操作或次级提示中。复验时既要让普通用户容易选择，也要保留维护者查询和复制 ID 的能力。

源码：[AgentManagePanel.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/agents/ui/AgentManagePanel.vue)，重点检查卡片 `subtitle`。

### 10 用户信息请求返回 502 后清除登录态

**高优先级 · 实际故障与浏览器验证。** 审查期间后端不可用，刷新页面后的 `/api/auth/me` 请求返回 502。浏览器随后跳回登录页，本地登录凭据被清除。路由守卫在用户信息加载失败时无条件调用 `logout()`，没有区分认证失败与服务异常。

建议按错误语义处理：认证确实失效时重新登录；网络或服务异常时保留凭据，显示可重试状态，同时阻止进入尚未完成身份校验的页面。后端权限检查仍是授权边界。

复验应分别覆盖 401、502 和网络中断；服务恢复后能够重新校验身份并继续原目标页面，且不能因保留凭据绕过身份或权限校验。

源码：[router/index.js](https://github.com/xerrors/Yuxi/blob/main/frontend/src/app/router/index.js)、[identity/model/user.js](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/identity/model/user.js)。

### 11 创建弹窗关闭后丢失未保存草稿

**高优先级 · 浏览器复现。** 新增智能体填写名称后按 Escape，弹窗直接关闭；重新打开时名称消失。新建知识库填写名称并进入第二步后，同样可以按 Escape 关闭且丢失已填内容，两者均没有放弃确认。

建议有修改时确认放弃，或保留本次本地草稿，不必直接引入服务端草稿系统。现有 Skill 编辑页已有放弃修改确认，可统一交互方式。

复验 Escape、关闭按钮及允许关闭的遮罩点击，确认其草稿处理一致；空表单仍可直接关闭。此结论只覆盖新建流程，不扩展为所有已有资源编辑弹窗都会丢失修改。

源码：[AgentEditModal.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/agents/ui/AgentEditModal.vue)、[KnowledgeBaseCreateFlowModal.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/knowledge/ui/KnowledgeBaseCreateFlowModal.vue)；可参考 [SkillDetailView.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/extensions/ui/SkillDetailView.vue) 的放弃修改确认。

### 12 搜索弹窗的 Enter 和焦点约束不完整

**中优先级 · 浏览器复现。** 搜索无匹配结果时，聚焦“关闭”按钮并按 Enter，弹窗没有关闭。连续按 Tab，焦点还会离开弹窗，进入被遮罩覆盖的页面导航。

容器级 `@keydown.enter.prevent` 会影响内部按钮的默认键盘行为，弹窗也缺少焦点循环约束。建议将结果选择快捷键限定在合适的输入区域，补齐焦点循环，并在关闭后将焦点返回触发位置。

复验有结果和无结果两种状态，覆盖 Enter、Escape、Tab、Shift+Tab；弹窗打开时背景不能被键盘操作。关闭后焦点恢复是建议的验收条件，本次没有将其列为已独立复现的问题。

源码：[GlobalSearchModal.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/GlobalSearchModal.vue)。

### 13 返回知识库列表后搜索条件丢失

**中优先级 · 浏览器复现。** 使用关键词定位知识库、进入详情，再通过浏览器返回时，列表搜索框恢复为空。连续查看多个匹配结果时需要重复输入。源码中的搜索与类型筛选仅保存在组件局部状态。

建议用路由参数或现有列表状态保存搜索条件，并按任务需要恢复列表滚动位置。复验连续打开多个匹配项再返回，确保关键词和筛选条件保留；清除筛选仍应是明确的用户操作。滚动位置恢复属于建议，未单独复现其丢失。

源码：[KnowledgeCatalog.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/knowledge/ui/KnowledgeCatalog.vue)，重点检查 `searchQuery` 与 `typeFilter` 的生命周期。

### 14 知识库与工具筛选框缺少可访问名称

**中优先级 · 渲染 DOM、axe 与源码核对。** 知识库“全部类型”和工具页“全部分类”在视觉上有提示，但渲染后的输入控件没有关联的 label、`aria-label` 或其他有效可访问名称。读屏用户难以判断筛选用途。

建议提供“知识库类型”“工具分类”等稳定名称，不依赖选中值或占位文字代替字段名称。复验无选择、有选择和清除选择时，可访问名称都应保持正确。

源码：[KnowledgeCatalog.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/knowledge/ui/KnowledgeCatalog.vue)、[ToolsCardList.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/extensions/ui/ToolsCardList.vue)。

### 15 凭据编辑的留空含义不一致

**中优先级 · 源码核对与设计建议。** OCR 凭据留空表示保留原值，并提供显式清除选项。网络搜索进入编辑后同样显示空输入框，但留空保存会清除数据库中的覆盖值。网络搜索界面已有文字说明，这不是未告知行为；风险在于相似表单使用相反规则。

建议统一为“留空保留，显式清除”，并明确环境变量与数据库覆盖值的关系。若保持不同规则，应进一步区分保存和清除操作。没有执行真实凭据保存或删除，运行结果仍需在隔离测试配置中验证。

源码：[WebSearchSettingsSection.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/settings/ui/WebSearchSettingsSection.vue)、[OCRSettingsSection.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/settings/ui/OCRSettingsSection.vue)。

### 16 保留优雅动效，减少不必要的动画位移

**低优先级 · 源码核对与设计建议；运行态未验证。** 公共页头和扩展详情的加载条每 1.5 秒横向循环移动一次。动效应保留优雅的节奏，并服务于加载反馈、状态变化和操作衔接；优化重点是减少与这些目的无关的持续位移或大幅移动。

建议结合实际页面评估加载条的移动距离、循环节奏和视觉强度，让反馈清晰但不抢占内容注意力。状态切换可使用轻微淡入淡出或小幅过渡，避免无意义的来回滑动和内容跳动。复验时通过慢请求及连续操作观察动效，确认加载状态可感知、衔接自然，且不干扰阅读或导致布局位移。

源码：[PageHeader.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/ui/PageHeader.vue)、[ExtensionDetailLayout.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/ui/ExtensionDetailLayout.vue)。

## 覆盖结果与未验证范围

| 检查范围 | 观察结果 | 结论边界 |
| --- | --- | --- |
| Hallmark 静态扫描 | 扫描 202 个前端源码文件，结合 DOM 与源码筛选告警 | 静态告警不直接等于产品缺陷；深色 token 定义、组件内部尺寸等误报未逐项计入 |
| 11 个登录后页面 | 在 1440、1024、768px 下未观察到整页横向溢出 | 不代表所有弹出层、数据长度或页面状态均无溢出；登录页问题单列为第 4 项 |
| 3 个已有会话 | 检查消息、执行过程与工具结果；测量的可见区域未观察到横向越界 | 未发起新任务，不能证明运行、流式响应、审批、取消或恢复链路正确 |
| 智能体与知识库创建弹窗 | 已检查较窄和较矮视口，正文可滚动且操作区可达 | 没有实际提交创建；草稿关闭问题见第 11 项 |
| MCP 与 Skill 详情 | 三档宽度下未观察到整页溢出，收缩为图标的操作仍有可访问名称或提示 | 不代表所有详情功能和提交结果均已验证 |
| 深色模式 | 部分会话与 MCP 页面做过截图检查 | 完整页面矩阵及交互状态未完成；补查期间后端不可用 |
| 保存与外部副作用 | Not run | 没有保存真实凭据、删除资源、执行外部工具或调用模型，避免改变现有业务数据 |
| 加载动效与动画位移 | Inspected | 已核对加载条循环位移实现；节奏、幅度及阅读干扰仍需运行态评估 |

浏览器复现材料包括路由尺寸记录、交互结果、axe 结果和截图；其中部分涉及已登录页面，仅用于本地审查，未随报告提交。本文保留不含用户数据的观察结果和可重新执行的复验条件。重新评估时应以当时源码和实际 DOM 为准。

## 建议处理顺序

1. **先处理状态与输入损失。** 优先解决服务异常清除登录态、创建草稿丢失、MCP 失败冒充空列表，以及知识库无搜索结果时缺少反馈。
2. **补齐基本操作能力。** 处理键盘可达性、搜索弹窗交互、登录表单错误反馈、登录页 768px 布局和功能文字对比度。
3. **改善连续任务体验。** 保留知识库搜索状态，补齐筛选名称，再评估技能分组、工作区空间与内部 ID 展示。
4. **统一细节规则。** 在隔离配置中验证凭据编辑语义，保留优雅且有反馈作用的动画，评估并减少不必要的持续位移和大幅移动。

修复范围与验证方式由各项实际变更决定，遵循[前端约定](https://github.com/xerrors/Yuxi/blob/main/frontend/AGENTS.md)和[测试规范](../testing-guidelines.md)。本报告中的复验条件是建议的验收输入，不表示测试已经执行或修复已经完成。
