# OpenAI 兼容供应商推理内容适配

状态：archived
类型：simplification
Owner：backend/package/yuxi/models/chat.py

## 问题

硅基流动、OpenCode 和 GLM Coding Plan 的 reasoning_content 在模型解析、v3 事件投影和历史展示之间丢失。GLM 工具续答还需要回传原始推理内容。

## 决策

`models/chat.py` 统一拥有通用聊天入口、模型加载和 Chat Completions 协议适配，Agent 仅消费模型能力。加载与适配不单独拆文件；`yuxi.agents` 为已公开的 Agent 扩展示例保留模型加载函数的转导出，内部调用直接引用 `yuxi.models.chat`。此目录收拢是行为等价的移动，不改变请求、配置或历史契约。

消息解析辅助函数位于 `models/utils.py`，命名为 `parse_assistant_message_body`。标准块使用结构模式匹配单次遍历，分别收集正文与推理，保留各自顺序和空白；显式传入历史 metadata 才启用旧格式恢复。

项目自有 `ChatCompletionsAdapter` 复用 ChatOpenAI 的 HTTP、重试与工具绑定，在解析边界将 reasoning_content / reasoning 直接写入标准内容块，消息使用 output_version=v1。`lc_reasoning` 与 `lc_text` 索引保证多片完整合并且不与整数工具索引冲突；工具续片空串归一化也由该适配器拥有。适配由模型工厂针对硅基流动、OpenCode 与智谱/Z.ai 系列开启；普通 OpenAI、Anthropic、Gemini 不添加第三方推理字段。适配器从标准块编码出站正文和推理，旧 checkpoint 的扩展字段仅在出站边界尽力读取。

`services/chat_service.py` 将 v3 delta 或标准内容块投影为唯一的 content / reasoning_content 展示字段，完成块不重复追加。`models/utils.py` 拥有正文投影和后端历史读取恢复：标准块优先，旧扩展字段或开头 think 标签仅在显式历史读取时尽力提取，缺失或不可用的推理保持为空。`services/conversation_service.py` 在同一消息内调用该投影，不关联其他 Run、不批量修改旧数据。前端只显示这两个字段，保留既有工具事件结构和统一平滑缓冲；AgentMessageComponent 思考中也允许键盘或点击展开。原文回传遵循[智谱保留式思考约束](https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode)，不修改依赖源码、供应商配置或默认思考开关。

## 替代方案

- 仅修前端：无法恢复模型解析时丢失的数据。
- 修改依赖源码或复制整个 HTTP/SSE 客户端：维护面更大。
- 将所有供应商当作 DeepSeek：供应商身份和请求约束并不相同。
- 保留全局 translator、虚拟 model_provider 和前端多格式读取：同一内容由多层重复解释，故选择标准块与单一展示投影。
- 全量迁移旧历史：无法恢复从未保留的数据，故只在读取边界尽力提取，不增加数据库迁移或修复任务。

## 后果

私有解析钩子依赖 LangChain 版本，确定性协议回归覆盖升级风险。只有供应商实际返回或历史已经保留的推理才能展示，缺失是正常结果；不生成、不补写，也不因缺失阻断正文或工具。新适配消息的 content 是标准块列表，纯文本消费者使用 message.text。思考开关继续由供应商请求配置拥有；请求失败、限流、工具错误仍明确失败，不能把这些错误当作推理缺失。探针只报告长度和用量，不记录凭据或推理正文。

## 验证

2026-09-07，在主工作区 Compose 环境、langchain-openai 1.6.0 / langchain-core 1.6.0 / langgraph 1.2.11 上测试。模型边界每个场景为流式工具调用一次、非流式续答一次；不调用沙盒、不限制输出长度、不进行并发压测。显式开启思考只作用于探针请求，不修改保存的配置。

| 供应商 / 模型 | 思考设置 | 首轮 / 续答推理字符 | 工具与续答结果 |
|---|---|---|---|
| 硅基流动 DeepSeek-V4-Flash | 保存配置 enable_thinking=false | 0 / 0 | 通过；关闭思考的预期行为 |
| 硅基流动 DeepSeek-V4-Flash | 探针 enable_thinking=true | 83 / 214 | 通过，原文回传一致 |
| OpenCode Go deepseek-v4-flash | 保存配置 | 0 / 301 | 通过，首轮无推理也正常完成工具续答 |
| GLM Coding Plan glm-5.3 | 保存配置 | 87 / 142 | 通过，原文回传一致 |

