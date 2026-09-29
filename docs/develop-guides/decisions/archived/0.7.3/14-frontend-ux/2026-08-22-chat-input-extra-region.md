# 聊天输入器 Extra 区域

状态：archived
类型：feature
Owner：web/src/components/AgentInputArea.vue

## 问题

新对话的 Project 选择作为输入框外的独立表单行出现时，割裂了“这些选项属于本次输入上下文”的认知，也让 Project 看起来像唯一且特殊的前置步骤。Project 需要属于聊天输入器的组合上下文，但不能侵入白色正文编辑框。

## 决策

`AgentInputArea` 拥有通用 Extra 插槽。Extra 是白色输入框之前的 sibling 承载层：左右略窄、使用次级背景，并由更高层级的输入框覆盖下沿，形成输入框外部的叠放关系。附件和正文继续只属于白色输入框内部。

新对话把 Project 选择器装配为 Extra 中的紧凑 chip；chip 只显示当前项目名称，不附加前置图标或尾部箭头。点击 chip 从其上方打开可搜索列表，列表底部保留新建 Project 和添加历史项目动作。添加历史项目在同一弹层内切换为紧凑二级菜单，返回键与搜索框同排，不保留重复标题；候选使用小图标、单行标题和相对时间。选择历史对话只取出其实际 Workdir，然后打开同一个“新建项目”弹窗并预选该目录；项目名称保持为空，由用户命名并确认后才创建 Project。

新建 Project 只保留一个弹窗。Workspace 目录浏览默认显示在该弹窗中，不再要求用户先选择目录模式，也不再打开嵌套目录弹窗；手动新建 Project 必须选择目录。目录选择由独立 `WorkspacePathPicker` 负责，它与知识库“从工作区添加文件”共享目录导航和内联新建文件夹能力，选择器内不提供搜索。Project 是当前唯一 Extra consumer，但 Extra 区域不绑定 Project 数据结构。已有 Conversation 不渲染 Extra DOM；默认“新建 Project”只在发送时走隐式 managed Project，业务语义不变。

## 替代方案

- 保留输入器外的 Project 表单行，仅调整颜色和圆角：拒绝。视觉美化不能修复 Project 与本次输入上下文割裂的操作逻辑。
- 把 Project 放进白色输入框的 top slot：拒绝。它会侵入编辑器内部层级，无法形成参考界面的外部叠放关系，也会与附件预览混为一类。
- 把 Project 塞进左下角“添加内容”菜单：拒绝。Project 是新 Conversation 的持续工作上下文，不是一次性附件或 mention，隐藏后无法确认当前选择。
- 为历史对话维护独立创建表单或直接用对话标题创建 Project：拒绝。历史候选只负责选择目录，随后进入既有“新建项目”弹窗完成命名和确认。
- 为 Workspace 目录选择继续打开下一层弹窗：拒绝。目录浏览仍直接嵌在“新建项目”弹窗中。
- 在手动新建 Project 中保留“系统管理 / 已有目录”单选项：拒绝。用户进入手动创建流程时必须明确选择目录；隐式 managed Project 只属于默认发送链路。
- 同时设计 Skills、知识库等更多 Extra 类型：拒绝。当前没有明确的新 consumer；只保留可装配区域，不扩大功能范围。

## 后果

- Extra 与输入框属于同一组合组件，但拥有独立 DOM 和视觉层级；后续上下文项应作为并列 chip 装配，不能重新写进正文或附件区。
- Extra 左右各缩进 12px，并与输入框重叠 10px；Project chip 默认透明，仅在 hover 或展开时显示轻背景；窄屏弹层单独限制宽度，避免从 chip 左侧锚点溢出视口。
- Project 弹层打开后聚焦搜索框；历史列表作为可返回的紧凑二级菜单展示，以小图标辅助识别，并只显示分钟、小时、天、月、年级别的相对时间。
- 新建弹窗默认加载 Workspace 根目录。手动创建只能提交已选 linked 路径；仅 Workspace 根目录不可选，`agents/`、`projects/` 和已被其他 Project 使用的目录均可选择。
- `WorkspacePathPicker` 只拥有目录导航、选择和新建文件夹交互；可选文件类型和业务提交仍由各 consumer 决定。
- `/projects` 下匿名、未被选择的目录由后端统一 tree 可见性契约过滤；前端不按目录名或 UUID 外观猜测。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Project 位于输入框外部 Extra 层且由输入框覆盖下沿 | Project 进入白色编辑框，或退回孤立表单行 | `AgentInputArea.vue`、`AgentChatComponent.vue` | Playwright 回读 Extra 与输入框矩形：Extra 左右各缩进 12px、垂直重叠 10px；最终截图 | 检查附件 top slot 不包含 Extra | Passed |
| Project 弹层可按名称搜索，历史项目使用二级菜单 | 长列表不可查找，或历史项目仍打开独立弹窗 | `ProjectSelectionSection.vue`、`projectSelection.js` | `pnpm run test:unit`；Playwright 打开历史项目并回读弹层内标题、返回按钮和候选项 | 无匹配搜索返回空状态；历史候选为空时显示空状态 | Passed |
| 历史对话只预选目录并复用新建表单 | 点击候选立即创建，或对话标题静默成为项目名 | Project UI、Project API | Playwright 点击候选后回读新建弹窗、空名称和预选路径；API boundary 不存在历史专用创建请求 | 缺少可用目录时不打开弹窗并显示错误 | Passed |
| 新建项目仅用一个弹窗表达必选目录 | 仍要求选择目录模式、未选目录可提交，或目录浏览打开嵌套弹窗 | `ProjectSelectionSection.vue`、Project service | Playwright 回读新建弹窗；前后端 unit 断言 linked 路径 | 名称或目录任一为空时按钮禁用；直接提交 managed/空路径返回 422 | Passed |
| Project 与知识库复用独立 Workspace 选择器 | 两处目录浏览继续复制状态和交互 | `WorkspacePathPicker.vue` | Playwright 分别回读两个入口均存在 picker、新建文件夹且 picker 内搜索框为 0；`pnpm run test:unit` | consumer 分别限制目录与文件选择，目录加载错误使用 alert | Passed |
| Extra 仅在新对话存在 | 已有 Conversation 留下空白 Extra 层或可改绑 | `AgentInputArea.vue`、`AgentChatComponent.vue` | Playwright 打开已有 Conversation 后 `.input-extra-region` 与 `.has-extra` 均为 0 | 已有 Conversation 即使仍传入具名 slot，也不渲染 Extra DOM | Passed |
| Extra 在浅/深色及窄屏下保持可读 | 弹层穿帮或超出窄屏 | 组件 LESS 与全局 token | 1440px 浅/深色与 375px 页面截图；console error 为 0 | 375px 弹层宽度限制与暗色 token 检查 | Passed |
