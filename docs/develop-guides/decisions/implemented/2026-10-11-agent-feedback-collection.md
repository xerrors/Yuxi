# 用户反馈与 Agent 改进标记采集

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/feedback.py

## 问题

用户需要积累两类用于改进 Agent 的材料：对最终结果的评价，以及执行时发现的流程纠正、绕路经验和可复用方法。运行记录保存过程，但需要额外的采集入口标出值得复盘的片段。

本决定落实[自进化调研](../../audits/self-evolving-skills-research-2026-10-11.md)中的第一阶段采集，部分取代[移除消息反馈决定](2026-09-30-remove-message-feedback.md)中的产品反馈占位行为。Langfuse 评分同步、满意度统计和旧消息反馈接口继续保持移除。

## 决策

### 实现方案

agents 业务域的 feedback service 拥有授权、最终结果校验、执行 lease 校验和事务提交，repository 拥有受限查询与写入，`agent_feedback_records` 拥有来源组合及唯一约束。Web 产品 HTTP 接收用户评价，拒绝 API Key，运行时工具接收 Agent 标记，两者同步提交 PostgreSQL 后返回 ID，不增加队列或后台任务。协议字段由[Web 反馈接口](../../../advanced/agent-feedback.md#反馈采集)和[改进标记工具](../../../agents/tools-system.md#内置工具-记录改进线索)维护。

| 入口 | 行为与事实 Owner |
| --- | --- |
| 最终回答下方赞/踩及可选说明弹窗 | `RefsComponent.vue`、`UserFeedbackModal.vue` 和 sessionFeedback store 按服务端已提交值展示；Session runtime 批量恢复当前消息页的评价 |
| `mark_for_improvement(reason)` | extensions tools runtime 统一装配基础工具集合，不以 Run 或 Skill 作为可见性条件，授权与 Skill 门控保持它可见；executor 使用注入的身份、Run、worker token 和 call ID |
| 系统管理 → 智能体反馈 | 管理列表只读展示、筛选与分页；system 路由及 repository 均限制为产品 JWT 的 superadmin，普通 admin、Agent 管理者和全部 API Key 被拒绝 |

用户评价只允许 Run 所属的产品登录用户提交，APP 会话不通过 Web 反馈开放，目标必须是终态 Turn 的 `result_run_id` 和它绑定的非空最终助手 Message。每个 Run 保留一份当前用户评价，修改覆盖、撤销删除，重复完整状态不增行。客户端每个目标只允许一个在途写请求，保存失败保留说明草稿和已保存值；切换赞/踩清空旧说明。没有最终输出的错误、人工等待、协作等待、中间 Run 或流式正文 done 不开放评价。

点击赞/踩先保存评价，成功后打开可选说明弹窗；空说明也能保存，用于清除原说明。关闭或按 Escape 保留已提交评价，再点同一评价撤销。弹窗提供可多选的常见原因和选填文字，选项以分号连接，再与文字合并保存到 reason，管理员详情展示完整内容；两部分合计最多 2000 字符。关闭后不提供补充或查看入口，下一次新的评价清空旧说明与选择。历史数据重读不能覆盖正在编辑的草稿；浏览器重载不恢复未提交内容。弹窗初始焦点进入首个原因选项，失败后焦点进入错误提示，关闭后返回触发按钮。同一 Run 的评价与草稿共享，弹窗开关只由触发它的视图持有；离开缓存视图关闭弹窗并使在途打开意图失效，返回后迟到响应仍可恢复已保存评价。

Agent 标记只接收去除首尾空白后 1–4000 字符的 reason，不添加类型或正负分值。模型 schema 不暴露执行身份。首次写入在 Session/Run 行锁内检查有效账号、Agent 使用权、运行状态、worker attempt 与 lease；已经提交的同一 `(run_id, tool_call_id)` 在原身份与权限有效时重放返回原 ID，不新增记录，改变 reason 返回冲突。数据库失败返回明确工具错误，取消和授权撤销信号继续传播。失败、取消或 yield 不删除此前已提交标记。

两类记录通过 Run 派生 Agent、APP、用户、Session 和 Turn，不复制消息或另一套身份字段。用户 reason 可空且最多 2000 字符；source、rating、reason、tool_call_id 的合法组合由 CHECK 约束固定，两类记录分别使用部分唯一索引。归档保留记录，软删除 Session 后所有查询隐藏记录，硬删除 Run 级联删除反馈。

反馈列表只返回正文和精简执行标识。原会话、manifest、审计与文件仍走原授权接口；管理员列表不授予原会话访问权，页面在原接口拒绝时显示无权查看。正文按纯文本展示，不执行指令或自动读取其中路径。

管理员默认列表优先呈现来源、反馈正文、Agent 名称与记录时间；完整说明、Agent 与来源、记录及更新时间放入详情弹窗；内部 ID 和运行状态的删减由[反馈精简决定](2026-10-11-feedback-simplification.md)维护，每次打开从顶部开始并聚焦正文标题，关闭后焦点返回原记录；两种弹窗均支持 Tab 循环与 Escape 关闭。离开列表或切换标签关闭详情，原会话查询的迟到结果不接管已离页的导航。来源、Agent 名称及用户评价立即筛选，切换来源清除不适用的评价筛选；Agent 选项加载失败不阻断反馈读取。条数只表达“已加载”，按 `created_at` 展示与后端游标排序一致的记录时间。分页失败保留已有记录并用原游标重试；响应式布局按列表容器宽度切换，避免展开侧栏时裁切操作。

schema-init 在 advisory lock 内支持 business 8→9：新增反馈表、索引、约束与版本提交属于同一 PostgreSQL 事务，中断回滚后可以重跑。新空库建立完整 9，知识域保持 3，API/worker 只读校验版本。其他旧版本继续拒绝，不恢复已撤下的历史升级路径、不导入旧反馈表、不清库。

## 替代方案

| 方案 | 取舍 |
| --- | --- |
| 只恢复赞/踩或只提供 Agent 标记 | 单一入口不能同时保留用户结果评价和执行经验 |
| 两个独立模块和表 | 重复执行关联、授权和分页；当前由 source 与数据约束区分两种信号 |
| 保存每次点击事件 | 需要撤销事件和当前状态投影；本阶段只消费当前有效用户评价 |
| 在回答下方展开说明表单 | 占用对话阅读空间；采用可关闭的选填弹窗，让已提交评价与未提交说明有明确边界 |
| 在共享评价 store 中统一打开弹窗 | 新对话草稿与已创建会话可被同时缓存，共享打开状态会激活两个 Teleport 弹窗；视图独立持有打开意图，store 继续持有评价与草稿 |
| 用 Ant4 抽屉展示管理员详情 | 当前组件缺少焦点循环且丢弃补充的无障碍属性；改为原生 Modal，复用已有键盘与标题语义，长正文在弹窗内滚动 |
| 增加“强化经验、记录教训、沉淀路径”类型 | 类型重叠且没有当前消费逻辑，先保留原始原因 |
| 自动扫描会话、修改或发布 Skill | 增加模型成本、判据、文件归档和发布一致性，超出采集范围 |

## 后果

平台具备两类可回看的原始信号，保存不表示经验正确、Agent 已学习或 Skill 已更新。标记与原 Run 关联，后续执行失败也可保留有价值的过程线索；是否适时调用仍受模型影响，确定性 E2E 只证明工具装配和持久化链路可靠。

reason 可能包含错误、提示注入或敏感内容，当前仅保存和转义展示。原脚本路径与产物不自动复制归档，文件覆盖或会话删除后部分依据可能无法重现。未来复盘、筛选、脱敏及 Skill 更新需要独立契约。

第一阶段不增加 Langfuse 对接、自动反思、分类/评分、向量检索、经验审核流、满意度图表、导出、脚本归档或模型训练。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 用户当前评价准确持久化、恢复、修改与撤销 | HTTP、PG、DOM | feedback service/repository、session UI | `test/integration/api/test_agent_feedback_api.py`；Playwright 真实页面操作后独立查询 PG；sessionFeedback 单测 | 503 保存失败保留草稿；并发首次提交一行；撤销后刷新无评价；读写两种启动顺序均不回退已保存值 | Passed |
| 评价只绑定明确最终输出，用户与 APP 隔离 | Turn/Run/Message、身份 | feedback service、Web JWT scope | HTTP integration 和 `test/integration/services/test_agent_feedback.py` | 中间 Run、等待、空输出、跨用户、APP 会话与全部 API Key 均拒绝 | Passed |
| Agent 工具绑定当前 Run、可信调用与有效 lease | worker、工具、事务 | tools runtime、feedback service | `test/e2e/test_agent_feedback_e2e.py`；PG integration | 未选 Skill/工具仍能调用；过期 lease、旧 attempt、失权或伪造身份无法写入 | Passed |
| 标记重放、冲突与失败不会产生假成功 | 并发、SQL、工具结果 | feedback repository/service | PG 并发重放；真实触发器拒绝 insert 后回读并重试；worker 无效原因场景 | 同 call 不同原因拒绝；数据库拒绝后零记录；无效原因返回错误且原任务完成 | Passed |
| 管理列表限制平台 JWT 并过滤删除 | HTTP、repository、DOM | system 路由、feedback 查询、管理 UI | HTTP 身份矩阵；PG 删除与游标测试；浏览器来源、评价过滤和错误重试 | 普通 admin、用户、superadmin full Key 均拒绝；列表无 manifest；删除后隐藏 | Passed |
| 可选说明与详情可用，窄屏操作不被裁切 | DOM、焦点、客户端状态 | Refs、反馈弹窗、管理列表 | `frontend/test/browser/agentFeedback.js`；反馈 store 单测 | 空说明仍可保存；关闭后评价保留；503 留住草稿并聚焦错误；空白最终输出隐藏评价；1001px 展开侧栏操作完整；后页失败以相同游标重试 | Passed |
| 缓存视图与迟到响应不残留弹窗或接管导航 | KeepAlive、DOM、路由 | Refs、管理列表 | `frontend/test/browser/feedbackModalKeepAlive.js`；`frontend/test/browser/feedbackNavigation.js` | 新对话首次评价出现两个弹窗；保存期间离开再返回后重开弹窗；进入原会话后详情残留；查询期间离页后被迟到结果导航回会话 | Passed |
| Schema 8→9 原子、可重跑且保留旧数据 | PostgreSQL DDL、版本 | migrations | `test/integration/services/test_schema_migration_version.py` | 真实 DDL 后注入中断，表和版本回滚；重跑后原用户样本与唯一索引存在；其他旧版拒绝 | Passed |

已执行命令与结果：

- `docker compose exec api uv run --group test pytest test/integration/services/test_agent_feedback.py test/integration/services/test_schema_migration_version.py -q`：32 passed。
- `docker compose exec api uv run --group test pytest test/integration/api/test_agent_feedback_api.py -q`：6 passed。
- `docker compose exec api uv run --group test pytest test/e2e/test_agent_feedback_e2e.py -m e2e -q`：2 passed。
- `docker compose exec api uv run --group test pytest test/unit -m "not slow" -q`：2676 passed，55 skipped；跳过项为该集合原有条件项，不作为新增功能证据。
- `docker compose exec frontend node --test test/unit/sessionFeedback.test.js test/unit/sessionRuntime.test.js`：20 passed；保存期间启动读取的负向用例在缺少过滤时以旧记录覆盖新记录的断言失败；去掉草稿保护后，历史重读用例以 `'' !== '未提交的具体建议'` 失败。
- `docker compose exec -T frontend pnpm run test:unit`：559 passed，0 failed；`pnpm run lint:check`、`pnpm run build`：通过。
- `python3 scripts/verify_engineering_contracts.py`：通过；`python3 -m unittest scripts.test_verify_engineering_contracts`：64 passed；`git diff --check`：通过。
- `cd docs && pnpm run build`：通过，保留完整相对链接检查；修正测试审计文档中的两处根目录 AGENTS 链接为公开源码链接。
- `playwright-cli --raw -s=feedback-ui-polish run-code --filename=frontend/test/browser/agentFeedback.js`：真实合成会话上完成可选说明弹窗、Tab/Escape/焦点恢复、空说明提交、撤销及重载恢复、503 后重试、双来源筛选、详情与原会话拒绝、empty/error、同游标后页重试；浅/深色及 1440/1024/1001/768/375px 均通过。空白输出与故障分页使用明确的 HTTP 响应注入，实际评价写入使用真实 API，独立 PG 回读确认最终保存为 `dislike`、`reason = NULL`。
- 在 5173 页面使用已配置的真实模型创建四条体验对话，完成五个 Turn：只点赞不填说明、点踩并填写说明、用户纠正流程后请求 Agent 标记，以及页面切换回归。独立 PG 回读确认五个非空最终输出、四条用户评价和一条 Agent 标记；工具审计为 completed，标记与纠正后回答绑定同一 Run，全部运行的 Skill 清单为空。管理员真实列表展示五条记录，来源与点赞筛选、原会话跳转、浅/深色和 375px 页面验证通过；体验对话保留供人工检查。
- `frontend/test/browser/feedbackModalKeepAlive.js` 在真实新对话中验证单弹窗、离页关闭、草稿保留与刷新恢复；`frontend/test/browser/feedbackNavigation.js` 用明确的浏览器响应延迟验证保存期间离开再返回、进入原会话关闭详情、离页后的迟到查询不导航，以及切换标签销毁后的迟到查询。延迟注入不改变服务端已有评价。独立 Review 的三个浏览器 oracle 在缺少退出处理时恢复对应弹窗与导航缺陷，修复后通过；在浏览器编译响应中移除列表销毁钩子后，离页时的迟到查询再次导航回原会话并使对应断言失败，恢复原响应后五项检查通过。
- 前端完整 unit 一次运行在 Vite 模块加载时出现 60 秒 transport timeout；随后在 frontend 容器重新运行，557 passed，0 failed。
- 独立 Review 的负向浏览器检查恢复了空白最终输出仍显示评价、详情关闭后焦点落到 BODY 的缺陷；修复后同一 DOM oracle 通过。布局修复前 1001px 下详情按钮越过面板右边界 26px，改为容器响应式后五档宽度无裁切。合成测试资源已按明确 ID 清理，截图为本地验收产物，不提交凭据或用户运行数据。

反馈与 Schema 测试及 worker E2E 已加入 `system-tests.yml`，本地确定性运行器包含同一 worker 用例。核心采集路径不调用 Langfuse 或 Skill 写入，未配置 Skill 的 worker 用例按空 Skill 清单完成；真实模型在用户明确纠正并要求记录时的标记已验证，无明确指令时是否适时自主标记及后续 Skill 改进效果不在本阶段验证范围。

补充验收：`frontend/test/browser/feedbackPresets.js` 在真实 5173 体验会话中通过多选原因与文字保存、总长度超过 2000 时阻止提交、关闭后无二次补充入口，以及管理员详情完整展示。独立 PostgreSQL 回读确认 `reason` 包含两项预设与补充文字。

Web 路由和统一基础工具的边界与验证由[Web 反馈与基础工具决定](2026-10-11-feedback-web-and-base-tools.md)维护。
