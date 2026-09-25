# 默认 Chatbot 使用完整段落回答

状态：implemented
类型：feature
Owner：backend/package/yuxi/agents/buildin/chatbot/prompt.py

## 问题

默认 Chatbot 仅要求专业严谨和减少 Emoji，没有约束短碎片、密集项目符号或缺少背景说明的回答。复杂问题因此可能被压缩成缺乏上下文的要点，降低连续阅读和理解体验。

## 决策

默认系统提示继续要求专业严谨，同时减少不必要的 bullet points，优先使用完整句子和段落。对于复杂问题，回答应提供足够的解释和背景信息。该规则只改变默认 Chatbot 的模型可见风格提示，不改变工具、权限、工作区边界、引用协议或用户自定义系统提示。

## 替代方案

- 只调整前端排版：不能改变模型生成的碎片化结构，也不能补足缺失的背景信息。
- 强制禁止项目符号：列表在步骤、枚举和对比场景仍有价值，因此只要求减少不必要的使用。
- 引入可配置的回复详细度：当前没有多个配置消费者，会增加不必要的配置和持久化表面。

## 后果

默认回答更偏向连续、完整的解释；简单问题仍可简短回答，适合列表的内容仍可使用项目符号。模型输出具有非确定性，本决策只拥有系统提示契约，不把单次模型回答作为稳定 oracle。

## 验证

- `backend/test/unit/agents/test_chatbot_prompt.py` 直接约束模型可见提示必须包含减少项目符号、使用完整句段和为复杂问题补充背景的三项要求；删除任一要求都会使测试失败。
- `cd backend && uv run pytest test/unit/agents/test_chatbot_prompt.py test/unit/agents/toolkits/buildin/test_ask_user_question.py test/unit/services/test_chat_stream_interrupt.py -q`：28 passed。
- `cd backend && uv run ruff check package && uv run ruff format package --check && uv run ruff check --select I package`：通过。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，验证器单测 62 项。
