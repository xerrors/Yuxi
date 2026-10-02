# QA 前缀解析边界与超长限长切分

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/knowledge/chunking/ragflow_like/parsers/qa.py

## 问题

QA 分块解析器存在四个影响索引质量的缺陷：

1. `# Q: xxx` / `## 问题: xxx` 这类带问答前缀的 Markdown 标题不被识别，标题剥 `#` 前无法匹配行首 Q/A 前缀，导致此类 FAQ 文档的问答边界丢失。
2. 渲染后的 QA chunk 没有任何长度上限。目标 embedding 模型 bge_m3 的上下文为 4096 tokens，超长单条问答在索引时会被服务端截断或报错，且截断点不可控。
3. 代码围栏只识别反引号围栏，不识别 Markdown 同样允许的波浪号围栏。答案中的 `~~~` 块内若有形似 `Q:`/`A:` 或 `# 标题` 的行，会被前缀提取和标题提取误切为虚构问答对。
4. 前缀提取中 `A:`/`Answer:` 行在问题出现前被无条件记入答案缓冲，而 `flush_pair()` 在无活跃问题时不清空缓冲。文件前言里的孤儿答案行会被拼接到其后首个真实问答对的答案前，污染提取结果。
5. 标题识别未校验 ATX 标题要求的井号后空白，会把 `#include` 等代码行当作分节标题并截断答案；超长问答又会优先寻找任意语言的答案标记，问题正文中的异语言标记可能被误认成结构分隔符。

## 决策

1. 行首前缀匹配前先把标题行剥为纯文本，使 `# Q:` / `## 问题:` 与裸 `Q:` / `问题:` 走同一边界识别。
2. `chunk_markdown` 输出的所有 QA chunk 统一经 `_split_long_qa_chunks` 限长：超过 `_QA_CHUNK_MAX_CHARS = 4000` 字符的 chunk 保留完整问题与前缀，只切答案；答案按段落 → 行 → 固定字符硬切逐级降级，问题本身超限或 chunk 非标准 QA 格式时整条硬切，保证任意单条不超上限。问答分隔定位紧邻已知答案前缀（`\t回答：` / `\tAnswer: `）的 tab，而非首个任意 tab，问题本身含 tab 时不被截断。按行切分时 strip 仅用于判空，重组保留每行原始缩进，避免破坏围栏代码块与嵌套列表的语义。
3. 4000 是面向 bge_m3 4096 token 上限的保守字符数兜底：中/英/数字混合内容按接近 1 token/字的最坏情况预留缓冲，不引入 tokenizer 依赖。
4. embed 侧配套 `_log_long_inputs` 调试日志，只记录超限输入的 index 与长度，不输出内容，避免知识库文本进入应用日志。
5. 围栏状态由 `_update_fence_state` 统一追踪，` ``` ` 与 `~~~` 各自成对开关，异类围栏行不关闭当前块；前缀提取与标题提取两处共用同一状态机。围栏行与块内行只作答案正文，不参与问答边界与标题识别。
6. 答案前缀行只在存在活跃问题时记入答案缓冲，与普通文本行的守卫一致；问题出现前的孤儿 `A:`/`Answer:` 行直接忽略。
7. Markdown 标题只接受井号后为空白或行尾的合法 ATX 语法；超长问答只使用与序列化问题语言一致的答案标记定位边界，其他标记保留为问题正文。

## 替代方案

- 按 tokenizer 精确计数切分：引入 tokenizer 依赖并与具体模型耦合，而 qa.py 目前仅依赖标准库，且 embedding 模型由部署配置决定，parser 层无法确知远端词表。仓库现有 `count_tokens` 是正则近似（英文单词/数字/CJK 单字），完全忽略 emoji 与其他符号，对 byte-fallback 多 token 场景反而低估，不构成更可靠的 oracle。拒绝，字符数兜底与仓库「近似计数、避免引入额外依赖」的既定取舍一致。
- 在 embedding 调用处截断超长输入：会静默丢失答案后半段且切断问答结构，检索语义受损，拒绝。
- 复用 general parser 的 token 限长：通用切分不识别 QA 结构，会把问题与答案拆进不同 chunk，破坏每条 chunk 的问答完整性，拒绝。
- 把上限提到 8000+ 字符逼近 token 上限：中文内容 token 密度高，超限时失败点转移到模型服务端，不可观察，拒绝。
- 围栏沿用统一布尔翻转（不区分围栏类型）：`~~~` 块内的 ` ``` ` 行会提前关闭代码块，混合围栏文档重新出现误切，拒绝。

