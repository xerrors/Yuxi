# 自进化 Skill 的反馈来源与 Yuxi 接入调研

面向 Yuxi 维护者，回答四个问题：从什么证据学习、怎样把经验改成 Skill、怎样提交包含脚本的完整包、什么时候让新内容生效。阅读前提是了解 Agent 专属 Skill、Project Workdir 与 Turn/Run。本记录是架构调研，建议尚未构成实现决定。不修改业务或数据库，也不修改运行中的 Skill。

调研日期为 2026-10-11，Yuxi 源码基线为 `b642f0ab`。外部资料使用官方文档和公开源码，也使用原始论文。源码检查记作 `Inspected`，方案推断记作 `Inferred`，未执行的系统实验记作 `Not run`。文中 Hermes 指 Nous Research 的 Hermes Agent。“自进化落在 Skill”解释为更新操作指南及其脚本，也更新参考资料和模板，模型权重保持不变。

## 推荐的最小闭环

优先实现闭环。这条闭环是明确纠正或可验证执行问题 → 一次复盘 → 修改专属 Skill 草稿 → 验证 → 提交完整包 → 后续执行使用。用户反馈决定希望改变什么，执行轨迹帮助解释发生了什么，独立验证决定候选改动是否值得保留。三个角色组合使用，能够避免把普通运行记录直接当成正确经验。此建议为 `Inferred`。

第一阶段从用户主动要求“复盘并改进这个 Agent”开始，也允许把一次确定失败及其修复选为输入。每次处理一个有证据的问题，允许输出“不修改”。自动定时复盘和跨用户聚合留到实际收益可测之后，自出题训练和模型微调也留到那时。它们都增加数据筛选或运行成本。

文件修改继续使用现有 read、write、edit、shell 能力。平台补齐受控入口，用于导出一致的专属包编辑基准，并在验证后提交自身绑定包。持久化复用现有 Skill 内容服务。为脚本引入可靠验证与生效边界，是这个方案的主要工程工作。

## 学习来源及其可信边界

| 来源 | 提供的有效信息 | 使用方式 | 不能直接推出的结论 |
| --- | --- | --- | --- |
| 用户明确纠正 | 期望结果、禁用做法、适用范围 | 保留原话和所指 Turn/Run，将纠正转成一个验收例 | 单个用户的偏好适用于共享 Agent 的所有用户 |
| 用户评价或接受结果 | 满意度、偏好方向 | 作为挑选复盘案例的信号；进一步定位具体差异 | 点赞证明脚本、数据或事实正确；沉默代表成功 |
| 执行轨迹 | 输入、工具参数、错误、重试、修复步骤、资源版本 | 对比失败和修复后的结果，找出可重复的程序性知识 | Run completed、退出码零或 Agent 自述证明业务成功 |
| 可回读的环境结果 | 文件内容、数据库状态、测试断言、协议结果 | 用作成功判据，并形成回归样例 | 在一个环境通过即可跨环境泛化 |
| 自主探索 | 沙盒试验、对照执行、构造边界样例 | 在固定任务和预算内生成新的经验 | 模型自己出题、自己判分就产生了独立监督 |

反馈和轨迹是原材料，自学习是处理这些材料的方法。用户无需每次显式评价，Agent 也能从具有确定判据的执行失败中学习；遇到审美和偏好，以及业务取舍，用户反馈通常更能明确目标。缺少外部判据的自我反思只生成候选假设，不能独自授权覆盖有效 Skill。

建议优先收集用户明确纠正和失败后成功修复，也收集重复出现的同类问题。成功轨迹用来保留已经有效的步骤。一次无解释的成功不适合固化为通用规则。临时服务故障更适合修运行设施，用户私有偏好更适合保存在对应用户作用域，特定业务事实更适合进入知识库；只有可复用操作方法进入专属 Skill。

