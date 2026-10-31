# 受限个人助理复用现有 Agent 与会话运行机制

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/personal_assistants.py

## 问题

用户需要自己的持续对话助手，能够查阅网页、保存工作记录和委派具体任务。通用 Agent 的配置与管理界面过宽，不能保证专用助理模型和受限编辑范围；另建运行时和记忆平台又会重复现有能力。

## 决策

### 实现方案

登记表只保存用户、Agent、Project 和主会话的必要关联。创建用例锁定有效人类用户，在单个 PostgreSQL 事务中创建私有 PersonalAssistantAgent、关联目录的隐式 Project、主会话及登记；唯一约束兜底并发创建。`assistant:{uid}` 是无损逻辑标识，内部 Agent slug 使用固定前缀加随机 UUID，不规范化或截断 uid；身份判断查询登记，不能根据 slug 前缀猜测。

助理 GET/POST/PATCH 接口沿用现有认证和用户身份。普通 Agent 列表不列出助理，通用编辑、Skill 绑定和定时任务沿用普通授权；删除登记 Agent 和归档固定主会话仍受生命周期约束；专用编辑仅接受名称、头像和个性化提示词。会话和历史访问沿用普通用户、APP 与资源权限，不另加 JWT-only、API Key 禁用或登记 Project 复核。列表隐藏不改变已知会话的访问权限。个人助理不提供历史检索工具，也不新增专用历史查询路径。

`personal_assistant_model` 由 superadmin 的 Options 配置拥有，与普通 `default_model` 分开。新建会话或主会话首次输入按专用默认值补齐模型，已有会话保留模型和审批选择；显式输入覆盖遵循普通协议。资料编辑同步主会话名称和个性化说明，已接收输入、运行中 steer 和恢复使用既有快照。实际模型未配置或不可用时，可以创建和编辑助理、查看历史，新工作明确失败。配置细节见[交互与执行边界收敛](2026-10-31-personal-assistant-policy-simplification.md)。手动压缩使用普通流程读取 Session 配置。

