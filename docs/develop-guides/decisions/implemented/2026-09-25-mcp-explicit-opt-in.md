# Agent 资源选择按字段解释

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/context.py

## 问题

Agent 的 `mcps` 缺省会加载所有已启用服务器，新启用的服务器因此自动进入未配置 MCP 的 Agent。前端又用配置元数据的 `kind` 推断缺省选择：`skills` 与 `preload_skills` 同属一个 `kind`，却分别表示缺省全选和不预加载。表单、Store 与 mention 对同一字段给出不同解释。

## 决策

Agent 的 `mcps` 字段只控制直接添加的 MCP 服务器；缺省、`null` 和空列表均不直接加载。显式列表经当前可用服务器过滤。有效 Skill 激活后按需加载其 `mcp_dependencies`，预加载 Skill 从首轮加载，均只使用管理员已启用的服务器。

前端按字段名解释缺省选择，`kind` 只用于展示分组。`preload_skills` 的候选项受当前 Agent 的 Skill 范围约束；mention 只投影实际可提及的四个资源字段。Store 将后端 UI 覆盖展开一次，并停止请求没有消费者的 MCP、Skill 列表。表单和 CLI 将未直接选择的 MCP 显示为零项或“无”。

## 替代方案

保留 MCP 缺省全选会让服务器启用动作扩大已有 Agent 能力；强制 Skill 依赖也写入 Agent 的 `mcps` 字段会要求重复配置，并破坏依赖随 Skill 激活的时机。给元数据新增通用缺省选择协议会扩大后端与前端契约；当前固定字段直接按字段解释即可。

## 后果

现有 Agent 缺省或 `null` 的 MCP 配置不再直接加载全部服务器；Skill 激活后仍可能加载其声明的 MCP 依赖。预加载项在表单中只显示当前 Agent 可用的 Skill；既有但暂不可选的引用在普通编辑时保留，运行时继续过滤。子 Agent 空列表仍表示使用全部可见子 Agent。

## 验证

旧能力不存在：`kind=skills` 不再把预加载项当成缺省全选或 mention 来源；Store 不再请求没有消费者的 MCP、Skill 列表；Agent 的 `mcps` 缺省不再直接展开全部服务器。

重新引入条件：出现真实的新资源字段、外部消费者或兼容承诺时，再评估通用字段策略或额外加载路径。

| 主张 | 语义 Owner 与证据 | 负向案例 |
| --- | --- | --- |
| 缺省、`null`、空列表不直接加载 MCP，显式列表只保留可用服务器 | Context unit；真实 HTTP 保存并从 PostgreSQL 独立回读的 integration | 恢复缺省全选或跳过服务器过滤会失败 |
| Skill 激活后加载 MCP，读取失败或零行读取不会激活 | Skills middleware unit 检查时序、工具执行绑定和错误结果 | 过早加载、读取失败仍激活会失败 |
| 动态 MCP 工具不与已注册本地工具同名 | Skills middleware unit 覆盖未激活 Skill 的本地工具 | 只检查模型可见工具会漏检并失败 |
| 表单、mention 与保存采用相同字段语义 | Web unit；静态 API fixture 的页面检查；CLI unit | 恢复 `kind` 推断或预加载项进入 mention 会失败 |

完整 Agent Run E2E 与真实远程 MCP 服务未验证；实际执行命令、结果和环境限制记录在 PR 中。
