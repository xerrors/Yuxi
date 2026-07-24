# 版本变更记录

本页用于记录各版本发布说明（新增、修复与破坏性变更）。

同一版本的多次功能更新时，应以功能为单位进行更新，比如之前添加了 A 功能的更新，在后续的更新中修复了因 A 功能引入的 bug，那么这个修复说明应该和 A 功能描述放在一起（重新修改表达，而不仅仅是补充），而不是新增一条修复记录，功能更新同理。必须遵守：每一个修改不应超过 200 字，注意高度凝练。

## v0.7.2 (current)

- HTML 辅助可视化迁移为内置 `html-preview` Skill：默认 Chatbot Prompt 不再常驻注入 `html:preview` 专属说明，Agent 改为通过统一的 Skill 描述发现并按需读取静态 HTML/CSS 的适用场景、布局和安全边界；未显式配置 Skills 的 Agent 按现有默认规则自动获得该能力，使用显式 Skills 允许列表的 Agent 需选择 `html-preview`，内置 `deep-research` 已声明依赖；保留前端既有围栏清洗、sandboxed iframe、自适应高度和流式占位行为，普通 HTML 源码继续使用 `html` 代码块。
- 模型供应商的单个 chat 模型配置新增“模型请求参数 JSON”：管理员可为每个模型独立保存、回显、修改和清空思考参数，未配置或空对象保持原行为；运行时模型缓存会携带该配置，测试模型连接与正式聊天/Agent 调用统一在模型加载入口合并。该字段仅面向 OpenAI/OpenRouter 等 OpenAI 兼容供应商，并通过 `extra_body` 透传；出于安全考虑，顶层字段采用白名单机制，当前支持 `enable_thinking`、`thinking_budget`、`thinking`、`reasoning` 和 `reasoning_effort`，对象内部结构交由供应商校验。
- 新增通用管理员配置 Options 模块：使用 PostgreSQL `config_options` 保存系统维护的名称、说明、受控表单参数和管理员 JSON 值，核心定义、校验、显式 `sensitive` 字段脱敏及“数据库非空值 → 环境变量”解析集中在 `config/options.py`；每个 `Option.get()` 均直接查询数据库，不建立进程内缓存，接口仅管理员可用。OCR 作为首批消费者，不再建立专用配置表或维护逐引擎启停状态；数据库密钥只显示真实首尾字符的脱敏预览，环境变量只显示配置来源。设置页不集中陈列健康状态。
- 修复部署配置：开发与生产 Compose 中的 Milvus 现在复用对应环境文件里的自定义 MinIO 凭据，避免对象存储认证失败导致服务无法健康启动；Web 生产镜像会统一将静态资源目录设为 `755`、文件设为 `644`，避免 Nginx 因构建产物权限过严返回 403。
- 丰富模型选型参考信息：接入 `@opencode-ai/models` 内置 snapshot，补充模型上下文、能力和价格等信息；模型选择器移除价格悬浮提示并关闭搜索自动完成，模型供应商候选列表支持美元与人民币价格切换，默认美元，人民币按固定汇率 `1 USD = ¥7` 换算，并明确 models.dev 数据与固定汇率仅供参考；候选模型工具栏统一靠右排列搜索、币种和类型筛选控件，类型文案改为“对话 / 向量 / 重排”。DashScope 中国站内置标识修正为 `alibaba-cn`，`alibaba` 改为使用 `dashscope-intl.aliyuncs.com` 的国际站定义。
- 优化 Agent 会话与设置页多项交互细节：修复文件侧栏重新展开丢失预览、审批模式改为本地记忆最近选择、下拉面板点击外部区域自动收起、输入框添加内容入口改为 `+` 并可直接引用知识库与 Skill、Skill 图标统一为 Lucide `WandSparkles`、账户设置合并 Memory 开关等；侧栏导航“智能体管理”简化为“智能体”，路由由 `/model-manage` 重命名为 `/agent-manage`；同步优化首页首屏视觉细节。
- 新增 Agent backend 工具审批与完全信任模式：Agent 配置可定义默认模式，聊天 run、Agent Call 与评估请求可用 `tool_approval_mode` 做单次覆盖，实际模式固化到 `AgentRun` 并由 resume/子智能体继承；默认审批仅拦截 `write_file`、`edit_file`、`execute`，只读 filesystem 工具直接执行，完全信任保持自动执行。前端输入框左下角新增线程级“请求审批 / 完全信任”选择器，支持刷新恢复显式选择；智能体配置页将带选项的 `string` 字段正确渲染为选择控件，模式解析按“线程显式选择、智能体显式配置、本地最近选择、系统默认值”依次回退，避免本地缓存覆盖智能体配置。同轮多个工具调用按顺序一次展示一项，参数默认只显示单行摘要，点击后带轻量动画在卡片内展开完整内容，展开区最大高度为 300px。审批卡进一步调整为直接覆盖输入区，按工具类型优先展示命令或目标路径，操作固定为“拒绝 / 允许”，审批期间底层输入控件不可交互。默认模式下子智能体隐藏敏感 backend 工具，避免绕过主线程审批。补充模式解析、middleware、run 继承、事件压缩、子智能体过滤、前端状态恢复测试，并在真实页面验证刷新恢复、逐项批准或拒绝、参数点击展开与完全信任自动执行。模式解析合并为单次 context 共享（resolve_agent_run_config）避免 run 创建/intake 重复加载上下文；resume 与子智能体继承统一读取固化快照，前端审批弹窗参数序列化改为 computed，并清理 `reviewConfigs` 等未消费状态。resume 解析 tool_approval_mode 时对旧版本固化、缺少该字段的 interrupted 运行回退默认值而非报错，避免历史中断会话升级后无法恢复。
- 新增智能体请求队列（Phase 1/2/Steer）：同一线程运行中提交的新请求默认持久化 FIFO 排队，也可显式选择 Steer，在当前模型步骤已经开始的全部工具调用完成并写入 checkpoint 后、下一次模型调用前安全结束旧 Graph，再原子创建 replacement Run；已有 queued request 可原地升级，同线程最多一个 pending Steer。旧 Run 使用 `cancelled/error_type=steered` 保留已完成内容，Request SSE 的 `run_created` 是唯一交接入口。继续支持排队位置、单条取消、刷新恢复、`reject`、失败暂停与人工继续；interrupted 必须先 resume。Conversation 行锁、部分唯一索引、终态单赢家和 pending Run 投递意图共同保证并发 FIFO、Steer 唯一性、交接事务和故障恢复。前端新增显式“引导”、排队项升级与安全交接状态，默认回车仍为普通发送。补充 LangGraph 并行工具安全点、真实 Redis ARQ 租约恢复、单元、API 和 PostgreSQL 并发测试，并更新《Agent 请求队列与调度设计》。
- 新增知识库 external API 与 `yuxi kb` 查询类命令：后端在 `/api/knowledge/databases/external/*` 下暴露列库、文件搜索、检索、打开和文件内查找接口，统一走认证身份校验；CLI 新增 `yuxi kb list/files/query/open/find`，补充后端 external API 集成测试与 CLI client/命令测试。管理端同步新增 `GET /api/knowledge/databases/{kb_id}/documents/search`，复用底层文件名搜索能力供前端调用；知识库详情页工具栏新增「搜索文件」按钮，打开命令面板式弹窗（与历史对话搜索弹窗同风格），输入关键词按文件名搜索并在结果列表展示状态/大小/更新时间，placeholder 明确标注仅匹配文件名、不搜索文件内容，点击结果可直接打开文件详情。搜索请求增加序号校验，连续回车与快速重搜时丢弃过期响应，避免后发先至覆盖当前关键词结果。文件名搜索改为数据库侧按 filename/status 过滤、`updated_at desc` 排序与 count 统计，避免大知识库超过仓储单页上限时静默漏结果；管理端搜索先做当前用户可见性校验再判断文档能力，external open/find 统一清晰处理只读源与预期参数错误；CLI 查询结果从 `metadata.score` 读取并展示检索得分。
- 新增 `download_kb_file` 知识库工具：通过 `file_id` 调用 `knowledge_base.get_file_download(variant="original")` 从 MinIO 拉取原始二进制（pdf/docx/xlsx 等），落盘到沙盒 `outputs` 目录并返回沙盒内可见的虚拟路径，供后续代码工具以文件对象方式读取（`openpyxl.load_workbook`、`pdfplumber.open` 等），弥补 `query_kb`/`open_kb_document` 只返回文本切片、丢失原始文件结构的不足。复用会话可见知识库校验与 `ocr_parse_file` 的落盘范式，支持 `save_as` 指定文件名（剥离目录防穿越，重名追加 `_N` 后缀），工具只搬运不解析、不自动登记交付物。工具已登记到 knowledge-base 内置 Skill 的 `tool_dependencies`，并在 SKILL.md 可用工具段补充说明；只读源拦截下沉到 `manager.get_file_download` 内部，与 `open_document`/`find_in_document` 落点一致，工具/router/CLI 所有调用方统一获得 dify 等只读检索源的拦截；返回的 `size_bytes` 与落盘统一用下标访问 `data["content"]`，避免回退掩盖契约异常。

## v0.7.1 (2026-07-17)

### 安全

- 生产 Compose 不再回退到公开的 Neo4j、MinIO 和 PostgreSQL 默认凭证，并要求显式配置 JWT 随机密钥与实例标识；相关配置缺失时会在解析阶段拒绝启动并提示具体变量名。管理员初始化、创建用户、创建部门管理员及修改用户密码在前后端统一要求密码不少于 8 位。
- 修复沙箱执行边界：每个动态 Docker 沙箱使用只与 provisioner 相连的独立网络，沙箱之间不能互访，也不再加入业务 `app-network` 或发布随机宿主机端口；provisioner 重启后会重新接入已有沙箱网络，清理时只删除自身创建且标签匹配的网络。API/worker 使用至少 32 字符的 `SANDBOX_PROVISIONER_TOKEN` 调用 provisioner，并通过认证代理访问沙箱文件与命令接口，代理在应用生命周期内复用 HTTP 连接池。生产 Compose 同时移除 PostgreSQL 和解析服务的宿主机端口，阻断沙箱对其他租户、业务数据库、对象存储和无鉴权 provisioner 的横向访问。
- 公开头像和 Agent 图片改用同源 `/minio/public/...` 地址，由开发 Vite 和生产 Nginx 只读代理 `public` bucket；MinIO `9000` 对象 API 与 `9001` 管理控制台无需对外开放，私有 bucket 不进入前端代理。
- Markdown 渲染兼容历史 PDF 解析结果中的 `http(s)://<host>:9000/public/...` 图片链接，在展示时转换为同源 `/minio/public/...`，无需批量重写 MinIO 中已有的 `.md` 文件或重新解析文档。


### 破坏性变更

- 沙箱 provisioner 现在强制要求 `SANDBOX_PROVISIONER_TOKEN`。升级前运行初始化脚本自动补生成，或手工使用 `openssl rand -hex 32` 生成并写入 `.env` / `.env.prod`；API、worker、provisioner 必须使用同一个值，但不能把它写入 `sandbox.env`。已有动态沙箱会因网络不匹配被 provisioner 删除并按新网络重建。
- API Key 收紧到具体用户：`api_keys.user_id` 收紧为非空，启动 schema 演进会先清理 `cli_auth_sessions` 中对未绑定 API Key 的引用，再 `DELETE FROM api_keys WHERE user_id IS NULL`，最后 `ALTER COLUMN user_id SET NOT NULL`。**升级前请在 0.7.0 库执行 `SELECT id, name, department_id FROM api_keys WHERE user_id IS NULL;`**，决定每个未绑定 Key 的归属用户并手动 `UPDATE`，未绑定的 Key 升级后会被静默删除且无法恢复；清理前后端日志会输出 `Schema migration will delete N unbound API key(s)` 告警以便回溯。
- Dashboard 收紧到 superadmin：所有 `/api/dashboard/*` 端点从 `get_admin_user` 收紧为 `get_superadmin_user`，前端路由同步收紧。0.7.0 中创建过 `role='admin'`（非 superadmin）的运维用户升级后将失去 Dashboard 访问权限，且应用内无自助提权路径；升级前请在数据库中将需要继续访问 Dashboard 的 admin 用户 `UPDATE users SET role='superadmin' WHERE uid=...`。首装场景的首个管理员始终是 superadmin，新部署不受影响。
- CORS 生产环境默认拒绝跨域：CORS 改为通过 `YUXI_CORS_ORIGINS` 显式配置允许来源；`YUXI_ENV=production` 且未设置该变量时返回空列表（拒绝所有跨域），显式设为 `*` 时会自动关闭 credentials。**前后端跨域部署的运维请在升级前设置 `YUXI_CORS_ORIGINS=https://your-frontend.example.com`**，否则浏览器跨域请求将被拒绝；同源部署（前端与 API 同源）不需要额外配置。
- 系统配置接口权限下放：`GET /api/system/config` 由 admin 收紧到任意登录用户可读，便于普通用户读取 `default_ocr_engine` 等运行时配置；接口会暴露 `sandbox_provisioner_url`、`sandbox_virtual_path_prefix`、默认模型 ID 等基础设施信息（不包含任何密钥/Token），如有更高保密要求请通过反向代理限制该路径。

