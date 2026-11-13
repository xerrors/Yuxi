# Agent 统一创建、共享权限与资源配置

状态：implemented
类型：simplification
Owner：frontend/src/modules/agents/ui/AgentEditModal.vue

## 问题

创建 Agent 时 Context 与基本信息分离，MCP 创建被混入 Agent 的事务，资源入口与编辑配置不一致。管理员创建主 Agent 时看不到权限配置；私有与共享定义使用相同 Schema，需要在基本信息中选择共享并维护范围。表单的存储分组、重复介绍和分类标签增加操作负担。

## 决策

创建包含基本信息、Context 和可选专属 Skill ZIP。主 Agent 默认个人使用，管理员在创建弹窗 header 的原标题位置使用“个人使用 / 团队共享”分段选择，共享时在描述下方展开通用权限范围组件，读取权限默认全局共享。已有私有定义的编辑沿用“共享此 Agent”入口。基本信息之后可进入高级配置，复用编辑 Agent 的表单并平铺有效字段，任一区域都可直接创建。MCP 与共享 Skill 是 Context 的资源选择，通过独立入口创建。

### 实现方案

AgentCreate 同时拥有基本字段与 config_json.context；有 ZIP 时 multipart 适配同一模型。Agent definitions service 在一个 PostgreSQL 事务中提交 Agent 与可选专属包。移除 mcp_servers 协议、Agent 内的 MCP 创建循环和没有消费者的共同提交参数；旧字段明确拒绝。MCP URL、请求头和管理凭据不进入 Agent 创建请求。

AgentUpdate 只允许 visibility=shared。AgentRepository 锁定当前定义与有效操作者，校验管理权、管理员角色和明确的 share_config，再原地更新私有定义；Agent definitions service 在同一事务中校验绑定依赖并刷新投影。普通用户、无明确范围、越权管理员和共享转私有均拒绝；ID、slug、owner、Context 与绑定保持同一身份。can_share 表达现有共享编辑与管理员私有共享能力，不另设 publish 接口或 can_publish 能力。读取、使用、转移和用户文件继续遵循[资源权限](../../../mechanisms/resource-permissions.md)。

AgentRuntimeConfigForm 通过显式配置、Schema 与事件服务创建和编辑。未修改字段沿用后端默认值，刷新候选保留草稿；切换后端清除不适用的 Context 并使旧响应失效。AgentEditModal 保存 visibility 和权限草稿，仅共享时提交并验证 share_config。私有草稿取消共享不制造修改，已有共享定义继续维护范围。

MCP 仅超级管理员创建，共享 Skill 管理员创建，复用现有资源弹窗与 API。发布成功刷新并加入选择；取消 Agent 或创建失败仍保留这些独立资源。共享快捷入口固定为共享安装，个人 Skill 保持现有入口。全部资源选择使用 all 或固定数组；“选择全部”位于清空全部左侧，选中状态表示包含后续新增，并通过消息提示；逐项修改与清空退出全部模式。

### 表单与失败

创建弹窗使用简洁标题、内容与固定底部操作区，按内容限制高度并在较长时内部滚动；创建内容的 Grid 行使用 `minmax(0, 1fr)` 约束滚动容器高度，避免自动行按表单固有高度撑开后被外层裁切。标题栏展示使用方式，基本信息依次展示名称、描述、共享权限和可选 ZIP；Agent 标识放入高级配置，默认值与提交契约保持一致。共享范围直接使用 ShareConfigForm，选人浮层沿用默认挂载点，不嵌套共享 popover。ShareConfigForm 拥有权限卡片与选人项的通用状态样式，浅色选中态使用纯白表面与主色描边，暗色选中态将主色混入中性表面，悬停态使用中性色，避免暗色主题中近黑的选中底色；知识库和 Skill 等调用方共同生效。表单与弹窗使用同一表面，删除装饰底色、分隔边框与次要操作描边，描述输入框保留可见边框，保留悬停和焦点反馈；ZIP 和资源操作使用现有 Lucide 按钮样式。基本与高级区域隐藏而非卸载已加载表单，切换管理键盘焦点。名称或共享校验失败返回基本区域并聚焦对应位置，错误持续可见。保存、图标上传、独立资源创建及未知结果期间沿用现有保护；专属 ZIP 首次导入等待阻止离开入口。

McpFormModal 按名称与标识、描述、地址与传输、图标与标签组织字段。请求头与超时放入高级连接选项，复用 CollapseTransition 的 250ms 高度、透明度与箭头动画；创建默认收起、编辑展开，v-show 保留草稿，减少动画偏好关闭过渡。连接协议与权限保持当前 Owner。

## 替代方案

- Agent 创建内导入 MCP：重复资源管理与事务 Owner，采用独立创建。
- 只在创建后配置 Context 或另建创建表单：需要多次保存或复制默认值和可见性规则，复用同一 Schema 与组件。
- 新增向导状态机、独立发布接口或复制共享 Agent：一份草稿与同一资源更新已经满足需求。
- 将已有私有定义的权限草稿自动改为全局共享：已有私有转换继续保留仅所有者初值；新建流程在管理员明确选择团队共享后默认全局范围。
- 以数据库分组展示表单、保留分类标签和常驻重复说明：增加查找和切换，按当前配置任务排列字段。

## 后果

共享更新原记录，并受现有共享角色上限约束。专属依赖不足会使整次更新回滚。高级配置更长，内部滚动与固定页脚保持提交可达；默认权限入口只对管理员可见。Agent 与专属包失败不留半成品，独立 MCP、共享 Skill 和图标不属于此事务。未知创建结果需取消并刷新核对，不增加幂等状态机。完整布局使用现有 Vue、Ant Design、Lucide 和主题变量。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 基本信息与 Context、可选 ZIP 原子创建 | HTTP / PostgreSQL / 文件 | Agent definitions / content | agent create resources integration；worker E2E | ZIP、依赖、字段、权限或提交失败不留半成品 | Passed |
| 默认私有与管理员原地共享 | Vue / HTTP / PostgreSQL | AgentEditModal / AgentRepository | permission convergence integration；实际组件 unit | 普通用户、缺少授权、共享转私有、绑定依赖不匹配 | Passed |
| 草稿、默认值与独立资源选择保持一致 | Vue / HTTP / DOM | RuntimeConfigForm / resource modals | 前端 unit、真实页面完整创建后 API 回读 | 切换后端旧响应、刷新、失败、忙碌和未知结果 | Passed |
| 平铺表单、全选工具栏、折叠动画与响应式 | DOM / CSS | AgentEditModal / McpFormModal | 真实页面浅深色与 1440px、1024px、768px 检查 | 个人使用不展示共享范围或标识；校验失败返回共享位置；字段与页脚可达 | Inspected |

旧能力不存在：Agent 创建内发布 MCP、mcp_servers 输入、独立 publish 接口与 can_publish、创建副标题和高级分类标签、头像下共享 popover、基本信息内的 Agent 标识、资源列表上方全选行、MCP 存储分组标题。生产入口、配置、导出、测试与文档采用统一创建契约。

重新引入条件：明确 consumer 要求共享资源共同发布、独立发布协议或复杂向导，并重新裁决权限、幂等和副作用 Owner；真实配置任务证明平铺查找成本不可接受时再定义用户分类。

创建弹窗的可重放浏览器验证位于 `frontend/test/browser/agentCreateDialog.js`，覆盖默认个人使用、共享范围切换与草稿保留、基本/高级切换、标识提交、共享校验定位、真实创建后 API 回读与清理，以及浅深色和支持宽度的截图。准确命令、结果与未验证范围记录在交付说明；截图保存在仓库外。
