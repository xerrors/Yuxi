# DeepSeek 官方聊天接口的思考块

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/models/chat.py

## 问题

内置 DeepSeek 供应商通过官方 Chat Completions 接口返回独立的思考字段。模型工厂缺少该供应商的推理适配入口，导致思考在进入运行事件前丢失。

## 决策

### 实现方案

内置 `deepseek` 使用既有的 Chat Completions 推理适配器。适配器把响应的 `reasoning_content` 转为标准 reasoning 块，为发往 DeepSeek 的工具续答回填消息中已保存的推理原文。事件投影与会话页面复用既有思考块，当前思考段结束后由页面移除。供应商与请求配置决定是否产生思考。[官方思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)

响应解析由[通用响应兜底](./2026-11-19-chat-completions-reasoning-fallback.md)提供，供应商注册决定出站回传。

## 替代方案

- 在前端识别供应商字段无法接触模型适配层已丢弃的原文，也无法补齐工具续答的请求载荷。
- 新建 DeepSeek 专用思考协议会重复现有事件、持久化和展示逻辑。

## 后果

实际收到的思考原文可随事件显示，并与最终答复分别保存。旧消息只能使用当时保留下来的原文。前端沿用[仅展示当前思考段](./2026-10-31-live-reasoning-display.md)的交互。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| DeepSeek 思考原文形成标准块和事件，工具续答回传原文 | 字段丢失、混入正文或工具续答缺字段 | 模型工厂及 Chat Completions 适配 | `test/unit/agents/test_provider_reasoning.py` 使用真实 SDK 解析协议夹具和 LangGraph v3 | 回传测试检查请求原文，通用响应测试检查入站兜底 | Passed |
| 官方模型的当前思考块在浏览器可见 | 前端收到增量却未显示，或终态仍显示旧思考 | 运行事件与会话视图 | Chromium 在 5173 提交 `deepseek:deepseek-flash` 请求，核对思考 DOM、正常结束、历史独立保存及最终回答 | `liveReasoning.js` 回放过期增量、完成后隐藏和历史重开 | Passed |

显式启用的 `test/integration/services/test_provider_reasoning_live.py` 对官方 `deepseek:deepseek-flash` 运行带工具的首轮和续答。实测首轮直接调用工具，续答返回非空思考及正文。确定性 SDK 测试同时覆盖首轮存在推理且续答逐字回传的场景。