### 开发记录

- 修复 Milvus 知识图谱子图查询忽略 `max_depth` 的问题：查询会按请求深度展开路径，并完整返回路径中的中间节点与关系；排除 Chunk 时同时限制整条路径，避免通过 Chunk 间接扩展。路径结果继续遵循现有节点和边数量上限。

- 修复线程文件接口的同步文件 I/O 阻塞：交付物预览仅异步读取媒体类型识别所需的 512 字节文件头，不再同步加载完整文件；线程文件全文读取和目录扫描下沉到工作线程，避免大文件或大目录并发访问时阻塞 API 事件循环。
- 修复应用 lifespan 关闭时未释放共享 Neo4j driver 的问题，避免同进程重载或重复启动后残留图数据库连接。
- 修复删除 Milvus 知识库阻塞事件循环：`MilvusKB.delete_database` 恢复异步基类契约，并将同步的主集合与图集合清理下沉到工作线程，避免删除期间阻塞其他对话和 SSE 推送。
- 修复 Agent 对话流式输出时的前端性能问题：自动滚动改为监听 `conversations` computed 的顶层引用变化，不再对完整对话与消息树执行深度 watch，避免每个 token 到达时递归遍历全部历史消息。
- 修复删除知识库文件图谱时清理范围过宽：Neo4j 仅删除本次文件 `MENTIONS` 边触及且已无任何 `MENTIONS` 引用的实体，不再顺带删除同知识库内其他文件遗留的孤儿实体。
- 对照当前解析器、知识库工具和 Agent 运行链路重整正式文档：补充默认 OCR、文件级处理参数、工作区 `AGENTS.md` / `USER.md` / `MEMORY.md`、知识库 `knowledge-base` Skill、`search_file`、`ocr_parse_file`、子智能体进度和图片 OCR 回退语义；更正知识库工具使用 `kb_id`、MCP 配置按数据库实时读取等过时描述；移除正式文档中的问答式栏目。
- 统一用户菜单的设置入口：管理员与普通用户均显示“设置”，打开后默认进入账户设置；管理员专属的基本设置、用户管理等标签继续按原权限展示。
- 工作区 `agents` 目录新增 `USER.md` 与 `MEMORY.md` 上下文文件，并与 `AGENTS.md` 一起在 Agent 运行开始时加载；三个默认文件首次创建时均写入对应标题和说明，不再生成空文件，已有内容保持不变。
- 新增 Summary 上下文压缩实时状态流式同步：`YuxiSummarizationMiddleware` 触发压缩时通过 `langgraph.config.get_stream_writer()` 推送 `yuxi.context_compression` 自定义事件（started/completed/failed），复用 DeepAgents 已有 `_summarization_event` 作为完成数据源；`base.py` 通过 `astream_events(version="v3")` 的 `CustomTransformer` 透传 custom 流，`chat_service`/`agent_run_service` 将事件映射为 `context_compression` chunk 并透传到前端；前端收到 `started` 时将"正在生成回复"加载态文案切换为"正在压缩上下文"，压缩结束（`completed`/`finished`）即切回，不额外渲染分隔符、不保留压缩完成态。为避免摘要 LLM 调用的 token 流被 LangGraph messages stream 捕获并广播成 phantom 摘要消息，重写 `_create_summary`/`_acreate_summary` 在摘要模型 invoke 的 config 上挂 `TAG_NOSTREAM`，让流式层在源头跳过该调用，主 messages 流天然只含用户可见回复，无需 `chat_service` 下游过滤（参考 DeerFlow 实现）。异步 L2 压缩路径的 `_aoffload_to_backend` 与 `_acreate_summary` 改回 `asyncio.gather` 并发执行，与 DeepAgents 父类一致，避免串行等待一次文件 I/O 与一次摘要 LLM 调用；两路复用 `_SUMMARY_SANITIZED_MESSAGES` 的 id 缓存。L1-only 调用若仍触发 provider context overflow，会回落到 L2 summary 后重试；`summary_tool_result_token_limit` 默认改为 300，并同时作为 L1 工具结果 offload 阈值和预览上限，L2 只消费 L1 视图，不再对工具结果做第二轮 offload；L2 摘要模型的待摘要历史输入上限改为与 `summary_threshold` 对齐，避免固定 4000 token 裁剪丢失早期历史；新增 `summary_l2_trigger_ratio` 管理 L1 后进入 L2 的比例阈值，默认 `0.4`。

