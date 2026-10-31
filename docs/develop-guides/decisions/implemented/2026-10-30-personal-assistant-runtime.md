# 个人助理的显式沟通与独立会话委派

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/runtime/agent_backends/personal_assistant/graph.py

## 问题

个人助理与普通 Agent 共用持久执行，但长期主对话有不同的沟通和委派方式。将普通 assistant 正文直接显示给用户，会让后台检查也产生打扰；将工作创建为 child，会继承助理配置、Project 和协作树生命周期。持久等待工具还会阻止用户在协作等待中发送 steer。

本决定补充并部分替代[个人助理首版](../implemented/2026-10-29-personal-assistant.md)的 Backend、协作和入口安排，不扩展外部消息、定时唤醒、主动推送、Public API、沙盒或记忆平台。

## 决策

### 实现方案

个人助理使用 `PersonalAssistantAgent`，与 Chatbot 分别继承 BaseAgent，各自声明 Context、Prompt、状态和能力中间件；公共执行机制由 runtime/graph.py 组合，见[执行图装配决定](2026-10-30-agent-graph-composition.md)。Input、Receipt、Turn、Run、worker、checkpoint、文件系统和沙盒继续共用。登记关系保存用户、Agent、Project 和主会话；通用 Agent 创建入口不提供此 Backend。直接业务工具默认为 `web_search`、`web_fetch`，最小文件记录和协作工具保留；共享 Skill、预加载 Skill、直接 MCP 和知识库选择默认为空。个人 Skill 自动加入，依赖工具和 MCP 继续按普通激活规则开放，运行授权仍在 executor 执行。

主聊天投影用户消息、成功 `send_to_user(content)` 的工具结果，以及成功创建会话的入口气泡。发送内容复用已有工具结果的消息持久化和 SSE，成功写入后才显示，不从调用参数猜测已经发送。普通 assistant 正文、推理和失败的发送工具只保留在完整轨迹；静默结束仍有正常 Turn/Run 终态，不补造对用户的回答。

助理页面发送时明确指定 steer，后端沿用请求模式；空闲时形成新 Turn，运行中在既有安全边界接管，附件和独立 Input 保留。主界面不提供停止、暂停或队列控制；取消、停止、撤回待处理输入和恢复队列沿用普通服务。助理不装配 `wait_inputs`、`wait_sessions`。独立任务完成后只更新其真实状态，本范围不自动唤醒助理、不产生返回 Input，也不承诺用户离开后自动汇报。

助理的协作工具由[公共协作工厂](2026-10-31-cooperation-tool-ownership.md)装配，`PersonalAssistantMiddleware` 只负责 `send_to_user`。`create_session` 只暴露名称、任务描述和目标 Agent，调用共享服务的独立任务入口，固定 root 和新 Project；目标按普通 Agent 可见性校验。省略目标时使用普通默认 Agent；目标使用自己的模型、工具和提示词配置，不复制助理个性化提示词。任务的背景、资料、范围和交付要求由 `description` 作为首条输入传递，创建入口不接受提示词覆盖。可信审批模式沿用用户当前授权。

任务默认无已选择的 Project，复用普通聊天的 implicit managed Project/Workdir；数据库已有非空 Project 约束不变。助理仍在用户空间 `agents/assistant` 保存长期工作记录。Project 目录分配由 workspace service 持有自己的分配锁，协作沿用 Project、Session 的既有锁序，以来源 Run 和工具调用 ID 复用稳定会话及 Input，提交后再派发。root 的 parent 为空，tree root 为自身；保留 `created_by_run_id` 和 Input 来源。

协作 repository 沿用普通用户和 APP 作用域；同一授权范围的会话可双向查询和投递消息，不增加助理专用身份或目录检查。助理不消费协作收件箱来唤醒自身。任务列表按来源主会话的 Run 查询，不扫描 checkpoint，不新建任务表。委派关系用于列表归类，不能替代普通会话的读取权限。

入口位于主导航的对话下方、智能体上方，收起为带提示的固定图标，展开为现有导航按钮；页面使用独立 `/assistant` 路由。主聊天沿用窄布局、附件和轻量资料编辑。成功 create_session 的持久结果投影为带名称的会话气泡，点击通过已有导航授权在右侧查看轨迹；失败、执行中或缺少会话身份的结果不生成入口，刷新从原始工具结果恢复，不新增消息或状态存储。右侧复用普通 `SessionWorkspace` 的完整历史、流和审批呈现，默认只读，不继承个人消息过滤；个人助理不装配文件树标签，显式文件预览仍可打开。点击任意授权任务或主会话轨迹不离开助理页，刷新通过 `session_id` 恢复。关闭面板或最后一个轨迹标签清理此查询参数并收起侧栏。重排阅读定位见[对话阅读位置](2026-10-31-chat-reading-position.md)。

旧登记 Agent 使用显式离线升级切换 Backend，保留 slug、主会话和历史；只在旧 Backend 没有活跃执行或清理责任时切换。升级幂等，已经完成切换的 Agent 不因当前新执行而阻止重放。旧 child 历史保留，不通过清空 parent 字段伪装成 root。

## 替代方案

