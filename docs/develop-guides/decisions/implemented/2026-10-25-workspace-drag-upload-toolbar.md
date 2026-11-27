# 个人空间拖拽上传与工具栏快捷入口

状态：implemented
类型：feature
Owner：frontend/src/modules/workspace/ui/WorkspaceFileList.vue

上传语义与批次约束的次要 Owner 是 frontend/src/modules/workspace/model/workspaceFolderUpload.js 与 frontend/src/pages/WorkspaceView.vue；目录上传的后端契约沿用 [2026-10-23 个人空间文件夹上传](2026-10-23-personal-space-folder-upload.md)，本次后端零改动。

目录拖放与批次约定继续适用。当前上传入口的位置见[个人空间概览与文件阅读](2026-11-26-personal-space-overview.md)。

## 问题

个人空间的上传入口只有侧边栏下拉菜单（上传文件/上传文件夹/新建文件夹），文件列表区域本身没有拖拽投放能力，也没有贴近当前目录的工具栏入口；与常见网盘交互差距明显。目录上传的后端（`relative_paths`、分批、冲突预检）与新建目录接口已经就绪。

## 决策

### 实现方案

| 模块 | 职责 |
|---|---|
| `workspaceFolderUpload.js` 新增 `collectDragUploadRoots` / `traverseDirectoryEntry` | 在 drop 事件内同步区分顶层文件与目录入口；目录经条目 API 递归读取为与文件夹选择同构的 `{file, relativePath}` 列表 |
| `WorkspaceFileList.vue` | 列表外壳作为投放区：拖入文件时显示覆盖层，drop 时 emit `upload-drop`；工具栏在计数旁新增仅图标的"上传文件"与"新建文件夹"按钮（仅 `uploadable` 时渲染） |
| `WorkspaceView.vue` `handleDropUpload` | 先传平铺文件，再逐目录递归收集并按既有批次规则上传；`finally` 中回读目录，部分成功也投影最终状态 |

- 可上传性口径与侧边栏一致：`activeSourceKey === 'personal' && !isReadonlyWorkspacePath`（知识库与只读目录不激活投放区、不渲染按钮，且不阻止浏览器默认行为之外的任何事件）。
- 拖拽支持文件、文件夹与两者混合；混合时先上传文件再上传文件夹，失败即中止并提示已成功部分不回滚。空目录延续"空文件夹不会被上传"语义，仅提示。
- 工具栏"上传文件"按钮打开既有多文件选择器；文件夹选择继续由侧边栏"上传文件夹"或拖拽承担。原因：原生 `<input>` 单个对话框无法同时选择多个文件和一个文件夹（`webkitdirectory` 模式只能选目录），强行合并需要 File System Access API 或 fork 选择器，超出最小实现。

## 替代方案

- 上传按钮动态切换 `webkitdirectory` 以"自适应"选择：对话框模式在打开前必须确定，无法感知用户意图，实际不可行；未采用。
- 用 `showDirectoryPicker`（File System Access API）实现单对话框选目录：仅 Chromium 支持，需要另一套句柄遍历与 File 构造路径，扩大范围；未采用。
- 把 drop 处理放在 `WorkspaceView` 根元素：投放区会覆盖预览面板与只读知识库视图，误投面更大；未采用。
- 混合拖拽拆成两次独立上传请求之外再加原子性：后端跨批本就无原子性（见 2026-10-23 记录），保持一致语义。

## 后果

- 拖拽目录依赖 `webkitGetAsEntry` 条目 API（Chromium/Firefox/Safari 均支持）；极端环境缺失时退化为纯文件拖拽，目录条目被忽略。
- 覆盖层 `pointer-events: none`，drop 始终落在列表外壳；拖拽计数用 dragenter/dragleave 深度平衡，避免子元素闪烁。
- 工具栏按钮在上传中禁用，但同一时刻侧边栏菜单仍可发起上传——`uploadingFile` 守卫会拒绝并发，与既有行为一致。
- 部分成功语义与文件夹选择上传一致：成功批次保留，界面报告失败原因；空目录不上传。

## 验证

- `frontend/test/unit/workspaceFolderUpload.test.js`：拖拽根收集（文件/目录/混合/非文件项/无条目 API 回退）与目录递归遍历（相对路径层级、`readEntries` 分批循环、非目录入口）。
- `frontend/test/unit/workspace_action_semantics.test.js`：锁定工具栏按钮、`uploadable` 渲染条件、drop 守卫与页面侧个人空间只读语义。
- 前端全量 unit 862 passed、改动文件 eslint 通过、`pnpm run build` 通过。
- Chromium 真实页面：工具栏按钮与新建文件夹模态可用；构造 `DataTransfer` 派发 drop，文件上传后列表回读一致；投放覆盖层与列表外壳同宽（外壳 `flex: 1 1 0`，独立复核 shell 与 main clientWidth 相等），预览面板/模态与列表共存正常；知识库/只读目录不显示按钮与覆盖层。真实 OS 文件夹拖入浏览器的 `webkitGetAsEntry` 路径未在真实 OS 拖拽中验证，递归遍历由单测覆盖。

本记录直接写 `implemented`：目录上传的后端契约、分批与错误语义已在 2026-10-23 记录中裁决并实现，本次仅新增前端入口且无待裁决替代或新风险面，故未走 `proposed`。