- 知识库详情页新增整页内容加载态：切换或首次进入详情时，在知识库信息返回前仅展示居中 loading，避免标题、标签页和文件区域先渲染旧数据或空状态。
- 修复知识库文件处理中频繁刷新时，旧目录请求覆盖当前子目录列表并造成列表抖动的问题。
- `InfoCard` 新增统一的 `card-more-action-corner` 菜单插槽，并在组件内部固定渲染横向三点按钮；更多操作从卡片绝对定位改为进入 header 的正常 flex 布局，与图标、标题和 `status` 共享同一垂直中心线，业务页面只能提供菜单内容；智能体、知识库和用户管理卡片均改为复用该组件与菜单能力，用户部门/角色标签使用现有 `status` 插槽展示在标题区右侧，菜单图标与文字使用统一行高居中，知识库菜单支持复制 ID、直接打开编辑弹窗，以及确认后删除并刷新列表。
- 智能体管理页的普通智能体卡片新增“去对话”入口，点击后进入新建对话并预选对应智能体；子智能体卡片不展示该入口。
- 修复 API/Worker Docker 镜像构建失败：后端项目要求 Python `>=3.12,<3.14`，Dockerfile 基础镜像与 `.python-version` 同步到 `python:3.13-slim`，并将 `docker/api.Dockerfile` 的 `COPY` 源路径改为相对仓库根目录的 `backend/...`，与 `docker-compose` 中 `build.context: .` 保持一致；同时移除 `uv sync` 对 BuildKit `--mount` 的依赖并启用 `--no-cache`，避免分别因 Python 版本不兼容、`../backend/...` 越出 build context、未启用 BuildKit 或 uv 缓存残留导致镜像构建失败或体积膨胀。
- 新增用户级配置：保留现有全局配置链路不变，新增 `user_config` 表、`UserConfigSchema` 与无缓存的 `UserConfig` PostgreSQL 读取/保存入口；新增 `/api/user/config`，所有登录用户可读写自己的配置。首个字段为 `enable_memory`（是否启用 Memory），作为预留开关仅持久化与展示，不接入运行逻辑；设置弹窗新增“用户配置” Tab 展示并保存该开关。
- 优化 Skills 管理页展示文案：补充推荐 Skills 与内置 `mysql-reporter` 的卡片描述，避免短描述在两行卡片布局下显得过空。
- 新增 PaddleOCR 云端 API OCR 解析器：支持 `paddleocr_vl_1_6` 调用 `PaddleOCR-VL-1.6` 输出版面 Markdown，支持 `paddleocr_pp_ocrv6` 调用 `PP-OCRv6` 输出纯 OCR 文本；解析器复用 PaddleOCR jobs 提交、轮询与 JSONL 下载逻辑，健康检查仅校验 `PADDLEOCR_API_TOKEN` 配置状态，不创建真实 OCR 任务；知识库上传与临时附件解析弹窗同步增加两个 OCR 选项。
- 优化对话消息代码块交互：助手消息中的 Markdown 代码块右上角新增简约复制按钮，支持点击快速复制代码内容并显示短暂“已复制”反馈。
- 新增 Markdown `html:preview` 辅助可视化预览：仅显式标记的围栏会渲染为 sandboxed iframe，普通 `html` 继续展示源码；预览使用清洗后的静态 HTML/CSS `srcdoc`，按内容自适应高度并最高限制为 700px，超高时保留 iframe 内滚动，流式输出期间复用预览节点避免闪烁；内置 Agent Prompt 同步约束 Markdown 仍为回答主体，HTML 只补齐指标、对比、时间线、关系结构等可视化短板，不承载大段叙事、完整报告或正文解释。
- 新增历史对话搜索：侧边栏增加“搜索对话”入口，打开命令面板式弹窗，支持默认最近对话、新对话入口、搜索中骨架屏、结果列表、方向键选择与 Enter 跳转；后端新增 `/api/chat/threads/search`，按当前用户 active 对话中的非工具消息 `content` 检索并按对话聚合返回命中片段，同时将侧边栏导航项高度统一调整为 32px。
- 模型供应商管理前端开放 Anthropic provider type：Provider Type 下拉仅保留 OpenAI Completions API 与 Anthropic Messages API 两种可选项，保存值继续使用后端枚举，并在供应商卡片中展示友好类型名称。
- 优化 Agent 状态面板子智能体弹窗：弹窗消息列表复用对话消息渲染路径，打开运行中的子智能体时会展示主 run SSE 已路由到 child thread 的流式消息，并在生成中保持与主对话一致的处理态；修复当前 run 的历史半成品消息与 ongoing 流式片段叠加导致同一个子智能体在主对话中重复展示的问题，子智能体状态查询工具不再渲染成独立 Agent 卡片，弹窗会随子智能体条目补齐 run_id 后订阅对应 SSE，并复用主对话的流式平滑输出与底部跟随滚动控制；已完成的子智能体改为直接读取持久化 Message 历史，不再从 Redis run event 重放渲染。
- 增强异步子智能体 `subagent_status`：状态查询会从子 run 的 Redis 事件流反向提取最近 3 条可读进度摘要，并在工具卡中优先展示，终态结果读取语义保持不变；同时移除模型侧 `subagent_events` 工具，Redis 原始事件流继续仅供运行基础设施与前端 SSE 使用，避免包含重复 metadata、query 与嵌套 payload 的事件信封进入模型上下文并被写入 `large_tool_results`。
- 优化任务中心（Tasker）定位为「后台作业实体 + 只读进度面板」。前端修正失效的任务类型标签、状态判断收敛、任务详情补充参数/结果，并把轮询收敛到 store 修复抽屉关闭后角标不更新；后端 `TaskContext` 暴露 `payload` 消除私有穿透，进度更新按增量节流降低写放大，新增终态任务保留上限自动裁剪内存与数据库，`_load_state` 恢复历史任务使任务中心重启后仍可见。修复运行中任务关闭时 `shutdown()` 持有状态锁等待 worker、worker 又等待同一锁写入取消状态形成的死锁；生命周期操作改用独立锁串行化，等待 worker 前释放状态锁，并区分服务关闭取消与任务协作式取消，确保关闭能够完成且普通任务取消不会损失 worker。后台任务增加默认 6 小时且可通过 `TASKER_DEFAULT_TIMEOUT_SECONDS` 调整的执行上限，入队时可按单任务覆盖；超时会取消并等待业务协程清理后释放 worker，知识库文件与评估任务同步退出“处理中”状态。
- 知识库访问能力迁移为内置 Skill：新增 `knowledge-base` Skill，绑定 `list_kbs`、`query_kb`、`find_kb_document`、`open_kb_document`、`get_mindmap` 等知识库工具；内置 Agent 不再默认挂载知识库工具，改为读取并激活 Skill 后按需加载，同时保留 `knowledges` 作为知识库资源范围与权限边界。Agent 配置页在启用知识库但显式未选择 `knowledge-base` Skill 时实时展示提示，保存时不阻断。修复 Skill 依赖工具的可执行性：`create_agent` 中「模型可见工具」与「ToolNode 可执行工具」是两套，仅靠 `awrap_model_call` 动态追加工具只会绑定给模型、不进 ToolNode，导致激活 Skill 后调用 `list_kbs`/`query_kb` 报 `not a valid tool`；现由 `resolve_configured_runtime_tools` 统一把所有可见 Skill 依赖的本地工具随基础工具一起注册进 ToolNode（可执行），`SkillsMiddleware` 运行期再按 Skill 激活状态门控模型可见性（保持按需加载）。新增 `search_file` 工具支持按文件名关键词跨/指定知识库搜索文件，并已加入 `knowledge-base` Skill 的依赖工具；其分页统计基于全量扫描结果计算 `total`/`has_more`，避免按 `limit+offset` 截断导致计数失真。
- 增强知识库工具结果豁免：`open_kb_document` 工具结果加入 Summary 卸载豁免名单，避免大文档窗口被摘要后丢失上下文。
- 新增 Yuxi Python CLI 首版底座：新增独立 `packages/yuxi-cli` 包，提供 `remote add/use/list/ping`、`login --browser`、`login --api-key`、`whoami`、`status`、`logout`；配置统一写入 `~/.yuxi/config.toml`，remote URL 只保留实例入口并派生 `/api` 请求路径。后端新增 `/api/auth/cli/sessions` device flow 授权接口与 `cli_auth_sessions` 持久表，浏览器确认后为当前用户创建一次性返回的 API Key；新增公开 `/api/system/discovery` 声明服务端版本、API 前缀、CLI 能力和关键端点，CLI 登录前校验服务端版本至少为 `0.7.1`（`0.7.1.dev*` 按 release tuple 兼容）及对应能力；前端新增 `/auth/cli/authorize` 授权确认页。补充 CLI 本地单测与后端服务/路由单测。
- 安全与健壮性加固：token 兑换接口改为 `POST /api/auth/cli/sessions/token`，`device_code` 改走请求体，避免凭据出现在访问日志的 URL 路径中；兑换与批准会话时对会话行加 `with_for_update` 行锁，防止并发/重试导致重复签发 API Key；CLI 浏览器登录轮询区分瞬时错误（网络层错误、5xx）与终止错误，瞬时错误继续重试而非中断整个登录；`config.toml` 以 `0600` 原子创建并对名称等写入值做引号/反斜杠转义，避免明文凭据短暂可读及特殊字符破坏配置；API Key 认证在绑定用户失效时改为直接拒绝，不再 fallback 到部门管理员或 superadmin，创建 API Key 时校验部门与关联用户一致，用户软删除会同步禁用其 API Key；进一步要求 API Key 必须绑定具体用户，启动 schema 演进会清理历史未绑定用户的 API Key 并将 `api_keys.user_id` 收紧为非空；Dashboard 管理接口与前端入口改为仅 superadmin 可访问；用户软删除脱敏名改用用户主键生成，避免短哈希碰撞触发唯一索引冲突；前端授权页新增确认提示与对结构化错误 `detail` 的兼容渲染。
- 收敛 API Key 生成逻辑：移除独立 API Key 生成服务，统一通过 `AuthUtils.generate_api_key()` 生成 CLI 授权与用户管理中的 API Key。
- 收敛认证模块命名：CLI 浏览器授权路由合并到 `auth_router.py`，授权会话服务迁移到 `auth_service.py`。
- 为 CLI 知识库上传补齐后端接口边界：discovery 新增 `cli.kb_upload` 能力声明；普通文件上传接口在传入 `kb_id` 时先校验知识库存在且支持文档，校验通过后才读取文件或写 MinIO；新增同步 `POST /api/knowledge/databases/{kb_id}/documents/add`，用于把已上传的 MinIO 文件添加为知识库文档记录但不解析、不入库、不进入 Tasker；新增 `GET /api/knowledge/databases/{kb_id}/documents/exists?filename=...`，用于上传前按文件名或相对路径检查知识库内是否已有同名文件；旧 `/documents` ingest 入口保留兼容，但在 enqueue 前补充空 items、非 MinIO URL 与缺失 content hash 的请求级校验。
- 新增 `yuxi kb upload` 上传命令：默认仅包含 `.md/.txt/.docx/.html/.htm`，省略 `--kb-id` 时会从 remote 拉取并只展示支持文档上传的知识库，支持非全屏的方向键单选知识库与多选文件类型；支持 `--include-ext/--exclude-ext` 与 `--concurrency` 控制本地并发队列，并发默认 10、上限 300；交互终端上传阶段显示进度条，非交互输出保留文本进度；每个并发单元默认会先按相对路径调用 `/documents/exists` 检查知识库中是否已有文件，存在则直接跳过，传入 `--force-upload-file` 时跳过该预检并完全依赖上传接口的重复文件校验；单文件上传成功后立即调用 `/documents/add` 添加该文件记录，不触发解析/OCR/入库；目录上传通过 `source_paths` 保留相对路径，后端创建文件记录时使用该路径作为展示文件名以保持前端目录层级；上传接口返回“同内容文件已存在”时按已上传过跳过，不再作为错误展示；大批量上传调度改为有界提交，避免数十万文件时一次性创建全部 future 导致资源峰值过高。
- 发布 `yuxi-cli` 到 PyPI，并新增 GitHub Release 触发的 PyPI Trusted Publishing 工作流；文档新增命令行工具使用说明；CLI 运行访问 remote 的命令前会先输出当前 CLI 版本、remote 名称和 URL。CLI 输出测试在断言前去除 ANSI 样式，避免 GitHub Actions 的强制彩色输出拆分版本号、URL 与参数名并误阻塞 PyPI 发布。
- 修复知识库文件入库/解析成功却被统计为失败（#793）：成功的文件元数据会固定携带 `error: None`，而后台任务此前以「结果中是否存在 `error` 键」判定失败，导致成功项也被计入失败数并在全部成功时仍抛出「处理完成，失败 N 个」。改为统一通过 `_is_failed_item` 按「显式 `status == failed` 或非空 `error`」判定，覆盖入库、解析、单独解析/入库三处统计。
- 修复 Windows 初始化脚本自动生成 JWT 配置失败（#804）：`init.ps1` 改用 Windows PowerShell 兼容的 `RandomNumberGenerator.Create().GetBytes(...)` 生成随机字节，避免旧 .NET 环境缺少 `RandomNumberGenerator.Fill()` 导致按 Enter 自动生成时报错。
- 优化 Bash 与 Windows 初始化脚本：目标镜像标签已存在时直接跳过重复拉取；已有 `.env` 会逐项检查必填 API Key、JWT 密钥、实例 ID 和 Sandbox Provisioner Token，缺失或为空时提示输入，安全配置支持回车生成，并避免写入重复键。
- 优化知识库文件列表状态流转与文件预览边界：`uploaded/parsed/error_parsing/error_indexing` 状态分别展示解析、入库或重试操作；源文件预览与解析后的 Markdown 查看分离，txt/图片/Markdown/HTML/PDF/代码类按源文件类型预览；Office 源文件仅支持 `.docx/.pptx`，点击预览时按需生成并缓存 PDF 预览内容，由同一个预览接口直接返回，不再把解析 Markdown 产物当作源文件预览。
- 收敛知识库分块策略选项来源：后端以单一 `CHUNK_PRESETS` 配置派生 preset id、描述和选项列表，并新增 `/api/knowledge/chunk-presets`；前端分块策略选择器改为通过接口读取选项，避免前后端重复维护同一份文案。
- 优化大规模知识库文件列表加载：知识库详情接口默认不再返回全量 `files`，新增按 `parent_id/path_prefix/page/page_size/status` 查询的轻量文件列表接口；前端文件管理页改为目录懒加载与服务端分页，后端按 `source_path`/路径型文件名聚合虚拟目录，列表项只保留交互所需字段，顶部统计改用后端聚合结果，避免数十万文件场景下前端全量建树和传输压力。工作区知识库文件浏览统一改用同一套分页懒加载查询，支持真实目录和虚拟目录页码分页，非文档型知识库不再出现在工作区文件源中；文件浏览组件和后端列表接口均不再承载文件名搜索，后续搜索能力由独立后端接口和组件实现；文件列表展示抽出共享 `FileBrowserTable`，知识库详情和工作区共用展示层，并移除原知识库文件列表拖拽移动入口。
- 优化知识库启动元数据加载：服务启动时不再把全部 `knowledge_files` 记录加载进 `self.files_meta`，文件解析、入库、预览、下载、打开内容等单文件操作改为按 `file_id` 从数据库懒加载；文件状态流转改为通过数据库窄字段更新和状态条件更新完成，移除进程内处理队列修复逻辑，避免 api/worker 多进程下出现虚假的状态修复；文件统计刷新改用数据库聚合，文件大小补全从启动阶段移入显式统计修复任务，并收敛处理参数合并日志，避免大规模文档场景下启动内存和日志压力随文件数线性放大。
- 调整知识库待处理统计卡行为：文件管理顶部“待解析/待入库”统计卡从状态筛选改为提交对应后台处理任务；新增按待处理状态批量解析/入库接口，任务内按 500 条游标分页读取文件 ID，避免前端一次拉取和提交海量 ID；显式选中文件解析/入库接口增加 1000 个 ID 的单次上限。
- 修复大规模知识库统计修复失败：`repair_missing_file_stats` 不再对未入库文件查询 chunk 表，未入库文件残留的 chunk/token 统计会归零；chunk repository 的批量 `IN` 查询统一分批执行，避免 asyncpg 单条 SQL 参数超过 32767。
- 优化思维导图构建接口设计，支持增量构建和更新：新增 GET /mindmap/diff 接口检测文件变更，POST /mindmap/generate 新增 incremental 参数支持增量更新；纯删除场景无需 AI 调用（递归树手术），新增文件时 AI 整合进现有分类结构；思维导图文件加载改为显式 repository 查询，增量 diff 会按已追踪 file_id 补查分页外文件，避免把分页文件列表误当全量文件集；前端导图 Tab 新增"增量更新"按钮和变更数量 badge。修复删除文件后知识导图仍展示旧内容：单文件删除接口成功后调用 `remove_file_from_mindmap`、批量删除接口成功后调用 `batch_remove_files_from_mindmap`，同步移除导图快照中对应叶子节点，无需用户再手动增量更新。
- 优化文档结构与智能体运行说明：项目简介去除对 LangGraph 具体版本的强调；中间件文档按当前内置 Agent 链路重写，补充知识库工具、Skills 激活、附件/文件系统、子智能体 task、Summary 上下文压缩与工具结果卸载机制；知识库文档补充知识导图与示例问题生成机制；Langfuse 集成文档从“智能体开发”移动到“高级配置”分组。
- 移除知识库普通上传接口遗留的 `allow_jsonl` 参数，上传类型判断统一依赖 `SUPPORTED_FILE_EXTENSIONS`；评估数据集 JSONL 继续通过独立评估接口上传。
- 修复 Dependabot esbuild 告警：web 与 docs 统一锁定 `esbuild@0.28.1`，docs 同步升级 Vite/Vue 插件 override 并固定 pnpm 版本，避免旧锁文件继续解析到存在漏洞的 esbuild 版本。
- 修复 CORS 与依赖安全告警：后端 CORS 改为通过 `YUXI_CORS_ORIGINS` 配置允许来源，开发环境默认仅允许本机前端端口，生产环境未配置时不开放跨域，显式使用 `*` 时会关闭 credentials；同步刷新前后端锁文件，将 `aiohttp`、`cryptography`、`langchain`、`langchain-anthropic`、`pypdf`、`python-multipart`、`starlette`、`pyjwt`、`torch`、`torchvision`、`dompurify`、`js-yaml`、`markdown-it`、`vite` 升级到安全版本。
- 修复添加/编辑 MCP 弹窗中环境变量无法新增的问题：环境变量编辑器存在 rows -> object -> rows 的双向同步回环，`modelValue` 变化时会完全根据已有 key 重建行，导致只填了 key 的行（含刚点击「添加变量」生成的空行）被过滤掉而无法新增；现在仅当传入值与组件自身 emit 的内容不一致时才重建行，避免回声覆盖未填 key 的行。
- 修复模型与知识库后端导入循环：`yuxi.models` 改为惰性导出模型选择函数，知识库可见范围和知识库工具延迟读取全局 `knowledge_base` 实例，避免单测、热重载或轻量导入知识库包时因模块尚未完成初始化而失败。
- 修复知识库创建权限持久化一致性：创建知识库时由 Manager 归一化 `share_config/created_by` 后作为受控记录字段随首次知识库元数据插入写入数据库，避免先插入基础记录再二次更新权限字段产生短暂不一致。
- 修复 HTML 预览 iframe 高度问题：侧边预览模式改为 `height: 100%` 适应父容器，避免底部内容裁切；全屏预览模式移除 `min-height: calc(80vh - 40px)`，避免短内容下方白边；iframe 设为 `display: block` 消除行内基线间隙导致的底部白边；全屏渲染改用独立 `srcdoc`（不注入 `zoom`）按 100% 显示，侧边预览仍保持 0.75 缩放。
- 对话消息图片支持点击全屏预览：对话中用户上传的图片支持点击放大查看，复用文件预览的全屏蒙层交互（Teleport 蒙层，点击图片/空白处或按 Esc 关闭），不引入额外依赖。
- 新增 Agent token usage 状态快照，在状态面板中作为普通可折叠分组展示完整 `messages`、当前传给 LLM 的 `messages`、system/tools 构成、输入构成堆叠条和上下文窗口占用估算。
- 优化 Agent token usage 状态面板展示：后端补充 LLM 内容消息与工具消息的 token/count 拆分字段，前端将内容消息、工具消息、系统消息与工具定义分开展示，并修正上下文窗口/剩余信息换行与对话流式输出期间的底部跟随滚动。
- 收敛 Agent `read_file` 多模态边界：仅 UTF-8 文本和图片可读，PDF/Office 文档会引导使用 `ocr_parse_file` 转为 Markdown，音视频及未知二进制不再注入模型消息；OpenAI 兼容链路的 tool-role 图片桥接从私有 payload 覆盖迁移到公开模型中间件，Provider 明确拒绝图片输入时会自动调用 `ocr_parse_file` 提取文字，并在后续请求中移除同一张历史图片，避免文本模型重复报错。
- 新增默认 OCR 解析引擎配置 `default_ocr_engine`，普通登录用户可读取系统配置；知识库上传弹窗与临时附件解析弹窗默认选中系统默认 OCR，解析入口仅在未显式传入 `ocr_engine` 时使用该默认值。修复读取该配置时因反向导入知识库模块导致配置初始化循环、并中断后续配置加载的问题；OCR 注册表改为轻量模块，知识库单例迁移到显式 runtime 入口，解析器调用方直接导入真实定义模块，包初始化不再加载运行对象。
- 新增 Agent 内置 `ocr_parse_file` 工具：只允许解析 `/home/gem/user-data/{workspace,uploads,outputs}` 下的沙盒虚拟路径文件，使用指定或系统默认 OCR 引擎生成 Markdown，并把结果写入 `outputs/ocr/*.md`；工具返回结果文件路径、字符数和短预览，不写入知识库 MinIO，也不创建知识库文件记录。
- 收敛 Agent Invocation 服务边界：新增 `agent_invocation_service.py` 承接 agent-call/eval 的外部调用语义、同步等待、异步响应与 OpenAI-compatible 响应装配；`agent_invocation_router.py` 收敛为 HTTP 适配层，`agent_run_service.py` 只保留通用 AgentRun 生命周期能力，`subagent_run_service.py` 改为调用公开 AgentRun 创建 API，不再穿透私有函数。
- 修复 Agent 状态读取与消息落库在重新读取 LangGraph checkpoint 时未传入运行时 context 的问题，避免主智能体或子智能体线程因系统默认模型已不可用而查询状态/保存历史失败；模型供应商管理页新增默认模型保护，阻止删除、停用默认模型所属供应商或移除当前默认模型。
- 评估数据集自动生成支持断点续跑：生成过程中按 `YUXI_DATASET_PERSIST_BATCH_SIZE`（默认 1）批量持久化已生成的题目，任务失败或中断后可从已持久化进度继续生成；新增 `POST /api/evaluation/databases/{kb_id}/datasets/{dataset_id}/resume` 接口与前端"继续生成"按钮。修复生成器先收集后产出导致批量持久化在生成中途不生效的问题：改为 worker 产出即流式回报、消费端按 attempt_no 重排输出，异常或取消时已产出未落库的题目（含队列中未消费与 buffer 残余）一并保存；恢复接口改用原子化入队，消除并发恢复创建重复任务引发的唯一约束冲突。失败数据集支持查看已持久化题目：数据集详情接口状态限制放宽为 completed/failed 白名单，前端放开失败数据集的点击查看，下载与发起评估仍仅限生成完成。
- 优化 Agent 上下文压缩：Yuxi 的 DeepAgents summary adapter 在生成 summary 与写入 conversation history 时，会先对本次模型调用的临时消息视图执行 L1 结构精简，截断旧 `write_file`/`edit_file` 大参数，并把超过阈值的大 `ToolMessage.content` 写入 `outputs/large_tool_results` 后替换为路径和有限预览；L1 不修改 LangGraph state 原始消息，L1 后若上下文低于入口阈值的 40% 则直接调用模型，不生成 summary event，仍超过时才进入 L2 summary。L2 继续使用 DeepAgents `_summarization_event.cutoff_index` 重建 effective messages；Summary 阈值判断改为使用 Yuxi 自己的近似 token 计算结果，不再根据 provider `usage_metadata.total_tokens` 或 usage scaling 提前触发；首次写入 `conversation_history` 前读取旧文件的 sandbox 404 会按 `file_not_found` 处理，不再产生误导性 warning；`present_artifacts` 会拒绝展示 `large_tool_results` 与 `conversation_history` 等工具调用阶段文件。新增管理员可配置项 `summary_keep_messages`、`summary_prompt`、`summary_tool_result_token_limit` 与 `max_execution_steps`，分别控制摘要后保留消息数、摘要提示词、summary 阶段工具结果预览上限和 LangGraph `recursion_limit`。
- 收敛普通聊天模型加载链路：`select_model` 保留旧 `.call()` 调用契约，内部改为通过 LangChain chat model adapter 复用 Agent 侧模型加载器，统一 OpenAI-compatible、Anthropic 与 Gemini 等 provider 的运行时适配；移除旧 `OpenAIBase` wrapper，默认重试策略迁移为 LangChain provider 参数。
- 统一 Redis 客户端管理：新增 `yuxi.storage.redis` 作为 Redis 配置、短生命周期同步客户端、共享异步客户端与 ARQ RedisSettings 的唯一基础设施入口；运行队列、系统配置快照同步、模型缓存和 worker 不再各自散落读取 `REDIS_URL` 或直接创建 Redis 客户端，Redis 连接失败日志统一使用脱敏 URL。
- 新增系统配置 Redis 快照同步：管理员保存配置时仍以 `saves/config/base.toml` 作为唯一持久化来源，成功写入后将可运行时同步的公开配置字段写入 `yuxi:runtime_config`；API 与 worker 进程在启动时各拉起一个后台同步线程，按 5 秒间隔从快照刷新内存值，读取端按普通属性访问、无需感知，Redis 不可用时继续使用当前内存值。`save_dir` 是启动期内部路径配置，不在管理员配置中展示、不从 `base.toml` 读取、不写入 Redis 快照且不支持通过管理员配置接口修改；sandbox 相关配置仍属于启动期敏感配置，运行中的已初始化组件不承诺完整热更新，修改后仍需重启保证生效；移除已无运行时调用点的 `enable_reranker` 与 `default_agent_id` 配置字段。
- 优化 FastAPI 请求链路并发能力：Milvus 知识库检索中的同步 embedding、向量/BM25/混合检索调用，以及图谱查询中的同步 Milvus/Neo4j 读操作（含连接建立）统一通过有界 `asyncio.to_thread` 在线程中执行，避免阻塞 API 事件循环；并发上限按事件循环懒加载信号量控制，不改变检索默认行为与参数上限。
- 修复 AgentRun worker 在 LLM 流式响应期间长期占用 PostgreSQL 连接：chat 与 resume 在完成运行时解析、会话和附件等预处理后，进入流式执行前显式提交事务并归还业务连接，最终消息保存时再按需获取连接。
- 修复异步文档解析阻塞 API 事件循环：DOCX、PPTX、XLS/XLSX、DOC、CSV 与 HTML 的同步转换统一下沉到工作线程，文本读取改用异步文件 I/O；Docling 单例转换增加线程互斥，避免并发解析共享转换器，并补充事件循环可继续调度的回归测试。
- 改进 OpenAI 兼容提供商流式工具调用兼容（替代 v0.7.0 的按 provider 禁流式处理）：根因是 LangGraph v3 流式累积对 tool_call 字段“后值覆盖”，SiliconFlow、阿里云百炼等在参数续片里把 `name`/`id` 下发为空字符串覆盖首片真实值。改为 `_ToolCallChunkFixChatOpenAI` 把续片空串 `name`/`id` 归一化为 `None`，对所有 OpenAI 兼容 provider 通用生效且保留流式，移除原 `_NON_STREAMING_TOOL_CALL_PROVIDERS` 名单。
- 新增 Agent 评估运行入口：`POST /api/agent-invocation/eval/runs` 会创建正常对话与 AgentRun，复用 worker 执行链路，并以 `source=agent_evaluation` 与 `agent_invocation_meta.evaluation` 标记写入 conversation、AgentRun 输入消息与 Langfuse trace；接口阻塞至运行结束后直接返回最终结果（状态、最终 assistant 输出、Langfuse trace id），并支持通过 `include_trajectory_summary` 按需返回轻量工具调用轨迹摘要。`yuxi-cli` 新增 `yuxi agent eval` 命令，用于从 Langfuse 数据集读取输入并回传实验输出
- 对话消息点赞/点踩反馈接入 Langfuse score：本地 `MessageFeedback` 保存成功后，如助手消息已关联 Langfuse trace，则同步写入 `user-feedback` score，点赞为 `1`、点踩为 `0`，点踩原因写入 comment，便于在 Langfuse 中按用户反馈筛选 trace。
- 新增外部系统 Agent 调用入口：独立 `agent-invocation` router 提供 `POST /api/agent-invocation/agent-call/runs` 与 `POST /api/agent-invocation/agent-call/runs/result`，字段沿用 Yuxi 命名（`agent_slug/thread_id/request_id/model_spec`），复用 AgentRun 队列和结果读取能力；支持非流式同步等待或 `async_mode=true` 立即返回 `run_id`，Agent Call 不允许通过 `agent_call_meta.context` 覆盖 Agent context，运行时模型覆盖只允许走独立 `model_spec`；修复无 `thread_id` 且模型校验失败时提前提交空对话，导致孤儿对话和 `request_id` 失败重试非幂等的问题；Agent Call 的 `messages[].content` 兼容 OpenAI 风格的 `text`/`image_url` 多模态数组，纯文本数组不再误报 422，图片输入会保留原始 LangChain 多模态消息供 AgentRun worker 恢复；Agent Eval 与 Agent Call 统一通过 conversation-backed invocation helper 创建 run，后续定时任务等入口只需做请求解析和结果出口适配。
- 修复 Agent Invocation 创建的 eval/call 对话进入用户对话导航的问题：侧边栏最近对话与对话搜索会按 conversation metadata `source` 排除 `agent_evaluation` 与 `agent_call`，保留 run/conversation 持久化与结果追踪能力。
- 下沉 AgentRun 基础能力：将「读取某个 run 的最终结果」（`get_agent_run_result`/`load_agent_run_result`，含状态、最终 assistant 输出、Langfuse trace id 与错误）与「阻塞至 run 终结再取结果」（`await_agent_run_result`，复用有限事件流、无额外轮询）提升进 `agent_run_service`，供 chat/eval 及未来定时任务统一复用；eval 运行入口改为非流式复用该能力（不再做 SSE 封装），移除其私有结果构建逻辑（结果不变）。
- 重构 AgentRun 接口底座：`agent_run_service` 拆出内部 `create_agent_run`、`enqueue_agent_run` 与 `request_cancel_agent_run`，保留现有 `/api/agent/runs` 行为并新增 `/api/agent/runs/{run_id}/result` 结果读取接口；`AgentRunRepository` 增加按 `parent_agent_run_id` 查询 child run 的能力，为后续异步 subagent 生命周期控制预留统一入口。
- 修复子智能体流式事件兼容：Yuxi task middleware 的 DeepAgents 子智能体 transformer 改用专用 `yuxi_subagents` projection，避免与 LangChain `create_agent` 默认注册的 `subagents` projection 冲突导致运行流式消息时报错；子线程路由收集优先读取 Yuxi projection，并保留原 `subagents` fallback。
- 重构 AgentRun 与子智能体运行链路：保留现有 `/api/agent/runs` 行为并新增 `/api/agent/runs/{run_id}/result` 结果读取接口；子智能体新增 `subagent_start/status/cancel/await` 工具，支持后台启动、轻量进度查询、等待结果、取消运行和已完成 child thread 续跑；同一用户、同一子智能体、同一 conversation thread 存在运行中 run 时返回 busy，不做隐藏排队。
- 修复子智能体同步等待超时语义：`await_agent_run_result` 在有限 SSE 等待结束后会校验 run 终态，非终态时抛出明确等待超时；`task` 与 `subagent_await` 不再把仍在运行的子智能体误报为“已完成但无文本结果”，同步 Agent Call / Eval 入口遇到等待超时返回 504 和当前 run 快照。
- 收紧子智能体运行创建边界：`SubagentRunService` 显式拒绝以子智能体 run 作为父 run 创建新的子智能体，固化“不支持孙子智能体”的架构约束。
- 修复 AgentRun busy 检查的并发窗口：为同一用户、智能体和 conversation thread 的非终态 run 增加数据库部分唯一索引，并在插入冲突时返回现有 `run_busy` 结构，避免不同 `request_id` 并发启动绕过忙碌检查；AgentRun 创建冲突改用局部 savepoint 处理，避免 `_create_agent_run` 在共享 session 上 rollback 撤销调用方刚创建的子智能体线程关系或输入消息。
- 收敛 AgentRun 数据模型与输入语义：运行记录统一使用 `agent_slug`、`conversation_thread_id`、`created_by_run_id`、`input_message_id` 等字段，子智能体通过 `subagent_threads` 关系表维护 parent/child conversation 归属；补齐旧库升级时 `agent_runs` 旧字段到新字段、`subagent_threads.subagent_slug/created_by_run_id` 的静默回填与约束收敛，并在创建部分唯一索引前终结重复活跃 run，避免早期分支库保留 nullable schema 或历史重复活跃数据阻塞升级；Agent 状态中的 `subagent_runs` 改为以 `run_id` 作为执行身份，`resume` 请求字段明确为 `Command(resume=...)` 输入载荷。
- 精简旧链路与失败语义：恢复审批统一走 `POST /api/agent/runs` 的 `resume` 载荷，移除旧 `POST /api/chat/thread/{id}/resume` 流式接口和已废弃的 `chat_service.agent_chat`；子智能体运行缺少必要线程上下文时直接报错，状态查询只在真实缺失或无权访问时返回 404，内部运行记录格式异常返回 500。
- 统一流式事件线程 ID 提取契约：新增共享 `extract_thread_id` 工具，`BaseAgent`、聊天服务和 run worker 统一只读取规范化事件的一层稳定路径，并通过显式 fallback 处理父线程归属，避免递归扫描嵌套 metadata 导致父/子线程事件路由分歧。

