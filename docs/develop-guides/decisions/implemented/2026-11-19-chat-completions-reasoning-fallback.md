# Chat Completions 思考响应兜底

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/models/chat.py

## 问题

OpenAI 兼容接口有时以独立字符串字段交付思考文本。把所有流式响应统一成内容块，会扩大普通正文、工具合并与续答序列化的兼容范围。

## 决策

### 实现方案

模型适配器只在简单文本响应或流式帧上读取非空字符串 `reasoning_content` 或 `reasoning`，优先使用前者，并补充标准思考块。结构化内容由 SDK 解析。普通网关没有这些字段时，正文继续使用 SDK 的结果。Agent 沿用[当前思考段展示](./2026-10-31-live-reasoning-display.md)。

工具续答沿用供应商名单，[DeepSeek](./2026-11-19-deepseek-thinking.md)与[百炼 DashScope](./2026-11-19-bailian-thinking.md)使用既有的原文回传。Responses 请求排除无原生 ID 的思考项目。

## 替代方案

- 全量转换普通响应可以覆盖混合结构，但会让通用兜底承担正文顺序、工具合并和请求格式恢复。
- 仅为名单内供应商解析思考，会要求自定义供应商逐家注册。

## 后果

复杂对象、结构化帧内的额外字段以及跨协议旧历史由各协议已有能力处理。流式兜底通过 Agent v3 展示，普通非流式响应使用相同的字符串规则。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 简单字段生成思考事件及独立历史，普通正文和工具照常续答 | 思考丢失、正文乱序、出站混入供应商字段 | Chat Completions 适配器 | `test_provider_reasoning.py` 的实际 SDK 请求体、LangGraph v3 事件和 checkpoint 回读 | 对象与结构化帧的扩展字段保持 SDK 行为，普通文本保留字符串 | Passed |
| 名单内的官方供应商可完成工具续答 | 推理原文或工具 ID 丢失 | Chat Completions 适配器 | `test_provider_reasoning_live.py` 的 DeepSeek 与百炼工具调用 | 回传请求必须与首轮的非空思考原文一致 | Passed |

旧能力不存在：全量流转换、跨帧状态机及旧 Responses 历史推断已移出适配器。
重新引入条件：出现可复现的主链路需求并有独立协议证据。
