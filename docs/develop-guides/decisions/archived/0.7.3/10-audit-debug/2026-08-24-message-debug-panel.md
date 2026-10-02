# Agent 消息调试面板

状态：archived
类型：feature
Owner：web/src/components/AgentChatComponent.vue

## 问题

聊天气泡内直接展示完整消息对象会打断阅读，也会把工具调用、工具结果和 AI 内容拆成容易误判顺序的视觉块。调试入口需要保留后端返回的消息顺序和原始字段，同时不能成为普通用户界面的常驻内容。

## 决策

超级管理员通过全局调试面板开启对话 Debug 模式后，Agent 页头显示调试入口，并在既有 AgentPanel 中打开独立消息调试 section。`AgentChatComponent` 把 `/history` 返回的原始消息数组与当前运行的未持久化消息传给该 section；持久化消息保持接口顺序，当前 active run 的流式 AI 投影替换同 run 的已持久化中间投影，避免重复。

每条原始消息对应一行，并按消息已有的 `run_id` 建立连续 Run 视觉分组；没有 `run_id` 的流式乐观消息或历史数据明确显示为“未关联 Run”，不会根据相邻消息猜测归属。Run 分组通过后端授权与惰性 URL 解析提供 [Langfuse 精确跳转](./2026-08-24-agent-run-langfuse-jump.md)。折叠态只显示角色图标、角色和摘要。AI 摘要同时显示文本与 `tool_calls` 中的工具名称；独立 tool、system、resume 和未知类型消息不因聊天展示投影而丢失。展开态使用可折叠 JSON 树查看同一条消息的原始对象。

关闭对话 Debug 模式或当前用户不再是超级管理员时，入口隐藏并移除已打开的消息调试 section。调试复制统一通过剪贴板工具执行，在非安全 HTTP 或 Clipboard API 被拒绝时降级为浏览器传统复制路径。

## 替代方案

- 继续在聊天气泡内展示消息对象：实现简单，但严重干扰主阅读路径，也无法形成集中筛选与检索。
- 复用聊天展示用 `conversations`：能直接渲染，但该投影会过滤独立 tool、system 和 resume 消息，不适合作为调试事实源。
- 新增独立历史接口：可以明确区分调试数据，但当前 `/history` 已返回所需原始消息，新增接口会扩大后端与权限维护面。

## 后果

消息调试视图与聊天展示视图拥有不同的前端投影，但共享同一个后端历史事实源。AgentPanel 新增一个只在显式开启后存在的 section；JSON 树和剪贴板降级成为可复用的轻量前端工具。Debug 模式仍是超级管理员体验入口，不构成后端授权边界。

## 验证

- `web/test/unit/messageDebug.test.js` 证明调试转换保持输入顺序、保留独立 tool/system 消息、按连续 `run_id` 分组且不猜测无 ID 消息的归属，并解析去重工具名称与安全的 Langfuse HTTP(S) URL；删除 tool 映射、移除 Run 分组、放宽外链协议或重新按聊天轮次过滤会使测试失败。
- `web/test/unit/jsonTree.test.js` 证明字符串值与键名按 JSON 语法转义；恢复字符串拼接会使测试失败。
- `web/test/unit/clipboard.test.js` 证明非安全上下文和 Clipboard API 拒绝都会进入传统复制路径；删除降级会使测试失败。
- 前端只读 lint、unit、build 和真实页面验证共同检查装配、交互与视觉结果；未执行的页面状态必须在交付说明中明确记录。