## v0.7.0 (2026-06-13)

### 破坏性变更

- Provider 与模型配置收敛：移除旧版 v1 模型配置与 Ollama 支持，运行时模型统一使用 `provider_id:model_id` 与独立 provider 模块；自定义 provider 实现逻辑从文件移动到数据库，并从 config 文件迁移到 provider 模块。
- 智能体运行时语义收敛：用户可见的 `AgentConfig` 收敛为数据库持久化的一级 `Agent`，内置 Python Agent 改为智能体后端；聊天、运行任务、恢复审批和文件预览均从线程绑定的 Agent 解析运行时上下文，前端只提交 `agent_id`。
- 知识库能力边界收敛：移除 Upload 与 LightRAG 知识库/图谱能力，知识库类型收敛为 Milvus 与只读连接器；知识库 API 统一使用 `/databases/{kb_id}/xxx` 形式，并整合 mindmap / eval 等子接口。
- Agent 资源默认选择与权限过滤：未显式配置工具、知识库、MCP、Skills、子智能体时默认启用当前用户可访问/可用的全部资源，显式选择后按允许列表过滤；Agent 创建前统一完成最终资源权限过滤、知识库 `kb_id` 可见范围派生和 Skill prompt/readable 依赖闭包派生。
- Skill 安装与权限模型收敛：Skill 元数据使用 `source_type/share_config/enabled` 表达来源、生效范围与启用状态；内置 Skill 启动或同步时自动写入数据库并默认全局启用，上传和远程添加统一改为解析草稿后确认安装，不保留旧直接安装兼容路径。
- 历史兼容层精简：移除 sandbox provisioner `local` 后端别名、ask_user_question 单问题旧协议、JWT 历史默认密钥特殊判断、内置 Skill `SKILLS.md` 文件名回退、运行事件数字 seq 兼容和前端旧字段回退。
- 用户身份命名收敛：原业务登录标识统一改为 `uid`，Agent/LangGraph runtime、conversation、agent_run、sandbox 路径和前端用户态均使用字符串 `uid`；`user_id` 仅保留给外部响应中的数值 `users.id` 或真实外键场景。

