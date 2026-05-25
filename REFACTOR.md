


# 兼容性报告

无法兼容历史的数据库、知识库、配置项，只能重新创建项目

# 功能性报告

1. 将 LightRAG 从调用 package 修改为手动实现，不依赖原本仓库，更好管理。
2. 移除 legacy 参数，简化代码逻辑，减少维护成本。
3. 权限结构重构

# 待办事项

- [x] 移除所有对于 v1 的模型的代码的支持、移除所有对于 legacy 参数的支持
- [x] 移除 lightrag
- [x] 新增图谱构建，支持自定义 Schema、支持自定义并发数，知识库与向量检索解耦
- [x] 知识图谱抽取要求支持并发处理


## 权限部分
- [x] Agent 的 user_id 使用的有歧义，从数据库表到代码中都修改为统一使用 yuxi_id 来代替
- [ ] 权限新增一个 guest 的预设字段，暂无任何权限
- [ ] rename database table name, such as skills -> agent_skills, subagents -> agent_subagents, mcp, tool_call, 等等
- [x] department 的 id 也不能使用那个索引的 id 来使用了，应该是一个独立的 dept_id，需要确认
- [ ] allow user config skill, and envs
- [ ] 添加 Skill 的权限设计
- [ ] add model retry times to agent context config
- [ ] add user envs when load sandbox
- [ ] Config spacy model 的 load
- [x] allow multi-hop qa generate
- [x] 在工作区的文件编辑的时候，保存和取消的按钮应该是悬浮在编辑框的右上角，而不是在 header 上面
- [x] default enable all build in tools / kbs / skills / mcps / subagents
- [x] 链接 Notion 和 feishu 目前来看，都是支持的
- [x] 知识库的权限调整，修改为三个等级，全局共享、部门共享（选择多个部门，默认是自己部门且必须包含自己部门）、指定人可访问（选择多个用户，默认是仅自己，可以添加其他人）。UI 上也需要调整，三个卡片不再是等宽，而是选中的会宽一点，并展示描述以及选择按钮，未选中的则是默认宽度，仅显示标题。对于选中的卡片除了展示描述、按钮之外，还包括“X 个部门可访问”、“X 个用户可访问”的信息展示。全局的就看是所有用户可访问。所以等级的字段配置也要重新设计，不需要考虑兼容，所有知识库都会重新构建。
- [x] databaseinfo 的重构，在左侧展示那个 tab 标签吧，将文件管理（filetable）以及右侧的那些图谱、检索、检索配置、评估之类的，都列为不同的 tab，进入之后默认激活的是 filetable。这样页面布局就好的多。作恶侧边栏除了这些 tab 之外，顶部是和 Skill Detail 那里的 header 一样，
- [x] 当前的评估基准是最重要的是评估数据集和评估结果都是放在一个文件里面的，这个是绝对不可以的，应该是放在数据库里面，比如评估数据集是一个表，每一个评估的题目是一个表，评估的结果是一个表，每一个评估的 item 也是一个表，但是数据表太多要注意命名规范。现在第一步就是完成原本的评估的功能的重新梳理
- [ ] 考虑如何将知识库更好的挂载到沙盒，是不是可以使用一个别的后端，但是使用别的后端是否还能读取到数据？应该不能
- [ ] 智能体体系改进。
