# 前端细节优化保持局部状态与现有契约

状态：archived
类型：feature
Owner：web/src/components/AgentChatComponent.vue

## 问题

xhome #36 汇总了 HTML 流式预览占位、侧边栏搜索入口、Agent 文件面板、已完成对话过程展示和知识库创建流程五项体验问题。前三项中的“子智能体会话进入通用 Tab”会改变子线程消息加载、SSE 生命周期和 Tab 身份模型，若与局部视觉优化同时实现，会扩大状态与回归边界。

## 决策

本次保持后端接口、持久化和路由不变：HTML 流式预览使用独立的较小 loading 高度；侧边栏搜索与折叠入口组成头部按钮组；Agent 文件面板增加局部最大化/还原状态；已收尾且存在最终助手消息的对话将中间助手消息与工具调用聚合为默认收起的耗时分隔行；知识库创建改为“类型、配置、权限”三步向导，并继续消费后端知识库类型元数据。AgentPanel 的文件树、文件预览与子智能体线程 Tab 由后续聚焦决策记录拥有。

## 替代方案

- 一次完成通用文件/子智能体 Tab：功能更完整，但需要拆分子智能体 Modal 内容状态机，并决定多 Tab SSE、切换重载和父线程切换清理语义，不适合与局部优化捆绑。
- 只调整 CSS：无法解决已完成对话的信息密度和知识库长表单的错误恢复问题。
- 抽象通用向导或通用面板插件系统：当前只有一个知识库创建流程和两个候选面板内容类型，会增加没有当前 consumer 的维护表面。

## 后果

前端增加局部的过程摘要、面板最大化和知识库向导状态，但不增加后端接口、持久化格式或全局状态。历史对话默认更紧凑，用户仍可展开完整中间过程；知识库创建失败会保留当前弹窗和输入。子智能体多类型 Tab 仍需后续独立决策与实现。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| HTML 流式预览 loading 高度约为正式预览默认高度的一半 | loading 继续占用 360px 或改变正式 iframe 自适应高度 | `web/src/utils/htmlPreviewRenderer.js` | `pnpm run test:unit` | 恢复 loading 高度为正式预览高度后单测失败 | Passed |
| 侧边栏搜索与折叠操作在品牌区形成按钮组，折叠后仍保留搜索入口 | 搜索仍占用展开态主导航行，或折叠入口丢失 | `web/src/layouts/AppLayout.vue` | Playwright 1280px 展开态与折叠态页面快照 | 删除折叠态搜索按钮后真实页面检查失败 | Inspected |
| 文件面板可最大化并还原原宽度，最大化时不触发拖拽 | 普通 75% 上限仍限制最大化，或还原丢失用户宽度 | `web/src/components/AgentChatComponent.vue` | `pnpm run build`；Playwright 文件面板最大化检查 | 最大化时拖拽手柄仍可用或主聊天最小宽度导致溢出 | Passed |
| 已收尾对话默认收起最终助手消息前的中间处理过程，并以耗时分隔行展开 | 运行中、等待用户动作或没有最终回答时错误收起 | `web/src/utils/messageGrouping.js` | `pnpm run test:unit`；Playwright 历史工具对话检查 | streaming、尾部工具调用和无最终助手消息均不得生成过程组 | Passed |
| 知识库按类型、配置、权限三步创建，失败时保留输入且只有成功才关闭 | 空名称进入下一步、切类型清空通用字段、store 返回 false 仍关闭 | `web/src/components/knowledge/DatabaseCreateFlowModal.vue` | `pnpm run test:unit`；Playwright 桌面与 375px 检查 | 必填动态字段为空已验证；创建返回 false、API 抛错后的组件事件未自动化验证 | Inspected |
| 前端 lint、unit、build 与工程契约不回归 | 新组件未接入、样式或 decision 生命周期失效 | `web/` 与工程契约脚本 | `pnpm run lint:check`；`pnpm run test:unit`；`pnpm run build`；工程契约命令 | 删除 decision 证据矩阵或恢复未使用 import 后 gate 失败 | Passed |

## 风险

过程摘要依赖现有前端“对话已收尾”语义，不替代 AgentRun 持久化状态；子智能体调试轨迹首版保持原样。面板最大化只覆盖聊天内容区域，不使用浏览器 Fullscreen API，也不改变单文件已有的全屏预览。知识库向导仍由后端执行最终类型参数、模型和权限校验；前端只提供可由当前元数据确定的即时校验。
