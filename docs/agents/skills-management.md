# 管理 Skills

Skill 是一个可复用的能力包，通常包含一个 `SKILL.md`、提示词、参考资料和可选脚本。智能体先看到 Skill 的描述，再按需要读取 `SKILL.md`；本地工具和 MCP 依赖随 Skill 激活进入模型请求。

## 什么时候用 Skill

把一类稳定、可复用的工作方式放进 Skill，例如文档处理、研究流程、报表生成或某个外部服务的操作规范。单次任务要求写在对话中；需要跨对话复用的规则和资料再考虑 Skill 或用户工作区文件。

## Skill 保存在哪里

| 来源 | 位置和事实 | 谁可以安装/管理 |
| --- | --- | --- |
| 内置 | 随代码发布，由 `app-init` 同步文件、元数据和依赖 | superadmin 可以启用/停用，不能删除或直接编辑 |
| 共享上传/远程 | 不可变 Skill 内容包 + PostgreSQL 当前引用和索引 | 管理员按共享范围管理 |
| Agent 专属 | Skill 持久目录 + 唯一 Agent 绑定 | 访问与管理权限跟随所属 Agent |
| 个人 | 当前用户 UserWorkspace 的 `agents/skills/<slug>` | 当前用户；不创建共享 Skill 数据库记录 |

运行时路径是：

- 共享和内置 Skill：`/home/user/skills/<slug>/`，对 Agent 只读；
- 个人 Skill：`/home/user/workspace/agents/skills/<slug>/`，位于当前用户的 UserWorkspace。

沙盒标准目录 `~/.agents/skills` 链接到 `/home/user/workspace/agents/skills`，CLI 全局安装与网页个人安装使用同一份持久文件。

同一用户有个人 Skill 和共享 Skill 使用同一个 slug 时，个人版本覆盖该用户看到的共享版本。删除个人版本后，如果共享版本仍可访问，会恢复共享版本。

## 对话组件 Skill

内置 `chat-widgets` 用于图片、图文卡片、柱状图、折线图和简短表单。内置“智能助手”预设预加载 `chat-widgets` 和 `knowledge-base`，从首轮获得完整说明；用户无需指定 Skill 或编写 JSON。其他未配置预加载的 Chatbot 通过 Skill 描述发现用途并按需读取正文。组件 Skill 遵循已选且可访问的资源范围。Skill 的组件字段和完整 JSON 示例由 [SKILL.md](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/builtin/chat-widgets/SKILL.md) 维护。

普通会话可以直接说“帮我直观比较这几类阅读时间”“展示这几张图片”“把这张图与说明并排展示”或“让我填写出行日期和偏好，提交后再安排行程”。纯图片展示优先使用单图或横向图片组；需要配合说明、参数或比较时，图片可以与 Card、Row、Col 组合。图文卡片使用已提供或已查询的真实图片地址，尚未读取画面时沿用用户描述。