经验条目保留最少关联：原始 Input/Turn/Run、实际使用的 Skill 内容哈希、纠正原文或失败观察、期望结果和验证证据。后续纠正应明确关联被纠正的结果 Run，不能靠“上一条消息”猜测。共享 Agent 的内容变更沿用管理权限；普通使用者的反馈是证据，写权限由服务端另行检查。

## 外部方案有哪些可借鉴的部分

| 方案 | 学习来源与更新对象 | 对 Yuxi 最有用的部分 | 效果证据的边界 |
| --- | --- | --- | --- |
| Hermes Agent 主产品 | 对话、用户纠正、工具执行经验；更新 Skill 正文及辅助文件 | 任务结果交付后复盘、手动复盘入口、文件包维护 | 源码证明有更新机制，未证明每次修改都提高任务成功率 |
| ACE | 执行反馈与反思；增量修改操作手册 | 小补丁合并，保留有效旧经验，减少整篇重写造成的遗忘 | 作者基准实验；反馈不可靠时也会污染经验 |
| GEPA | 可重放任务、执行轨迹、分数与文字诊断；搜索候选文本参数 | 有稳定任务集后，用新旧对照挑选候选 | 依赖 evaluator、搜索预算和正确接线；不是即装即用的在线学习 |
| Voyager | 自主课程、环境反馈、执行错误与检查；形成可执行代码技能 | 脚本先执行和修复，再进入复用库 | 原始实验在 Minecraft；业务环境的验收与副作用隔离需要另做 |

