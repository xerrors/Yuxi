# Yuxi Agent 开发约定

Yuxi 是基于 LangGraph、FastAPI、Vue 和多种持久化服务构建的知识库与多智能体平台。Docker Compose 是开发拓扑的事实来源；用符号搜索确认模块的真实实现。

## 按任务范围加载

- 始终以本文件为准；修改 `backend/`、`frontend/` 或 `docs/` 时，同时读取该子树的 `AGENTS.md`。子树规则只补充本目录，不复制回根文件。
- 改一个文案、修一个小 bug、机械重命名或等价清理：只读根与适用子树指令及直接相关源码或文档；架构和开发指南按下一条的任务条件加载。
- 跨模块设计或实现、改变状态/权限/持久化/外部副作用，或需要选择架构方案：先读 [ARCHITECTURE.md](ARCHITECTURE.md)；非平凡变更再读 [Yuxi Spec Loop](docs/develop-guides/spec-loop.md) 和[工程信任系统](docs/develop-guides/engineering-trust.md)，涉及测试时读[测试规范](docs/develop-guides/testing-guidelines.md)。
- 提交或创建 PR 前读取[贡献指南](docs/develop-guides/contributing.md)。只有同时运行多个分支、复用长期数据或处理 Schema 不兼容时，才读取[并行工作树与隔离运行环境](docs/develop-guides/parallel-worktree-environments.md)。
- 用户在当前任务中的明确要求优先于本文件。

## 任务与决策

