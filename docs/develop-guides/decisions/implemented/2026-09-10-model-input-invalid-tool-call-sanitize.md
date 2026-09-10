# 模型输入净化：invalid_tool_call 必须落在 wire payload 上

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/models/chat.py

## 问题

DeepSeek 等接口对两类消息报错并中断整轮：

1. `unknown variant invalid_tool_call`：模型生成的工具调用参数被截断/格式错误时，LangChain 记录 `invalid_tool_calls`，序列化后以该变体发往模型。
2. `role='tool' must be a response to a preceding message with 'tool_calls'`：孤儿 `ToolMessage`（`tool_call_id` 已无对应 `tool_calls`）。

只清空 `invalid_tool_calls` 解析字段并不能解决第 1 类：`langchain_openai` 的 `_convert_message_to_dict` 在 `tool_calls` 与 `invalid_tool_calls` **都为空**时会回退使用 `additional_kwargs["tool_calls"]`（OpenAI 响应的原始 wire 表示），把同一条截断参数的调用原样重发；而配对 `ToolMessage` 已被过滤删除，形成「有 tool_calls、无工具响应」的非法请求。

## 决策

净化必须作用在最终发送载荷上，并保持解析表示与原始表示一致：

- 清空 `invalid_tool_calls`；
- 把 `additional_kwargs["tool_calls"]` 收敛为「id 在合法 `tool_calls` 内」的子集（无合法调用时即为空列表，阻断 `_convert_message_to_dict` 的回退路径）；
- content 数组里的 `invalid_tool_call` block 转文本；
- 删除 `tool_call_id` 不在任何合法 `tool_calls` 内的孤儿 `ToolMessage`。

净化返回**新的消息对象**，不原地修改入参——这些对象来自共享的 graph state 与 checkpoint。`_InvalidToolCallFilterMixin` 在 `_generate/_agenerate/_stream/_astream` 四个入口发送前统一调用。

## 替代方案

- 只清空 `invalid_tool_calls`：`additional_kwargs` 回退会让畸形调用重发；已被 wire payload 回归测试证伪，拒绝。
- 只处理 `additional_kwargs`、不处理 `invalid_tool_calls`：模型侧仍收到 `invalid_tool_call` 变体；拒绝。
- 在 `_convert_message_to_dict` 处打补丁：属于第三方内部实现，升级即失效；拒绝。

## 后果

模型输入侧的畸形调用与孤儿工具响应在发送边界被统一消解，不再随供应商序列化实现变化而回退。过滤会丢失原始调用细节，因此每次过滤都写 warning 日志，保留可观测性。合法调用与其工具响应不受影响。

## 验证

`backend/test/unit/models/test_chat_invalid_tool_call_sanitize.py` 断言**最终 wire payload**（经 `_convert_message_to_dict`）：

- 纯无效调用：wire 上不出现该调用，`invalid_tool_calls` 为空；
- 有效/无效混合：wire 只保留 `call-good` 及其原始参数；
- 不得原地修改入参（原消息的 `invalid_tool_calls` 与 `additional_kwargs` 不变）；
- 孤儿 `ToolMessage` 随无效调用一起被删除；
- content 里的 `invalid_tool_call` block 转文本，wire 上无该变体；
- 流式入口（`_stream` / `_astream`）同样在发送前净化。