表格记录标准块实现的小量实测，共 8 次模型请求。供应商可以在任一轮不返回推理；空推理不作为失败，正文、工具参数与续答仍有断言。实测字符数只证明该样本确实返回了推理，不保证其他请求输出相同内容或一定包含推理。

`test/unit/agents/test_provider_reasoning.py` 的 41 项测试通过，覆盖三家两种字段、同步/异步、流式/非流式、v0/v1、多片合并、空工具续片、工具续答原文、真实 v3 图与 checkpoint、历史恢复和普通 OpenAI 不加字段。负向案例覆盖关闭适配后丢失推理、供应商完全无推理仍完成工具续答、畸形历史扩展字段、流式不猜测旧标签、标准块不重复编码。前端测试明确拒绝解释供应商字段，由后端历史读取拥有尽力恢复。

`test/e2e/test_provider_reasoning_e2e.py` 以 OpenCode 实际经过 API、worker、SSE、PostgreSQL 和历史 HTTP 接口，234 字推理逐字一致，数据库 messages.content 保存正文字符串，messages.extra_metadata["content"] 保留标准块、additional_kwargs 不再重复保存推理，1 项通过。其余两家的证据为真实模型边界探针，不宣称覆盖三家全部 Worker E2E。测试创建的临时 Agent 与 Conversation 已清理。真实浏览器使用当前 semantic 事件驱动实际 Vue 组件，验证思考中键盘展开、增量更新、完成后继续展开、历史一致，以及无推理时正文正常显示；浏览器数据为测试替身，不宣称正式账号页面端到端通过。

复测模型边界：向 API 容器传入 `YUXI_REASONING_PROBE_MODELS='provider:model,...'`，按需设置 `YUXI_REASONING_PROBE_THINKING=1`，运行 `uv run --no-sync --group test pytest test/integration/services/test_provider_reasoning_live.py -s`。完整链路：设置 `YUXI_REASONING_E2E_MODEL='provider:model'`，配置既有 E2E 账号后运行 `test/e2e/test_provider_reasoning_e2e.py`。未显式选模型时探针跳过，避免日常测试产生计费调用。

最终回归：`COMPOSE_PROJECT_NAME=yuxi docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -k 'not test_finish_run_terminal_loser_does_not_append_end_event'` 为 1922 通过、52 跳过、1 排除、7 子测试通过。排除项在既有验证中停滞；容器标准 uv 依赖同步存在权限错误，使用已安装依赖测试，不宣称未排除的标准全量命令通过。Web `pnpm run test:unit` 275 项通过，`lint:check`、Web build、`git diff --check` 通过。浏览器覆盖浅色桌面、深色 375px 与减少动态效果。

旧能力不存在：源码搜索确认不再注册全局 translator、不写入虚拟 model_provider，前端不存在 additional_reasoning_content、additionalReasoningBuffer、additional_kwargs 推理读取及 think 解析。重新引入条件：真实消费者需要且现有标准块无法表达时另行决策；历史缺失不构成引入猜测或补写机制的理由。

工程契约检查及其 61 项单测、docs build 通过。独立 Reviewer 审查完整工作区差异，并复跑协议、前端与工程契约测试，未发现功能性阻塞；提出的持久化字段描述与 changelog 版本归属问题已修正。

模型模块合并后，模型选择、推理、工具续片与摘要图配置的 74 项相关测试通过，以上同口径后端回归仍为 1922 通过、52 跳过、1 排除。干净 Python 进程验证 models.chat 导入不加载 agents，已公开的 agents 模型加载导出与实际函数相同，主 Agent 与子 Agent 图正常导入；源码与文档搜索无已删除模块引用。Ruff、工程契约检查及 61 项单测、docs build 通过。本次目录整理不重复计费供应商探针，前表是协议实现的既有实测。

消息解析函数整理后，补充混合无效块的顺序与空白、持久正文优先、无效块仍恢复旧扩展字段三个用例，同口径后端回归为 1925 通过、52 跳过、1 排除。Ruff 与格式检查、工程契约及 61 项单测、docs build、`git diff --check` 通过；旧函数名和模块引用搜索为空。
