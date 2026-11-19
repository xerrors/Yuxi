# 百炼聊天接口的思考块

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/models/chat.py

## 问题

百炼的 OpenAI 兼容接口通过独立字段返回思考。内置 DashScope 供应商缺少推理适配入口，思考在生成事件进入会话前丢失。

## 决策

### 实现方案

中国内地与国际站 DashScope 供应商复用 Chat Completions 推理适配。SDK 响应中的 `reasoning_content` 转为标准 reasoning 块，后续发往百炼的请求独立回传已保存的推理原文。思考开关继续由供应商默认值和现有请求参数决定。会话复用[当前思考段展示](./2026-10-31-live-reasoning-display.md)和既有事件格式。[百炼 Chat Completions 协议](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)

响应解析由[通用响应兜底](./2026-11-19-chat-completions-reasoning-fallback.md)提供，供应商注册决定出站回传。

## 替代方案

- 前端识别供应商字段无法接触解析边界丢失的数据，也无法为工具续答提供原文。
- 为千问创建专用适配器会重复思考块、事件与工具消息逻辑。

## 后果

模型产生的思考可独立于正文显示和保存。百炼 `qwen3.8-flash` 默认保留思考上下文，后续输入用量由供应商计算。已有消息只能回传曾保存的思考。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 百炼思考形成标准块，工具续答原样回传 | 思考丢失、混入正文、续答漏传 | 模型工厂及适配器 | `test_provider_reasoning.py` 的 SDK 和 LangGraph v3 测试，以及官方 `qwen3.8-flash` 工具探针 | 回传测试检查请求原文，通用响应测试检查入站兜底。关闭思考时仍返回正文 | Passed |
| 官方模型在会话中显示当前思考 | 思考事件无可见 DOM，或思考结束后旧块仍显示 | 运行事件与会话视图 | Chromium 在 5173 用 `alibaba-cn:qwen3.8-flash` 发起真实对话，回读思考 DOM、最终正文和独立历史字段 | `liveReasoning.js` 覆盖过期增量、结束状态和重开历史 | Passed |

官方工具探针以 `YUXI_REASONING_PROBE_MODELS=alibaba-cn:qwen3.8-flash` 和 `YUXI_REASONING_PROBE_THINKING=1` 运行 `test/integration/services/test_provider_reasoning_live.py`。首轮与续答均携带工具定义，续答的独立思考字段与首轮收到的原文逐字相同。独立关闭 `enable_thinking` 时，官方模型返回正文且无思考块。国际站使用 SDK 协议夹具验证，真实网络探针使用中国内地端点。
