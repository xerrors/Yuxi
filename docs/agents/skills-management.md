# Skills 管理系统

Skills 是 Yuxi 系统中用于扩展 Agent 能力的重要机制。通过 Skills，开发者可以将特定的工具、提示词模板或领域知识打包成可复用的技能包，让 Agent 在对话过程中能够调用这些额外能力。

## 为什么需要 Skills

在实际业务场景中，我们常常会遇到一些特定的需求：比如需要 Agent 能够查询特定的 API、调用某个外部服务、或者使用特定的提示词模板来完成特定任务。传统的做法是在代码中硬编码这些功能，但这样会导致系统变得越来越臃肿，且难以复用。

Skills 系统的设计理念就是将这类"可插拔"的能力封装成独立的技能包。每个 Skill 包含完整的实现文件和元数据，Agent 可以根据配置动态加载所需的技能，实现能力的灵活组合。

## 架构设计

Skills 系统采用「文件系统存内容，数据库存索引」的分离架构：

```
┌─────────────────────────────────────────────────────────────┐
│                      Skills 存储架构                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   /app/saves/skills/          数据库索引                    │
│   ├── skill-a/               ┌──────────────┐              │
│   │   ├── SKILL.md           │ skills 表    │              │
│   │   ├── tools/             │ - slug       │              │
│   │   └── prompts/           │ - name       │              │
│   └── skill-b/               │ - description│              │
│       ├── SKILL.md           │ - dir_path   │              │
│       └── ...                │ - source_type│              │
│                              │ - share_config              │
│                              │ - enabled     │              │
│                              │ - deps...     │              │
│                              └──────────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 存储结构

- **文件系统**：`/app/saves/skills` 目录下，每个 Skill 占用一个子目录
- **数据库索引**：`skills` 表存储元数据（slug、name、description、来源、共享范围、启用状态、依赖关系等）
- **关联机制**：通过 `dir_path` 字段关联文件系统目录与数据库记录

::: tip 不能直接在文件系统创建
由于 Skills 的元数据需要写入数据库，因此不能直接在文件系统中创建 Skill。必须通过系统的导入或安装功能来完成，系统会自动处理数据库记录的创建。
:::

## 创建方式

系统提供以下方式创建或安装 Skills：

1. **推荐 Skill 安装**：在 Skills 管理页的推荐分组点击 `+`，系统会拉取对应远程来源并生成安装草稿
2. **ZIP / SKILL.md 上传**：上传后先解析为安装草稿，确认共享范围后再写入正式 Skills 存储和数据库
3. **远程仓库安装**：填写 skills 仓库地址、ModelScope Skill 地址或合集地址，后端调用 `npx skills` 下载并解析为安装草稿，确认后导入系统
4. **在线编辑**：对已有且可管理的 Skill 在线创建目录、编辑文件和维护依赖
5. **Agent 内安装**：主智能体可通过 `install_skill` 工具，从沙盒路径或 Git 来源安装当前用户私有 Skill；子智能体禁用该工具

不建议直接操作数据库或文件系统导入 Skill。直接写文件不会自动生成 `skills` 表记录，也无法参与权限、依赖和沙盒挂载。

## Skills 来源

Skills 本质上是提示词和工具的封装，以下是一些可以参考的 Skills 实现：

- **Anthropic 官方 Tools**：https://github.com/anthropics/skills 可以参考其 skills 的组织方式和提示词设计
- **ModelScope Skill 市场**：https://modelscope.cn/skills 支持单个 Skill 地址，也支持合集地址批量拉取
- **MiniMax-AI CLI**：https://github.com/MiniMax-AI/cli 文本、图片、视频、语音和音乐生成 + Web 搜索（可通过 `MiniMax-AI/cli` 远程安装）
- **社区 Skills**：各平台分享的 Agent 提示词模板
- **自定义开发**：根据业务需求自行开发

系统也会在启动时同步仓库内置 Skills。内置 `html-preview` 用于指导 Agent 在普通
Markdown 不足以清晰表达指标、对比、流程、时间线或层级关系时，按需输出
`html:preview` 静态 HTML/CSS 围栏；普通 HTML 源码仍使用 `html` 代码块。该 Skill
不依赖额外工具，前端继续通过清洗后的 sandboxed iframe 渲染预览。

未显式配置 Skills 的 Agent 会按现有资源默认规则自动获得该 Skill。使用显式 Skills
允许列表的 Agent 需要选择 `html-preview` 才能使用；内置 `deep-research` 已声明该依赖，
升级后仍可继续输出辅助可视化。

## 快速开始

### 创建你的第一个 Skill

一个标准的 Skill 目录结构如下：

```
my-awesome-skill/
├── SKILL.md              # 必选，Skill 的核心定义文件
├── tools/                # 可选，相关的工具脚本
│   └── helper.py
└── prompts/              # 可选，提示词模板
    └── system.md
