# 以局部可理解性降低接手成本

状态：proposed
类型：simplification
Owner：docs/develop-guides/spec-loop.md

读者是接手项目的工程师、维护者和 Reviewer。本文评估代码组织如何增加理解与修改成本，并提出优化顺序及评审规则；前置知识仅为常规 Web、数据库与异步任务开发。规则由 [Spec Loop](../../spec-loop.md)维护，运行事实仍归各业务模块。阅读入口和评审规则见[局部可理解性决定](../implemented/2026-11-17-local-understanding-rules.md)，知识库管理与删除见[管理边界决定](../implemented/2026-11-17-knowledge-management-boundaries.md)，聊天阅读见[阅读决定](../implemented/2026-11-17-thread-session-reading.md)。本提案保留尚未完成的方向，各切片的实施范围与证据由对应决定拥有。

## 问题

### 目标与证据范围

目标：让工程师从一个具体用户问题出发，定位身份与状态事实、找到负责业务决定的位置、识别提交与失败边界，并选出能证明修改正确的测试。非目标：读完全部代码、统一所有文件命名、减少文件总数、重写状态机、改变公开 API 或持久化格式。

评估基线为提交 `3c6ccd27cb591f7dc4d1eadfc01dd49fd98f5f46`，不包括原开发目录的未提交改动。源码抽样、调用搜索与文档核对为 `Inspected`；接手效率为 `Inferred`。没有进行新人计时实验，因此“局部修改需要 3～5 天、跨域修改需要 4～6 周”不能作为测量结论或排期依据。

在该基线下，后端 `modules` 的 Python 文件共 58,128 行，前端 `src` 的 JS、TS、Vue、CSS、LESS 共 104,593 行，均包含空行与注释。这些数字只辅助定位，不能量化接手难度。本提案深读知识库删除、Agent 共用边界、聊天页面与 runtime；Skill、OAuth 和分块器按入口及消费者抽样，不构成全库逐行审计。源码链接用于定位当前主干，本文判断以该固定基线为准。

### 从现象到本质

一次可靠修改的最小流程是：用户结果 → 业务身份与当前事实 → 决策及写入位置 → 失败与恢复 → 独立验证。接手成本来自寻找这些信息、翻译不同叫法、重建关系和排除错误修改位置。需要同时放在脑中的前提越多，局部修改越难。

事务、权限、lease、异步清理与多种生命周期是现有产品承诺带来的必要复杂度。额外成本主要来自三处：同一事实的命名缺少明确映射；同一个业务决定散落在多层；接口未封装前提，调用者必须了解实现顺序。缩短文件或增加转发层都不保证降低这些成本。

| 观察与源码依据 | 本质判断 | 对原建议的修正 |
| --- | --- | --- |
| [Session ORM](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/models/sessions.py) 的表是 `sessions`，业务标识是 `thread_id`，`agent_id` 保存 slug | 同一领域身份跨代码与契约采用不同叫法，注释“一对一”还可能暗示额外实体 | 优先澄清身份映射；不能直接规定所有新接口改成 `agent_slug`，否则同一公开契约出现两套词汇 |
| [删除用例](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/services/administration.py) 调用 manager；[manager](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/manager.py) 又调用 repository 或目录 service | 同一用例在 service、manager 间往返，能力判断、持久变更和统计刷新分散 | HTTP 删除已进入 service；应收敛该用例，不能把“所有 manager 禁用 repository”当作最终验收 |
| [thread_access](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/thread_access.py) 共同校验用户、APP 与状态；[queue_control](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/queue_control.py) 由多个生命周期用例调用 | 短文件承载真实共同规则，调用方拥有事务并不等于拥有这条规则 | 保留共同边界；`directory.py` 与 `runs.py` 也各有两个函数和不同消费者，不适合按单函数转发删除 |
| [SessionWorkspace](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/SessionWorkspace.vue) 共 4,649 行，包含草稿、文件预览缓存、导航、布局及运行视图回调；[runtime](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/model/sessionRuntime.js) 已拥有共享 Thread 数据与订阅 | 页面聚集不同生命周期，页面内部的状态与清理关系仍需整体阅读 | 沿已有状态 Owner 分离完整职责；不能把整段脚本搬进一个 composable，或把 UI 状态全部塞进共享 runtime |
| Skill 的 package、projection、resolved 被共享、个人、运行时以及其他业务域消费；OAuth 分为配置、协议、授权事务、HTTP 运行身份适配 | 产品分类不等于完整依赖边界；部分实现模块正隐藏安全与一致性前提 | 四类 Skill 可作为管理阅读入口，但不能吞并运行契约；OAuth 不因四个文件而合为一个 |
| router 引用 [presets](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/chunking/presets.py) 的预设选项与 ID 校验；[dispatcher](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/chunking/dispatcher.py) 装配具体 parser | 产品配置与算法实现需要区分 | 没有发现 router/manager 直接依赖 parser 类；Milvus 对内部 `count_tokens` 的引用才是一个具体待核对接缝 |
| 域内重复 `models.py`、`service.py`；模型供应商域同时命名为 `modules/models` | 文件 basename 重复与领域概念冲突是两种问题 | 域内约定名称可以保留；`modules/models` 同时包含 chat/embed/rerank 调用适配与 providers 子域，整体改为 model_providers 会缩窄名称语义，暂保留 |