## 后果

- 模型可见 chunk 内容变化：超长答案被拆为多条 chunk，每条重复完整问题。检索命中任一子 chunk 都能带出问题，但单条内的答案可能不完整，由召回多条补偿。
- 重复问题占用每条子 chunk 的 embedding 容量，这是保留问答语义付出的代价。
- 4000 是启发式保守值而非精确 token 换算；更换 embedding 模型或上游 token 上限变化时需要重估该常量。
- 已知限制：字符数不严格等于 token 数。byte-fallback tokenizer 下 emoji 或罕见 Unicode 单字符可占多个 token，含大量此类字符的 chunk 即使 ≤4000 字符仍可能超 4096 token。此时 embedding API 显式报错（非 429/可重试状态码），索引任务失败可见，不会静默截断；`_log_long_inputs` 记录的 index/len 可辅助定位。严格闭合要求加载与部署模型一致的 tokenizer，暂不接受该依赖。
- 已知限制：行级切分点不感知围栏状态，超长单段落中的代码块可能跨 chunk（开闭围栏分离），影响命中文案的渲染完整性，不影响 embedding 向量语义；围栏感知切分的状态机复杂度与该场景的触发频率不成比例，暂不引入。
- 本记录由 PR review 补录：变更本身小而完整、已随修复生效且无待裁决替代，按规范直接写 `implemented`，未先建 `proposed`。

## 验证

- `backend/test/unit/test_qa_parser.py` 覆盖短 chunk 透传、空白过滤、段落/行/硬切三级降级、行切分保留代码缩进、问题含 tab 或异语言答案标记时完整保留、无答案前缀 tab chunk 硬切、问题超限硬切、非标准格式硬切、`max_chars<=0` 语义，以及 `chunk_markdown` 端到端「任意 chunk ≤ 4000 字符且保留问题」的验收主张。断言边界硬编码 4000，不引用实现常量 `_QA_CHUNK_MAX_CHARS`，避免自我引用 oracle。
- 负向验证：在同一进程内 monkeypatch 移除限长步骤后，端到端测试输入产出单条 9011 字符 chunk，`test_all_chunks_within_embedding_limit` 在正确原因上失败；将默认常量调大到 8000 时产出 8000 字符 chunk，端到端断言同样失败；恢复 guard 后通过。
- `test_all_chunks_within_embedding_limit` 同时断言结果发生切分，防止测试输入缩水导致端到端上限断言退化为恒真。
- 同一 `backend/test/unit/test_qa_parser.py` 还覆盖 tilde/backtick 围栏边界、带 info string 围栏、混合围栏不提前关闭、未闭合围栏吞并剩余、标题提取路径围栏、端到端 `chunk_markdown` 只产一对、首问题前/分节后的孤儿答案被忽略、活跃问题下多 A: 行仍完整归入，以及前缀和标题路径均保留 `#include` 代码行。
- 围栏负向验证：monkeypatch 回退为只识别反引号的旧状态机后，评论场景输入产出 2 对虚构问答，`test_tilde_fence_content_stays_in_answer` 在正确原因上失败；回退行级 strip 后缩进代码行丢失，`test_line_split_preserves_code_indentation` 在正确原因上失败；回退首个 tab 分隔后含 tab 问题被截断为 tab 前部分，`test_question_containing_tab_kept_whole` 在正确原因上失败；回退孤儿答案守卫后前言被拼入真实答案，`test_orphan_answer_before_first_question_discarded` 在正确原因上失败。
- dispatcher 回归：用修复后的 qa.py 复跑 `test_qa_chunking_from_markdown_headings` 的标题风格 FAQ 场景，产出与预期一致；`test_ragflow_like_chunking.py` 依赖完整包环境，待容器内补跑。
- embed 日志修复（只记 index/len）：AST 语法解析与 `git diff --check` 通过。
- 待 PR 环境补跑：`docker compose exec api uv run --group test pytest test/unit/test_qa_parser.py test/unit/plugins/test_ragflow_like_chunking.py`。
