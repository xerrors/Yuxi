# 文档信息架构与机制详解分层

状态：archived
类型：process
Owner：docs/AGENTS.md

## 问题

Yuxi 的用户教程、配置参考、运行机制、开发治理和历史材料没有形成稳定分层。部分页面同时解释使用步骤、内部调用链、环境变量、安全边界和排障方法，导致读者无法按知识前提渐进阅读，同一事实也容易在 `ARCHITECTURE.md`、Agent 文档和高级配置中重复或漂移。沙盒、上下文压缩和知识库已有重要实现，但缺少以当前源码为依据、明确状态与失败语义、链接真实 Owner 的机制页；现有说明还存在权限范围和配置建议不够精确的问题。

## 决策

文档按“入门教程 → 配置与操作参考 → 机制详解 → 开发治理 → 决策与事故”组织。教程提供有顺序、可观察结果的学习路径，参考页集中当前可查询事实；一个页面出现实质性混合职责时拆分。`docs/AGENTS.md` 保存进入 `docs/` 工作时必须执行的短指令，完整写作标准由[文档编写规范](../../../../documentation-guidelines.md)拥有，其他页面只链接、不复制规则。

站点设立“机制详解”导航层，覆盖沙盒、Summary 上下文压缩和知识库。机制页从高层组件关系进入真实装配链，再说明状态、持久化或文件 Owner、权限与隔离、失败语义、可观测结果和源码/测试入口。沙盒旧页只承担配置与运维职责；知识库入门页只承担首次成功路径，高级参考集中共享权限、管理 API、知识导图和知识图谱运维。

Yuxi 采用 DSH 的“一事实一层级 Owner”、tutorial/reference 分类、current-state prose、slop checklist、预算与机械校验原则，不引入中英文配对和 TypeScript 文档类型检查。正式文档优先用肯定句陈述职责、边界与结论；工程检查拒绝依靠否定和转折制造强调的对举句式。Zread/DeepWiki 的渐进阅读路径、父子页面层级、系统图、代码实体映射和页面源码入口用于表达方式参考；Yuxi 当前源码、配置、数据约束和测试仍是事实来源。

## 替代方案

- 保持现有目录，只扩写三份旧文档：改动更少，但会继续让配置、教程和内部机制互相挤占页面职责，无法解决重复与漂移。
- 完全复制 DSH 的目录、双语配对、生成目录和全部 gate：规则成熟，但与 Yuxi 的 Python/Vue 技术栈、单语文档规模和现有 VitePress 站点不匹配，会引入没有当前 consumer 的维护表面。
- 只依赖 Zread/DeepWiki 自动生成仓库说明：适合发现缺口和建立阅读地图，但索引会滞后，也不能替代权限、事务、持久化和失败语义的源码 Owner 与项目 Review。

## 后果

- 机制页比入门页更接近实现，代码演进时必须同步对应 owning page；页面只描述稳定链路并提供源码入口，不手工枚举容易漂移的完整 API 或文件清单。
- 现有公开路径保持不变并收窄职责，新模块承接内部机制，高级参考承接管理与集成事实，从而避免因大范围移动破坏外部链接。
- 图示只表达正文明确说明的关系，条件分支、状态和失败语义仍由正文与源码 Owner 定义。
- VitePress 构建只能证明 Markdown、导航和链接有效，不能证明机制语义准确；文档变更仍需独立 Reviewer 对照源码、测试和配置。
- Yuxi 维持单语文档，不承担 DSH 双语同步与 TypeScript 文档类型检查的维护成本。

## 验证

| 主张 | 证据 | 结果 |
|---|---|---|
| Agent 能判断文档层级、教程/参考类型、事实 Owner 与验证方式 | 独立 Reviewer 对照完整需求、DSH 规则和 Yuxi 源码审查；`python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts` | 工程信任检查扫描 196 份文档并通过；59 个 verifier unit 通过，其中负向测试覆盖对举连接词变体、列表与引用容器的显式或隐式围栏边界，并允许历史状态迁移说明；独立 Reviewer 未发现 P0–P2 问题 |
| 站点提供渐进导航，机制页和知识库高级参考可访问 | `cd docs && pnpm run build` | 通过；保留现有 `env` lexer、Rolldown 插件和 chunk size 警告 |
| 沙盒配置与内部机制分离，路径、身份、变量映射和 secret 边界与实现一致 | 对照 sandbox backend、provider、Compose 与 Kubernetes 配置；运行相关 sandbox/provider/viewer unit | 101 个测试通过，2 个现有警告；因工作树缺少 provisioner token，未运行 Docker/Kubernetes 真实集成 |
| Summary 机制与知识库教程、权限、Tasker 和工具链路符合当前实现 | 对照 middleware、graph、router、manager、repository、Web store 与上传组件；运行 Summary、Tasker 和知识库权限相关 unit | 相关测试通过；未运行真实模型 Summary 和 Milvus、MinIO、Neo4j、Dify、Notion 集成 |
| Langfuse 配置、trace 映射和反馈同步说明符合当前实现 | 对照 `langfuse_service.py`、`feedback_service.py` 与对应 unit | 8 个测试通过，1 个现有 SQLAlchemy 警告 |
| 补丁没有空白错误 | `git diff --check` | 通过 |