- 普通正文直接作为聊天：不能支持检查后静默结束；采用显式发送工具。
- 保留 child 或自克隆：配置和生命周期仍属于助理树；采用普通 Agent 的独立 root。
- 允许助理指定任务角色或覆盖目标提示词：任务说明已由 `description` 承载，额外覆盖会改变目标 Agent 的配置语义；移除工具与服务中的覆盖参数、校验和快照写入分支。目标 Agent 自身的配置编辑能力保留。
- 委派后调用等待工具：会改变 steer 准入；主对话采用不挂起的查询方式，自动唤醒另行设计。
- 新建消息表、任务平台和执行后端：现有工具结果、会话、Input 和执行机制已有事实 Owner，无须重复实现。

## 验证

交互限制、配置默认值和最终回归以[交互与执行边界收敛](2026-10-31-personal-assistant-policy-simplification.md)为准；下列早期停止拒绝断言不再是当前契约。

- 委派参数清理在独立 Compose API 容器中执行：`uv run --no-sync --group test pytest test/unit/agents/test_personal_assistant_runtime.py test/unit/middlewares/test_cooperation_middleware.py -q`：47 passed；`uv run --no-sync --group test pytest test/integration/services/test_personal_assistant_cooperation.py -q`：3 passed，真实 PostgreSQL 回读默认与显式目标的配置、任务原始输入及幂等回执；`uv run --no-sync --group test pytest test/e2e/test_personal_assistant_e2e.py -q`：3 passed，真实 HTTP/worker 回读目标配置、成功文件和失败终态。确定性模型验证执行协议，不证明真实供应商输出质量。
- 负控在独立测试进程恢复旧工具定义，`test_personal_graph_registers_only_supported_tools` 因 schema 多出 `system_prompt` 明确失败。旧能力不存在：工具和服务创建参数、覆盖校验、快照覆盖、幂等比较、助理角色提示及重放字段全部移除，调用链搜索无残留。重新引入条件：有明确且不能通过任务描述表达的产品需求，并单独评审目标配置语义。
- 完整回归：容器以 `YUXI_KNOWLEDGE_ENABLED=true` 执行 `uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：3528 passed、125 skipped（容器缺少仓库根文件）；只读挂载完整仓库并设 `YUXI_ENV_FILE=.env` 后执行 `python -m pytest test/unit/config -q -p no:cacheprovider`：196 passed、11 skipped（缺少 Docker CLI）。宿主虚拟环境未安装 pytest，测试使用已有 API 镜像；配置补跑首次受槽位 env 文件路径影响失败，纠正测试环境后通过。
- 工程契约检查及其 56 项测试、全源码 Ruff 检查/格式/导入检查、相对链接检查和 `docs` 下 `pnpm run build` 通过。
- 会话气泡：前端全量 996 项通过，投影与分组定向 5 项通过；lint、类型检查及构建通过。真实浏览器验证实时创建、刷新恢复、键盘打开右侧轨迹，浅色、暗色及 1280/1024/768px 无溢出。创建成功且没有 send_to_user 正文时入口仍可见。

下列为首版运行机制的验证记录。服务边界与权限收敛后的验证由[服务收敛决定](2026-10-30-personal-service-convergence.md)记录，旧限制断言不作为当前权限依据。

- 工程契约脚本及其 56 项测试通过。后端全量 unit：3525 passed、125 skipped；测试进程使用默认 Knowledge 能力，运行中的独立环境保持 Knowledge 关闭。前端全量 unit：994 passed；lint、类型检查、构建及文档构建通过。
- 真实 PostgreSQL/HTTP：登记与迁移 10 例、协作可见性和消息 2 例、历史隔离及 Schema 29 例通过。迁移负例包括 pending Input、running/waiting Turn、interrupted Run 的清理责任；完成切换后重放不再检查新图的活跃执行。
- 真实 API、worker、PostgreSQL 和文件系统 E2E 3 例通过：静默正文保留在原始历史，显式发送来自成功工具输出；主对话停止入口拒绝；委派的 Session/Project/快照/来源回读符合独立 root，成功产物回读与失败终态均核验，完成不产生助理唤醒 Input。
- 无请求拦截的真实浏览器验证显式沟通与静默、主对话恢复、任务和主会话完整轨迹、运行中 steer、资料编辑、上传菜单、输入自适应、375/768/1024/1440 宽度、明暗主题及收起导航。右侧只读问询保留已有恢复接口，选项提交后真实任务形成完成终态；真实附件上传和图片粘贴保留在同一 Input，运行完成后从持久历史回读图片。
- 独立 Astra Reviewer 审查完整需求与 Diff，并复核消息投递方向、旧图迁移、控制拒绝及只读问询修复；未发现剩余明显问题。

验收模型为本地确定性 OpenAI 协议重放，业务 API、数据库、worker 和用户文件系统均真实执行。此证据不验证真实供应商联网可用性、任意模型的 Prompt 遵循质量或用户级高并发负载。运行命令与可重复场景由 `test/e2e/test_personal_assistant_e2e.py`、`test/browser/personalRuntime.js` 和 `test/browser/personalTaskAnswer.js` 拥有。

## 后果

独立任务使用目标 Agent 的配置快照，任务约束只经 Input 进入会话。已创建会话及其历史配置快照保留，不因移除创建参数而回写历史数据。

独立 root 使用各自的树执行额度，现有树并发限制不代表用户级全局并发限制。任务使用普通 Agent 能力，固定 Prompt 的“未经授权不行动”不等于所有可选工具都有审批；实际动作继续受现有 executor 权限和审批范围约束。确定性模型验证协议和状态，不证明真实模型一定遵循沟通偏好。
