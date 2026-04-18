# 版本变更记录

本页用于记录各版本发布说明（新增、修复与破坏性变更）。

## v0.6.1

### 修复

- 修复聊天附件状态同步链路：上传文件转换出的 Markdown 现在会同步写入 LangGraph `files/uploads` 状态，附件不再只显示在文件树中，智能体也能在对话执行时稳定感知并读取附件。
- 修复生产部署中的 sandbox provisioner 挂载配置错误：正式环境改为挂载当前项目实际 `saves` 目录，避免附件 Markdown 已生成但 sandbox 内不可见，导致 `read_file('/home/gem/user-data/uploads/attachments/*.md')` 返回 404。
- 补充附件读取回归测试：新增附件状态同步、聊天接口和端到端验证，覆盖“上传 office 文件 -> 转换 Markdown -> 智能体读取附件”的完整链路。

## v0.6 (2026-04-01)


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
- 重构后端测试目录结构：按 `unit / integration / e2e` 分层迁移现有测试，拆分全局 `conftest.py`，统一测试入口为 `uv run --group test pytest`，并新增独立测试规范文档 `docs/vibe/testing-guidelines.md`
- 新增工具元数据 `config_guide` 字段：后端工具列表接口现在可返回“给人看的配置说明”，前端工具详情页会展示该说明，用于提示工具使用前需要配置的环境变量或入口；首批为 MySQL 工具和 `Qwen-Image` 补充了配置指引
- 补充 Langfuse 集成方案文档：明确采用“云端优先、先 tracing 后 feedback”的接入路径，并约定 Yuxi 的 `user/thread` 到 Langfuse `user_id/session_id` 的映射关系
- 新增面向用户的 Langfuse 集成文档：在“智能体开发”分组中说明 Langfuse 的定位、能力、配置方式与查看路径，并与当前 `LANGFUSE_BASE_URL` 配置保持一致

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
