# Chat Completions 思考展示与工具续答

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/models/chat.py

## 问题

DeepSeek 官方接口、百炼和其他 OpenAI 兼容接口可在独立字段返回思考。Agent 需要显示这些文本。DeepSeek 和百炼的工具续答还需要使用消息中保存的推理原文。

## 决策

### 实现方案

模型适配器从简单文本响应或流式帧读取非空字符串 `reasoning_content` 或 `reasoning`，优先使用前者，并补充标准思考块。结构化内容由 SDK 解析。普通网关没有这些字段时，正文使用 SDK 的结果。Agent 经 v3 事件展示思考，思考和正文分别保存。界面沿用[当前思考段展示](./2026-10-31-live-reasoning-display.md)。

模型工厂把内置 `deepseek`、`alibaba-cn` 和 `alibaba` 纳入既有回传名单。发给名单内供应商的 Chat Completions 请求通过 `reasoning_content` 回传已保存的历史原文，供工具续答使用。Responses 请求排除没有原生 ID 的思考项。思考开关由供应商默认值和现有请求参数决定。参见 [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。

## 替代方案

- 全量转换普通响应可以覆盖混合结构，但会让通用兜底承担正文顺序、工具合并和请求格式恢复。
- 仅为名单内供应商解析思考，会要求自定义供应商逐家注册。
- 为 DeepSeek 和百炼各建适配器会重复事件与续答逻辑。在前端解析也无法恢复模型边界已丢弃的原文。

## 后果

结构化帧内的额外字段和跨协议旧历史由各协议已有能力处理。历史消息只能回传当时保存的原文。直接合并 SDK 流中的字符串与内容块时可能出现正文重复，应用的直接流式入口逐帧读取正文，Agent v3 回放输出完整正文。

百炼 `qwen3.8-flash` 默认保留历史思考，回传的 `reasoning_content` 计入后续输入用量。参见[百炼 Chat Completions 协议](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 简单字段生成思考事件及独立历史，普通正文和工具照常续答 | 思考丢失、正文乱序、出站混入供应商字段 | Chat Completions 适配器 | `test_provider_reasoning.py` 的实际 SDK 请求体、LangGraph v3 事件和 checkpoint 回读 | 对象与结构化帧的扩展字段保持 SDK 行为，普通流保留字符串，Responses 排除无原生 ID 的思考项 | Passed |
| DeepSeek 与百炼可完成工具续答 | 推理原文或工具 ID 丢失 | 模型工厂和 Chat Completions 适配器 | `test_provider_reasoning_live.py` 对 `deepseek:deepseek-flash` 和 `alibaba-cn:qwen3.8-flash` 的官方探针，百炼国际站使用 SDK 夹具 | 两家官方探针均校验回传原文与首轮一致。无思考时仍可续答由 SDK 夹具覆盖 | Passed |
| 会话显示当前思考，最终正文和思考历史分别保存 | 思考增量不可见或结束后仍显示旧思考 | Agent 事件投影和会话视图 | 浏览器在 5173 验证 DeepSeek 与百炼真实对话，`liveReasoning.js` 验证展示边界 | 过期增量、完成后隐藏和重开历史 | 供应商适配通过，收窄版本的完整浏览器链路 Not run |

百炼显式设置 `enable_thinking=false` 的真实探针返回了正文且无思考块。收窄版本的关闭思考探针为 Not run。

旧能力不存在：全量流转换、跨帧状态机及旧 Responses 历史推断已移出适配器。
重新引入条件：出现可复现的主链路需求并有独立协议证据。