### 开发记录

- 发布版本号更新至 `0.7.0`，同步 package、Docker 镜像标签与快速开始分支引用。
- 新增内置「深度研究」多智能体：编排器 Agent（`deep-research`，ChatbotAgent 后端）负责澄清、拆解、并行调度子智能体与综合成稿，配套两个子智能体 `research-explorer`（围绕单个子问题多轮检索网页/知识库并返回带引用发现）和 `fact-verifier`（对抗式核验关键论断、标注冲突与置信度）；完整研究方法论沉淀为新增内置 Skill `deep-research`（依赖 `tavily_search`），编排器运行时读取并据此调度。三者随 `lifespan` 启动通过 `AgentRepository.ensure_deep_research_agents` 幂等落库（已存在不覆盖管理员修改）。
- 新增内置 `general-purpose` 通用任务子智能体：使用 `SubAgentBackend` 与空运行配置，作为 `task` 工具的通用委派目标，由启动初始化自动写入数据库。
- 收敛 MCP 创建与编辑入口：前端移除整段配置文本入口和模式切换器，仅保留表单字段提交；后端 MCP 创建/更新请求拒绝额外配置字段，避免绕过表单约束。
- 调整内置 MCP 默认项：移除 `sequentialthinking` 的系统内置同步，启动同步时清理历史系统内置记录，保留用户手动创建的同名 MCP。
- 图片生成能力迁移为 Skill：Qwen-Image 从内置 Python 生成工具迁移到内置 Skill `image-gen`，模型调用与图片下载在 Agent 沙盒中完成，生成结果保存到 outputs 并通过 `present_artifacts` 展示，为多图片生成模型接入复用同一产物展示链路。
- 优化前端头像加载兜底：用户与智能体头像优先展示已配置图片，加载失败后回退到基于 ID 的 DiceBear 默认头像；离线或默认头像不可达时显示名称前两个字和稳定背景色。
- 降低知识库路由与工具模块复杂度：示例问题生成迁移到知识库 utils，文件上传统一 100 MB 限制，URL 预处理入库路径与旧 `content_type=url` 行为收敛，并修复 uid、导出 MIME 与异常透传等路由问题。
- 重构智能体配置语义：用户可见的 `AgentConfig` 收敛为数据库持久化的一级 `Agent`，内置 Python Agent 改为智能体后端；新增 `/api/agent` 管理与运行接口，聊天、运行任务、恢复审批和文件预览均从线程绑定的 Agent 解析运行时上下文，前端只提交 `agent_id`，并在模型配置页新增“智能体”管理页签。
- 删除 Upload 与 LightRAG 图谱/知识库能力：知识库类型收敛为 Milvus 与 Dify，只保留 Milvus 知识库内图谱构建/展示/检索，移除独立 `/graph` 页面和默认上传图谱工具。
- 收敛只读知识源连接器：新增 `ReadOnlyConnectors` 基类，Dify 改为声明自身创建参数与校验规则，新增 Notion Data Source 只读知识库并支持 Search/Find/Open；知识库类型接口返回创建参数 schema，前端新建表单按类型动态渲染非 Milvus 配置并统一保存到 `additional_params`。
- 新增知识库 Chunk 持久化：Milvus 知识库索引/更新流程会将 chunks 双写到 PostgreSQL `knowledge_chunks` 表与 Milvus，文件内容查看优先查询 PostgreSQL，并为位置信息、图谱实体关联、标签和抽取结果预留结构化字段；chunk 入库改为分批 embedding 与分批写入，避免大文件一次性写入触发 gRPC 消息大小限制；入库成功后将单文件 chunk 数与 token 数写入文件元数据，并将知识库级总 chunk 与总 token 汇总保存到 metadata，前端文件管理页展示该统计并支持一键修复历史文件缺失的统计值。
- 完善 Milvus 知识库图谱构建：修复 Chunk 图谱写入返回值、Neo4j 同步写入阻塞事件循环、重复构建任务竞态、图谱查询提前终止、Neo4j 连接复用、LLM 抽取超时重试和前端错误详情展示等问题；图谱构建会将 entity/triple 本体与 chunk 引用写入 PostgreSQL，并为唯一 entity/triple 建立 Milvus 语义索引，单文件删除时同步清理图谱引用和孤儿向量。
- 优化图谱抽取器配置：未配置时在图谱中心展示配置入口，抽取方案收敛为 LLM，前端仅保留“更多拓展中”占位；LLM 抽取器使用固定 Prompt + 自定义 Schema，并支持模型参数与并发队列数；已配置后允许修改参数并提示重置重抽风险。修复上传并入库新文件时旧内存 metadata 覆盖数据库图谱配置的问题。
- 新增 Milvus 图谱检索链路：Query 可召回图谱实体和三元组，结合 Chunk 命中实体构造 seed entity，读取 Neo4j 2-hop 子图后用 igraph 执行 PPR，最终以 Chunk 为产物并通过 RRF 与原 Chunk 召回融合；检索配置改为 dataclass 元数据生成，支持 `depend_on` 控制重排序和图检索参数展示。
- 收紧用户管理部门隔离：普通管理员创建用户时固定归属本部门，用户列表、访问选项、详情、更新和删除接口均限制在本部门范围内。
- 修复用户管理列表超过 100 人时被默认分页截断的问题：前端按 `skip/limit` 分批加载用户，并在用户卡片列表中补充分页渲染。
- 调整 Agent 资源默认选择与运行时上下文：未显式配置工具、知识库、MCP、Skills、子智能体时默认启用当前用户可访问/可用的全部资源，显式选择后按允许列表过滤；Agent 创建前统一完成最终资源权限过滤、知识库 `kb_id` 可见范围派生和 Skill prompt/readable 依赖闭包派生，聊天运行时与文件系统预览复用同一结果。
- 重构 Skills 权限与安装流程：Skill 增加 `source_type/share_config/enabled`，内置 Skill 作为启动同步入库的全局资源，不再保留前端安装/更新状态，支持启停但不允许删除；上传和远程添加统一为解析草稿后确认生效范围，安装 slug 优先读取 `SKILL.md` 的 `slug` 字段并保留 `name` 展示名，压缩包名称不参与 slug 校验；管理端支持编辑生效范围与启停；Agent 运行时按当前用户可访问 Skills 派生 prompt/readable 依赖闭包并限制挂载/激活，Skills prompt 改为模型请求级注入以避免污染 runtime context；主智能体恢复 `install_skill` 工具，允许当前用户安装私有 Skill 并激活当前会话，子智能体配置和运行态均禁用该工具。
- 精简历史兼容层：移除 sandbox provisioner `local` 后端别名、ask_user_question 单问题旧协议、JWT 历史默认密钥特殊判断、内置 Skill `SKILLS.md` 文件名回退、运行事件数字 seq 兼容和前端若干旧字段回退。
- 重构知识库共享权限：`share_config` 改为全局共享、部门共享、指定人可访问三档，部门共享必须包含当前用户部门，指定人可访问必须包含当前用户，并补充权限过滤测试。
- 移除知识库沙盒文件系统映射：不再通过 `/home/gem/kbs` 暴露知识库文件树，Agent 继续使用 `query_kb` 与 `open_kb_document` 访问知识库内容。
- 修复 MinerU 文档解析配置说明：文档处理指南原先指引启动 `openai-server`（30000 端口，仅提供 `/v1/chat/completions`），与解析器实际调用的 `/file_parse` 接口不匹配导致 `mineru_ocr` 不可用；更正为使用项目内置的 `mineru-api` 服务（30001 端口），并补充镜像构建与显存调优说明。
- 规范 Agent 知识库 Search/Find/Open 工具协议：`resource_id` 统一表示知识库 `kb_id`，Search 返回结构化 `resource_id/file_id/chunk` 结果，新增 `find_kb_document` 在已知文件内做关键词或正则定位，Open 默认窗口扩大到 1800 行。
- 收敛知识库分块配置：分块预设仅表达策略选择，通用分块参数统一通过 `chunk_parser_config` 传递；移除 `chunk_size`、`chunk_overlap`、`qa_separator` 等旧 root 字段兼容。
- 收敛知识库文件解析参数：文件级 `processing_params` 统一保存 `ocr_engine` 与 `ocr_engine_config`，解析阶段直接使用该结构并保留分块参数快照。
- 修复知识库文件大小显示为 0 的问题：文件上传时 `file_sizes` 参数未正确传播或历史数据缺失导致 DB 中 `file_size` 为 `None`；新增 `MinIOClient.stat_file/astat_file` 获取文件大小方法，`add_file_record` 在 `size` 缺失时从 MinIO 回补，`_load_metadata` 加载元数据后自动为缺少 `size` 的文件从 MinIO 补全并持久化。
- 优化评估基准自动生成：生成任务支持配置队列并发数，默认 10，范围 1-20。
- 完善模型供应商类型：普通聊天模型运行时新增 Anthropic provider type 适配，并清理不再支持的旧 provider type 入口。
- 重梳理知识库评估存储：评估数据集、题目、评估运行和逐题结果统一入库，JSONL 仅作为导入/导出格式；后端和前端 API 统一使用 dataset/run 语义；评估运行支持用户命名，历史记录按名称展示，综合评分只聚合检索指标。
- 扩展知识库上传来源：添加“从工作区上传”模式，后端将当前用户工作区文件预处理上传到 MinIO，前端沿用现有 `addDocuments` 入库链路提交 MinIO URL、内容哈希和文件大小。
- 重构知识库详情页布局：`DatabaseInfo` 改为顶部详情 header + 左侧功能 tab 侧边栏 + 右侧内容区，Milvus 默认进入文件管理，并将检索测试、知识图谱、知识导图、检索配置、RAG 评估和评估基准统一纳入侧边栏导航；只读连接器保留检索测试与检索配置。
- 整合知识导图接口：移除独立 mindmap router 与前端 API 模块，思维导图生成、查询和文件列表接口统一收敛到知识库 API 下。
- 收敛独立模型配置模块运行时：运行时 chat / embedding / rerank 均统一从 provider 模块与模型缓存读取 `provider_id:model_id`；旧版静态模型配置、v1 slash spec、旧模型列表接口和 Ollama 适配已移除；内置 provider 模板补充 XiaomiMiMo、XiaomiMiMo Token Plan CN 与 Kimi Code（`kimi-for-coding`）。
- 调整智能体模型配置默认值：`BaseContext.model` 默认保持为空，运行时按“请求模型 > 智能体配置模型 > 系统默认模型”解析；子智能体未配置模型时继承主智能体当前运行模型，避免把系统默认模型固化进每个智能体配置。
- 调整智能体配置归属与字段权限：`AgentConfig` 从部门共享改为按 `uid` 隔离，所有登录用户可管理自己的配置；`BaseContext` 支持字段级 `auth` 元数据，后端按用户角色过滤可见与可保存的配置项。
- 新增用户级沙盒环境变量：增加 `agent_envs` 表与 `/api/user/agent-env` 接口，设置面板支持当前用户维护 Agent 沙盒环境变量；创建新沙盒时与全局 `sandbox.env` 合并注入，用户变量优先。
- 收敛用户身份命名：原业务登录标识统一改为 `uid`，Agent/LangGraph runtime、conversation、agent_run、sandbox 路径和前端用户态均使用字符串 `uid`；`user_id` 仅保留给外部响应中的数值 `users.id` 或真实外键场景。
- 工作区知识库分类显示：知识库侧边栏按创建者分组为“我的知识库”和“共享知识库”，自己创建的知识库显示在“我的知识库”下，非自己创建的显示在“共享知识库”下；`knowledge_bases` 表新增 `created_by` 字段记录创建者 uid。
- 工作区文件上传支持多选：`/workspace/upload` 与 Viewer 工作区上传统一使用 `files` 多文件字段，一次最多上传 50 个文件，批量上传失败时清理本次已写入文件。
- 聊天附件新增 MinIO tmp 临时上传、可选 PDF/图片解析、确认后加入线程附件的流程；前端改为弹窗内上传、解析与确认。
- 修复智能体对话上传透明 PNG 后图片失真的问题：多模态图片处理在导出 RGB 前会先按白底合成 alpha 通道，避免透明像素中的隐藏颜色被直接转为可见像素；交付物预览优先按文件头识别 MIME，避免 `.jpg` 文件名包裹 PNG 内容时前端按错误格式加载；Agent run 输入消息会持久化为 `multimodal_image`，刷新历史后仍能显示用户上传图片。
- 优化智能体对话页细节：状态面板隐藏空 section，待办名称限制为 20 个中文汉字以内，模型选择器展示供应商名称，并收紧附件状态标签与文件编辑浮动操作样式；
- 标准化 Agent run/SSE 执行链路：run 创建时持久化输入消息并提交后入队，worker 统一写入 Redis Stream envelope，SSE 输出 `event/data/id`、心跳注释、`Last-Event-ID` 回放和终止 `end` 事件；前端强制使用 run API 并支持 ask_user_question 中断后以 resume run 恢复；事件 envelope 构造收敛到统一 helper，前端优先使用 envelope 一级 `thread_id` 路由。
- 修复 AgentRun 恢复与取消边界：无显式 `request_id` 的 resume run 改为按父 run 与恢复载荷派生稳定幂等 key，避免恢复重试创建重复 run；取消请求先提交数据库状态再发布 Redis 取消信号，避免 worker 在提交窗口内读到旧状态。
- Agent run SSE 新增 `verbose=false` 精简模式：默认仍返回完整事件载荷；精简模式仅在 SSE 输出前重建最小 payload，跳过 `metadata` 和空 `yuxi.agent_state`，将同一 data 内的 `request_id` 外提为单个字段，移除 chunk 中重复的 `meta`、`metadata`、`thread_id`、`response`、空 `namespace` 和图片 base64 等调试字段，保留消息增量、工具调用、工具结果、非空 Agent state、终止状态和 SSE 游标，前端订阅默认使用精简模式。
- 修复 SiliconFlow MiniMax 与阿里云百炼工具调用流式兼容：二者的 OpenAI 兼容流经 LangGraph v3 event stream 累积工具调用时会丢失关键字段（MiniMax 在参数增量 chunk 返回空 `function.name`，百炼丢失 `tool_call.id`），空值被写入 checkpoint 后会导致工具执行失败或工具结果无法按 `tool_call_id` 关联、工具状态永远停留在“进行中”；这两类提供商默认对工具调用禁用流式模型响应（正文回答仍流式），保留 LangGraph v3 运行事件并拿到完整 tool_call。该缺陷属 LangChain v3 流式协议上游问题（参见 langchain#37420、langchainjs#10937、langgraphjs#2496），截至 langchain-core 1.4.4 仍未修复，待上游修复后可移除对应提供商的禁流式处理。
- 收敛后端模块边界：文档解析从 `plugins.parser` 移动到 `knowledge.parser`，内容审查从 `plugins.guard` 移动到 `services.guard`。
- 收敛文件服务边界：文件预览判断抽为独立服务，Viewer 文件系统的 workspace 分支复用用户 workspace 服务，线程运行时上下文解析从泛化 `filesystem_service` 拆出为 agent runtime helper。
- 升级 DeepAgents 到 0.6.7 并适配新版文件系统协议：SubAgentMiddleware 改为显式 subagent spec，Skills prompt 补齐新版占位符；sandbox/skills backend 复用新版 `ReadResult`、`GlobResult`、`GrepResult` 等协议类型，文件权限在 backend 层明确区分 skills、uploads、outputs 与 workspace，保留最小 `CustomCompositeBackend` 以避免非 route glob 误扫其他 route；Agent 上下文压缩改为复用 DeepAgents SummarizationMiddleware，历史摘要与大工具结果统一 offload 到 outputs。
- 优化聊天输入 @ 文件提及：未创建 Thread 时可搜索用户 workspace，创建 Thread 后按当前对话文件优先、workspace 兜底的来源顺序搜索，并拆分 workspace/thread 缓存避免假 thread 与跨用户缓存污染；输入框与用户消息支持将 raw mention 渲染为带类型图标的引用单元，文件仅显示文件名且保留原始沙盒路径文本。
- 重构子智能体为 Agent-backed 形态：移除旧 `subagents` 表与 `/api/system/subagents` 管理链路，子智能体改为 `agents.is_subagent=true` 且使用 `SubAgentBackend`，创建/编辑统一走 Agent 管理入口；内置后端收敛为 `ChatbotAgent` 与 `SubAgentBackend`，Context 分为 `BaseContext`、`ChatBotContext` 与 `SubAgentContext`；主 Agent 通过 Yuxi task middleware 启动真实子 Agent graph，子智能体不再嵌套调用子智能体。沙盒挂载同步拆分为 child checkpoint thread、父对话 uploads/outputs、用户级 workspace 与子 Agent skills scope；主线程状态记录 `subagent_runs` 并在前端 task 工具中展示子智能体名称、执行状态、child thread 和产物，task 工具结果会暴露 child thread ID 且支持传回 `thread_id` 继续既有子智能体线程；子智能体执行复用 `agent_runs(run_type=subagent)` 记录父 run、child thread 与状态，child thread state 查询以 `agent_runs` 关系为准，不再解析 thread ID 反推父线程；真实流式 E2E 覆盖子智能体输出文件可由父线程文件/Viewer API 读取。流式链路参考 DeepAgents event streaming，后端将 LangGraph v3 raw event 归一化为 Yuxi semantic stream event，按父/子线程归属隔离 run SSE chunk，并支持通过 child thread state 拉取子智能体中间过程。
- 修正评估综合得分计算：`overall_score` 改为有答案准确率时取各题准确率平均，否则取各题 `recall@10` 平均，不再把 recall/f1/各 k 检索指标混合平均；历史已存运行不回填。
- 清理无效鉴权中间件：移除启动时未实际校验令牌的 `AuthMiddleware` 和公开路径残留判断，后端认证边界明确收敛到路由依赖；`/api/auth/me` 改为强制登录并补充未登录访问返回 401 的集成测试。