自然选择和输出合法性取决于模型与请求语境。真实模型的验证与限制见[对话组件决定](../develop-guides/decisions/implemented/2026-10-12-chat-widgets.md#真实模型与天气数据)和[图文组合与表单决定](../develop-guides/decisions/implemented/2026-10-17-widget-forms-and-composition.md)。前端对非法 JSON 或未知属性明确显示错误，不修造模型输出。

在智能体配置中确认 Skills 选择包含 `chat-widgets`；使用默认“全部”的 Agent 会在下一次运行发现它，固定允许列表需要显式选入。启用联网查询工具后，在正式会话发送“帮我生成未来 7 天北京和无锡的天气分布，分别画一张折线图，显示每日最高温和最低温”。模型遵循 Skill 说明、查询数据，在最终回复中输出两张图；每张图带日期范围、单位和来源。工具触发审批时，按会话中的现有审批界面处理。数据查询失败时回复说明缺失，不生成假天气。最终回复中的图表随历史消息保存，刷新会话后继续渲染。

Agent 把组件放在完整闭合的 `widget` JSON 围栏中，聊天前端按有限组件子集渲染，支持数据图表、图片、间距/对齐、文本层次、标签变体、图标和表单。浅色使用白色卡片，深色使用炭灰卡片与细边框，语义颜色随主题调整。Form 支持文本输入、多行文本、单选和日期；默认表单卡片使用正文宽度，桌面短字段排成两列，多行备注及三项以上的单选占整行，提交按钮右对齐。显式 sm、lg 卡片保留指定宽度。点击提交先进行必填和日期范围校验，通过后将填写结果作为普通用户消息发送，模型收到后继续回答。Button 的字段沿用 ChatKit 命名，当前只支持发送消息，不直接调用业务接口。编辑器里的未发送草稿、图片和附件不随表单提交。旧列表的 compose 动作仍只追加草稿。生成期间和会话不能接收消息时控件只读；提交中、失败和已发送有明确反馈。未闭合围栏显示“组件生成中”，不合法的组件显示原因和原始内容。表单未提交内容只保留在当前页面，刷新后以会话中的用户消息为准。

## 创建 Skill

标准目录至少包含根级 `SKILL.md`：

```text
my-skill/
├── SKILL.md
├── tools/       # 可选脚本
└── prompts/     # 可选提示词或参考资料
```

`SKILL.md` 使用 YAML frontmatter：

```markdown
---
name: 文档整理
slug: document-cleanup
description: 按指定结构整理文档，并保留可核对的来源。
tool_dependencies: []
mcp_dependencies: []
skill_dependencies: []
---

# 文档整理

## 何时使用

说明这个 Skill 解决什么问题，以及什么时候不该使用。

## 操作步骤

写出 Agent 应遵循的步骤、限制和验收方式。
```

必填字段是 `name` 和 `description`，名称和显式 `slug` 最多 128 个字符。`slug` 可省略：符合机器标识格式的 `name` 直接作为 slug；中文展示名转为拼音，英文大写转小写，空格和其他分隔符转为短横线，派生结果最多 64 个字符；没有可用字母或数字时使用 `skill-` 加名称摘要。展示名保留原样，显式非法 slug 会被拒绝。

建议显式填写 slug，把自然语言名称和稳定标识分开。

依赖字段含义：

| 字段 | 作用 |
| --- | --- |
| `tool_dependencies` | 需要的内置工具 |
| `mcp_dependencies` | 需要的 MCP 服务器 |
| `skill_dependencies` | 需要先提供说明的其他 Skill |

个人 Skill 只作为用户文件读取，不解析这些平台依赖；需要依赖工具、MCP 或其他 Skill 时，安装为共享 Skill。

## 安装方式

进入“扩展 → Skills”，可以选择：

1. **推荐 Skill**：从推荐列表生成安装草稿。
2. **上传**：上传 ZIP 或单个 `SKILL.md`，解析后生成草稿。
3. **远程安装**：从 GitHub 仓库、ModelScope Skill 或合集拉取一个或多个 Skill。
4. **在线编辑**：编辑已有且有管理权限的共享 Skill 文件和依赖。
5. **Agent 内安装**：通过 `execute` 执行 `npx -y skills add <source> -g -y`，全局安装直接进入当前用户的个人目录；联网来源需要沙盒允许网络访问。

上传和远程安装都先解析为草稿，再选择个人或共享位置并确认。草稿中的可安装条目只包含已解析的临时包；远程拉取或解析失败会单独显示，不能被确认安装。确认前可以检查名称、说明、文件和依赖；取消草稿不会写入正式 Skill。共享安装在权限允许时默认全局共享，可在确认前改为部门或指定人；受限账号沿用后端提供的允许范围和默认值。

有管理权限的用户可在 Skill 详情页编辑文本文件。文件和“配置”中的依赖表单共用一份草稿；切换文件或页签保留修改，文件区域或运行依赖区域的“保存修改”一次提交全部内容。根文件的名称、描述及依赖声明同步到数据库索引，依赖选择也回写根文件。保存使用加载完整内容时取得的修订值；其他人修改任何文件后，页面拒绝覆盖并保留全部草稿，需要核对最新内容后重试。离开页面或刷新时，未保存的修改会触发确认。共享范围和启停状态分别保存。

后续 Run 从当前内容引用准备 Skill；运行期间，授权边界也会刷新可见资源、普通 Skill 的预加载说明和用户投影。Agent 专属 Skill 的根说明在本 Run 内保留准备时内容，脚本和参考文件按当前授权投影读取。新增本地工具依赖需要后续 Run 重新构图注册。

### Agent 专属操作指南

在已有智能体的编辑弹层选择“专属 Skill”，可以创建最小操作指南，或上传单 Skill ZIP。上传限 10 MiB，展开后限 50 MiB、2000 个条目。ZIP 仅用于首次导入；已有专属 Skill 时，通过编辑入口修改文件，上传接口返回 409 且保留原内容。

新建智能体时可以直接附带 ZIP，创建完成后在“专属 Skill”回读指南和资源。高级配置中也可选择或独立创建共享 Skill；提交与失败行为见 [创建智能体](./agents-config.md#创建智能体)。

专属入口直接预览只读 SKILL.md；“编辑”打开共用文件详情页。专属 Skill 自动预加载，系统提示词继续定义角色与回答要求。访问与管理权限跟随智能体，普通 Skill 列表和依赖候选隐藏该绑定；专属 Skill 不单独配置共享范围、停用或删除，资产随智能体删除。内置智能体同样支持专属 Skill；由于内置智能体不支持删除，其绑定持续保留，当前内容仍可编辑，历史仍可恢复和删除。它可依赖授权范围匹配的普通 Skill、工具与 MCP，普通共享 Skill 和其他专属 Skill不能引用它。

预加载根文本在本次 Run 内保留准备时内容；后续 Run 使用新内容。脚本和参考文件通过实时用户投影读取。同一用户的多个 Agent 共用用户投影，因此专属绑定用于配置与激活，不提供同用户不同 Agent 之间的文件保密边界。APP 终端用户的绑定读取跟随其 Agent 的显式可见性，其他 Skill 依赖仍按实际终端用户权限解析。详情见[工程决策](../develop-guides/decisions/implemented/2026-10-03-agent-bound-skill.md)。

### 专属 Skill 的历史版本

专属 Skill 使用固定的短 slug（新绑定为 `self-UUID8`），保存直接更新当前内容。在历史页点击“发版”，为当前已保存内容生成完整快照；有未保存草稿时先保存或放弃，不会自动保存。版本名由北京时间年月日与 UUID8 组成，例如 `20261004-a1b2c3d4`。

在历史版本中选择“恢复”并确认，会覆盖当前内容的全部文件和依赖配置，尚未发版的修改会丢失；需要保留时先发版。恢复不会创建新版本，也不改变 Agent 绑定。历史包只读，可单独删除，删除快照不会改变当前内容；删除 Agent 会清理当前包与全部历史。有未保存草稿时先保存或放弃修改，再恢复历史。普通 Skill 复用完整内容编辑功能，不提供历史版本入口。

恢复会校验当前整包修订、历史文件完整性和依赖权限。修订冲突时刷新后核对内容再恢复；历史依赖不能覆盖当前 Agent 共享范围时，调整共享范围或修改当前内容后重新发版。内容保存后，下一次执行准备按当前引用刷新用户投影。运行中的根说明与辅助文件仍遵循[现有运行加载机制](../mechanisms/agent-runtime.md)。

### 远程来源限制

管理员在“设置 → 基本设置 → Skill 配置 → 远程来源白名单”中维护 `remote_skill_source_policy.allowed_hosts`。默认允许 `github.com` 和 `modelscope.cn`；主机名必须精确匹配，保存空列表会关闭管理页远程安装。此设置不约束用户在沙盒中自行执行 CLI 的网络访问。

示例来源：

```text
anthropics/skills
https://github.com/anthropics/skills
https://modelscope.cn/skills/@anthropics/pdf
https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills
```

除了按仓库或合集拉取，也可以在远程安装对话框切换到“全局搜索发现”，输入关键词检索 `skills.sh` 上的开源 Skills，再选择结果批量拉取和安装。搜索和拉取使用操作者的持久 Cube VM，在每个请求专属的临时 HOME 与工作目录中运行；请求结束清理该目录，保留用户 VM。选中结果后的实际拉取仍经过来源白名单和安装确认流程。

GitHub 的 `owner/repo` 简写会被转换为 HTTPS 地址。远程下载复用用户环境，临时 HOME 只隔离本次 CLI 的配置和文件，不提供一次性虚拟机隔离。宿主安装边界在接收内容时拒绝绝对路径、路径穿越与符号链接，并限制文件数、目录深度和总大小。来源白名单限制产品允许的地址，不充当网络出口防火墙；安装前需审查来源及脚本权限。

### 新增内置 Skill

在 `backend/yuxi/modules/extensions/skills/builtin/<slug>/` 新增目录，至少包含 `SKILL.md`。启动同步按目录名排序发现直接子目录，忽略下划线或点开头的目录；无需修改 Python 注册清单。

`SKILL.md` frontmatter 唯一拥有名称、描述、版本和依赖。`slug` 必须与目录名一致，省略时使用 `name`；`version` 省略时为 `1.0.0`，建议使用引号包裹版本字符串。工具、MCP、Skill 依赖使用本页定义的字段。缺少根文件或元数据不合法时，启动同步明确失败。

`app-init` 部署任务同步文件、元数据和依赖，保留数据库中的启停状态；API 与 worker 等待同步成功后启动。发布后在“扩展 → Skills”核对新增项的说明和依赖，新增脚本或资源也必须随发行包携带。内置内容在源码目录维护，用户投影由运行准备按授权刷新。

安装前仍应审查 Skill 的提示词、脚本、依赖和网络行为。不要把数据库密码、云平台密钥或 Cube 控制凭据放进 Skill 或 Agent 环境。

## 依赖和加载时机

Agent 的 `skills` 配置语义（`"all"`、固定数组与默认值）由[资源选择契约](./agents-config.md#资源选择语义)拥有，本页描述激活时机。每个 Run 准备 Context 时，系统按权限解析共享选择，合并当前用户全部个人 Skill，再展开 `skill_dependencies`；同树会话使用相同规则。`skills=[]` 时个人 Skill 仍进入模型可见的描述列表。个人安装不修改 Agent 配置，后续 Run 自动发现新增个人 Skill；运行中也在模型和工具边界重新解析当前权限与 Skill 资源，本地工具的执行注册仍由该 Run 构图拥有。

### 普通渐进加载

1. 创建 Graph 前，模型得到有效 Skill 的名称、描述和 `SKILL.md` 路径。
2. 模型读取某个可见 Skill 的 `SKILL.md` 后，该 Skill 进入 `activated_skills`。
3. 后续模型请求加入它声明的本地工具，并加载已启用的 MCP 依赖服务器提供的工具。

模型没有读取的 Skill 的依赖工具继续隐藏。未激活的 Skill 本地工具即使已注册到 ToolNode，也不能被模型调用。

### 预加载

Agent 配置可以用 `preload_skills` 指定少量需要从首轮就可用的 Skill。预加载选择器只列 `skills` 中当前用户可访问的共享和内置 Skill；系统会展开其依赖闭包，读取根级 `SKILL.md`，并从首轮模型请求开放本地工具和已启用的 MCP 依赖。

预加载的根文件缺失或不可读时，Graph 创建会明确失败，不会静默退回渐进加载。通用配置的预加载默认为空列表，内置角色可在预设中指定预加载项。预设默认值仅在角色首次初始化时写入，已有角色通过配置页修改；详见[智能助手预加载决定](../develop-guides/decisions/implemented/2026-10-17-default-chatbot-preloaded-skills.md)。选择“选择全部”时，每次运行预加载当前 Agent 已选且可访问的共享 Skill 及其授权依赖。同名个人版本覆盖共享版本时读取个人内容；其他个人 Skill 始终可发现，正文按需读取。固定列表和全部模式均遵循[资源选择契约](./agents-config.md)。

## 权限和选择

共享 Skill 使用 `source_type`、`share_config` 和 `enabled` 表达来源、范围和启用状态。范围使用 version 2 的 `read_scope`、`manage_scope`，可以是全局、部门或指定用户；范围关系遵循[资源权限的同类型子集规则](../mechanisms/resource-permissions.md#共享资源)。

| 用户 | 可见和使用 | 可管理 |
| --- | --- | --- |
| `superadmin` | 全部允许的共享/内置 Skill | 全部共享 Skill；内置 Skill 维护 |
| `admin` | 命中读取范围且已启用的 Skill | 自己的或同时命中读取和管理范围的非内置 Skill |
| 普通用户 | 命中读取范围且已启用的 Skill | 自己的个人 Skill；共享 Skill 只读 |

普通用户安装的新 Skill 固定进入个人来源，不配置共享范围。管理员安装到共享来源时才会写入 PostgreSQL 索引，并可以配置部门或用户范围。扩展管理页展示当前用户可访问的共享和个人 Skill；Agent 配置选项只展示可访问的共享和内置 Skill。后端在保存时校验共享引用，运行时按当前用户身份加载个人目录。

Agent 配置中的 Skill 选择不能扩大用户的文件、知识库或 MCP 权限。

## 内置 Skill 说明

系统启动时同步仓库内置 Skills。对话里的图表、图片卡片、推荐列表由 `chat-widgets` 维护，使用方法见[对话组件 Skill](#对话组件-skill)。旧 `html:preview` 围栏作为代码展示，移除行为与重建环境的前提由[移除决定](../develop-guides/decisions/implemented/2026-10-12-remove-inline-html-preview.md)维护。

内置 `deep-research` 默认在当前 Workdir 中合适的位置生成独立、响应式的 HTML 阅读文档，并通过交付物入口展示；用户明确指定其他格式时除外。宽屏报告可以提供侧栏目录，窄屏隐藏或折叠侧栏；报告可以按内容需要使用外部图片等公开资源，来源以普通链接呈现。

## 运行时文件行为

解析结果的 `source_type` 记录安装来源，`source_scope` 区分个人、共享和专属范围，内部 `source_dir` 指向真实文件；列表中的 `overrides_shared` 与 `shadowed_by_personal` 分别描述个人卡片和被覆盖共享卡片，管理页仍需要显示后者。整包编辑和版本恢复比较包含路径、文件字节及可执行位的目录 `revision`；单文件编辑比较该文件 sha256，根说明接口比较 `SKILL.md` sha256。`content_hash` 始终使用整目录算法，内置同步与安装包一致。修订冲突返回领域冲突，HTTP 层映射为 409。

远程安装和安装确认草稿共用远程下载、白名单及提取边界。网页个人安装采用创建型原子发布，同名目录即使为空也失败；共享内容先发布唯一不可变包再提交数据库引用，失败清理未提交包；内置更新保留旧目录用于失败恢复。投影按 uid 锁定并核对完整授权集合：单项变更先撤销旧投影，再重建用户集合，失败保留撤销结果并由下一次执行刷新补偿。不同发布点保留各自的覆盖与撤权规则。沙盒 CLI 直接维护个人文件，其覆盖和更新遵循 CLI 自身语义。

管理页远程 CLI 的临时 HOME 按请求清理；递归删除遇到符号链接时只移除链接本身，不访问目标。文件读取、复制和父目录遍历继续拒绝符号链接。CLI 的 Node/npm 与 Git 依赖由[沙盒模板](../advanced/sandbox-operations.md)提供。

共享和内置 Skill 投影对 Agent 只读，但沙盒命令仍可能执行其中的脚本。脚本如需写文件，应写入当前 Project Workdir 或 User Data，而不是 Skill 目录。个人 Skill 直接从当前用户工作区读取，不会复制到共享投影。

Skill 的选择影响 Prompt 和工具激活；共享投影按用户授权集合生成，不会因为某个 Agent 选择了 Skill 就改变 Sandbox 身份。路径穿越、沿符号链接访问和跨用户访问由文件系统边界拒绝。

## 维护建议

- slug 使用小写字母、数字和单个短横线，尽量短且能表达用途。
- `description` 先说明能力和适用场景，再写实现细节。
- 把“何时使用”“不要做什么”“产物放在哪里”和“如何验收”写进 `SKILL.md`。
- 依赖链保持短小，避免循环依赖；只有真实需要时才声明工具或 MCP 依赖。
- 脚本按不可信输入处理，不读取或输出运行环境中的秘密。
- 修改共享 Skill 的依赖、范围或文件后，用一个真实 Agent Run 验证模型可见工具和最终产物。

## 源码定位与验证

[Skill 访问契约](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/access.py)拥有权限、依赖与带锁读取，[内容编辑](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/edit.py)拥有修订校验与不可变包发布，[安装草稿](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/install_draft.py)拥有安装确认前的暂存。[内置同步](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/builtin/sync.py)由 `app-init` 调用；[运行时解析](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/skills/runtime.py)和[Skills middleware](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/middlewares/skills.py)分别拥有资源快照与模型可见性。

[编辑 HTTP 测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_shared_skill_edit_router.py)覆盖修订冲突和保存结果，[投影集成测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/services/test_user_skill_projection.py)核对授权后的实际文件，[远程安装测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/sandbox/test_remote_skill_install.py)检查 CLI 全局安装后的个人文件和运行时发现。源码和固定测试不能证明第三方 Skill 的当前内容安全，安装前仍需审查目标包。
