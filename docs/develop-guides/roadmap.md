# 开发路线图

路线图可能会经常变更，如果有强烈的建议，可以在 [issue](https://github.com/xerrors/Yuxi/issues) 中提。

日志添加规范（For Agent）:


### 看板

**知识库**
- [ ] office 组件预览，docx/pptx 可以转PDF，然后前端预览 <Badge text="v0.7.1" />
- [ ] 知识库工具新增 query_keywords 工具，专门用于基于关键词命中的排序 <Badge text="v0.7.1" />
- [ ] 调研将当前知识库映射为虚拟文件系统的可行性，先明确文件树映射、权限边界、内容读取与 Agent 工具调用形态，再决定是否实现
- [ ] 增强知识库检索体验：增强 metadata、标签等
- [ ] 新增基于 PaddleOCR 的解析器：接入 PaddleOCR-VL-1.6、PP-OCRv6、PP-StructureV3，并抽象共用基类复用相似的脚本调用、产物收集和配置处理
- [x] 优化思维导图构建的接口设计，支持增量构建和更新
- [ ] 个人工作区增加可检索能力（但是不做向量化） <Badge text="v0.7.1" />
- [ ] Yuxi CLI 支持 `yuxi kb upload <DIR_PATH|FILE_PATH>`，将本地文件上传、解析并添加到已有知识库 Badge text="v0.7.1" /> <Badge type="warning" text="开发中" />


**智能体**
- [ ] 子智能体缺少异步的机制 <Badge text="v0.7.1" />
- [ ] 子智能体缺少 steer 机制 <Badge text="v0.7.1" />
- [ ] 子智能体的双向通信，缺少 ask_for_main_agent 的机制
- [ ] 子智能体与子智能体的通信机制
- [ ] 如何停掉一个子智能体、查看智能体的进度
- [ ] 优化 Agent `read_file` 工具：至少对齐 DeepAgents 的读取行为
- [ ] RAG 评估支持 Agent 模式 <Badge text="v0.7.1" />
- [ ] 添加 Agent 独立调用接口，方便后续评估使用
- [ ] 任务队列 <Badge text="v0.7.2" />

**并发与分布式**
- [ ] 提高 FastAPI 的并发能力

**其他**
- [ ] 历史对话新增分组（或者叫做项目）
- [ ] 集成 Memory，基于 deepagents 的文件后端实现，需要考虑定位
- [ ] 优化 Task 模块定位：区分真正的后台任务实体与进度条管理工具，重新定义任务中心/Tasker 的职责边界
- [ ] 模型供应商类型继续补齐非 OpenAI 兼容适配，并清理不再支持的 provider type 字样 <Badge text="v0.7.1" />
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