## v0.6.2 (2026-05-22)

### 新增

- 新增个人工作区预览与管理：提供独立于对话 thread 的用户级 workspace API，并增加“工作区”页面，用于浏览、预览、编辑、上传、下载、删除个人 workspace 文件；默认创建 `agents/AGENTS.md`，并在 Agent 执行时将其内容追加到系统提示词。
- 新增独立模型配置模块：增加 `model_providers` 表、独立管理接口和“模型配置”页面，支持 provider 基础信息、远端候选模型、enabled models 配置和手动添加模型能力。
- 新增远程 Skill 批量安装能力：后端新增 `install_remote_skills_batch()` 与 `POST /remote/install-batch`，前端补充批处理安装 API 和 UI 逻辑。

### 优化

- 下放扩展管理权限：普通管理员现在可进入扩展管理并完整管理 Tools、MCP、SubAgent、Skills；同步放开 Skill 管理接口权限并补充权限测试。
- 调整 Agent 知识库默认选择：未显式配置知识库时默认启用当前用户可访问的全部知识库，显式保存空列表仍表示不启用知识库。
- 优化评估基准自动生成：仅支持 commonrag/Milvus 知识库，默认参考 chunks 数量改为 1；多 chunk 场景复用知识库向量检索选择相似 chunks，不再对全量 chunks 重新计算 embedding。
- 优化 Agent 输入框文件 mention：用户级 workspace 文件候选改为从独立 workspace API 递归加载，不再依赖 active thread；插入时仍转换为 `/home/gem/user-data/workspace/` 沙盒虚拟路径。
- 调整知识库思维导图后端结构：将思维导图路由文件重命名为知识库语义更明确的 router，并把文件列表整理、提示词构建、AI JSON 解析等纯逻辑下沉到知识库 utils。
- 收敛知识库评估后端结构：将评估指标、单题评估、答案生成提示词和自动基准生成算法下沉到 `knowledge/eval`，`EvaluationService` 保留任务、文件和持久化编排职责。
- 扩展管理界面交互逻辑重构：MCP / Subagents / Skills 从“左侧边栏 + 右侧详情面板”调整为“卡片式网格布局 + 路由跳转二级页面”，工具标签页改为卡片网格布局 + 弹窗详情。
- 统一卡片样式：`ExtensionCard` 新增 `tags` prop 并复用于知识库列表页，知识库列表改用 `ExtensionCard` + `ExtensionCardGrid` 替代原有自定义卡片。
- 调整应用主导航：`AppLayout` 升级为默认展开的侧边栏，保留折叠态图标导航，并统一导航项、任务中心、GitHub、用户信息的图标与文字对齐。
- 合并智能体对话导航：移除 `AgentChatComponent` 内部聊天侧边栏，将新建对话入口和对话历史移动到 `AppLayout` 主侧边栏，并通过共享线程 store 统一管理。
- 统一前端 Markdown 预览渲染：新增共享 `MarkdownPreview` 组件与 `markdown_preview` 渲染工具，替换 Agent 消息、文件预览、知识库 chunk、任务工具结果、聊天导出等场景中的旧预览实现。

