# 管理 Skills

Skill 是一个可复用的能力包，通常包含一个 `SKILL.md`、提示词、参考资料和可选脚本。智能体先看到 Skill 的描述，再按需要读取 `SKILL.md`；Skill 声明的工具和 MCP 依赖会随激活状态加入模型请求。

## 什么时候用 Skill

把一类稳定、可复用的工作方式放进 Skill，例如文档处理、研究流程、报表生成或某个外部服务的操作规范。单次任务要求写在对话中；需要跨对话复用的规则和资料再考虑 Skill 或用户工作区文件。

## Skill 保存在哪里

| 来源 | 位置和事实 | 谁可以安装/管理 |
| --- | --- | --- |
| 内置 | 随代码发布，启动时同步索引 | 管理员可以启用/停用，不能删除或直接编辑 |
| 共享上传/远程 | Skill 持久目录 + PostgreSQL 索引 | 管理员按共享范围管理 |
| 个人 | 当前用户 UserWorkspace 的 `agents/skills/<slug>` | 当前用户；不创建共享 Skill 数据库记录 |

运行时路径是：

- 共享和内置 Skill：`/home/gem/skills/<slug>/`，对 Agent 只读；
- 个人 Skill：`/home/gem/user-data/agents/skills/<slug>/`，位于当前用户的 UserWorkspace。

同一用户有个人 Skill 和共享 Skill 使用同一个 slug 时，个人版本覆盖该用户看到的共享版本。删除个人版本后，如果共享版本仍可访问，会恢复共享版本。

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

必填字段是 `name` 和 `description`。`slug` 可省略，省略时直接使用 `name`，因此省略 slug 时 `name` 本身也必须是小写字母、数字和单个短横线组成的值；中文或带空格的展示名称会校验失败。建议显式填写 slug，把自然语言名称和稳定标识分开。名称和 slug 最多 128 个字符。

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
5. **Agent 内安装**：主智能体使用 `install_skill` 把 Skill 安装到当前用户的个人来源；子智能体不能使用该工具。

上传和远程安装都先解析为草稿，再选择个人或共享位置并确认。确认前可以检查名称、说明、文件和依赖；取消草稿不会写入正式 Skill。

### 远程来源限制

管理员在“设置 → 基本设置 → Skill 配置 → 远程来源白名单”中维护 `remote_skill_source_policy.allowed_hosts`。默认允许 `github.com` 和 `modelscope.cn`；主机名必须精确匹配，保存空列表会关闭远程安装。

示例来源：

```text
anthropics/skills
https://github.com/anthropics/skills
https://modelscope.cn/skills/@anthropics/pdf
https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills
```

除了按仓库或合集拉取，也可以在远程安装对话框切换到“全局搜索发现”，输入关键词检索 `skills.sh` 上的开源 Skills，再选择结果批量拉取和安装。搜索在一次性 Sandbox 中执行；选中结果后的实际拉取仍经过来源白名单和安装确认流程。

GitHub 的 `owner/repo` 简写会被转换为 HTTPS 地址。远程来源会在不继承全局或用户环境变量的一次性 Sandbox 中下载和提取，系统会拒绝绝对路径和路径穿越，并限制文件数、目录深度和总大小。来源白名单限制产品允许的地址，不是网络出口防火墙。

### 新增内置 Skill

在 `backend/package/yuxi/agents/skills/buildin/<slug>/` 新增目录，至少包含 `SKILL.md`。启动同步按目录名排序发现直接子目录，忽略下划线或点开头的目录；无需修改 Python 注册清单。

`SKILL.md` frontmatter 唯一拥有名称、描述、版本和依赖。`slug` 必须与目录名一致，省略时使用 `name`；`version` 省略时为 `1.0.0`，建议使用引号包裹版本字符串。工具、MCP、Skill 依赖使用本页定义的字段。缺少根文件或元数据不合法时，启动同步明确失败。

API/worker 启动时同步文件、元数据和依赖，保留数据库中的启停状态。重启后在“扩展 → Skills”核对新增项的说明和依赖；新增脚本或资源也必须随发行包携带。源码目录与共享投影分别拥有发布内容和安装文件，编辑应落在源码目录。

### 内置 `html-preview`

系统启动时会同步仓库内置 Skills。`html-preview` 用于在普通 Markdown 难以清晰表达指标、对比、流程、时间线或层级关系时，指导 Agent 输出静态 `html:preview` 围栏；普通 HTML 源码仍使用 `html` 代码块。前端会把该围栏清洗后放入 sandboxed iframe 预览，不依赖额外工具。

