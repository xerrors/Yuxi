# 知识库回答按编号确定性引用

状态：implemented
类型：feature
Owner：backend/package/yuxi/knowledge/citations.py

## 问题

`chatbot/prompt.py` 原有 `SOURCE_CITE_PROMPT` 要求模型输出 `<cite source="$SOURCE" type="file">$INDEX</cite>`，其中来源名称由模型自己写。该提示被作者标注"效果不好，暂时不启用"，且全仓无任何引用者——它是死代码。

根因不是措辞：模型要凭上下文记忆拼出文件名，既不稳定，也没有任何一端能校验这四个字是否真实存在。因此"引用"要么不可用，要么会把编造的来源显示成真实引用。前端 `MarkdownPreview.vue` 已经为 `cite` 准备了样式，`markdown_preview.js` 的 DOMPurify 配置也显式放行了 `source` 属性，说明这条管线最初就是为此设计的，但缺少凭证来源。

## 决策

把"模型自己写来源"替换为"模型引用编号、编号由检索侧产生并由消费侧解析"：

- `query_kb` 在返回检索结果时，用 `yuxi.knowledge.citations` 给每条结果编号（写入结果项的 `cite` 字段），并在负载上附加 `citation_hint`，列出本次可用的编号与来源。
- 系统提示改为要求 `<cite type="file">编号</cite>`，明确编号只能取检索结果给出的编号、编号在一次会话内唯一，禁止自造来源名；网页搜索仍走 `<cite source="$URL" type="url">` 旧约定，不受影响。
- 前端 `web/src/utils/kbCitations.js` 从同一条助手消息的工具调用结果重建注册表；渲染后 `MarkdownPreview.vue` 用 `resolveKbCitation` 逐个校验 `cite`，解析到条目的补真实 `kb_id/file_id/chunk_id` 并变成可点击入口，解析不到的**降级成 `[n]` 纯文本**。

### 编号在一次会话内连续递增

初版让每次检索各自从 1 开始编号，消费侧"从最后一次检索向前找"。这在连续多轮检索时会把编号 1 解析到后一轮检索的片段上——引用指向错误来源，属于正确性缺陷。

改为 run 内全局连续：`query_kb` 通过 `ToolRuntime.state` 读取既有消息，用 `count_prior_citations` 算出本轮之前的编号基数，`build_citation_entries(results, offset=...)` 接着往下编。前端不再自行推算编号，直接以结果里的 `cite` 为键建注册表，前后端规则不可能错位。

基数取两个口径的较大值：历史检索的条目总数，以及残留消息里已写入的最大 `cite`。后者用于上下文压缩（`context_compression_service` 走 summarization middleware，会移除早期工具消息）截断历史时兜底，避免新检索复用已发过的编号。历史完全无法解析时返回 0，退化成从 1 编号，与改造前一致。

### 无法确定时宁可不给出跳转

同一编号若指向不同片段（并行发起的多次检索会读到相同的 state 基数，或者历史被压缩截断），条目标记为 `ambiguous`，`resolveKbCitation` 返回 null，引用渲染成纯文本而不是可点击链接。错误跳转比没有跳转危害更大。

## 替代方案

- 让后端在落库时改写答案文本：需要侵入 SSE 流与 `run_worker` 的分片写出路径，改动横跨流式契约，而在没有 Milvus/MinerU 完整栈的环境无法端到端证明；且流式期间的渲染与最终文本会出现不一致。
- 为引用注册表建表或 per-run 计数状态：需要新的持久化、迁移和运行 Owner。改用 `ToolRuntime.state` 推导基数可以达到同样效果，且无状态。
- 让前端自行推算编号基数：一旦与后端推导规则出现分歧，编号会整体错位，引用将大规模指向错误来源。现在前端只读后端写入的 `cite`，不存在两套规则。
- 保留 `source` 属性作为解析主键：来源名本身不可信，等于把不确定性原样保留。

## 后果

- 模型不再需要复述文件名，引用格式从"自由文本"降为"枚举取值"。
- 编号在会话内唯一，跨轮检索不会互相覆盖。
- `query_kb` 返回负载新增 `citation_hint` 字段、结果项新增 `cite` 字段，会进入模型上下文与持久化的工具消息。
- 解析失败的 `cite` 降级成 `[n]` 纯文本，而不是移除节点——既不让编造的引用显示成真实引用，也不吞掉正文。
- 检索结果 UI 显示编号：`groupKnowledgeChunks` 为每个文件分组收集 `cites`，`KbResultGroupedList` 与 `KbFileChunksModal` 渲染 `[n]` 徽标，用户能把回答里的编号对回具体片段。
- 引用跳转复用既有 `FileDetailModal`，新增可选的 `focusChunkId`：命中时切到 Chunks 视图并高亮定位；id 对不上或没有分块视图时静默退回默认视图，不影响其它入口。
- 已知局限：并行发起的多个 `query_kb` 会读到相同的 state 基数，可能产生重复编号——此时条目被判为 `ambiguous` 而不给出跳转，不会跳错，但这类引用会失去可点击性。彻底解决需要在图层面串行化或引入共享计数器。

## 验证

- `backend/test/unit/knowledge/test_citations.py`（13 项）：编号连续性、脏数据不留编号空洞、来源缺失兜底、`file_id` 取值优先级、offset 递增、历史条目计数、序列化工具消息、无效消息忽略、压缩截断时按已写编号兜底、非法 `cite` 值忽略、提示正文约束、空结果返回空串。
- `web/test/unit/kb_citations.test.js`（12 项）：编号取自 `cite` 而非前端推算、多轮检索不互相覆盖、无 `cite` 时回退顺序编号、编号冲突判为 `ambiguous`、重复检索同一片段不算冲突、对象态工具结果、非知识库工具不入注册表、空结果与坏内容不产生条目、未知编号返回 null、缺少 kb/file 身份的条目、编号文本白名单。
- `web/test/unit/kb_result_groups.test.js`（4 项，含既有用例）：分组收集并升序排列 `cites`、无编号不影响聚合、`getChunkCite` 只接受正整数。
- `ruff check` 通过改动后的后端文件；`eslint` 通过改动后的前端文件；`vite build` 成功。
- 未验证范围：真实 Milvus 检索与浏览器渲染未执行，chunk 定位依赖检索结果 id 与文件分块 id 一致，本次验证不包含 E2E。