`YUXI_PERSONAL_ASSISTANT_ENABLED` 默认关闭，API/worker 启动验证同一环境配置；页面与 Options 入口、能力发现、专用接口以及启动时的 Backend 注册消费开关。关闭后保留数据，不能启动助理模型执行；不支持运行中动态撤销。迁移由 `migrations/personal_assistants.py` 显式将 business schema v1 升到 v2，启动不静默迁移，旧数据不重建；部署顺序由[生产部署](../../../advanced/deployment.md#可选启用个人助理)维护。

工作目录通过 UserWorkspace 的 no-follow 操作创建在 `agents/assistant`，Project 保存相对路径，执行时沿用普通 Project 绑定解析。个人助理复用普通用户文件系统和沙盒；Workdir 决定默认目录，不是额外的用户内隔离根。现有 `agents/AGENTS.md`、`agents/USER.md`、只读用户记忆沿用 Context 装配，具体工作记录按需读取，不自动注入，也不另建记忆系统。

直接业务工具默认为 `web_search`、`web_fetch`；共享 Skill、预加载 Skill、直接 MCP 与知识库选择默认为空，个人 Skill 及其按需依赖沿用普通 Agent 规则。最小文件与协作能力继续复用普通 executor 授权。专用 Backend、显式用户沟通、独立 root 委派、steer 和右侧完整轨迹由[运行机制决定](2026-10-30-personal-assistant-runtime.md)拥有。

前端复用 SessionWorkspace 和消息渲染，隐藏资源提及、通用模型和工具配置；保留加号选图、粘贴或拖拽图片、附件上传。入口位于主导航的对话下方、智能体上方，使用固定图标，每次进入从登记读取固定主会话，编辑通过独立轻量界面完成。普通列表、搜索、置顶和历史目录候选在数据库分页前排除个人 Agent 会话；委派给普通 Agent 的独立任务仍遵循普通会话规则。会话实际选用模型失效禁止新执行，但不阻止已冻结运行的合法 steer。

创建页只呈现名称、五个原创预设头像和上传入口。消息采用轻量气泡；主对话右上角按钮切换资料卡片，集中呈现头像、名称、状态和编辑入口。卡片在空间足够时与正文平铺，较窄时悬浮在按钮下方；定位与间距由[资料卡片布局](2026-10-31-personal-profile-layout.md)拥有。输入框和发送按钮默认同一行。编辑器使用浏览器自然排版随内容和容器宽度增高，达到上限后内部滚动，避免依赖反复测量高度；显式换行、删除、清空和输入法沿用现有编辑器事件语义。

### 交互与 Prompt 的一手依据

实际查看 [Meta 的 Muse 设计文章](https://introducing.muse.ai/)及其中头像、状态与活动截图：采用姓名/头像的个人身份、连续主对话、紧邻身份的状态和按需展开活动；不复制 Goals、后台主动工作或外部审批系统。[Dots 官方入门帮助](https://help.openai.com/en/articles/20001530-getting-started-with-your-dot)确认创建时命名、头像/名称进入资料编辑。[Dots 官方产品页](https://chatgpt.com/features/dots/)公开的 Felipe 对话图片可直接在浏览器查看，呈现个性角色、浅灰助理气泡和强调色用户气泡；结合用户提供的桌面参考图，采用柔和气泡和紧凑输入框。产品页本身的浏览器访问受限，未观察到的完整登录后界面不作为视觉事实。预设头像使用原创 SVG，不依赖竞品资源或外部图片服务。

固定 prompt 描述身份、证据、工作记录、委派和授权；用户个性化说明及工作区文本置于其后，不能修改平台能力。未经明确要求，不发送、发布或修改业务数据；草拟只交付草稿。下面项目的具体提示词及装配文件已直接读取，采用行为思想并重新表述，没有复制完整提示词：

- OpenClaw，提交 `20121545966a5212dc3783a0375333fb6cd29eeb`：[身份](https://github.com/openclaw/openclaw/blob/20121545966a5212dc3783a0375333fb6cd29eeb/docs/reference/templates/SOUL.md)、[工作约定](https://github.com/openclaw/openclaw/blob/20121545966a5212dc3783a0375333fb6cd29eeb/docs/reference/templates/AGENTS.md)、[系统提示词装配](https://github.com/openclaw/openclaw/blob/20121545966a5212dc3783a0375333fb6cd29eeb/src/agents/system-prompt.ts)、[上下文装配](https://github.com/openclaw/openclaw/blob/20121545966a5212dc3783a0375333fb6cd29eeb/src/agents/system-prompt-context-files.ts)、[MIT 许可证](https://github.com/openclaw/openclaw/blob/20121545966a5212dc3783a0375333fb6cd29eeb/LICENSE)。借鉴直接沟通、使用已有上下文、区分工作记录和长期信息、外发先确认；不采用渠道、定时主动联系和自动推送。
- Letta Code，提交 `a8900b882446d7aa99fedf2845d400743affd1b6`：[提示词](https://github.com/letta-ai/letta-code/blob/a8900b882446d7aa99fedf2845d400743affd1b6/src/agent/prompts/letta.md)、[提示词解析](https://github.com/letta-ai/letta-code/blob/a8900b882446d7aa99fedf2845d400743affd1b6/src/agent/system-prompt-resolution.ts)、[本地记忆编译](https://github.com/letta-ai/letta-code/blob/a8900b882446d7aa99fedf2845d400743affd1b6/src/backend/local/system-prompt-compilation.ts)、[Apache-2.0 许可证](https://github.com/letta-ai/letta-code/blob/a8900b882446d7aa99fedf2845d400743affd1b6/LICENSE)。借鉴精简记忆、按需回查和有目的的协作；不采用自行改写系统规则、Git 同步记忆、后台反思和主动定时唤醒。


## 替代方案

- 只保存一份通用 Profile：无法阻止模型、工具和协作覆盖，管理界面也超出用户需要。
- 直接把 `assistant:{uid}` 作为现有 slug：与现有字符和长度契约冲突；内部存储标识与逻辑标识分离更小。
- 另建 Personal runtime、记忆索引或事件系统：增加状态所有权与部署负担，当前消费者不需要。
- 将任务建为助理的子会话：会继承助理配置、目录与协作树生命周期；采用按普通可见性授权的 Agent 独立根会话，具体取舍见[运行机制决定](2026-10-30-personal-assistant-runtime.md)。

## 后果

新增登记表与一条显式数据库升级路径，不增加依赖、队列或运行引擎。个人助理与普通 Agent 共用底座，但登记身份在配置、运行和数据查询边界有明确分支。本范围不提供对外助理 API、共享 Skill 配置面板、连接器、邮箱、定时唤醒或主动推送；委派目标继续受普通 Agent 可见性授权约束。

助理工作记录保存在固定目录，委派任务使用各自的隐式 Project 与独立目录，不继承助理配置。现有整文件写入不提供协作编辑合并。模型的自然语言行为仍有不确定性，固定 prompt 不充当权限边界，也不能保证所有回答质量。

## 验证

- 真实隔离 PostgreSQL／HTTP 检查覆盖并发创建、唯一绑定、受限编辑、用户与 APP 隔离、关闭后保留数据、迁移幂等性、模型快照及协作可见性；登记、协作、Memory、Schema 与对话提及定向集合为 45 passed。授权、模型及锁序的负向证据由[交互与执行边界收敛](2026-10-31-personal-assistant-policy-simplification.md)维护。
- 个人助理 E2E 使用真实 API、worker、PostgreSQL 和文件系统，确定性模型重放下 3 passed：核对静默内容与显式发送、普通输入与取消、整树停止及继续、独立委派的配置和目录、成功文件与失败终态。命令为 `uv run --no-sync --group test pytest test/e2e/test_personal_assistant_e2e.py -q -p no:cacheprovider`，在隔离 Compose API 容器内执行。
- 真实供应商的文件读写探针运行于首版 ChatbotAgent 路径，证明共用底座的 API→worker→文件回读；该历史条件不证明当前 PersonalAssistantAgent 的沟通与委派语义。
- 页面检查覆盖浅／深主题、响应式导航、资料卡片及阅读位置；前端单测、lint/typecheck/build、后端 Ruff、工程契约及文档构建通过。重排与资料卡片的直接证据分别由[阅读位置决定](2026-10-31-chat-reading-position.md)和[资料卡片布局](2026-10-31-personal-profile-layout.md)维护。

未验证：未运行完整 E2E 套件或 CI、生产备份恢复演练、长期对话质量、个人身份的人工审批恢复和自动摘要完整链路。当前 Backend 未完成真实供应商的沟通、委派及“只起草不发送、子报告无成果不算完成、恶意网页不扩大授权”等自然语言样例盲测；确定性模型与 Prompt 均不保证任意真实模型的行为质量。