ACE 的可借鉴点是局部更新过程，无需引入它的完整条目管理机制。GEPA 更适合第二阶段离线优化重复任务。Voyager 直接说明“技能可以是可执行程序”。标准 Agent Skills 也明确包含 `scripts/`、`references/` 和 `assets/`。把 Yuxi 的进化对象定义为完整文件包有现成依据。来源：[ACE 原始论文](https://arxiv.org/abs/2510.04618)、[GEPA 原始论文](https://arxiv.org/abs/2507.19457)、[GEPA 官方实现](https://github.com/gepa-ai/gepa)、[Voyager 原始项目](https://voyager.minedojo.org/)与 [Agent Skills 规范](https://agentskills.io/specification)。这里的适用性判断为 `Inferred`，没有在 Yuxi 上复现实验。

Hermes 主产品源码固定核对到 `f86276d2ebcccf0530873c11e2965c3f1cbd5a31`。其后台 reviewer 读取已完成对话，从用户纠正和失败后修复中提炼经验，也从可复用方法中提炼。前台也能直接调用 `skill_manage`，用户可用 `/refine` 主动复盘。自动后台复盘要等最终回复交付，并且当时非中断、计数阈值已经满足，才启动。默认 Skill 阈值为 10 个工具循环迭代，可跨 turn 累积，memory 阈值为 10 个用户 turn，不能理解成每个回合必改一次。来源：[background_review.py](https://github.com/NousResearch/hermes-agent/blob/f86276d2ebcccf0530873c11e2965c3f1cbd5a31/agent/background_review.py)、[turn_finalizer.py](https://github.com/NousResearch/hermes-agent/blob/f86276d2ebcccf0530873c11e2965c3f1cbd5a31/agent/turn_finalizer.py) 和 [agent_init.py](https://github.com/NousResearch/hermes-agent/blob/f86276d2ebcccf0530873c11e2965c3f1cbd5a31/agent/agent_init.py)。

Hermes 已支持 supporting files 的创建和补丁，也支持删除。这些操作包括 scripts。当前批处理实现有单 Skill 锁与失败回滚。启用 `skills.write_approval` 后可暂存修改并查看 diff，也可批准或拒绝。另有变更 ledger、内容 blob 和 curator rollback。因此，“只能改 Markdown”“没有审批和回滚”都不符合当前实现。来源：[skill_manager_tool.py](https://github.com/NousResearch/hermes-agent/blob/f86276d2ebcccf0530873c11e2965c3f1cbd5a31/tools/skill_manager_tool.py)、[skill_manager_batch.py](https://github.com/NousResearch/hermes-agent/blob/f86276d2ebcccf0530873c11e2965c3f1cbd5a31/tools/skill_manager_batch.py)、[Skills 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)、[Curator 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator)与 [skill_ledger.py](https://github.com/NousResearch/hermes-agent/blob/f86276d2ebcccf0530873c11e2965c3f1cbd5a31/tools/skill_ledger.py)。

Hermes 最值得注意的缺口是默认后台 reviewer 可以通过 Skill 管理工具写脚本，却默认没有 terminal 执行权限。更新链路没有强制的业务回归门禁。其提示词也鼓励频繁沉淀更新，尽管允许无内容可保存。普通变更的 ledger 记录失败不会阻止文件修改。这些源码事实意味着 Yuxi 需要额外提供候选验证和正常的 no-op 出口，也要提供与发布结果绑定的证据记录。不能把 Hermes 写入成功当成改进已验证。该结论来自上述 background review 与 ledger 源码，未通过运行 Hermes 验证实际发生率。

另一个官方仓库 [hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution) 是独立实验项目。调研时 README 只将第一阶段 SKILL.md 优化标为已实现，工具代码和连续循环仍列在计划中。入口使用的 [fitness](https://github.com/NousResearch/hermes-agent-self-evolution/blob/main/evolution/core/fitness.py) 包含基于预期文字与输出词重合的评分。它不足以证明脚本业务效果。应分别评价主产品复盘与这个实验优化器，不把 roadmap 当成已交付能力。

2026-07-31 提交的预印本《Rethinking Self-Evolving Agent Skills》提供了更谨慎的多轮证据。在 14 组模型与任务设置中，388 个候选只有 55 个形成字节不同且验证更优的新最佳。选中的 11 个进化版本都来自包含失败轨迹的条件，其中 9 个提高测试表现。它支持的方向包括保留失败信息，也包括验证筛选，并允许不更新，但不能推出所有业务里失败样本一定最好。该研究没有证明 Yuxi 的收益。来源：[论文摘要与版本日期](https://arxiv.org/abs/2608.02636)、[实验方法与局限](https://arxiv.org/html/2608.02636v1)。

## Yuxi 已有的基础与实际缺口

以下是源码检查结果，均为 `Inspected`，不代表本次运行了对应服务或测试。

| 能力 | 当前事实 | 对自进化的含义 |
| --- | --- | --- |
| 专属绑定 | `Skill.bound_agent_id` 是唯一外键，包使用稳定 `self-…` slug，权限从 Agent 派生 | 修改内容继续使用同一 Skill 行，无需重新绑定数据库 |
| 创建范围 | 创建 Agent 可附带专属包，也可后续创建；运行时允许没有绑定 | 代码支持专属 Skill，无法据此断言每个部署中的 Agent 已有一份 |
| 完整内容提交 | `commit_skill_content` 先准备不可变目录，再提交目录引用、哈希和依赖索引；`expected_revision` 拒绝过期覆盖 | 复用提交服务，避免直接改有效目录或让文件监听器补数据库 |
| 历史 | 专属 Skill 可保存完整包历史、恢复和删除历史；`release=true` 记录提交后的包 | 自动更新需保留更新前版本；只保存新版本不足以回退到上一状态 |
| 人工编辑接口 | `/api/system/skills/{slug}/content` 提供完整编辑基准和批量文本变更；可保存 Markdown、Python、Shell 等支持的文本 | 已有编辑和提交能力，无需新建一套 Skill 编辑器；目录提交的 Agent 适配层仍需补齐 |
| Agent 工具 | `install_skill` 安装到用户个人 Skill 空间；已检查工具注册与 Skill 服务，未发现自身绑定包的受控更新工具 | 安装个人 Skill 不能替代更新数据库中的专属绑定 |
| 运行加载 | 专属根在 Run 准备时自动预加载，同 Run 保留已加载的根文本；manifest 记录内容哈希 | 已有经验归因基础，但 manifest 不等于脚本字节已经固定 |
| 文件投影 | 专属包沿用共享投影，沙盒 `/home/user/skills` 只读；个人 Skill 位于可写 user-data | 为自进化开放整个共享挂载的写权限会越过提交、授权和修订边界 |
| 用户反馈 | 聊天点赞/点踩只显示“反馈功能开发中”，没有反馈持久化链路 | 第一阶段可直接使用用户明确纠正的消息；结构化反馈需要另补 |
| 轨迹 | Message/ToolCall、Turn/Run 和运行 manifest 已持久化 | 需要授权后的证据读取与关联入口，无需先建设另一套轨迹数据库 |

绑定、授权与创建的源码 Owner 为 [models.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/models.py)、[bound.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/bound.py)、[shared.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/shared.py) 和 [Agent definitions](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/definitions.py)。包提交与历史见 [content.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/content.py)、[versions.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/versions.py) 和 [HTTP 契约](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/api/routers/extensions/skills.py)。

运行与文件边界见 [runtime.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/runtime.py)、[authorization.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/middlewares/authorization.py)、[projection.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/projection.py)、[preparation.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/preparation.py) 和 [provisioner 只读挂载](https://github.com/xerrors/Yuxi/blob/main/docker/sandbox_provisioner/app.py)。个人安装见 [install_skill.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/tools/builtin/install_skill.py)，消息轨迹见 [messages.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/models/messages.py) 和 [message_recorder.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/services/message_recorder.py)，反馈状态见 [RefsComponent.vue](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/RefsComponent.vue) 与[移除消息反馈决定](../decisions/implemented/2026-09-30-remove-message-feedback.md)。

需要区分两个“内置”：随产品发布的 `source_type=builtin` Skill 禁止直接修改文件；内置 Agent 自己绑定的专属 Skill 使用同一可编辑绑定机制。自进化的目标是后者或用户创建 Agent 的专属 Skill。已有完整包提交与运行取舍分别由[内容提交决定](../decisions/implemented/2026-10-05-skill-content-commit.md)和[专属 Skill 决定](../decisions/implemented/2026-10-03-agent-bound-skill.md)解释。

## 怎样把轨迹转成 Skill 修改

复盘可由同一个基础模型执行。建议使用独立的一次受控执行，只读取指定案例和当前包，也读取验收条件，使普通任务结束与学习工作在计费上分开，在失败和追踪上也分开。第一阶段也可在用户发起的复盘会话内完成，不必先建设常驻“进化 Agent”。以下为 `Inferred` 的最小工作流。

1. 选择一个案例：用户纠正，或具有外部判据的失败与修复。服务端按当前身份提供所需轨迹和产物引用，也提供资源版本，避免导出全量历史。
2. 定位原因：区分 Skill 指南遗漏、脚本缺陷、用户偏好、知识错误、工具/平台错误和暂时环境故障。只有前两类直接改包，其他类路由到相应 Owner。
3. 写出可验证假设：记录触发条件、观察到的问题、准备修改的文件、期待改变的行为和不能回归的已有行为。
4. 修改草稿：在 `SKILL.md` 写适用范围和步骤；把确定且重复并可测试的计算或操作实现到 `scripts/`；细节放 `references/`，模板放 `assets/`。一个问题形成一份小 diff，保留有效旧内容。
5. 验证候选：用固定输入比较旧包与新包，回读最终输出或状态；旧包应复现目标问题，新包应修复它。再用未参与改写的案例检查回归。
6. 决定保留：验证通过且有实际收益才提交；没有改进、证据不足或只是文案换写时保留旧包。记录被拒绝的候选，避免以后把同一失败重新“学”一遍。

例如，轨迹显示处理分页接口时漏掉第二页，用户指出数量不对。候选修改可以同时更新 `scripts/fetch_all.py` 的分页终止条件和 `SKILL.md` 的核对步骤。验收使用固定的多页响应，并检查收集到的全部 ID 和重复项。用户原话提供目标，工具轨迹定位漏页，固定响应与最终集合提供独立判据。只增加“以后认真检查”不能证明问题已经解决。

验证用的标准答案和保留案例，以及授权规则，应由平台或管理者维护，不能让待改进的 Skill 自己改掉判据。脚本语法检查可作为早期筛选，业务效果仍需真实执行。涉及写外部系统的脚本使用受限测试环境和假数据。测试通过不赋予脚本生产凭据或新增权限。

## 文件系统编辑与数据库提交怎样衔接

建议采用两个模型可调用入口，名称仅为提案，不代表当前已有 API。它们适配已有服务，文件编辑仍由现有工具完成。

| 提议入口 | 输入与输出 | 服务端职责 |
| --- | --- | --- |
| `prepare_self_skill_edit()` | 返回草稿位置、基准 revision 和可读的绑定信息 | 从受信任运行上下文定位当前 Agent；检查管理权限；持内容读锁复制一致的完整包到当前 Workdir 的独立草稿目录 |
| `submit_self_skill_edit(draft_id, evidence_refs)` | 返回验证失败、待审核、修订冲突或明确的新 revision/version | 复核自身绑定、身份和草稿归属，冻结候选，验证，按发布策略调用现有内容提交服务并记录结果 |

`draft_id` 只定位一份编辑工作，具体载体可复用已有持久记录。平台保存与它关联的操作者和 Agent，也保存来源 revision 和 Run，不能把草稿里的可编辑 JSON 当成授权依据。两个入口无需给模型传任意 `agent_id`、任意目标 Skill slug 或宿主路径；业务目标从实际 Run 派生。

文件流向为：

```text
数据库 Skill 行 ──引用──> 当前不可变完整包
                              │ 一致性导出
                              ▼
                     Workdir 中可写草稿
                              │ 普通文件编辑与脚本试验
                              ▼
                   服务端冻结候选并计算哈希
                              │ 独立验证及必要审核
                              ▼
                existing content service 提交新引用
                              │
                  同一 Skill 绑定，新内容和历史
```

草稿路径使用 Workdir scope，由 workspace filesystem 边界解析为实际目录。导入时检查归属和路径穿越，也检查符号链接和特殊文件，并检查预算。沿用现有 50 MiB、2000 条目的完整包约束。模型可写草稿不会因此获得 `/home/user/skills` 或服务端包目录的写权限。同一用户内的文件可见性也不构成 Agent 间的保密边界。

候选先复制到服务端独占的不可变目录，再校验哈希和测试。最终提交的必须是经过验证的同一份字节。校验完成后重新读取仍可编辑的草稿会产生时间差漏洞。人工审核也绑定候选哈希和基准 revision，审核后文件变动或正式版本变化使原结论失效。路径安全与候选冻结应由 owning filesystem/content 边界完成，普通路由和 Agent 工具不直接持有宿主路径。

第一阶段可以只允许现有 content 接口支持的文本文件和目录操作。服务端从草稿计算差异后调用 `save_skill_content`，未修改的二进制资源保留。新脚本用明确解释器执行。若需要替换二进制 assets 或变更执行位，再为完整目录源增加经过授权的适配，复用 `commit_skill_content` 的整包复制路径。现有函数的 `source: Path` 是内部接口，不能直接暴露成接受模型宿主路径的工具。用户要求的 Markdown 与脚本更新已在第一阶段范围内。

提交继续使用整包 `expected_revision`。两个进程基于同一旧包修改时，只允许一个成功；另一个保留草稿并重新基于最新包评估，不能静默覆盖。新增请求幂等关联用于区分“提交失败”和“提交成功但响应丢失”，不要把重复请求误报为第二次发版。

自动写入前保留旧包历史，新内容可使用现有 `release=true` 留存。需在同一资源锁内保证旧包留存与新引用切换的关系。当前保存接口只有“保存后发版”，这部分需要一个小的 service 编排扩展。原有 `bound_agent_id`、Skill 主键和 slug 均保持稳定，仅更新当前目录和内容哈希，也更新解析出的索引与历史关联。数据库拥有当前选择，文件系统拥有内容字节。

权限继承现有 Agent 管理范围。只拥有使用权限的用户和 APP end_user，以及运行在其身份下的 Agent，不能通过“修改自己的 Skill”获得管理权限。第一阶段由有管理权限的操作者触发。若后续要让普通使用者的轨迹推动共享 Agent 改进，应由管理者明确启用受限发布策略，并在 executor/service 执行，不能根据模型生成的身份字段提升权限。

## 触发时机与运行中的版本一致性

建议把“记证据”“启动复盘”“发布内容”作为三个时点处理。任务进行中可以记下经验候选；用户可在事后要求复盘，或由后续后台策略选择案例；只有验证和发布条件都满足才切换有效内容。复盘工作不应拖延用户原任务的结果发布，也不把自身失败写成原任务失败。

自动触发时以业务 Turn 的真实终态为依据，并通过 `result_run_id` 找到最终结果。一次 Run 结束可能是 steer 让出或审批等待，也可能是协作等待，不等于任务已经形成可评价的结果。迟到的用户纠正可以再次触发同一案例，但相同证据与相同基准包应去重。复盘自己的运行默认不继续触发复盘。

后台化时复用已有持久执行和 worker 投递基础。复盘任务需要自己的身份和 owner/lease，也需要终态和限额。领域完成事实提交后再投递，丢失投递可补发。现有 background_jobs 面向管理员业务，不能直接推断它已适用于普通用户自进化。具体使用受控 AgentRun 还是新增领域 job handler，应在实现提案中按消费方式选一个，不引入另一套任务调度平台。

当前最重要的限制是 Run 固定已加载根说明。脚本和参考文件仍从 uid 级实时投影读取，这同样属于这项限制。同一 Run 的模型/工具执行边界和其他 Run 的准备都会刷新该 uid 投影。发布后即使没有新 Run 启动，已有 Run 的下一执行边界也可能读取新脚本。因此“只在 A 结束后发布”仍可能影响 B。Skill 内容哈希写入 manifest 只说明准备时的来源，不能证明后续执行的脚本始终具有相同字节。这是由现有 runtime、authorization 和 projection 消费关系得出的 `Inferred` 风险，尚未通过并发实验验证。

建议按两个阶段处理：

- 试点使用手动触发与人工发布，在明确无相关活跃消费者的维护时段更新。这属于操作约束，不能宣称平台已有并发安全保证。先验证学习效果和目录提交闭环。
- 开放无人值守发布前，为专属完整包增加 Run 级固定读取：根说明、scripts、references 使用同一不可变内容版本；新 Run 使用新的当前引用，旧 Run 保留旧包直到结束。依赖权限仍在执行边界复核，固定字节不延长已撤销的授权。运行引用也必须参与包与投影清理，否则旧目录会被 prune 删除。

第二阶段可只覆盖专属包，不必同时建设所有 Skills 的通用版本系统。另一种实现是严格的发布排他窗口，阻止相关执行开始且等待全部消费者结束。在 uid 级共享投影和跨会话情况下，这个范围比单个 Agent 大，容易影响吞吐，通常不如固定包清晰。无论采用哪种机制，都需要真实并发测试，不能依赖“发布前查一次无活跃 Run”。

审批恢复和 steer 都会产生后续 Run，当前代码在这些 Run 中重新准备 Skill。若产品要求整轮任务都保持同一操作包，需要明确升级为 Turn 级固定；Run 级固定只承诺单次执行段一致，不能冒充整个 Turn 一致。

## 主要弱点与最小验收办法

| 弱点 | 具体失败方式 | 最小应对与证据 |
| --- | --- | --- |
| 错误归因 | 工具暂时不可用，被学成永久禁用规则 | 固定输入复现；确认缺陷在指南或脚本；保留“不修改”结论 |
| 自证循环 | Agent 写脚本，也修改期望输出使测试通过 | 平台持有判据与保留案例；候选没有修改判据的权限 |
| 过拟合与遗忘 | 修复一条反馈，破坏其他用户或旧任务 | 旧包/候选使用相同模型与预算比较；包含未参与改写的回归样例 |
| 内容不断膨胀 | 每次复盘都追加一段长经验，根文件挤满上下文 | 局部替换与去重；根保留短流程；以成功率和成本衡量收益 |
| 提示注入与信息泄露 | 把网页指令、工具输出或私有会话信息写进共享 Skill | 轨迹按不可信数据处理；保留来源；共享变更检查适用范围和隐私；权限边界位于 executor/service |
| 脚本副作用 | 测试修改真实外部系统；依赖新增后扩大能力 | 隔离执行和固定依赖；真实副作用边界授权；依赖/权限变化单独审核 |
| 并发覆盖与版本混用 | 两份草稿覆盖，或旧说明配新脚本 | 整包修订冲突测试；并发 Run 回读实际脚本产物及内容哈希 |
| 误把写入当收益 | Skill 更新次数增长，任务表现未改善 | 记录目标问题修复、既有任务回归率、耗时、模型/工具成本和回滚次数 |

试点可以先选一个重复、可验收的任务族，例如分页数据收集或固定格式文件生成。准备约 10–20 个覆盖常见输入和边界的案例，作为工程起步预算，不能把这个样本量解释成统计有效性保证。将用于改写的案例与保留回归案例分开。固定模型和工具，执行预算也固定。比较的三个条件是无改动旧包和仅复盘未发布，再加上候选包，避免把额外推理成本误算为持久学习收益。存在随机性时重复执行并报告波动，收益不清楚就保留原版。

Yuxi 工程验收先复用 [完整包 integration](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_agent_bound_skill_versions.py) 和 [专属加载 worker E2E](https://github.com/xerrors/Yuxi/blob/main/backend/test/e2e/test_agent_bound_skill_e2e.py)，再补真实链路中的负向案例：普通使用者提交、指定其他 Agent、草稿越界/链接、过期 revision、验证后改字节、数据库提交失败、提交成功响应丢失、并发投影刷新、旧运行所用包被清理，以及恢复后全部文件字节一致。已有测试不等于新接口已获得覆盖。

交付顺序建议为：先用手动案例验证改进能否成立；再补自身包编辑/提交入口和证据关联；随后补完整包运行固定与脚本验证，才启用受控自动发布；最后按真实收益决定是否增加定时复盘或 GEPA 候选搜索，以及是否增加自主探索。这样每一步都有独立可观察结果，避免在学习效果尚未确认前扩建平台。

## 本次验证范围

| 命令或检查 | 结果 | 能证明的范围 |
| --- | --- | --- |
| `python3 scripts/verify_engineering_contracts.py` | Passed | 当前工程契约静态检查通过 |
| `python3 -m unittest scripts.test_verify_engineering_contracts` | Passed，64 项 | 工程检查器自身测试通过 |
| `cd docs && pnpm run build` | Passed | VitePress 构建与页面生成；存在体积提示，无构建错误 |
| 本文件 Markdown 相对链接目标、尾随空白和末尾换行检查 | Passed | 本地引用存在，文本格式符合约定 |
| `git diff --check` | Passed | 已跟踪差异无空白错误；本次新增文件另经上述文本检查 |
| 后端 unit、真实 HTTP integration、worker E2E、Hermes 运行和论文实验 | Not run | 本次仅调研与文档；已有测试只读取，系统可用性和学习收益尚未实测 |

本记录没有读取部署凭据或真实会话，也没有读取用户工作文件，也没有把源码支持的绑定能力解释为某个部署的已存在数据。正式实现需要先建立 proposed decision，并按[测试规范](../testing-guidelines.md)完成验证。验证对应权限和文件，也对应提交和运行一致性。
