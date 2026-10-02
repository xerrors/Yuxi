# 对话绑定模型由 Conversation 持有

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/repositories/conversation_repository.py

## 问题

每次请求的实际模型只保存在 AgentRun，Conversation 没有模型事实。前端重新进入已有对话时只能回退到智能体配置，无法直接恢复该对话最后绑定的模型。

## 决策

普通请求按“请求显式模型、Conversation 绑定模型、智能体模型、系统默认模型”解析实际模型，并在创建 Request 和 Message 的同一事务内写入 `Conversation.extra_metadata.model_spec`。对话读取接口已经返回 Conversation metadata，前端按“当前未发送选择、Conversation 绑定模型、智能体模型、系统默认模型”的顺序展示，并在每次请求中下发当前展示模型；对话状态读取也使用 Conversation 模型，不从相邻 AgentRun 猜测。请求被接受后，前端同步当前线程 metadata；被拒绝的请求（包括 reject 策略派发竞争失败）不改变 Conversation 模型。

## 替代方案

不从 AgentRun 或历史 Message 反向推导当前模型。两者描述单次执行或消息历史，需要额外查询、排序和加载时序，且会与 Conversation 当前配置形成间接状态。

## 后果

模型事实与对话列表使用同一个 Conversation Owner，不新增 Schema。已有对话在下一次被接受的请求后获得模型 metadata；在此之前按智能体模型和系统默认模型回退。该修复没有数据迁移或待裁决替代方案，因此直接记录为 implemented。

## 验证

- `backend/test/unit/services/test_agent_request_queue_service.py` 验证已接受请求把解析模型写入 Conversation、后续请求继承 Conversation 模型，且立即拒绝或派发竞争失败的 rejected 请求都不覆盖已有绑定。
- `backend/test/unit/services/test_chat_service_sync.py` 验证状态读取使用 Conversation 模型而不是旧 AgentRun 模型。
- `web/test/unit/conversationModelBinding.test.js` 验证组件从 Conversation metadata 读取模型、每次发送当前展示模型并同步线程 metadata。
- 恢复为只保存 AgentRun 或仅使用智能体默认模型时，对应测试失败。
