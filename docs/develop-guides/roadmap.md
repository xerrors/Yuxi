# 开发路线图

路线图可能会经常变更，如果有强烈的建议，可以在 [issue](https://github.com/xerrors/Yuxi/issues) 中提。

项目看板（Maintainer Only）：[GitHub Project](https://github.com/users/xerrors/projects/2)

日志添加规范（For Agent）:


### 看板

**知识库**
- [ ] 知识库工具接口化与 CLI 集成：将当前知识库的主要工具（如上传、检索、建索引等）封装为后端 API 接口，并集成到 Yuxi CLI 工具中
- [ ] 知识库 Mindmap 扩展：新增基于文件名的文件“边”构建，支持聚类算法形成社区节点，并提供思维导图 (Mindmap) 可视化结构展示
- [ ] 知识库工具新增 query_keywords 工具，专门用于基于关键词命中的排序 <Badge text="v0.7.1" />
- [ ] 增强知识库检索体验：增强 metadata、标签等
- [ ] 个人工作区增加可检索能力（但是不做向量化） <Badge text="v0.7.1" />
- [ ] 新增基于 PaddleOCR 的解析器：接入 PaddleOCR-VL-1.6、PP-OCRv6、PP-StructureV3，并抽象共用基类复用相似的脚本调用、产物收集和配置处理


**智能体**
- [ ] 修复不同用户安装相同 Skill 时，因目前 Skill slug 全局唯一导致无法安装、会自动新增 versiontag 的问题，排查其对安装流程 and 版本管理的影响
- [x] 子智能体缺少异步的机制 <Badge text="v0.7.1" />
- [ ] 子智能体缺少 steer 机制 <Badge text="v0.7.1" />
- [ ] 子智能体的双向通信，缺少 ask_for_main_agent 的机制
- [ ] 子智能体与子智能体的通信机制
- [x] 优化 Agent `read_file` 工具：至少对齐 DeepAgents 的读取行为 <Badge text="v0.7.1" />
- [ ] Skill 详情页增强绑定能力展示：内置 Skill 也应清晰展示只读的工具/MCP/Skill 依赖说明
- [x] 添加 Agent 独立调用接口，方便后续评估使用
- [ ] 任务队列：调研是否可以通过修改 state 实现，添加一个中间件（after agents 钩子），并支持通过 after model 触发的引导模式。 <Badge text="v0.7.2" />
- [x] 反馈接入到 Langfuse

**其他**
- [x] 历史对话新增搜索能力（[#790](https://github.com/xerrors/Yuxi/issues/790)）
- [x] 消息中的代码块增加快速复制按钮（[#790](https://github.com/xerrors/Yuxi/issues/790)）
- [x] 集成用户级 Memory，基于 DeepAgents 文件后端在主智能体中按需维护长期记忆
- [x] 优化 Task 模块定位：区分真正的后台任务实体与进度条管理工具，重新定义任务中心/Tasker 的职责边界
- [x] 模型供应商类型继续补齐非 OpenAI 兼容适配，并清理不再支持的 provider type 字样 <Badge text="v0.7.1" />
- [ ] 优化 Agent 向用户追问交互：支持较长文本回答输入，并在流式输出时保持聊天区跟随最新内容（[#753](https://github.com/xerrors/Yuxi/issues/753)）

**仅设想**
- [ ] Yuxi CLI 更多管理命令，放在后续版本中实现（不是类似于编程助手，而是管理平台工具，等各个 router 接口优化之后）


### Bugs
- [ ] 目前的知识库的图片存在公开访问风险
- [ ] 点开对话的时候要能够自动定位到尾部，而不是最开始。

---

历史版本发布记录已迁移到 [版本变更记录](./changelog.md)。

维护说明：
- roadmap 仅保留未来规划（看板/Bugs/里程碑方向）。
- 具体版本发布内容统一维护在 changelog。