```

其中 `SKILL.md` 是每个 Skill 必须包含的核心文件，它采用 Markdown + Frontmatter 格式：

```markdown
---
name: My Awesome Skill
slug: my-awesome-skill
description: 这是一个用于处理特定任务的技能
---

# Skill 使用说明

这里是技能的详细使用文档，Agent 会读取这部分内容来了解如何使用这个技能。

## 功能列表

1. 功能一：xxx
2. 功能二：yyy

## 使用示例

当用户 xxx 时，可以调用此技能...
```

**Frontmatter 字段说明：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Skill 展示名称，可使用更易读的名称（如 `Word / DOCX`） |
| `slug` | 否 | Skill 唯一标识，必须是小写字母、数字、短横线的组合，且不能连续短横线（如 `my-skill`）。未填写时兼容旧格式，系统会使用 `name` 作为 slug，此时 `name` 也必须满足 slug 规则 |
| `description` | 是 | Skill 的功能描述，会在 Agent 配置时展示 |

### 导入 Skill

可以通过以下方式导入或安装 Skill：

**方式一：从推荐列表安装**

1. 在系统设置的「Skills 管理」页面查看「推荐」分组
2. 未安装的推荐 Skill 会以普通 Skill 卡片样式展示，右侧显示 `+`
3. 点击推荐卡片或 `+` 后，系统会使用该 Skill 的远程来源拉取内容
4. 拉取成功后会弹出安装草稿，确认共享范围后完成安装

已安装的推荐 Skill 不会继续显示在「推荐」分组中。

**方式二：通过 ZIP 包或 SKILL.md 上传**

1. 将 Skill 目录打包成 ZIP 文件（注意：ZIP 的根目录就是 Skill 目录）
2. 在系统设置的「Skills 管理」页面，点击「上传 Skill」
3. 上传 ZIP 文件或单个 `SKILL.md`
4. 系统解析上传内容并返回安装草稿
5. 确认共享范围后完成安装；也可以放弃草稿

系统会自动：
- 校验 ZIP 内容和路径安全性
- 检查 slug 冲突（如有冲突会自动追加 `-v2` 等后缀）
- 解析 SKILL.md 的 frontmatter 并存储到数据库
- 按当前用户角色校验可选择的共享范围

**方式三：从远程来源安装**

1. 在 Skills 管理页面点击「远程安装」
2. 在“按仓库拉取”中填写来源，例如：
   - `anthropics/skills`
   - `https://github.com/anthropics/skills`
   - `https://modelscope.cn/skills/@anthropics/pdf`
   - `https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills`
3. 点击“拉取技能”获取该来源中可发现的 Skills 列表
4. 单个 Skill 地址通常会自动选中；仓库或合集地址可在列表中勾选一个或多个 Skills
5. 点击“解析并确认”，系统返回安装草稿，确认共享范围后正式安装

也可以切换到“全局搜索发现”，输入关键字检索 skills.sh 上的开源 Skills，再选择结果安装。

系统会在后端：
- 调用 `npx skills add <source> --list` 校验来源并发现可安装的 skills
- 使用隔离的临时 `HOME` 执行 `npx skills add <source> --skill <name> -g -y --copy`
- 从临时目录中提取对应 skill，再按现有导入流程生成草稿；确认后写入 `/app/saves/skills` 与数据库

::: tip ModelScope 合集适合批量安装
ModelScope 合集地址可以作为远程来源填写，例如 `https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills`。拉取后在列表中勾选需要的 Skills，再统一解析为安装草稿。
:::

**方式四：在线编辑已有 Skill**

在 Skills 管理页面，你可以：
- 新建目录或文件
- 在线编辑文本文件（支持 .md、.py、.js、.json 等格式）
- 直接在网页上修改 SKILL.md 内容

只有具备 `can_manage` 权限的用户才能编辑文件、依赖、共享范围和启用状态。

::: tip 远程安装不会把 ~/.agents/skills 作为系统主存储
远程安装只把 `skills.sh` CLI 作为“下载器”使用。Yuxi 仍然以 `/app/saves/skills + skills 表` 作为正式来源，这样才能与现有的权限、线程可见性和沙盒挂载机制保持一致。
:::

## 依赖系统

Skills 之间可以建立依赖关系，形成一个松耦合的技能网络。

### 依赖类型

每个 Skill 可以声明三类依赖：

| 依赖类型 | 说明 | 加载时机 |
|----------|------|----------|
| `tool_dependencies` | 需要的内置工具 | 激活后按需加载 |
| `mcp_dependencies` | 需要的 MCP 服务 | 激活后按需加载 |
| `skill_dependencies` | 依赖的其他 Skill | 会话启动即生效 |

### 渐进式加载机制

系统采用三级渐进式加载策略，确保资源的高效利用：

**阶段一：会话启动**

当 Agent 会话启动时，系统会：
1. 在创建 Graph 前读取已过滤的 `context.skills` 列表
2. 递归展开 `skill_dependencies`，派生 `_prompt_skills` 和 `_readable_skills`
3. 将 `_prompt_skills` 对应的技能说明注入到系统提示词中

