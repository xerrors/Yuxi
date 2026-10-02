# Mention chip 删除边界

状态：archived
类型：bug-fix
Owner：web/src/components/MessageInputComponent.vue

## 问题

未加引号的 mention token 与后续正文只由一个空格分隔。用户删除该空格后，序列化文本无法区分 token value 与正文，第二次 Backspace 会把整段内容解析为一个 mention 并全部删除。

## 决策

编辑器中已渲染的 mention chip 由 DOM 的 `data-mention-raw` 和节点位置拥有删除边界。Backspace 位于 chip 紧邻位置时直接删除该 chip；其他删除场景继续使用文本解析逻辑。

## 替代方案

不改变 token 格式为始终加引号，也不依赖猜测未加引号 token 的结束位置，避免影响已有消息文本和展示格式。

## 后果

编辑态的 DOM 与序列化文本在 mention 边界上的语义保持一致。非编辑态文本渲染和现有带引号 token 解析不变。

## 验证

- 回归测试覆盖无空格相邻文本的 mention 解析与删除范围。
- `web` 单元测试和 lint/build 结果记录在本次交付报告中。
