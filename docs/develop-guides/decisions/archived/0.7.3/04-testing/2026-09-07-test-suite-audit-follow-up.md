# 测试审计与发布门禁 oracle 收敛

状态：archived
类型：simplification
Owner：backend/test/unit/agents/test_chatbot_prompt.py

## 问题

测试套件精简后的独立审查发现几处证据边界仍不完整：Prompt 测试没有验证实际组装结果，默认分块上限测试不足以排除错误默认值，benchmark reorder 测试依赖调度时序，决策记录还引用了已删除的测试文件。

## 决策

在运行时组装后的 chatbot prompt 上保留 `html:preview` 不被重复注入的负向断言。benchmark worker 用事件同步异常与后续调用，确保 reorder buffer 的测试不依赖固定睡眠。默认 512 token 配置使用精确 token 计数作为 oracle，并修正决策记录中的合并文件路径。

Web 测试按实际观察边界取舍：保留请求、状态、协议、解析和渲染结果测试；删除仅冻结 CSS 数值、图标、旧文案和搬迁路径的检查。混合测试只删装饰性断言，保留可访问性、路由、数据隔离与迟到响应检查。源码检查尚无等价行为覆盖时保留，并明确它只证明源码结构；不以测试名称相似或文件较长作为删除理由。

## 替代方案

- 只检查源字符串：拒绝，因为它不能证明实际运行时 prompt 没有重复注入。
- 保留固定延迟和调用计数：拒绝，因为事件循环调度变化会制造假失败或假绿。
- 只断言硬上限：拒绝，因为 1.5 倍硬上限允许错误的默认分块值通过。

## 后果

测试继续覆盖真实组装结果、worker 异常收敛和默认配置语义；精确分块 oracle 依赖当前 tokenizer 的确定性结果，tokenizer 或分块策略变更时必须同步审阅语义 Owner 和决策记录。

Web 的样式数值与一次性迁移不再由源码正则冻结；这些删除不代表视觉回归已有自动覆盖。共享详情框架的插槽、动态 Tab 和 forceRender 透传仍由真实模板测试验证；HTML 预览尺寸仍在渲染输出上检查，工具参数解析保留实际输入结果测试。队列、流式、API、文件隔离和设置交互测试继续独立维护；暂留的源码结构检查不能冒充浏览器行为证据。

Backend 测试保留业务、权限、事务、队列、文件隔离、恢复和部署边界的独立观察面；`live_api_cleanup`、性能工具、Compose 契约和惰性导入检查均有明确 consumer，不因文件名含 cleanup/tmp/source 或单文件用例少而删除。两个 QA 解析测试合并为 `backend/test/unit/test_qa_parser.py`，共享一次隔离加载并保留结构化提取、围栏边界、孤儿答案、超长切分和默认配置的全部 oracle；没有合并观察边界不同的 unit、integration 和 E2E 测试。

第二轮仅删除 3 个同文件内无消费者的死辅助对象，并将真实附件服务测试从 `test_tmp_attachment_service.py` 更名为 `services/test_attachment_service.py`，消除“临时脚本”的错误信号；不删除仍被外部清理入口调用的兼容函数。队列策略正向测试改为断言规范化返回值，而不是只验证不抛异常；两个 unit 测试移除无效的 cwd `sys.path` 注入，示例问题测试共享同一个可配置的知识库 fake。

## 验证

- Prompt、分块和 benchmark generation 相关单元测试：36 passed。
- `ruff check` 与改动文件 `ruff format --check`：通过。
- 将已删除的 `test_semantic_chunking_empty_heading.py` 引用改为合并后的 `test_semantic_chunking.py`。
- Web 守卫测试同时覆盖正常放行与拒绝；配置合并读取检查具体返回值和 Store 状态；模型优先级源码检查先确认表达式存在；轮询生命周期检查请求完成后再次调度及停止后的清理。测试入口仍使用 `web/package.json` 的全目录 selector，未跳过或排除测试。
- 共享详情框架的运行时测试检查具名面板内容实际渲染、删除 Tab 后对应内容消失；在内存中移除动态内容插槽的负控会因面板内容缺失而失败。
- Backend QA parser 合并后仍保留原有全部用例；后端测试目录的静态审计确认 6 个数据 fixture 均被解析或真实知识库路由使用，其中 `测试图片.png` 作为 `测试文档.docx` 的嵌入资源由转换结果间接校验。未删除仍被真实知识库路由或解析测试使用的 fixture。合并测试不改变 import 隔离、默认上限精确 token oracle 或解析围栏边界。
- 第二轮静态引用检查确认删除的 `FakeKnowledgeBase`、`_ChildContext` 和 `_make_thread_files` 在仓库内无消费者；附件服务更名后仍收集相同的 16 个用例，队列策略测试覆盖 `enqueue/reject/steer` 三个有效值的具体返回结果。
- unit 测试仍可在不修改 `sys.path` 的情况下收集；示例问题测试的 3 个重复 `FakeKnowledgeBase` 定义收敛为 1 个按详情注入的 fake，异常和成功分支的返回断言保持不变。

旧能力不存在：测试不得退回只检查源常量、固定睡眠调度或模糊长度上限的证据。

重新引入条件：只有新的测试仍能独立证明同一运行时事实、对调度有显式同步并能区分配置回归时，才可替换当前 oracle。
