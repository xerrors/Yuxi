# 开发路线图

路线图可能会经常变更，如果有强烈的建议，可以在 [issue](https://github.com/xerrors/Yuxi/issues) 中提。

日志添加规范（For Agent）:

- 同一版本的多次功能更新时，应以功能为单位进行更新，比如之前添加了 A 功能的更新，在后续的更新中修复了因 A 功能引入的 bug，那么这个修复说明应该和 A 功能描述放在一起，而不是新增一条修复记录，功能更新同理。


### 看板

- Langfuse 增加 self-host 模式支持，补齐私有化部署与配置说明（已支持 cloud，待调试）
- 检索测试中，添加问答
- 集成 Memory，基于 deepagents 的文件后端实现，需要考虑定位
- Yuxi-cli 相关的功能，放在后续版本中实现（不是类似于编程助手，而是管理平台的工，等各个 router 接口优化之后）
- 完善测试基准自动生成功能，目前的实现过于简单，无法覆盖实际需求
- 完善 Skills 的环境变量注入
- 拓宽检索的知识源，统一多知识源（channel），目前已知知识库/知识图谱/网页，可拓展：个人知识库、数据库、历史对话等
    - 前置任务，多知识库并行检索（扩展 query_kb）
    - 新增 query_keywords 工具，专门用于基于关键词命中的排序，也结合词频（和 BM25 的区别？）
- 参考 AgenticRAG 方案扩展当前 Search 工具：基于知识库工具返回的 resource_id/file_id 改进 Search 返回递增文件序列 ID，完善 Find 与 Open 能力；Summary 暂缓
- 评估，基于 Agent 的评估，这里应该是结合 Langfuse 实现

### Bugs
- 目前的知识库的图片存在公开访问风险

### BREAKING CHANGE（不兼容变更，0.7 版本再实现）
- 将自定义provider 的实现逻辑，从文件移动到数据库中，并将相关处理代码，移出 config 文件，放到 provider 模块中
- 已补充方案文档：`docs/vibe/2026-04-18-custom-provider-db-refactor-plan.md`，明确采用“provider 一行、models 放 JSON、移除 provider 默认模型”的落地方案
- 优化知识库的 API 接口设计，使用 /{db_id}/xxx 的形式，整合 mindmap / eval 接口
- 移除 v1 版本的 provider 统一接口，改为 v2 版本的 provider 模块接口



## 版本记录

### 0.6.2 开发记录

<!-- 0.6.2 的内容请放在这里 -->
- 重构输入框 `@` 提及药丸（Mention Pills）视觉系统与交互体验：移除强硬色块底色与粗边框，采用高雅通透的 Borderless Card（无界/轻量卡片）极简皮肤。通过字重（550）与各实体高饱和度专属主题色提供卓越的视觉锚点；文件类型左侧自动解析后缀名并渲染三色微发光的 CSS 迷你语法高亮代码行，其他类型统一升级为 `1.8` 极细描边精致 SVG 线框；引入 `height: 22px` 物理高度锁定与 line-height 控制，对图标及删除键做像素级（Pixel-perfect）垂直对齐纠偏，彻底解决浏览器亚像素舍入错位；删除按钮采用绝对定位并配套 `padding-right` 呼吸动效，实现 Hover 动态向右撑开交互，彻底避免空间冗余与排版抖动；在文本层 `.pill-text` 增加 `padding-right: 4px` 极窄弹性渲染缓冲槽，彻底解决因 `overflow: hidden` 做长文本省略截断时导致英文斜向或非平衡字形字母（如 `.py` 的尾字母 `y`）最右边缘像素被物理裁剪缺角的微小视觉缺陷。
- 建立全新「无界全局文件预览系统」：彻底消除了点击药丸和交付件（Artifacts）必须强制弹出左侧工作台面板的繁重交互，解耦并设计了自包含的高内聚全局单例弹窗组件 `AgentFilePreviewModal.vue`；通过 Pinia Store 建立响应式的全局预览触发信号源，实现了多触发端（药丸点击、交付件卡片预览、窄屏文件树节点选中）的极致 DRY 重构，彻底清洗并删除了 `AgentChatComponent.vue`、`AgentArtifactsCard.vue` 与 `AgentPanel.vue` 中总计几百行重复的私有 Modal 节点、API 调用逻辑与 Blob 内存销毁代码；将 `parseDownloadFilename` 文件名解析函数抽取至公共工具包 `file_utils.js`，达成极致的前端代码重构去重。
- 封堵双 Watcher 冲突 Bug 与性能守护：为 `AgentPanel.vue` 中的全局预览 Watcher 新增了 `useInlinePreview.value` 内联拦截守卫，彻底解决了宽屏模式下点击药丸产生双倍 API 重复调用的严重缺陷，完美隔离了宽窄屏下各自的预览高亮界限。
- 下放扩展管理权限：普通管理员现在可进入扩展管理并完整管理 Tools、MCP、SubAgent、Skills；同步放开 Skill 管理接口权限并补充权限测试。
- 调整 Agent 知识库默认选择：未显式配置知识库时默认启用当前用户可访问的全部知识库，显式保存空列表仍表示不启用知识库。
- 移除知识库沙盒文件系统映射：不再通过 `/home/gem/kbs` 暴露知识库文件树，Agent 继续使用 `query_kb` 与 `open_kb_document` 访问知识库内容。
- 优化评估基准自动生成：仅支持 commonrag/Milvus 知识库，默认参考 chunks 数量改为 1；多 chunk 场景复用知识库向量检索选择相似 chunks，不再对全量 chunks 重新计算 embedding，并移除前端 Embedding 模型选择。
- 修复知识库文档入库状态回退：当已解析文件缺失 `markdown_file` 解析产物时，索引流程会将文件状态恢复为未解析，便于重新解析而不是停留在索引失败。
- 优化 `@` 文件 mention 候选搜索与药丸插入：前端废弃全量递归遍历，改为后端 `/api/mention/search` 接口 + Redis `ormsgpack` 二进制缓存（TTL 60s，上限 10 万条）；扫描加宽度/深度/黑名单三重剪枝防卡死；搜索结果按文件名/前缀/路径加权排序，最多返回 50 条；前端加防抖（250ms）+ `AbortController` 防竞态，高亮渲染使用 DOMPurify 防 XSS；修复了当焦点聚焦在文本框内 `@` 字符或光标发生位移时，误触发重复搜索的性能开销，在 `insertMention` 首部加入物理熔断，并建立 `mentionPopupVisible` 的统一重置 watch 机制以精准控制生命周期和缓存熔断；在 `handleKeyUp` 中增加方向键及 Home/End 键在文本中移动光标时的自适应检测，实现与鼠标点击完全相同的交互一致性。
- 调整知识库思维导图后端结构：将思维导图路由文件重命名为知识库语义更明确的 router，并把文件列表整理、提示词构建、AI JSON 解析等纯逻辑下沉到知识库 utils。
- 收敛知识库评估后端结构：将评估指标、单题评估、答案生成提示词和自动基准生成算法下沉到 `knowledge/eval`，`EvaluationService` 保留任务、文件和持久化编排职责。
- 新增个人工作区预览与管理：提供独立于对话 thread 的用户级 workspace API，并增加“工作区”页面，用于浏览个人 workspace 文件、预览 Markdown/文本/代码/图片/PDF；支持新建文件夹、上传文件、下载文件、删除文件/文件夹和多选删除；工作区预览支持 Markdown/TXT 在右侧预览框内切换编辑并保存，其他格式和非工作区预览默认只读；知识库与团队空间入口先展示到占位层级；默认创建 `agents/AGENTS.md`，并在 Agent 执行时将其内容追加到系统提示词。
- 加固 JWT 鉴权安全：移除历史默认密钥回退，初始化脚本支持生成并持久化 `JWT_SECRET_KEY` 与 `YUXI_INSTANCE_ID`，签发和验证令牌时校验 `iss/aud`，并在鉴权阶段拒绝已删除或登录锁定用户继续使用旧令牌访问系统。
- 扩展管理界面交互逻辑重构：将 MCP / Subagents / Skills 三个标签页从「左侧边栏 + 右侧详情面板」布局重构为「卡片式网格布局 + 路由跳转二级页面」布局，工具标签页改为卡片网格布局 + 弹窗详情（保持弹窗内容不变）。新增共享组件 `ExtensionCard`、`ExtensionCardGrid`、`ExtensionToolbar`、`ExtensionDetailLayout`，详情页（`McpDetailView`、`SubagentDetailView`、`SkillDetailView`）使用居中宽度限制，路由规划为 `/extensions/mcp/:name`、`/extensions/subagent/:name`、`/extensions/skill/:slug`。
- 统一卡片样式：`ExtensionCard` 新增 `tags` prop 支持传入 `[{label, color}]` 数组，内部使用 `<a-tag bordered=false size=small>` 渲染，与知识库卡片标签风格统一；知识库列表页 `DataBaseView` 改用 `ExtensionCard` + `ExtensionCardGrid` 替代原有自定义卡片，移除冗余 card 样式。
- 调整应用主导航：`AppLayout` 从默认窄栏升级为默认展开的侧边栏，保留折叠态图标导航；侧边栏样式收敛为 14px 文本 + 18px 图标的标准紧凑密度，并统一导航项、任务中心、GitHub、用户信息的图标与文字对齐。折叠态改为仅通过显式按钮展开，避免空白区域误触发。
- 合并智能体对话导航：移除 `AgentChatComponent` 内部聊天侧边栏，将新建对话入口和对话历史移动到 `AppLayout` 主侧边栏，并通过共享线程 store 统一管理历史列表、当前线程、重命名、删除、置顶和分页加载。
- 新增独立模型配置模块：增加 `model_providers` 表、独立管理接口和”模型配置”页面，支持 provider 基础信息、可配置模型列表端点、远端候选模型、`enabled_models` 的早期配置验证；启动时会补齐内置 provider 模板，`provider_type` 暂统一默认为 `openai`，该模块暂不接入现有运行时模型选择逻辑。远端模型加载默认使用 `/models` 获取 chat/通用模型，provider 声明 `embedding` 能力时使用 `/embeddings/models` 获取 embedding 候选，rerank 模型列表端点按供应商文档显式配置后加载；修复路由请求模型未接收 `embedding_base_url`/`rerank_base_url` 导致前端已填写仍被后端校验拦截的问题。补充手动添加模型能力：`enabled_models[i]` 新增可选 `source: "manual"|"remote"` 字段（默认 `remote`），管理员可通过”+ 手动添加”入口录入远端清单未覆盖的模型（典型：自部署 embedding/rerank），手动模型在前端跳过”远端不存在”的 stale 警告并显示「手动」标签；type 选项受 `provider.capabilities` 约束，后端在 `_normalize_payload` 与 `update_provider_config` 双层一致性校验中拦截越权写入。
- 统一前端 Markdown 预览渲染：新增共享 `MarkdownPreview` 组件与 `markdown_preview` 渲染工具，替换 Agent 消息、文件预览、知识库 chunk、任务工具结果、聊天导出等场景中的旧 `md-editor-v3/marked` 预览；支持 KaTeX、任务列表、frontmatter 卡片、Shiki 代码高亮、DOMPurify 清洗和浅层渲染缓存，并抽取 HTML 转义与代码语言归一化工具。Skill 详情页复用 `AgentFilePreview`，统一文件预览、编辑、保存和全屏交互。
- 优化远程 Skill 批量安装：`remote_skill_install_service.py` 新增 `install_remote_skills_batch()`，利用 `npx skills add --skill A --skill B --skill C` 原生多 skill 支持，将安装 N 个 skill 的仓库克隆次数从 2N 降至 1；配套新增路由 `POST /remote/install-batch`、前端 `installRemoteSkillsBatch()` API 方法和批处理 UI 逻辑

---

历史版本发布记录已迁移到 [版本变更记录](./changelog.md)。

维护说明：
- roadmap 仅保留未来规划（看板/Bugs/里程碑方向）。
- 具体版本发布内容统一维护在 changelog。
