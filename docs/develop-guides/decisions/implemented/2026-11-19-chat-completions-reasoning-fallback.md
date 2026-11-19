# Chat Completions 思考响应兜底

状态：implemented
类型：feature
Owner：backend/yuxi/modules/models/chat.py

## 问题

新增 OpenAI 兼容供应商后，响应中已经返回的思考文本可能在标准消息构造时丢失。返回思考文本与接受工具续答回传是独立的供应商协议能力。

## 决策

### 实现方案

Chat Completions 模型适配器优先使用 SDK 解析的非空标准思考，其次依次读取非空字符串 `reasoning_content` 和 `reasoning`。流式请求在首次收到非空思考时确定来源，同帧有两种来源时选标准块。随后的帧沿用该来源，思考可以立即发出。内容块和工具片段使用独立的合并索引，结构化帧同时携带的工具调用依旧进入运行事件。续答从正文去除内部工具片段，通过独立的 `tool_calls` 发送工具。回传名单外的供应商沿用直接调用的标量正文和 v3 历史的文本块数组。

Agent 运行沿用[当前思考段展示](./2026-10-31-live-reasoning-display.md)。出站回传沿用既有供应商名单，[DeepSeek](./2026-11-19-deepseek-thinking.md)和[百炼 DashScope](./2026-11-19-bailian-thinking.md)的协议规则适用。展示采用标准块且供应商同时提供独立思考字段时，保留该字段原文用于名单内的续答。Anthropic、Gemini 和 OpenAI Responses 由原生 SDK 处理。切换到 Responses API 时，SDK 的原生元数据及旧版配对的 message/response ID 标识原生历史。其它历史保留正文与工具关联，出站移除思考、供应商正文 ID 和可能被 SDK 误用为续答锚点的响应 ID。原始消息历史保留。

## 替代方案

- 继续逐家授权识别，对新增供应商的常见响应仍需改代码。
- 同时对所有供应商开启回传，会改变未经确认的网关请求契约。
- 缓冲扩展字段直到整条流结束，能比较后续标准块，但会推迟思考展示。

## 后果

自定义供应商使用常见文本字段时可直接显示思考。思考开关由供应商和请求参数决定。Chat Completions 的流式消息统一使用标准块，既有正文和工具消费者继续读取标准消息。独立协议字段、嵌在正文里的标记由供应商专用协议决定。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新供应商的常见字段产生独立思考块与运行事件 | 思考丢失、重复或混入正文 | Chat Completions 模型适配器 | `test/unit/agents/test_provider_reasoning.py` 的 SDK 与 LangGraph v3 测试，5173 浏览器连接本地协议测试端点 | 恢复按供应商限制响应解析时，五项新供应商用例出现思考丢失 | Passed |
| 工具续答保留既有回传范围、原文及正文格式 | 结构化思考覆盖工具，续答发错思考原文或发送内部工具片段 | Chat Completions 模型适配器 | 同帧和分帧的 SDK 及 v3 回放核对标准思考、工具 ID 与原文请求。截断工具片段回放核对实际重试请求。DeepSeek 与百炼中国内地使用官方接口探针 | 复原共用索引、用展示文本代替原始字段或保留内部工具片段时，协议断言失败 | Passed |
| 切换到 Responses 时保留正文和原生思考项 | SDK 从标准块、封装块或旧版消息扩展重建外部思考项 | ChatCompletionsAdapter 出站协议 | `test_switch_to_responses_preserves_native_reasoning_items` 经真实 SDK 解析 Responses 和 Chat Completions，并将历史序列化后构造请求体 | 恢复仅检查顶层思考块的规则时，协议断言发现外部思考项或 SDK 无法构造请求 | Passed |

最小复测命令为 `docker compose exec -T api uv run --no-sync --group test pytest -q test/unit/agents/test_provider_reasoning.py test/unit/models`。浏览器测试在临时供应商、用户和会话上发送两轮消息，分别核对两个扩展字段、思考与正文 DOM、运行终态、历史独立字段以及第二轮的实际请求。非白名单供应商通过本地 HTTP 测试端点验证，未逐家访问生产端点。