未显式配置 Skills 的 Agent 按现有资源规则自动获得该 Skill；使用显式 Skills 允许列表的 Agent 需要选择 `html-preview`。

内置 `deep-research` 不依赖 `html-preview`。它默认在当前 Workdir 的 `outputs/` 目录生成独立、响应式的 HTML 阅读文档，并通过交付物入口展示；用户明确指定其他格式时除外。宽屏报告可以提供侧栏目录，窄屏隐藏或折叠侧栏；报告可以按内容需要使用外部图片等公开资源，来源以普通链接呈现。

安装前仍应审查 Skill 的提示词、脚本、依赖和网络行为。不要把数据库密码、云平台密钥或 `SANDBOX_PROVISIONER_TOKEN` 放进 Skill 或 Agent 环境。

## 依赖和加载时机

系统先根据当前用户权限和 Agent 的 `skills` 配置得到有效 Skill 集合，再展开 `skill_dependencies`。依赖链会进入 Skill 描述范围，但依赖工具和 MCP 不会因此全部立刻暴露。

### 普通渐进加载

1. 创建 Graph 前，模型得到有效 Skill 的名称、描述和 `SKILL.md` 路径。
2. 模型读取某个可见 Skill 的 `SKILL.md` 后，该 Skill 进入 `activated_skills`。
3. 后续模型请求加入它声明的本地工具和 MCP 工具。

模型没有读取的 Skill 依赖继续隐藏。未激活的 Skill 工具即使已注册到 ToolNode，也不能被模型调用。

### 预加载

Agent 配置可以用 `preload_skills` 指定少量需要从首轮就可用的 Skill。预加载项必须属于 `skills` 中当前用户可访问的 Skill；系统会展开其依赖闭包，读取根级 `SKILL.md`，并从首轮模型请求开放依赖。

预加载的根文件缺失或不可读时，Graph 创建会明确失败，不会静默退回渐进加载。默认值为空，适合大多数 Skill。

## 权限和选择

共享 Skill 使用 `source_type`、`share_config` 和 `enabled` 表达来源、范围和启用状态。范围使用 version 2 的 `read_scope`、`manage_scope`，可以是全局、部门或指定用户；管理范围必须包含在读取范围内。

| 用户 | 可见和使用 | 可管理 |
| --- | --- | --- |
| `superadmin` | 全部允许的共享/内置 Skill | 全部非内置 Skill，及内置 Skill 的启停 |
| `admin` | 命中读取范围且已启用的 Skill | 命中管理范围的非内置 Skill，及内置 Skill 的启停 |
| 普通用户 | 命中读取范围且已启用的 Skill | 自己拥有的非内置 Skill；新安装的 Skill 固定进入个人来源 |

普通用户安装的新 Skill 固定进入个人来源，不配置共享范围。管理员安装到共享来源时才会写入 PostgreSQL 索引，并可以配置部门或用户范围。前端列表和 Agent 配置只展示当前用户真正可访问的 Skill，后端仍在保存和运行时重新校验。

Agent 配置中的 Skill 选择不能扩大用户的文件、知识库或 MCP 权限。

## 运行时文件行为

共享和内置 Skill 投影对 Agent 只读，但沙盒命令仍可能执行其中的脚本。脚本如需写文件，应写入当前 Project Workdir 或 User Data，而不是 Skill 目录。个人 Skill 直接从当前用户工作区读取，不会复制到共享投影。

Skill 的选择影响 Prompt 和工具激活；共享投影按用户授权集合生成，不会因为某个 Agent 选择了 Skill 就改变 Sandbox 身份。路径穿越、符号链接和跨用户访问由文件系统边界拒绝。

## 维护建议

- slug 使用小写字母、数字和单个短横线，尽量短且能表达用途。
- `description` 先说明能力和适用场景，再写实现细节。
- 把“何时使用”“不要做什么”“产物放在哪里”和“如何验收”写进 `SKILL.md`。
- 依赖链保持短小，避免循环依赖；只有真实需要时才声明工具或 MCP 依赖。
- 脚本按不可信输入处理，不读取或输出运行环境中的秘密。
- 修改共享 Skill 的依赖、范围或文件后，用一个真实 Agent Run 验证模型可见工具和最终产物。

实现入口见 [Skill 服务](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/skills/service.py)、[运行时解析](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/skills/runtime.py) 和 [Skills middleware](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/middlewares/skills.py)。
