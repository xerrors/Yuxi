# WeKnora 与 Yuxi 知识库能力对比调研(企业应用视角)

- 调研日期:2026-10-11
- 对比对象:[Tencent/WeKnora](https://github.com/Tencent/WeKnora)(v0.8.2,2026-09-24)vs 本仓库 Yuxi 平台
- 方法:三个并行子智能体分别调研 WeKnora 核心技术链路、WeKnora 企业落地能力,以及 Yuxi 现有实现(代码级证据)。外部事实以 GitHub 仓库/官方文档站为第一来源,第三方评测交叉验证。Yuxi 侧结论均带 `file:line` 证据。
- 注意:WeKnora 的 stars(约 33k)、issues 编号等均为调研当日实测。v0.7.x 的发布年份在不同来源相差一年,已按官方发布文档对齐到 2026。

---

## 1. 结论摘要

WeKnora 的企业优势在**摄取广度、分块工程质量、溯源体验、集成生态、部署弹性**这五条"企业落地面"。检索算法单点上双方相同,都是"向量 + BM25 混合 + rerank + 图谱"的同代架构。Yuxi 在**图谱检索深度(GraphRAG/PPR)、引用防幻觉校验、权限执行纪律、数据一致性工程、评估 UI**上反而更强。

最值得借鉴的是一批低成本高感知的工程细节(分块预览,父子分块,面包屑,rerank 复合打分+MMR,评估指标),以及两条中期整合线(**数据源连接器体系**和**知识库 MCP Server 化**)。WeKnora 的安全历史(8 个 CVE、SSRF 回归)说明其快速迭代以牺牲加固节奏为代价,这是 Yuxi 在企业可信度上的相对优势,不应为追功能而放弃。

---

## 2. 项目定位

| | WeKnora | Yuxi |
|---|---|---|
| 定位 | 文档 → RAG 问答 / 自主推理 Agent / 自维护 Wiki,三模式共享一套知识库 | 知识库 + 多智能体平台(LangGraph),知识库是 Agent 的能力之一 |
| 技术栈 | Go 主应用 + Python docreader 解析服务(gRPC)+ Vue | FastAPI + LangGraph + ARQ + Vue |
| 检索后端 | 多引擎可插拔:PostgreSQL(pgvector+ParadeDB BM25)/ Milvus / 腾讯云 VectorDB / SQLite(FTS5) / Qdrant | Milvus 3.0(派生投影)+ PostgreSQL(权威事实) |
| 规模与活跃度 | 33k stars / 4.4k forks / 200+ 贡献者 / 双周发布 / MIT | 本仓库 |

定位差异决定对比结论。WeKnora 把知识库做成**产品主入口**,因此在摄取、分块、溯源这些用户直接感知的环节打磨得很细;Yuxi 的知识库服务于 **Agent 平台**,在工程纪律(权限 fail-closed、Run 语义、一致性投影)上投入更深。

---

## 3. 能力对比矩阵

### 3.1 摄取与解析

| 能力 | WeKnora | Yuxi |
|---|---|---|
| 文档格式 | PDF/Word/PPT/Excel/EPUB/XMind/图片(7 种)/音频(mp3 等,走 ASR)/在线 URL/本地 HTML/MHTML/txt/md/csv/json | PDF/Word/PPT/Excel/xls/txt/md/csv/json/html/zip/图片(OCR);无 EPUB/XMind/音频 |
| 解析引擎 | 6 套引擎 + 自动回退调度:builtin(pypdfium2 逐页区分文本/扫描页)、MarkItDown、OpenDataLoader(Java 版面分析)、MinerU、PaddleOCR-VL、anydoc(Go 进程内 cgo Rust) | 7 种 OCR/解析引擎(RapidOCR、MinerU 自托管+API、PP-Structure-V3、DeepSeek-OCR、PaddleOCR-VL、PP-OCRv6)+ 健康检查 + 运行时切换(`infrastructure/document_parsing/engines/__init__.py:73-119`),惰性加载 |
| 图片理解 | docreader 零 OCR 零 VLM,扫描页渲染 JPEG 交 Go 侧调 OCR/VLM;"属性观察管线"先让 VLM 判断页面含文字量再决定是否跑 OCR;每图生成 `image_caption` + `image_ocr` 两个子 chunk 分别索引 | 图片走 OCR 转文本,无 caption/图向量 |
| 表格 | docx 表格转 HTML 再归一 Markdown;表头自动追踪(跨页表格 chunk 补零宽表头);Excel 首行作表头 | Docling/xlrd 解析,未见跨块表头续接 |
| 网页抓取 | Playwright WebKit + trafilatura 正文抽取,SPA 等待,微信公众号适配,SSRF 双重防护(导航前校验 + 子请求 route 守卫) | 仅单 URL HTML(`utils/url_fetcher.py`),SSRF 防护更细(DNS rebinding 防护、私网阻断、云元数据 IP 封禁、跳数校验) |
| 批量导入 | 整目录上传保留层级、任务面板并发 3、可取消重试 | 多文件/zip/workspace 导入 + content_hash 去重(`management.py:1386`) |

**小结**:WeKnora 的引擎"自动回退链"和"每图双子 chunk"是亮点;Yuxi 的引擎切换 + 健康检查 + 解析产物完整性校验(缺图拒发布)不弱,差距主要在**格式长尾(EPUB/XMind/音频)**和**批量网页抓取**。

### 3.2 分块(WeKnora 明显领先的环节)

| 能力 | WeKnora | Yuxi |
|---|---|---|
| 策略 | auto/heading/heuristic/legacy 四层回退链,每层过 Validator(不合格自动降级);标题密度触发标题分块;候选边界按优先级打分(换页符 100 > 编号小节 90 > …);受保护区间(公式/表格/代码块) | RAGFlow 风格 6 预设 general/qa/book/laws/semantic/separator,KB→文件→请求三级配置合并(`knowledge/chunking/ragflow_like/presets.py`) |
| 父子分块 | 有:父块只入库不嵌入,子块是唯一嵌入粒度;命中子块返回父块;父块间链表支撑邻居扩展 | 无 |
| 上下文增强 | ContextHeader 面包屑:知识标题+章节路径与正文分离存储,只进 embedding 输入不污染原文偏移 | 无(有 start/end_line 原文定位) |
| overlap | 语义边界对齐(段落>换行>句末,防切开 3.14/v1.2),硬上限语义 | 固定参数 |
| 调试 | `POST /api/v1/chunker/preview` 只读预览,返回所选 tier、降级原因、逐块统计 | 无(有分块参数配置 UI `ChunkParamsConfig.vue` 但不能预览) |
| 特殊类型 | FAQ 问答对即 chunk(索引 question_only/question_answer);chunk 可编辑(乐观锁 + 修订历史) | qa 预设,但无独立 FAQ 库类型、chunk 不可编辑 |

**小结**:这是 WeKnora 工程打磨最细的地方。Yuxi 的 6 预设覆盖面相近,但**父子分块、面包屑、预览调试**三项是检索质量与调参体验的实打实差距。

### 3.3 检索

| 能力 | WeKnora | Yuxi |
|---|---|---|
| 混合检索 | 上层统一加权 RRF(`0.7/(k+rank_v) + 0.3/(k+rank_k)`),权重/租户级可配;BM25 依赖各引擎(ParadeDB/Milvus 稀疏/FTS5) | Milvus 稀疏 BM25(中文分词 analyzer)+ 稠密向量,WeightedRanker 融合(`implementations/milvus.py:1030-1035`);无 RRF 选项 |
| Rerank | 复合打分 `0.6×模型分 + 0.3×检索分 + 0.1×来源权重` + **MMR 多样性选择**(λ=0.7)+ 三级兜底(阈值降级重试/模型超时回退);11+ rerank provider | 已实现但默认关闭(`milvus.py:265`),失败回退向量分;无 MMR、无复合打分 |
| 文本约束 | 文档未见对应能力 | 必含词 TEXT_MATCH、排除词、完整短语 PHRASE_MATCH、服务端命中高亮(`milvus.py:1129-1169`) |
| 多模态检索 | 图片向量(VLM2Vec/GME/Qwen3-VL-Embedding),可图搜图;命中图片直接作为 ContextImages 发给 VLM(≤3 张) | 无,图片仅 OCR 转文本 |
| 召回规模 | 过召回 `max(topK*5,50)×n` 上限 500,多库 fan-out 并发 4 | recall_top_k=50,final_top_k=10(`MilvusRetrievalConfig`) |

**小结**:双方同代。WeKnora 赢在 rerank 工程化(复合打分+MMR+兜底)和多后端弹性;Yuxi 赢在**文本约束过滤**(企业"必须命中关键词"类合规查询场景)和检索测试 UI。

### 3.4 知识图谱(GraphRAG)

| 能力 | WeKnora | Yuxi |
|---|---|---|
| 构建 | 每 chunk 单独 LLM 抽取(temperature 0.3),独立队列 MaxRetry 3,OCR 子 chunk 也入图,Neo4j APOC 写入 | LLM 三元组抽取(schema 约束、超时并发校验、json_repair 容错,`graphs/extractors/llm.py:36`) |
| 检索 | 查询期 LLM 抽实体 → Neo4j 模糊匹配种子(≤200)→ 取一跳邻居携带的 chunks 并入候选重排 | 向量召回实体/三元组作种子 → Neo4j 子图 → **networkx PersonalizedPageRank 对 Chunk 节点全图排序** → 按 graph_weight 与主检索融合(`milvus_graph_service.py:949-1013`) |
| 一致性 | 双开关(环境变量 + KB 级) | PG 权威 + Neo4j 结构投影 + Milvus 向量投影三存储协同,outbox 重试、vector_status 可修复(`background_jobs.py:369-383`) |

**小结**:WeKnora 是"一跳邻居拉取"式浅 GraphRAG;Yuxi 的 **PPR 全图排序 + 权重融合更深**,且三存储一致性工程(投影锁、generation 幂等)更严谨。这是 Yuxi 的技术差异化点,值得保持并宣传。

### 3.5 引用与溯源

| 能力 | WeKnora | Yuxi |
|---|---|---|
| 标注方式 | 生成前即以 `references` SSE 事件推送召回面板("引用先行");模型输出 `ref id` 内联引用,流式展开为带 chunk_id 的标签;未知别名 fail-closed 删除 | 事后 LLM 标注:回答行号→来源逐字摘录,`validate_reference_matches` 验证 quote 逐字存在(防幻觉),撤权后来源自动隐藏(`agents/services/references.py:138-239`) |
| 定位坐标 | source locator:PDF page+bbox、Excel sheet+row、PPT slide、音频 time、每条带 ≤300 字符 quote | chunk 级 start/end_line + char_pos;前端"片段展开阅读 + 原文入口"(d95232df) |
| 图片还原 | 命中图片 Markdown 内联 + 对象存储 URL 还原 | 无 |

**小结**:两种哲学——WeKnora"引用先行"换体验,Yuxi"事后校验"换可信(逐字防幻觉 + 撤权回放,这在企业审计场景更硬)。WeKnora 的 **page/bbox/sheet/row 几何级 locator 和图片还原**是可借鉴的增量。

### 3.6 Agent 与知识库集成

| 能力 | WeKnora | Yuxi |
|---|---|---|
| 工具链 | search_knowledge、read_document、list_documents、query_knowledge_graph、database_query、data_analysis(DuckDB)、web_search/fetch、shell、Wiki 九件套、MCP 发现/调用、local_browser | list_kbs、query_kb、open_kb_document(行窗口读原文)、find_kb_document(文件内定位)、search_file、download_kb_file(下载到沙盒)——"检索→阅读→定位→下载"闭环(`modules/extensions/tools/knowledge/tools.py:23-41`) |
| 注入方式 | 检索工具 + RAG 直答两条路 | 纯工具式,Agent 主动调用;有 knowledge_base_qa preset |
| 安全 | MCP 工具 HITL 审批门(默认 fail-close,10 分钟超时);沙箱 Docker/E2B/Cube | 工具执行时权限复查(`tools.py:306-318`);共享用例层可见性校验 |
| Wiki | 内置:Map-Reduce 四阶段自动生成互链 Wiki,版本快照 diff/回滚,内置修复 Agent | 无 |

**小结**:Yuxi 的 Agent 平台语义(FIFO 输入、Turn/Run、lease/恢复)比 WeKnora 的 ReAct 循环工程化更深;WeKnora 的 Wiki 生成是差异化产品能力但工程量巨大。

### 3.7 评估

| | WeKnora | Yuxi |
|---|---|---|
| 指标 | 12 项:Precision/Recall/NDCG@3/@10/MRR/MAP + BLEU-1/2/4、ROUGE-1/2/L(中英分词) | Precision/Recall/F1@k + LLM-as-judge 正确性 + overall(`evaluation/metrics.py`) |
| 数据集 | 5 个 Parquet,内置样例,可替换 | LLM 从 chunk 自动生成基准集(支持图增强模式)+ JSONL 上传/导出 |
| 入口与持久化 | 仅 API(需 Admin),**结果存内存,重启即丢** | 持久化 runs + 前端工作台 `KnowledgeEvaluationWorkspace.vue` |

**小结**:Yuxi 评估的**持久化、UI 和基准集自动生成**强于 WeKnora;WeKnora 的**指标广度**(NDCG/MRR/MAP、BLEU/ROUGE)是低成本可补的差距。

### 3.8 权限与多租户

| | WeKnora | Yuxi |
|---|---|---|
| 模型 | 多空间(multi-space)+ 四级角色 Owner/Admin/Contributor/Viewer,KB 级归属 | KB 级 share_config v2:read_scope + manage_scope,粒度 global/department/user(`identity/permissions/resource_permission.py:75`) |
| 认证 | 登录 + OIDC(JWKS 验签)+ scoped API Key + 管理员建号 | OIDC(`infrastructure/oidc/`)+ public_v1 API-key |
| 审计 | 每空间审计日志(v0.7.1 起) | 无专门审计日志 |
| 短板 | 无 LDAP/SCIM;跨空间共享知识库检索不通(issue #4084);无文档级 ACL | 无文档级 ACL;共享粒度到 KB 为止 |

**小结**:双方都是 KB 级权限、都无文档级 ACL,大体同水位。WeKnora 多出**审计日志**(企业合规刚需);Yuxi 的优势在**执行点更严**(列表过滤/路由依赖/工具运行时/引用回放四层一致)。

### 3.9 部署与生态(WeKnora 企业优势最集中的地方)

**WeKnora**:
- 四种部署形态:Docker Compose / Helm / **Lite 单二进制(SQLite+内存队列,零外部依赖)** / 桌面应用(Wails);纯 CPU 可跑(建议 4C8G);腾讯云 Lighthouse 一键部署
- **MCP 双向**:内置 MCP Server(按空间把 KB 发布为 Streamable HTTP 服务,供 Cursor/Claude 调用)+ 可外接 MCP 工具(OAuth2、HITL 审批)
- **IM 矩阵**:企业微信、微信、飞书、钉钉、QQ、Slack、Telegram、Mattermost、云之家
- 数据源连接器:GitLab、RSS、Confluence、腾讯 IMA、web_crawler
- 浏览器剪藏扩展、iframe/浮窗嵌入、约 360 个 API 端点、Go SDK、CLI
- Langfuse 可选 profile 链路追踪、任务队列面板

**Yuxi**:
- Docker Compose 单一拓扑(PG16/Redis/MinIO/Milvus/Neo4j/mineru/paddlex),无 Helm/Lite
- 集成以 public_v1 API + dify/notion 只读连接器 + workspace 导入为主
- Langfuse 覆盖 Agent turn(`infrastructure/observability/langfuse.py`),检索调用本身无专门 tracing/metrics

**小结**:企业"最后一公里"(接 IM、接现有数据源,以及被外部 Agent 调用)是 WeKnora 的核心竞争面,也是 Yuxi 目前最薄的层。

### 3.10 安全与社区(选型尽调事实)

- WeKnora 安全历史:Unit 515/OPSWAT 2026-03 披露 8 个漏洞(CVE-2026-30247 SSRF、CVE-2026-30857 跨租户知识库克隆绕过、CVE-2026-30860 SQL 注入、CVE-2026-30861 RCE 等);v0.7.0 集中加固;**v0.8.2 后仍有 SSRF 绕过 open issue(#4092)**;Helm 生产示例在单 RWO 卷扩副本(#4106);VLM 限速 429 被静默标记 skipped 导致图片内容丢失(#4064)。
- WeKnora 社区:MIT License,33k stars,双周发布,腾讯微信团队主导(第一名贡献者 2,128 commits,集中度高),无商业版/SLA,Roadmap issue 已被社区指出与实际脱节。

---

## 4. WeKnora 的企业优势(按说服力排序)

1. **集成生态**:MCP Server + IM 矩阵 + 剪藏扩展 + iframe 嵌入 + 数据源连接器——"把知识库送进企业已有工作流"的能力矩阵完整。
2. **部署弹性**:Compose/Helm/Lite/桌面四形态,纯 CPU,离线友好;企业 PoC 门槛极低。
3. **摄取广度**:EPUB/XMind/音频(ASR)/整目录导入/深度网页抓取,格式长尾覆盖好。
4. **分块工程质量**:父子分块、ContextHeader 面包屑、语义 overlap、预览调试端点——直接决定检索质量与调参效率。
5. **溯源体验**:page/bbox/sheet/row 级 source locator + 引用先行 SSE + 命中图片还原。
6. **品牌与节奏**:腾讯背书、33k stars 和双周发布带来采购侧信任。

## 5. Yuxi 的相对护城河(WeKnora 弱或无)

1. **GraphRAG 深度**:PPR 全图排序 vs 一跳邻居;三存储投影一致性(outbox/generation 幂等)。
2. **引用可信链**:逐字 quote 校验防幻觉 + 撤权后引用自动隐藏,比"fail-closed 删标签"更硬。
3. **文本约束检索**:必含/排除/短语匹配 + 命中高亮,WeKnora 文档无对应能力。
4. **权限执行纪律**:四层一致执行点(列表/路由/工具/回放),AGENTS.md 的 fail-closed 约定 + 真实链路测试文化;对照 WeKnora 的 8 CVE 历史。
5. **评估体系**:持久化 runs + UI + 基准集自动生成(含图增强),WeKnora 是内存态 API-only。
6. **Agent 平台工程**:FIFO/steer/Turn-Run 语义、lease/heartbeat、崩溃可观察结局——多智能体平台本职,WeKnora 的 Agent 是单机 ReAct 循环。

---

## 6. 借鉴与整合建议(按投入产出排序)

### P0 快赢(低成本、直接补感知差距)

| # | 事项 | 对应差距 | 落点(基于现有代码) |
|---|---|---|---|
| 1 | **分块预览 API**:入参 = 分块配置+样例文本,返回逐块结果与原因,不改库 | WeKnora `/chunker/preview` | 复用 `knowledge/chunking/ragflow_like/` presets,新增一个只读路由 + `ChunkParamsConfig.vue` 里加预览面板 |
| 2 | **Rerank 复合打分 + MMR**:模型分与检索分加权融合,MMR(λ≈0.7)去冗余,保留现有失败回退 | `milvus.py:1119-1120` 现在是简单替换/回退 | `MilvusRetrievalConfig` 加权重与 λ 参数,默认关闭保持兼容 |
| 3 | **面包屑进 embedding**:chunk 索引文本 = 知识标题/文件标题 + 章节路径 + 正文,原文 content 不动 | WeKnora ContextHeader | 分块时按标题层级生成 `context_header` 字段入 `KnowledgeChunk` |
| 4 | **评估指标补齐**:NDCG@k、MRR、MAP(BLEU/ROUGE 可选) | `evaluation/metrics.py` 仅 P/R/F1 | 纯函数扩展,runs 持久化已有 |
| 5 | **知识库 MCP Server**:把 KB 检索/读原文发布为 MCP(Streamable HTTP),供外部 Claude/Cursor 调用 | WeKnora v0.8.2 内置 MCP Server;Yuxi 只有 REST | `/v1/knowledge/tools/*` 已有,加一层 MCP 协议适配即可 |

### P1 中期整合(企业刚需、中成本)

| # | 事项 | 说明 |
|---|---|---|
| 6 | **父子分块**:子块嵌入、命中返回父块,父块链表支持邻居扩展 | `KnowledgeFile.parent_id` 只表示文件夹树;chunk 父子关系需新增字段,分块预设加 parent_child 开关;预期对长文档 QA 召回提升明显 |
| 7 | **定位坐标增强**:解析产物带 PDF 页码(+bbox 可后置)、Excel sheet/row | `infrastructure/document_parsing` 产物结构扩展;前端"原文入口"已有,补页级跳转 |
| 8 | **图片 caption/ocr 双子 chunk**:每张解析图片生成两个子 chunk 分别索引,检索命中还原图片 | 不依赖图向量,MinIO 已存解析图片;先做文本链路,图向量(VLM2Vec 等)列为 P2 |
| 9 | **深度网页抓取**:批量 URL/sitemap + 正文抽取(trafilatura 类),保留现有 SSRF 防护(DNS rebinding/云元数据封禁比 WeKnora 更严,不要削弱) | `url_fetcher.py` 升级 + 批量任务走既有后台任务体系 |
| 10 | **数据源连接器体系**:基于 `KnowledgeBaseFactory` 现有抽象(read_only_connectors 已有 dify/notion)扩展 RSS/GitLab/S3/网盘,定时增量同步 | 企业"知识库持续进水"刚需;优先做目标客户点名的 1-2 个 |
| 11 | **操作审计日志**:KB 级共享变更、权限授予/回收、工具检索调用等事件落库可查 | 对标 WeKnora 每空间审计;合规投标场景硬要求 |
| 12 | **FAQ 知识库类型**:`kb_type` 字段已在,加 faq 类型(问答对即 chunk,question/answer 索引模式) | 客服场景高频;实现薄 |

### P2 观察项(不建议内核照搬)

| # | 事项 | 理由 |
|---|---|---|
| 13 | Wiki 自动生成 | WeKnora 工程量巨大(Map-Reduce 四阶段+版本系统);Yuxi 定位是多智能体平台,应由 Agent+技能实现,不进知识库内核 |
| 14 | IM 机器人矩阵 | 按目标客户选 1-2 个(如企业微信),走 Agent API/工具,不做平台化 IM 适配层 |
| 15 | Lite/桌面形态 | 与 Yuxi 拓扑(PG/Redis/Milvus/Neo4j)冲突,企业私有化用 Compose 已够 |
| 16 | 图片向量检索(图搜图) | 依赖多模态 embedding 模型生态,等客户场景明确再做 |
| 17 | 多检索后端可插拔(Milvus/ES/pgvector…) | WeKnora 为开源获客服务多形态;Yuxi 单后端 + PG 权威投影架构更简,无 consumer 前不引入 |

### 明确不建议做的

- **照搬 WeKnora 的"多引擎自动回退解析调度"全套**:Yuxi 已有 7 引擎 + 健康检查 + 显式切换,企业排障时显式选择比隐式回退可审计;可借鉴的只是"产物质量校验后回退"这一个点。
- **为追功能放宽安全边界**:WeKnora 的 SSRF 回归、VLM 429 静默 skip(#4064)恰是反例;Yuxi 新增抓取/连接器时保留显式失败与结构化降级约定(AGENTS.md 既有要求)。

---

## 7. 一句话战略判断

WeKnora 是"知识库即产品":在摄取→分块→溯源→集成这条用户可感知链路上打磨极细,适合作为企业功能雷达。Yuxi 是"知识库即 Agent 能力":图谱深度和引用可信,以及权限纪律和平台工程是差异化资产。**借鉴它的面,坚持我们的线**——P0 五项快赢 + P1 的连接器/审计两条线,即可把企业落地面的最大差距补掉,而无需动摇 Yuxi 的架构根基。

---

## 8. 主要来源

- WeKnora 仓库与文档:https://github.com/Tencent/WeKnora ;`website-docs/`(03-document-parsing / 04-chunking / 05-retrieval-engines / 06-models / 07-agent / 09-knowledge-graph / 14-wiki / 15-evaluation / 02-rag-pipeline)
- Releases:https://github.com/Tencent/WeKnora/releases(v0.8.2 = 2026-09-24,官方文档站确认)
- 企业能力:README_CN.md、官方安装文档 https://weknora.weixin.qq.com/docs/01-getting-started/02-installation
- 短板 issues:#13(竞品对比)、#414(roadmap)、#4084(跨空间共享)、#4092(SSRF)、#4106(Helm RWO)、#4064(VLM 429 静默)、#4077(PG crash)
- 安全披露:OPSWAT Unit 515(https://chinese.opswat.com)、SentinelOne 漏洞库(CVE-2026-30247/30857/30858/30860/30861)
- Yuxi 侧证据:见正文各表 `file:line`,关键文件 `backend/yuxi/modules/knowledge/`(models/chunking/implementations/milvus.py)、`backend/yuxi/infrastructure/document_parsing/`、`backend/yuxi/modules/agents/services/references.py`、`backend/yuxi/modules/extensions/tools/knowledge/tools.py`