这意味着：只要配置了某个 Skill，它的依赖 Skill 就会立即进入提示词和沙盒 `/home/gem/skills` 只读范围。

**阶段二：技能激活**

当 Agent 通过 `read_file` 工具读取 `/home/gem/skills/<slug>/SKILL.md` 时，视为"激活"该技能。系统会：
1. 验证该技能在可见列表中
2. 将其添加到 `activated_skills` 列表
3. 后续的模型调用会使用激活列表来加载依赖

**阶段三：按需加载**

每次模型调用时，系统会：
1. 检查 `activated_skills` 中的技能
2. 收集这些技能的 `tool_dependencies` 和 `mcp_dependencies`
3. 动态将需要的工具和 MCP 服务添加到可用工具集中

这种设计的好处是：不会在会话开始时加载所有工具，而是根据 Agent 实际使用情况按需加载，既节省资源又保证响应速度。

### 依赖声明示例

假设我们有三个 Skills：

- **base-skill**：基础技能，无依赖
- **advanced-skill**：依赖 `base-skill`
- **pro-skill**：依赖 `advanced-skill`

当在 Agent 配置中只选择 `pro-skill` 时：
1. 启动阶段：`_readable_skills` = [`pro-skill`, `advanced-skill`, `base-skill`]（自动展开依赖链）
2. Agent 首次调用任何 skill 时：所有三个 Skill 都可读
3. 当 Agent 读取 `pro-skill/SKILL.md` 时：触发激活，工具和 MCP 依赖被加载

## 权限管理

Skills 使用 `source_type`、`share_config` 和 `enabled` 控制来源、共享范围和启用状态。

| 字段 | 说明 |
|------|------|
| `source_type` | `builtin`、`upload` 或 `remote` |
| `share_config.access_level` | `global`、`department` 或 `user` |
| `enabled` | 是否允许在 Agent 配置与运行时使用 |

访问与管理规则：

| 用户 | 可见 / 可用 | 可管理 |
|------|-------------|--------|
| 超级管理员 / 管理员 | 可查看可管理或已启用且可访问的 Skills | 可管理所有非内置 Skills；可启停内置 Skills |
| 普通用户 | 可查看已启用且对自己可访问的 Skills，也可安装自己的私有 Skill | 可管理自己创建的非内置 Skills |
| 内置 Skills | 默认全局共享并启用 | 管理员可启停；不允许删除或直接编辑文件 |

共享范围限制：

- `global`：所有用户可访问
- `department`：指定部门用户可访问
- `user`：指定用户可访问；普通用户安装时只能选择个人范围

管理员和普通用户在创建或编辑 Agent 时，都只能从自己可访问且启用的 Skills 中选择能力。

## 运行时行为

### Agent 如何使用 Skills

1. **提示词注入**：系统在每次模型请求时动态注入可用 Skills 的描述（请求级注入，避免污染 runtime context）
2. **文件访问**：Skills 目录以只读方式挂载到 `/home/gem/skills/<slug>/...`
3. **工具调用**：当 Agent 需要使用某个 Skill 时，会先读取对应的 SKILL.md 了解使用方法

### 文件操作限制

运行时 `/home/gem/skills` 路径有以下限制：
- **只读**：Agent 只能读取文件内容
- **禁止写入**：不能创建、修改或删除文件
- **路径安全**：所有路径都经过安全校验，防止目录穿越攻击

::: tip 只读不等于不可执行
`/home/gem/skills` 对 Agent 是只读的，但沙盒命令工具仍可执行其中的脚本。Skill 应把依赖、运行方式和产物位置写清楚；脚本若需要写文件，应写入 workspace 或 outputs，而不是 Skill 目录。
:::

### 会话隔离

每个 Agent 会话都有独立的 Skills 可见集：
- 不同会话可以配置不同的 Skills
- 同一会话内修改 `context.skills` 会触发快照重建
- 后台修改 Skills 内容后，已有会话不会自动刷新

## 最佳实践

### Skill 命名规范

- `slug` 使用小写字母、数字和短横线，不能连续短横线
- `slug` 应具有描述性，如 `weather-query`、`sql-reporter`
- `name` 用于展示，可比 `slug` 更自然，例如 `Word / DOCX`
- 避免过长的 `name` 和 `slug`

### 依赖管理建议

- **保持依赖链简洁**：层级不宜过深，一般 1-2 层为宜
- **避免循环依赖**：系统会检测并阻止循环依赖
- **明确依赖必要性**：只在真正需要共享能力时才建立依赖

### SKILL.md 编写技巧

```markdown
---
name: example-skill
description: 简短描述技能功能
---

# 技能名称

这里是详细的使用说明...

## 何时使用

描述在什么场景下应该使用这个技能...

## 使用方法

1. 第一步...
2. 第二步...

## 示例

```
具体的使用示例...
```
```
