# 开发路线图

路线图可能会经常变更，如果有强烈的建议，可以在 [issue](https://github.com/xerrors/Yuxi/issues) 中提。

日志添加规范（For Agent）:

- 同一版本的多次功能更新时，应以功能为单位进行更新，比如之前添加了 A 功能的更新，在后续的更新中修复了因 A 功能引入的 bug，那么这个修复说明应该和 A 功能描述放在一起，而不是新增一条修复记录，功能更新同理。


### 看板

- Langfuse 增加 self-host 模式支持，补齐私有化部署与配置说明（已支持 cloud，待调试）
- 检索测试中，添加问答
- 集成 Memory，基于 deepagents 的文件后端实现，需要考虑定位
- 添加自定义向量模型和 rerank 模型的配置，在网页上面 `0.7`
- Yuxi-cli 相关的功能，放在后续版本中实现（不是类似于编程助手，而是管理平台的工，等各个 router 接口优化之后）
- 完善个人知识库（仅设想，欢迎讨论）

### Bugs
- 目前的知识库的图片存在公开访问风险
- 生成基准测试会把所有的向量都计算一遍不合理
- 登录失败分支调用了不存在的 `increment_failed_login()`，当前密码错误场景会直接抛出属性错误，尚未修复

### BREAKING CHANGE（不兼容变更，0.7 版本再实现）
- 将自定义provider 的实现逻辑，从文件移动到数据库中，并将相关处理代码，移出 config 文件，放到 provider 模块中
- 优化知识库的 API 接口设计，使用 /{db_id}/xxx 的形式，整合 mindmap / eval 接口



## 版本记录

### 0.6.1

<!-- 0.6.1 的内容请放在这里 -->
- 合并知识库导航入口：左侧导航仅保留“知识库”，文档知识库与图知识库在页面 header 中通过同一组轻量切换入口切换，保留原有列表与图谱内容区交互。
- 抽象页面轻量切换 header：知识库与扩展管理页直接共用 `ViewSwitchHeader`，通过统一切换样式和 actions slot 收敛文档知识库、知识图谱、Tools、MCP、Subagents、Skills 等入口的信息层级；扩展管理各列表的刷新入口下沉到搜索框右侧，并统一搜索框与工具按钮的边框和圆角，同时强化切换项选中态的边框、阴影与字重层级，提升 header 中当前视图的辨识度。
- 调整任务中心交互：入口移动到 GitHub 按钮下方，并将右侧抽屉展示改为居中弹窗，减少对主页面布局的占用。
- 调整 backend Python 工作区依赖边界：将 `backend/package/yuxi` 明确为承载核心运行依赖的业务包，根 `backend/pyproject.toml` 仅保留工作区入口与开发/测试配置，减少依赖职责混淆。
- 将 `yuxi` 从 uv workspace 成员调整为 `backend/package` 下可独立构建的本地 Python 包，backend 通过 path dependency 以已安装包形式发现依赖，移除对 `PYTHONPATH=/app/package` 的运行时耦合。
- 修复沙盒 `workspace` 隔离粒度：宿主机目录从共享 `saves/threads/shared/workspace` 收敛为用户级 `saves/threads/shared/<user_id>/workspace`，并同步传递 `user_id` 到 sandbox 路径解析、provisioner 挂载与 viewer/chat 测试，保证同用户跨线程共享、不同用户隔离。
- 调整输入框 `@` 提及中的文件搜索交互：无查询内容时不再直接展示文件列表，改为提示“输入相关内容以搜索文件”，避免未过滤结果干扰选择。
- 修复聊天附件状态同步：上传文件转换出的 markdown 现在会同步写入 LangGraph `files/uploads` 状态，避免附件只显示在文件树里、但智能体状态中缺失导致 `read_file` 无法稳定读取。
- 收紧文件系统安全边界：viewer/chat 下载与删除路径统一基于解析后的真实路径做允许目录校验，阻止通过软链接逃逸工作区/线程目录；同时将密码哈希默认实现升级为 Argon2，并移除 skill frontmatter 解析中的正则回溯风险。
- 调整 Skills 导入能力：`/api/system/skills/import` 现在除 ZIP 外也支持直接上传单个 `SKILL.md`，前端上传入口与后端导入服务同步兼容，便于快速导入单文件技能
- 扩展 viewer 工作区文件操作：`/home/gem/user-data/workspace` 支持从文件系统面板新建文件夹和上传文件，后端限制写入范围并保持同名冲突直接报错。
- 优化文件系统面板目录展开反馈：异步加载子文件时在对应文件夹图标位置显示 loading 状态，避免点击后无视觉响应。
- 新增 Skills 远程安装能力：Skills 管理页支持填写 `owner/repo` 或 GitHub URL，后端通过隔离的临时 `HOME` 调用 `npx skills add` 下载指定 skill，再复用现有导入链路写入 `saves/skills` 和数据库，避免将 `~/.agents/skills` 直接作为系统主存储；前端远程安装弹窗补充多选串行安装与批量进度展示，复用现有单 skill 安装接口逐个提交请求
- 调整部门删除语义：删除部门时不再要求用户数为 0，而是将部门下用户迁移到默认部门，同时清理部门级配置和部门 API Key，保证测试部门、撤换部门等场景可直接删除，并补充对应集成测试覆盖该链路
- 重构 MCP 运行时配置加载模型：移除 `MCP_SERVERS` 作为运行正确性前提的设计，改为每次直接从数据库读取最新 MCP 配置，并用 `server_name:config_hash` 作为本地工具缓存 key；同时将内置 MCP 初始化职责收敛为仅同步数据库默认项，前端 MCP 选项改为直接使用实时资源列表，解决 `api`/`worker` 分进程下的配置不一致与缓存失效问题
- 为知识库检索工具补充 `metadata.filepath` 注入：在 `query_kb` 统一出口基于会话可见知识库构建 `file_id -> /home/gem/kbs/...` 映射并回填 Milvus 检索结果，注入逻辑复用知识库只读后端命名规则；路径注入仅作用于 Milvus chunks 列表，Dify 和 LightRAG 等其他知识库保持原检索结果返回，不再兼容无显式 `file_id` 的推断注入，新增单测覆盖该约束
- 调整 Milvus 混合检索实现：集合 schema 增加 Milvus 内置 BM25 稀疏向量字段、BM25 函数和中文 analyzer 配置，`keyword` 模式改为 BM25 全文检索，`hybrid` 模式改为 Milvus 原生向量 + BM25 混合检索，并同步更新检索参数说明。
- 修复 DOCX 解析中的图片回插顺序：Docling 导出的多个 `<!-- image -->` 占位符现在按文档图片顺序替换，避免多图文档中的图片链接前后颠倒。
- 修复前端依赖安全告警：通过 `pnpm.overrides` 将传递依赖 `flatted` 锁定到 `3.4.2`、`lodash-es` 锁定到 `4.18.1`，并同步更新 `pnpm-lock.yaml` 以消除 DriftGuard 报告的高危 CVE
- 重写界面设计规范：参考 `DESIGN.md` 写法补充视觉气质、颜色 token、组件状态、布局层级、响应式与 Agent Prompt Guide，并基于该规范收敛首页视觉表现，移除装饰性渐变、重阴影、hover 位移和入场动画。
- 修复对话摘要中间件的工具结果卸载链路：摘要触发时改为将大体积 `ToolMessage` 写入当前 agent 可见的 sandbox outputs 路径，修正 `summary_offload` 路径拼接错误、`messages` 触发条件下不会真正裁剪历史的问题，并避免将 system message 重复纳入摘要与最终消息列表；补充对应单元测试覆盖。
- 调整智能体对话中的工具调用展示：连续工具调用默认折叠为“调用了 N 个工具”的轻量摘要，展开后改为弱化时间线样式，减少工具结果卡片对正文阅读节奏的干扰。
- 调整聊天首页的智能体切换入口：在无历史对话时，智能体数量 `<= 3` 且 `chat-main` 宽度不小于 `380px` 时继续使用横向 segmented；当智能体数量 `>= 4` 或内容区宽度小于 `380px` 时自动收敛为“当前智能体 + 下拉按钮”形式。
- 调整对话配置入口与侧边栏头尾交互：输入区配置按钮改为轻量 dropdown 触发器，默认保持透明、hover 才出现背景；下拉中直接提供配置切换、新建配置，以及查看/编辑当前配置的入口。配置侧边栏则移除头部配置切换和新建入口，改为仅显示当前配置名称、可点击星标和关闭图标，并将删除操作下沉到底部保存按钮右侧。

---

历史版本发布记录已迁移到 [版本变更记录](./changelog.md)。

维护说明：
- roadmap 仅保留未来规划（看板/Bugs/里程碑方向）。
- 具体版本发布内容统一维护在 changelog。