### 修复

- 修复聊天中普通用户 `@` 提及出不来技能和 MCP 列表的问题：放宽技能列表与 MCP 服务器列表读取接口至已登录用户，并对普通用户请求的 MCP 列表进行敏感连接参数脱敏。
- 修复知识库文档入库状态回退：当已解析文件缺失 `markdown_file` 解析产物时，索引流程会将文件状态恢复为未解析，便于重新解析。
- 修复附件上传后未立即刷新 mention 候选的问题。
- 加固 JWT 鉴权安全：移除历史默认密钥回退，初始化脚本支持生成并持久化 `JWT_SECRET_KEY` 与 `YUXI_INSTANCE_ID`，签发和验证令牌时校验 `iss/aud`，并拒绝已删除或登录锁定用户继续使用旧令牌访问系统。
- 修复模型配置路由请求模型未接收 `embedding_base_url` / `rerank_base_url` 导致前端已填写仍被后端校验拦截的问题。
- 修复知识库文档处理任务状态不一致问题：文件解析失败时任务中心正确显示"失败"而非"已完成"。

## v0.6.1 (2026-04-24)

### 新增

- 合并知识库导航入口：左侧导航仅保留"知识库"，文档知识库与图知识库在页面 header 中通过同一组轻量切换入口切换
- 抽象页面轻量切换 header：知识库与扩展管理页直接共用 `ViewSwitchHeader`，收敛文档知识库、知识图谱、Tools、MCP、Subagents、Skills 等入口的信息层级
- 调整任务中心交互：入口移动到 GitHub 按钮下方，并将右侧抽屉展示改为居中弹窗
- 将 `yuxi` 从 uv workspace 成员调整为 `backend/package` 下可独立构建的本地 Python 包，backend 通过 path dependency 以已安装包形式发现依赖
- 新增 Skills 远程安装能力：Skills 管理页支持填写 `owner/repo` 或 GitHub URL，后端通过隔离的临时 `HOME` 调用 `npx skills add` 下载指定 skill
- 调整部门删除语义：删除部门时不再要求用户数为 0，而是将部门下用户迁移到默认部门
- 扩展 viewer 工作区文件操作：`/home/gem/user-data/workspace` 支持从文件系统面板新建文件夹和上传文件
- 为历史线程补充前端本地配置变更提示：当已有历史消息的对话中切换 Agent、切换配置或编辑配置项时，插入非持久化的信息提示
- 调整 Worker run 模式下的消息首屏反馈：前端发送消息时先乐观渲染用户消息，再将前端生成的 `request_id` 透传给 `/api/chat/runs` 与服务端 `init` 对账
- 调整聊天首页的智能体切换入口：当智能体数量 `>= 4` 或内容区宽度小于 `380px` 时自动收敛为"当前智能体 + 下拉按钮"形式
- 调整智能体对话中的工具调用展示：连续工具调用默认折叠为"调用了 N 个工具"的轻量摘要
- 调整输入框配置入口与侧边栏头尾交互：输入区配置按钮改为轻量 dropdown 触发器

### 修复

- 修复沙盒 `workspace` 隔离粒度：宿主机目录从共享 `saves/threads/shared/workspace` 收敛为用户级 `saves/threads/shared/<user_id>/workspace`
- 收紧文件系统安全边界：viewer/chat 下载与删除路径统一基于解析后的真实路径做允许目录校验，阻止通过软链接逃逸工作区/线程目录
- 修复 OIDC 原始用户名绑定中的占位用户解析：解析目标用户 ID 时改为从右侧拆分，避免 `sub` 中包含冒号时把已绑定账号误判成冲突账号
- 修复 DOCX 解析中的图片回插顺序：Docling 导出的多个 `<!-- image -->` 占位符现在按文档图片顺序替换
- 修复前端依赖安全告警：通过 `pnpm.overrides` 将传递依赖 `flatted` 锁定到 `3.4.2`、`lodash-es` 锁定到 `4.18.1`
- 修复对话摘要中间件的工具结果卸载链路：摘要触发时改为将大体积 `ToolMessage` 写入当前 agent 可见的 sandbox outputs 路径
- 修复 agents 页对话侧边栏在 `keep-alive` 路由切换后的误关闭问题
- 调整 Milvus 混合检索实现：集合 schema 增加 BM25 稀疏向量字段、BM25 函数和中文 analyzer 配置
- 重构 MCP 运行时配置加载模型：移除 `MCP_SERVERS` 作为运行正确性前提的设计，改为每次直接从数据库读取最新 MCP 配置
- 为知识库检索工具补充 `metadata.filepath` 注入：在 `query_kb` 统一出口基于会话可见知识库构建 `file_id -> /home/gem/kbs/...` 映射并回填 Milvus 检索结果
- 移除知识库沙盒文件系统映射：Agent 不再通过 `/home/gem/kbs` 遍历知识库文件，继续通过 `query_kb` 和 `open_kb_document` 检索与打开文档。

## v0.6.0 (2026-04-01)


### 新增
- 重构后端代码 src -> backend/package/yuxi
- 重构文档解析，统一文档解析体验，并新增 Parser 类
- 新增 LITE 模式启动，启动时不加载知识库、知识图谱相关模块，可以使用 make up-lite 快捷启动
- 新增沙盒环境，详见后续文档更新，统一沙盒虚拟路径前缀默认值为 `/home/gem/user-data`
- 新增基于沙盒的文件系统，前端工作台可以查看文件系统，支持预览（文本、图片、PDF、HTML）、下载文件
- 新增 `present_artifacts` 内置工具：Agent 可将 `/home/gem/user-data/outputs/` 下的结果文件显式写入 LangGraph state 的 `artifacts` 字段，前端支持在输入框顶部以默认折叠的堆叠卡片展示本轮交付物文件，并保持可下载、可预览能力
- 交付物卡片新增“保存到工作区”能力：支持将单个交付物复制到共享目录 `workspace/saved_artifacts/`，并复用现有文件树/预览/mention 体系立即可见
- 新增基于沙盒的知识库只读映射，按“用户可访问知识库 ∩ 当前 Agent 已启用知识库”暴露原始文件与解析后的 Markdown
- 重构附件系统，直接集成在了沙盒文件系统中，附件上传后直接落盘到沙盒挂载目录
- 优化前端流式消息体验：新增通用 `useStreamSmoother` 调度层，统一平滑 Agent runs SSE、普通聊天流与审批恢复流中的 `loading` chunk
- 优化项目文档说明，并添加贡献指南
- 重构前端 Agent 路由结构，体验更加顺畅，切换更加自然（类 chatgpt 体验）
- 新增 API Key 认证功能，支持外部系统通过 API Key 调用系统服务
- 新增 subagents 的支持，支持在 web 中添加 subagents，以及两个内置的子智能体
- 新增内置Skills reporter，并移除内置 Agent reporter，数据库报表将由 Skills 完成
- 新增内置 Skills `deep-reporter`，用于指导生成科研报告、行业调研和其他深度分析类长报告
- 重构内置 Skills/MCP/Subagents 安装/添加/移除机制：内置 skill 支持按需安装、基于 `version + content_hash` 的更新提示与覆盖确认，不再使用服务器级开关切换
- 新增知识库 PDF、图片的预览功能
- 重构后端测试目录结构：按 `unit / integration / e2e` 分层迁移现有测试，拆分全局 `conftest.py`，统一测试入口为 `uv run --group test pytest`，并新增独立测试规范文档 `docs/develop-guides/testing-guidelines.md`
- 新增工具元数据 `config_guide` 字段：后端工具列表接口现在可返回“给人看的配置说明”，前端工具详情页会展示该说明，用于提示工具使用前需要配置的环境变量或入口；首批为 MySQL 工具和 `Qwen-Image` 补充了配置指引
- 补充 Langfuse 集成方案文档：明确采用“云端优先、先 tracing 后 feedback”的接入路径，并约定 Yuxi 的 `user/thread` 到 Langfuse `user_id/session_id` 的映射关系
- 新增面向用户的 Langfuse 集成文档：在“高级配置”分组中说明 Langfuse 的定位、能力、配置方式与查看路径，并与当前 `LANGFUSE_BASE_URL` 配置保持一致

<!-- 添加到这里 -->

### 修复

