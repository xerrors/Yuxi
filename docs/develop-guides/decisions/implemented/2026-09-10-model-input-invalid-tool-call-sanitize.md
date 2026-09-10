# 模型输入净化：invalid_tool_call 的截断 function 在发送边界降级为失败反馈

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/models/chat.py

## 问题

模型生成无效工具调用（JSON 参数截断/格式错）时，LangChain 把它记在 `AIMessage.invalid_tool_calls`。`langchain_openai` 序列化时把它转成 `type:"function"`、`arguments` 为截断 JSON 的 `tool_call`，DeepSeek 等接口因参数不合法报错并中断整轮。

## 根因澄清（对上一版假设的修正）

初版把现象归为「`invalid_tool_calls` 序列化成 `invalid_tool_call` 变体」，并为此清空解析字段、收敛 `additional_kwargs`、删孤儿 `ToolMessage`、动态包装四个入口。实测 `langchain_openai` 1.6.0 的真实序列化推翻了该假设：

- `AIMessage.invalid_tool_calls` 经 `_lc_invalid_tool_call_to_openai_tool_call` 转成 `type:"function"`，**不是** `invalid_tool_call` 变体；真正的问题是 `arguments` 截断导致参数解析失败。
- content 数组里的 `{"type":"invalid_tool_call"}` block 在 `_convert_from_v1_to_chat_completions` 里已被丢弃，不会泄漏到 wire。
- 孤儿 `ToolMessage` 的根因在历史裁剪/恢复/消息组装，不是发送边界能安全修复的。

## 决策

收窄为**只在 Chat Completions 发送边界**处理「截断 function」：`ChatCompletionsAdapter._get_request_payload` 里用原始消息（`invalid_tool_calls` 的 id）对账 wire 载荷，按 id 移除这些截断 `tool_calls`，并在 content 里追加 text 反馈「`[工具调用失败] name: error`」——给模型明确的失败反馈，而不是发出畸形参数。

- 只影响 `ChatCompletionsAdapter`（OpenAI 兼容协议）；Anthropic/Gemini 有自己的消息格式，不处理。
- 不改消息对象（原始 checkpoint 不动），净化只落在序列化后的 wire 载荷。
- 同步/异步/流式/非流式四条路径都经 `_get_request_payload`，一处覆盖。
- 不删孤儿 `ToolMessage`：id 存在不代表调用与响应顺序合法，按根因另行处理。

## 替代方案

- 动态 `_InvalidToolCallFilterMixin` 包装四个入口：覆盖所有供应商、范围过大，且掩盖消息链路本身的问题；拒绝。
- 在 `_convert_message_to_dict` 打补丁：第三方内部实现，升级即失效；拒绝。
- 全历史 id 集合清洗工具响应：会把「响应在前、调用在后」等非法序列保留，或误删实际执行结果；拒绝。

## 后果

发送给 DeepSeek 等接口的载荷不再含参数截断的 `function` 调用，改为明确的文本失败反馈。`invalid_tool_calls` 属性仍在 checkpoint 里原样保留（可观测、可追溯），仅在 wire 边界降级。非 OpenAI 兼容供应商不受影响。

## 验证

`backend/test/unit/models/test_chat_invalid_tool_call_sanitize.py`（真实 adapter + mock HTTP，断言最终请求体）：

- 纯无效调用：`tool_calls` 移除，content 追加失败反馈；
- 有效/无效混合：wire 只保留 `call-good`，追加失败反馈；
- 无无效调用：wire 原样不变；
- 四条路径（`invoke`/`ainvoke`/`stream`/`astream`）都净化。
