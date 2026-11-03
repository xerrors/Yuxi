# Yuxi Spec Loop

Yuxi Spec Loop 把非平凡工程请求从“实现建议”收敛为“可以被反证、审查和长期维护的仓库事实”。它复用现有语义 Owner、decision、测试、workflow 和独立 Review，不建立平行 notes、中央 claim ID 或手工状态清单。

## 适用范围

开始工作时把请求压缩为：可验证目标、非目标、显式假设、任务类型和风险层级。任务类型只使用 `feature`、`bug-fix`、`simplification`、`architecture`、`process`、`testing`。

满足任一条件即为非平凡（substantial）：改变持久状态或事务发布点；改变权限、隔离、外部副作用或公开兼容；改变 Run/worker/队列/恢复等长生命周期；改变模型可见输入；引入抽象、依赖、配置、fallback、状态机或长期维护表面；接受未来可能重开的非显然取舍。非平凡工作必须在实现前创建 tracked `proposed` decision。

局部文案、机械重命名和不改变行为的等价清理可视为 trivial。小而完整、在同一变更中已经生效且没有未来提案阶段的修复，可以直接写 `implemented`，但 PR 必须说明为什么没有需要先裁决的替代方案或风险。是否 trivial 属于语义 Review；不得用 diff 大小、文件数量或静态 heuristic 代替判断。

## 八阶段闭环

### 1. Scope / classify

先写 solution-independent problem：描述失败的外部结果、观察边界和约束，不预设类名、表名或框架。明确 goal、non-goal、assumption、task class、受影响 Owner 与最低证据等级。

### 2. Reconstruct authority

按以下顺序重建当前权威，发现冲突时先确认哪个材料拥有当前事实：

1. 根与子树 `AGENTS.md`、`ARCHITECTURE.md` 和安全不变量；
2. 当前公开契约、真实 provider/registration/composition、持久化与用户入口；
3. 可执行测试、workflow、构建产物和运行时探针；
4. active decision；
5. history、archive、changelog 与 `docs/vibe/` 临时材料。

后序材料不能静默覆盖前序当前事实；history 只解释来源，不证明现在仍成立。

#### 接口复用与语义边界

复用接口或新增条件分支前，针对受影响契约回答以下问题；非平凡取舍记入当前 decision，局部修复在变更说明中交代即可。

1. **事实由谁拥有？** 定位实际维护业务状态、稳定身份和生命周期的领域代码、数据约束或契约；展示名称与执行段临时标识不能替代契约承诺的稳定身份。
2. **接口承诺什么？** 核对输入、输出、失败、副作用与有效期，确认实际行为和命名一致。例如，完整文件读取与预览截断、业务结果中的 `error` 字段与执行失败状态分别具有不同语义。
3. **规则属于谁？** 调用者专属的展示、隐藏、截断和页面差异留在已有适配或投影层；特定工具的协议解释留在对应工具适配处；执行细节由执行器负责。共同能力继续拥有共同安全、资源与业务约束，显式适配不要求为每个调用者新增抽象或转发层。
4. **其他路径是否仍成立？** 用符号搜索追踪真实注册与消费者，按受影响范围核对其他工具、页面入口、直接调用和恢复路径。说明哪些契约共同成立、哪些差异由适配层显式表达，再按[测试选例规则](./testing-guidelines.md#跨入口与恢复的契约验证)选择证据。

Reviewer 对照上述回答与真实链路；将调用者专属规则写入共享接口、从展示信息猜测业务事实，或遗漏受影响消费者证据的方案，退回修正。

### 3. Propose

非平凡变更先在 [decisions/proposed/](./decisions/README.md) 写问题、类型、Owner、真实替代、验收与证据矩阵和风险。提案不保存推理流水账，也不成为运行时事实源。

每条验收主张使用同一结构：

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 用户可观察结果或工程不变量 | 它可能怎样错误地通过 | 最接近行为的代码、数据或契约 | 可复现 oracle 与准确命令 | 恢复目标缺陷后如何变红 | `Passed` / `Inspected` / `Not run` / `Inferred` |

结果词的含义固定：`Passed` 是命令实际成功且结果已核对；`Inspected` 是只读检查了事实；`Not run` 是未执行并说明原因/风险；`Inferred` 是根据间接证据推断。后三者都不能写成测试通过。

### 4. Implement assembled path

只实现验收所需的最小线性方案，并沿真实装配路径追踪 producer → registration → consumer → persistence/publication → user/model-visible result。同步更新真正拥有当前行为的文档、测试、fixture/snapshot/generated output 和 decision；不得以孤立 helper、mock 调用次数或未被 shipping composition 使用的实现宣告完成。

### 5. Verify

先运行最小相关检查，再按风险升级到真实 PostgreSQL、HTTP、worker、SSE、对象/文件、浏览器或外部 provider。每个新 guard 必须有能恢复目标缺陷的负向案例；expected output 只能显式更新并 Review。完成结论需要回读数据库、文件、对象、DOM 或协议结果，不能只看 Agent 自述、HTTP 200、日志关键词或 workflow 绿色状态。

确定性 replay 与真实 provider probe 是互补证据：前者适合 PR 阻断并证明 shipping composition，后者校准外部漂移。缺少密钥或环境时写 `Not run`，不把 optional skip 计为产品通过。

### 6. Independent review

commit 前由不继承开发上下文的全新 Reviewer 读取完整需求、decision、完整 diff、实际测试结果和未验证范围。Reviewer 检查目标/非目标、Owner、oracle 独立性、负控、复杂度和当前文档；涉及接口复用或条件分支时，执行[语义边界检查](#接口复用与语义边界)。Review 不能替代直接证据。

### 7. Converge

证据一致后，把 proposed 移到 `implemented/` 并改写为现在时的问题、决定、替代、后果和验证；拒绝则进入 `rejected/`。部分取代用新旧记录交叉链接，失去当前价值才归档。`docs/vibe/` 继续只做本地临时计划，不迁移旧计划冒充组织记忆。

### 8. Learn

达到 [postmortem 门槛](./postmortems/README.md) 的高影响逃逸缺陷，必须留下 reproducer、因果链、安全网漏过原因和更早的拒绝机制。普通缺陷仍需要风险相称的回归测试，但不制造事故文档。

## Simplification / deletion 闭环

`simplification` 不是“添加另一套更抽象的实现”。决策必须真实比较 keep、narrow、replace、remove，并回答删除会破坏哪个当前 consumer 或承诺。

删除验收至少检查：

- runtime consumer、provider registration、export/import 与调用入口；
- 配置、环境变量、manifest、generated catalog 和 capability discovery；
- durable/wire schema、migration、兼容承诺和部署脚本；
- tests、fixture/snapshot、示例、正式文档和依赖声明。

提案的验收矩阵必须包含“旧能力不存在”的负向搜索，并明确重新引入条件。若以依赖为“简化”理由，结果必须净删除 Yuxi 自有实现或维护表面；若依赖实际新增能力，应改为独立 `feature` 决策。公开 API、持久数据、部署脚本和真实用户都算 consumer，不能因代码搜索为空就假设可删除。

## 各材料的职责

- [工程信任系统](./engineering-trust.md) 定义 Owner、oracle、gate 与证据等级。
- [测试规范](./testing-guidelines.md) 拥有测试分层和运行命令。
- [工程决策记录](./decisions/README.md) 保存问题、决定、替代、后果与验证。
- [事故复盘](./postmortems/README.md) 只保存达到门槛的逃逸事故及其防复发机制。
- PR 描述记录本次变更实际执行的命令、结果、Reviewer 结论与未验证范围。
