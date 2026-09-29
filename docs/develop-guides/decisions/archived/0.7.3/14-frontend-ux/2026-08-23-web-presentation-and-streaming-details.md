# Web 展示与流式交互细节

状态：archived
类型：feature
Owner：web/src/views/HomeView.vue

## 问题

公开首页、工作区导航、工具结果和知识库检索结果需要统一当前产品表达并降低高频输出时的视觉跳动。流式文本平滑会改变模型输出的可见时序，知识库结果分组会决定用户打开哪个文件，两者需要明确身份和终态边界。

## 决策

首页和工作区继续使用现有 Vue 路由、运行时能力发现与后端接口，只调整内容层级、响应式布局和设计 token。工具结果继续由既有 registry 装配，各工具组件只补充当前结果需要的展示元数据。

流式消息在浏览器动画帧内按待处理字符量平滑释放；历史补发或重新进入对话产生的大块文本立即放行大部分内容，Run 终态仍同步清空剩余缓冲。该行为只改变前端渲染节奏，不改变 SSE 轮询频率、事件顺序或持久化结果。

知识库检索结果按 `kb_id`、`file_id` 和来源名称组成的文件身份分组。同名但身份不同的文件保持独立；用户可分别通过原生按钮查看命中片段或完整文件。

## 替代方案

- 降低共享 SSE 轮询间隔换取更快显示：会同时放大排队请求与 Run 流的 PostgreSQL、Redis 查询负载，不采用；Run 流后续采用与 Request 解耦的[自适应轮询](../12-concurrency/2026-09-04-agent-concurrency-capacity.md#redis-sse-与取消)。
- 所有流式字符立即渲染：实现简单，但突发 chunk 会造成明显跳动，历史补发与实时输出也无法区分。
- 只按显示文件名聚合知识库结果：无法区分不同知识库或目录中的同名文件，会把完整文件入口绑定到错误身份。
- 为本轮视觉更新引入新的组件库或动画依赖：现有 Vue、Less、Ant Design Vue 与浏览器动画帧已满足需求，不增加维护表面。

## 后果

前端新增局部流式缓冲状态和知识库片段弹窗，但不增加后端 API、持久化字段或 SSE 配置。大块历史文本优先恢复可读终态，小块实时文本保持平滑；同名知识库文件不会串组。视觉更新继续服从浅色、深色和窄屏 token。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 流式小增量按帧释放，历史大文本快速放行，flush 后内容完整 | 历史消息长时间重放或终态缺字 | `web/src/composables/useStreamSmoother.js` | `pnpm run test:unit` | 删除 fast-forward 或 flush 后对应单测失败 | Passed |
| 同名知识库文件按真实身份独立分组 | 不同 `kb_id/file_id` 的结果被合并并打开错误文件 | `web/src/utils/kbResultGroups.js` | `pnpm run test:unit` | 两个知识库中的 `guide.md` 必须形成两个分组 | Passed |
| 查看片段、完整文件和关闭弹窗均可由语义按钮触发 | 键盘或辅助技术无法识别主要动作 | `web/src/components/sources/KbResultGroupedList.vue` | `pnpm run lint:check`；真实页面检查 | 删除按钮可访问名称后 Review 失败 | Inspected |
| 视觉与交互更新不破坏前端构建 | 未使用导入、样式错误或组件装配失败 | `web/` | `pnpm run lint:check`；`pnpm run test:unit`；`pnpm run build` | 任一 gate 失败即拒绝提交 | Passed |