- 调整聊天首页的智能体切换入口：在无历史对话时，智能体数量 `<= 3` 且 `chat-main` 宽度不小于 `380px` 时继续使用横向 segmented；当智能体数量 `>= 4` 或内容区宽度小于 `380px` 时自动收敛为“当前智能体 + 下拉按钮”形式，避免多智能体或窄屏场景下入口被截断
- 发布前一致性修复：统一 0.6.0 版本号（backend/package/web）、更新 dev/prod 镜像标签语义（`0.6.0.dev` / `0.6.0`），并为 `/api/system/health` 补充 `version` 字段，提升部署可观测性与发版追溯能力
- 收敛“状态工作台”自动弹出规则：前端不再因为共享 `workspace` 或文件系统天然存在内容而默认展开，改为仅在 `/home/gem/user-data/uploads` 或 `/home/gem/user-data/outputs` 下检测到实际文件时自动弹出；手动打开、关闭、刷新和伸缩交互保持不变
- 调整智能体 todo 展示语义：待办状态不再作为 `capabilities` 前端开关，而是直接根据运行态 `agent_state.todos` 渲染；同时将 todo 入口从 Agent Panel 移到输入框内的轻量浮层，并让右侧“状态工作台”收敛为文件系统视图，输入框按钮文案同步由“状态”调整为“文件”
- 优化 Agent 输入框 mention 行为：在保留附件 mention 的同时，将共享 `workspace` 文件纳入候选范围；并将 `@` 空查询时的候选列表改为空，仅在继续输入后再执行筛选，避免工作区文件过多时直接铺满下拉面板
- 为前端工作台文件树补齐文件删除能力：`/api/viewer/filesystem/file` 新增删除接口，`AgentPanel` 文件节点新增删除按钮与确认交互，删除后会同步刷新树与预览状态
- 扩展 Agent Panel 状态工作台删除能力：继续复用 `DELETE /api/viewer/filesystem/file`，在保持接口不变的前提下支持删除文件夹；空目录与非空目录现在都会递归删除，`workspace` 下目录也可直接清理，前端目录节点同步新增删除入口与对应确认文案
- 调整前端工作台文件预览交互：恢复默认侧边/弹窗预览，并新增显式“全屏预览”入口；全屏模式下由预览内容直接覆盖整页，仅保留右上角悬浮关闭按钮；同时修复 HTML 文件首次在弹窗中预览偶现白屏的问题，改为在内容更新后强制重建 `iframe`
- 统一 Agent Panel 文件预览与消息区交付物预览组件：两处改为复用同一套 `AgentFilePreview` 预览实现，并为交付物预览补齐与工作台一致的“全屏预览”入口
- 修复交付物卡片展开后的长列表展示：当单轮交付物文件超过面板可见高度时，卡片内容区改为显示纵向滚动条，避免超过约 10 项后底部文件与操作按钮被裁切
- 兼容旧版已安装的内置 `reporter` 技能记录：`update_builtin_skill` 现在会识别由 `system` 或 `builtin-system` 管理的历史记录，避免更新时误报“技能 `reporter` 不是内置 skill”
- 调整沙盒 user-data 目录隔离策略：`workspace` 改为共享目录 `saves/threads/shared/workspace`，`uploads/outputs` 继续保持 thread 级隔离；同时更新 thread artifact 权限校验、viewer 文件系统列举逻辑，以及对应的 router/E2E 测试
- 重构聊天接口请求模型：流式与非流式聊天统一使用 `query + agent_config_id` 请求体，并移除路径中的 `agent_id`；同时修复非流式接口实际误走流式执行链路的问题，改为调用 `invoke_messages` 一次性执行，并补充对应测试
- 修复对话线程与 Agent 配置错位的问题：发送消息时将当前 `agent_config_id` 绑定到 thread 的 `extra_metadata`，线程列表接口返回该绑定值，前端切换历史 thread 时会自动恢复对应配置
- 为沙盒与 viewer 文件系统补齐知识库只读映射：新增 `/home/gem/kbs` 命名空间，按“用户可访问知识库 ∩ 当前 Agent 已启用知识库”暴露原始文件与解析后的 Markdown，并补充对应后端与 viewer 路由测试
- 优化 viewer 文件系统目录树加载：根目录与 `/home/gem/user-data` 改为直接读取本地线程挂载目录，不再为只读树视图触发 sandbox 冷启动，并补充对应后端测试
- 修复 `/home/gem/user-data` 根目录文件不可见的问题：根目录现在会同时展示 thread 目录下的真实文件和 `workspace` 入口，不再只保留固定命名空间目录
- 修复前端工具图标与渲染匹配不准确的问题：工具管理列表与工具调用结果统一改为基于工具 `id` 的精确映射，避免模糊匹配导致的误渲染，未命中的工具不再显示默认扳手图标
- 修复 GitHub Pages 文档部署工作流失败：移除 `actions/setup-node@v4` 对不存在 `docs/package-lock.json` 的缓存依赖，并将 `docs` 目录安装命令从 `npm ci` 调整为 `npm install`，避免因未提交锁文件导致 CI 在依赖缓存和安装阶段直接失败
- 修正沙盒 provisioner backend 命名与配置说明：统一对外使用 `docker` / `kubernetes`，保留 `local` 作为兼容别名；同步清理 compose 中未生效的 provisioner 环境变量、补齐 K8s 相关变量注释，并更新沙盒架构文档中的默认模式与 backend 描述
- 修复智能体配置列表接口在“无配置自动创建默认配置”路径下的参数缺失：补齐 `get_or_create_default` 的 `agent_id` 入参，避免 `/api/chat/agent/{agent_id}/configs` 返回 500
- 修复 LightRAG 同库写入并发导致的入库失败：为 `index_file` / `update_content` 增加按知识库维度的串行锁，并补齐 `documents` 接口 `auto_index` 阶段对最新解析状态的回写与回归测试，避免长时间入库任务进行中再次选择同库文件时直接并发写入报错

<!-- 添加到这里 -->


---


## v0.5

### 新增

- 优化 OCR 体验并新增对 Deepseek OCR 的支持
- 优化 RAG 检索，支持根据文件 pattern 来检索（Agentic Mode）
- 重构智能体对于“工具变更/模型变更”的处理逻辑，无需导入更复杂的中间件
- 重构知识库的 Agentic 配置逻辑，与 Tools 解耦
- 将工具与知识库解耦，在 context 中就完成解耦，虽然最终都是在 Agent 中的 get_tools 中获取
- 优化chunk逻辑，移除 QA 分割，集成到普通分块中，并优化可视化逻辑
- 重构知识库处理逻辑，分为 上传—解析—入库 三个阶段
- 重构 MCP 相关配置，使用数据库来控制 [#469](https://github.com/xerrors/Yuxi/pull/469)
- 使用 docling 解析 office 文件（docx/xlsx/pptx）
- 优化后端的依赖，减少镜像体积 [#428](https://github.com/xerrors/Yuxi/issues/428)
- 优化 liaghtrag 的知识库调用结果，提供 content/graph/both 多个选项
- 优化数据库查询工具，可通过设计环境变量添加描述，让模型更好的调用
- 优化任务组件，改用 postgresql 存储，并新增删除任务的接口
- 支持更多类型的文档源的导入功能（支持后端配置的白名单的 URL 导入）

### 修复

- 修复文件上传弹窗中 OCR 下拉选项展开时不会自动检查服务状态的问题
- 修复知识图谱上传的向量配置错误，并新增模型选择以及 batch size 选择
- 修复部分场景下获取工具列表报错 [#470](https://github.com/xerrors/Yuxi/pull/470)
- 修改方法备注信息 [#478](https://github.com/xerrors/Yuxi/pull/478)
- 修复多次 human-in-the-loop 的渲染解析问题 [#453](https://github.com/xerrors/Yuxi/issues/453) [#475](https://github.com/xerrors/Yuxi/pull/475)
- 修复沙盒后端接入回归：补齐 composite backend 的 `sandbox_backend` 参数、限制 `/api/sandbox/prepare` 仅允许访问当前用户线程、确保 `release()` 之后的 `destroy()` 会真正停止热池容器，并恢复 docker-compose 的完整模式默认值
- 重构沙盒为 deer-flow 风格的 AIO provider：切换为 thread-local sandbox、统一 `/home/gem/user-data/{workspace,uploads,outputs}` 固定路径、移除公开 `/api/sandbox/*` 生命周期接口，并补充 lite 模式下的 provider 生命周期、filesystem API 与 sandbox 复用/隔离 E2E 验证
- 调整聊天附件存储链路：线程附件改为直接落盘到 `saves/threads/<thread_id>/user-data/uploads`，解析成功后额外生成 `uploads/attachments/*.md`，不再依赖 MinIO 或显式上传到 sandbox
- 修复知识库文件列表包体异常膨胀：上传阶段不再把批次级 `content_hashes` 写入每个文件的 `processing_params`，并从数据库详情列表接口中移除该字段，改为按需读取单文件详情

## v0.4

### 新增
- 新增对于上传附件的智能体中间件，详见[文档](https://xerrors.github.io/Yuxi/advanced/agents-config.html#%E6%96%87%E4%BB%B6%E4%B8%8A%E4%BC%A0%E4%B8%AD%E9%97%B4%E4%BB%B6)
- 新增多模态模型支持（当前仅支持图片），详见[文档](https://xerrors.github.io/Yuxi/advanced/agents-config.html#%E5%A4%9A%E6%A8%A1%E6%80%81%E5%9B%BE%E7%89%87%E6%94%AF%E6%8C%81)
- 新建 DeepAgents 智能体（深度分析智能体），支持 todo，files 等渲染，支持文件的下载。
- 新增基于知识库文件生成思维导图功能（[#335](https://github.com/xerrors/Yuxi/pull/335#issuecomment-3530976425)）
- 新增基于知识库文件生成示例问题功能（[#335](https://github.com/xerrors/Yuxi/pull/335#issuecomment-3530976425)）
- 新增知识库支持文件夹/压缩包上传的功能（[#335](https://github.com/xerrors/Yuxi/pull/335#issuecomment-3530976425)）
- 新增自定义模型支持、新增 dashscope rerank/embeddings 模型的支持
- 新增文档解析的图片支持，已支持 MinerU Officical、Docs、Markdown Zip格式
- 新增暗色模式支持并调整整体 UI（[#343](https://github.com/xerrors/Yuxi/pull/343)）
- 新增知识库评估功能，支持导入评估基准或者自动构建评估基准（目前仅支持Milvus类型知识库）详见[文档](https://xerrors.github.io/Yuxi/intro/evaluation.html)
- 新增同名文件处理逻辑：遇到同名文件则在上传区域提示，是否删除旧文件
- 新增生产环境部署脚本，固定 python 依赖版本，提升部署稳定性
- 优化图谱可视化方式，统一图谱数据结构，统一使用基于 G6 的可视化方式，同时支持上传带属性的图谱文件，详见[文档](https://xerrors.github.io/Yuxi/intro/knowledge-base.html#_1-%E4%BB%A5%E4%B8%89%E5%85%83%E7%BB%84%E5%BD%A2%E5%BC%8F%E5%AF%BC%E5%85%A5)
- 优化 DBManager / ConversationManager，支持异步操作
- 优化 知识库详情页面，更加简洁清晰，增强文件下载功能

### 修复
- 修复 GitHub Actions 的 Ruff CI 在仓库根目录执行 `uv sync` 导致找不到 `backend/pyproject.toml` 的问题，同时统一检查路径为 `backend/package`
- 修复重排序模型实际未生效的问题
- 修复消息中断后消息消失的问题，并改善异常效果
- 修复当前版本如果调用结果为空的时候，工具调用状态会一直处于调用状态，尽管调用是成功的
- 修复检索配置实际未生效的问题
- 修复 sandbox 文件系统 `ls` 在异常输出下触发 `KeyError: 'path'` 的问题，并将工具调用异常降级为错误消息，避免直接中断聊天 stream
- 修复智能体状态面板中文件树仍依赖 `agent_state.files` 的问题，改为通过真实 `/api/filesystem/*` 接口按层懒加载后端可见文件系统，并让输入框下方状态按钮常态化打开工作区视图
- 为工作台新增 viewer-oriented filesystem service 与 `/api/viewer/filesystem/*` 接口，解耦 agent backend 语义，支持真实目录浏览、原始文件读取与下载
- 重写沙盒技术文档，明确 thread-local sandbox、viewer-oriented filesystem service、`/mnt` 命名空间、skills 可见性与当前实现边界，替换过时的 `/api/sandbox/*` 与 user-level 设计描述
- 收紧沙盒遗留代码：修复未注册 `sandbox_router` 中残留的 user/thread 参数错位，改进宿主机挂载路径映射逻辑，并为 remote sandbox provisioner 增加基础 URL 校验与销毁失败日志
- 修复 builtin skill 内容哈希计算对单文件使用 `read_bytes()` 的无上限内存读取问题，改为分块计算并补充回归测试

### 破坏性更新

- 移除 Chroma 的支持，当前版本标记为移除
- 移除模型配置预设的 TogetherAI


## v0.3
### Added
- 添加测试脚本，覆盖最常见的功能（已覆盖API）
- 新建 tasker 模块，用来管理所有的后台任务，UI 上使用侧边栏管理。Tasker 中获取历史任务的时候，仅获取 top100 个 task。
- 优化对文档信息的检索展示（检索结果页、详情页）
- 优化全局配置的管理模型，优化配置管理
- 支持 MinerU 2.5 的解析方法 <Badge type="info" text="0.3.5" />
- 修改现有的智能体Demo，并尽量将默认助手的特性兼容到 LangGraph 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 中
- 基于 create_agent 创建 SQL Viewer 智能体 <Badge type="info" text="0.3.5" />
- 优化 MCP 逻辑，支持 common + special 创建方式 <Badge type="info" text="0.3.5" />
- LightRAG 知识库应该可以支持修改 LLM

### Fixed
- 修复本地知识库的 metadata 和 向量数据库中不一致的情况。
- v1 版本的 LangGraph 的工具渲染有问题
- upload 接口会阻塞主进程
- LightRAG 知识库查看不了解析后的文本，偶然出现，未复现
- 智能体的加载状态有问题：（1）智能体加载没有动画；（2）切换对话和加载中，使用同一个loading状态。
- 前端工具调用渲染出现问题
- 当前 ReAct 智能体有消息顺序错乱的 bug，且不会默认调用工具
- 修复文件管理：（1）文件选择的时候会跨数据库；（2）文件校验会算上失败的文件；