1. 开始实现前，把请求压缩为可验证的目标、非目标和验收主张；多步任务先给出每步带验证方式的简短计划。
2. 设计前先从原始需求描述最小完整流程，区分用户明确要求、已有系统约束和自行添加的保证，并检查现有能力能否直接完成。按能力的业务语义确定归属，不能仅因某个调用者使用它就归入该调用者模块。新增机制须说明：删除它会破坏哪个真实需求、消费者或已有承诺；无法说明时，先尝试复用或删除。复用接口或新增条件分支前，按[语义边界检查](docs/develop-guides/spec-loop.md#接口复用与语义边界)核对事实 Owner、接口承诺、调用者专属规则和其他调用路径。
3. 假设显式记录后继续；只有当不同解释会改变验收结果、数据、安全或外部状态时才阻塞询问，并把候选解释一并列出而不是默默选择其一。存在明显更简单的方案时，先说明取舍再动手。
4. 把"可以""也可以""类似这样""例如"当作简单方向，不是设计更大机制、配置项或兼容层的许可。
5. 在真实语义 Owner 处闭合受影响的工程主张：源码、数据约束或当前契约拥有事实；独立 oracle 与负向案例证明它；实际 workflow 或可问责 Reviewer 产生拒绝后果。不要建立可独立编辑的中央主张清单或 claim ID 体系。
6. 非平凡变更必须新增或更新一份 tracked [决策记录](docs/develop-guides/decisions/README.md)。记录问题、当前决定、真实替代项、后果和验证，不保存推理流水账或实现过程叙事。
7. `docs/vibe/` 只用于本地临时计划，被 Git 忽略，不是组织记忆，也不能作为已完成事实的唯一来源。
8. 只修改验收标准需要的范围；不顺手重构、格式化或添加想象中的配置、兼容层和扩展点。

## 不能破坏的系统事实

- HTTP 路由保持薄；用例流程属于 各业务域的 `services`，持久化查询属于 各业务域的 `repositories`。
- 普通输入先在 PostgreSQL 中持久化 Input 原始消息和 Receipt；消费事务创建正式 Message 并绑定 Turn/Run、附件，提交后才投递 ARQ。Redis 负责投递、短期事件、取消和缓存，不拥有最终业务状态。
- 每次提交拥有独立 Input；同一用户、APP、Agent、Thread 的 follow-up 按 FIFO 单条派发，steer 优先并在消费时合并连续就绪前缀，运行中在安全边界接管。等待回答或审批时禁止普通消息；协作等待只接收 FIFO 输入。取消与转引导只操作指定 pending Input。
- Turn 的最终输出必须来自 result_run_id 指向的顶层 Run；Run 的输出、事件、artifact 和错误绑定同一 Turn/Run，禁止从相邻 Run 猜测结果。
- 非终态 Run 必须有明确执行 Owner、lease/heartbeat 或等价机制，以及崩溃后的可观察结局。
- `/api/system/health` 只表达进程 liveness；接流量前置条件由 `/api/system/ready` 证明，业务正确性仍由真实链路测试证明。
- LangGraph checkpoint 只使用 PostgreSQL；API、worker 与 Agent 不提供本地后端选择或静默降级。
- 权限在后端依赖与 repository 可见性查询处最终执行；前端守卫、prompt、schema omission 和 UI 隐藏不是授权边界。
- 默认部署包含知识库、图谱与评估能力；显式关闭 Knowledge 时，启动、路由、能力发现与 worker 装配保持一致，保留代码和数据。Agent 知识库访问经 MCP，后端复核当前用户权限与会话选择交集；附件 parser 按需加载。
- 沙盒虚拟路径、对象 URL 和宿主机路径不可混用；所有用户路径必须在 owning filesystem boundary 校验。

## 证据规则

- Agent 的完成报告、HTTP 200、日志关键词或 mock 调用次数都不是最终事实；重新读取数据库、文件、对象、DOM 或协议结果。
- 每个新 guard 都要有一个能恢复目标缺陷并使其在正确原因上失败的负向测试。
- 测试从最小相关集合开始，再按风险扩大；不要用 unit 结果替代真实 PostgreSQL、真实 HTTP、worker 或浏览器语义。
- Expected output、snapshot 和 fixture 只能显式更新并审阅；CI 不得一边生成 oracle 一边验证 oracle。
- 实际执行命令、结果和未执行原因写入 PR；未验证不能写成通过。

代码变更提交前在仓库根目录至少运行：

```bash
python3 scripts/ci/verify_engineering_contracts.py
python3 -m unittest scripts.ci.test_verify_engineering_contracts
docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"
```

仅文档或导航改动按下表执行最低证据，不要求运行后端 unit；其他改动按表选择并升级。不要用低层级结果替代高风险语义。

| 改动面 | 最低证据 | 何时升级 |
|---|---|---|
| 纯 Python / JS 逻辑 | 相关 unit，断言业务结果 | 触及数据库、缓存或文件副作用时补 integration |
| API / 权限 / 持久化 | 真实 HTTP integration | 跨 worker、队列或用户主链路时补 E2E |
| Run / FIFO / SSE / 沙盒 / 恢复 | E2E，验证最终状态与产物 | 依赖外部可选服务时记录环境与未验证范围 |
| 前端交互 | lint + unit（[frontend/AGENTS.md](frontend/AGENTS.md)） | 行为关键时补 build 与真实页面验证 |
| 文档与导航 | 相对链接检查 + docs build | 公开行为变化时与代码验证一起执行 |

完整命令由 [测试规范](docs/develop-guides/testing-guidelines.md) 维护。

## 实现与安全

- 使用满足验收标准的最小、线性实现。抽象、状态机、fallback、兼容路径和依赖都要有当前 consumer 与 Owner；仓库外用户、持久数据和部署承诺也算 consumer。
- 实现明显长于问题本身，或一半行数可以清楚表达同样行为时，先简化再提交；自检"高级工程师是否会认为这段代码过度设计、过度防御或过于零碎"。
- 不可信输入只在 parser、配置、模型/tool JSON、持久化、worker、process、wire 和用户路径等真实信任边界校验；授权与隔离在产生副作用的 executor/repository fail-closed，直接或替代调用路径都不能绕过。
- 预设条件不成立时显式失败。可选能力可以降级，但必须结构化、可观察，且不把未就绪伪装为成功。
- 新增函数/类使用简洁中文 docstring；注释解释非显然约束、时序、Owner 和安全用法，较长处理段可在段首用一句话概括职责与阶段，不逐行翻译代码。
- 不输出或提交 `.env`、账号、Token、用户数据、运行目录和构建产物。
- 文件以一个换行结尾；提交前运行 `git diff --check`。

## Review 与交付

所有代码变更在 commit 前必须由不继承开发上下文的全新 Reviewer Agent 审查完整需求、diff、测试和规范；修复所有影响功能、边界、证据可信度或认知负担的问题。提交信息使用中文 Conventional Commit，PR 使用仓库模板，以自然语言列出受影响主张、语义 Owner、证据、风险和未验证范围，不引用中央 claim ID。

Reviewer 先依据原始需求审查方案的必要性、职责归属和可复用的现有能力，再审查实现正确性；允许否定或简化整个方案。测试通过只能证明被验证的行为，不能证明新增机制有必要；测试要求的额外保证也必须能追溯到真实需求或已有契约。