机制入口已有[按任务阅读地图](../../../mechanisms/index.md)，无需再复制一套架构总览。[决策生命周期](../README.md)明确区分 proposed、implemented、archived；不能把所有决策统一标成档案。接手时从当前机制进入，仅在修改对应取舍时阅读有关 decision。

## 提案

### 实现方案

剩余工作沿[前端重构提案](./2026-09-30-agent-view-frontend-refactor.md)推进：SessionWorkspace 仍拥有输入编辑、附件准备、审批与队列命令、混合面板导航和布局。下一切片先核对现有 sessionInput 与持久草稿能力能否闭合输入职责，再决定是否提取组件；不能把剩余脚本整体搬进巨型 composable。

其他模块只在具体维护任务证明仍需跨层重建前提时调整。Agent 共同授权和事务边界、Skill 运行契约、OAuth 安全边界及供应商域名称均按真实消费者保留，不以机械执行最初建议为完成标准。已实施职责与验证只由开头链接的决定及[文件预览决定](../implemented/2026-11-17-file-preview-session.md)维护。

### 退出条件

对一个真实修改任务，维护者能从入口解释事实写入、失败与释放边界，并选择最小有效测试。新增边界必须消除调用者的具体前提，连同被替代状态和入口一起删除；状态、回调、生命周期、测试装配和文档维护的净成本按 [Spec Loop](../../spec-loop.md#局部可理解性)审查。行数和文件数不单独作为收益。

## 替代方案

| 选择 | 收益 | 代价与决定 |
| --- | --- | --- |
| keep：保留实现，仅补术语与导航 | 风险小，能消除部分找不到入口的问题 | 无法消除知识库用例往返和页面混合生命周期；适合作为起点 |
| narrow：按真实用例收敛现有职责 | 逐个切片减少需要同时理解的前提，能复用测试与已有方案 | 需要跟踪每个消费者及失败语义；优先采用 |
| replace：统一 facade、四类 Skill 容器、单一 OAuth 模块或通用任务模型 | 入口表面变少 | 不能证明认知依赖减少，可能隐藏或合并不同安全与状态边界；不采用 |
| remove：批量删短文件、全库重命名 | 目录或名称可迅速统一 | 会破坏已有共享边界并扩大修改面；仅对已证明无独立语义和兼容消费者的局部对象采用 |

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 输入职责迁移减少页面前提 | 仅把页面状态搬进另一大文件，增加回调协调 | sessionInput / 后续输入组件 | 对照原提案验收输入、草稿与切换行为，并审查被删除的页面职责 | 隐藏、换 Thread、迟到接收、失败重试仍闭合；不得复制输入事实 | Not run |
| 理解成本降低且修改位置正确 | 文件变短但仍需理解所有内部状态 | 相关源码与机制入口 | 记录首次正确定位用时、无关跳转、错误修改位置与遗漏前提 | 只读新入口无法解释写入与清理时判定拆分未达标 | Not run |

认知成本实验选两个任务：解释“文件已删除但外部清理尚未完成”的证据位置；定位“切换 Thread 后收到旧请求结果”的写入与释放边界。要求接手者交付调用图、准确修改位置和最小验证集，维护者按当前源码核对。单人前后对比受熟悉度影响，可用等难度任务或不同接手者交叉核对；没有新人实验，不能宣称耗时改善，也不把阅读时长做成 CI 分数。

文档验证使用 `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`、`cd docs && pnpm run build` 和链接目标检查。独立源码审查不能替代产品运行证据。

旧能力不存在：每个后续切片须删除已替代的页面状态或转发入口，搜索覆盖生产、测试和文档；既有授权、事务、恢复及公开契约保留。

重新引入条件：明确的新消费者或独立生命周期无法由现有契约表达时重新评估，不因文件变长恢复转发或平行状态。

## 风险

静态搜索只能证明已检查的调用与职责，无法证明接手耗时或产品缺陷。原开发目录的未提交改动可能影响 Skill、MCP 等诊断，实施前须在目标分支重新核对。必要的抽象可以降低理解成本；过度内联会复制规则，过度拆分会增加隐含共享状态，两者都由具体任务验收裁决。

知识库搬迁可能改变统计刷新失败、部分成功、锁序和清理时机；前端搬迁可能改变草稿、订阅及异步清理时序。文档构建与 unit 均不能替代这些运行证据。每个实现切片独立验证和审查，不把整份提案合成一个跨域重构提交。
