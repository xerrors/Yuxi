# AgentPanel 文件预览身份与刷新生命周期

状态：archived
类型：bug-fix
Owner：web/src/components/AgentPanel.vue

## 问题

AgentPanel 同时预览 Viewer Workdir scope、UserWorkspace scope 和跨 Project/User Data/Skills 的 thread artifact runtime 路径，但读取接口只靠 tab 上一个可能残留的 `artifact` 布尔值选择。文件从 artifact 切回文件树时旧来源残留，使 `/outputs/...` 被发送到只接受 runtime 路径的 artifact 接口，文件无法加载。文件树还在 AgentPanel 挂载期间固定每秒刷新，不区分页面可见性、Run 状态或 keep-alive 页面状态。

## 决策

三种文件来源保留各自 owning wire identity：Viewer tree/search 使用当前 Workdir scope `/foo`，Workspace tree/search 使用 UserWorkspace scope `/foo`，thread artifact 使用 `/home/gem/...` runtime 路径。前端 tab 固化 `workdir` 或 `workspace` 来源；Viewer 与 Workspace 走结构化预览接口，artifact 走原始字节接口。普通 JSON artifact 继续按原始文本渲染，不把文件内容误解释为预览响应协议。

读取和下载接口由打开时固化的来源标记决定：当前 Workdir 文件走 Viewer 接口，用户目录文件走 Workspace 接口，其余 runtime 路径走 artifact 接口。同一路径的来源变化时前端失效旧缓存并重新加载，Workspace 预览使用独立缓存前缀。文件树工具栏保活切换对话目录与用户目录，两个目录分别调用 Viewer 与 Workspace 搜索接口。

文件系统在文件树或 Viewer 文件 Section 变为可见时执行一次刷新，只在页面可见、面板打开且当前 Run 执行期间轮询。页面隐藏、组件 deactivated、面板关闭、切换到 artifact 或子智能体 Section、Run completed、failed、cancelled 或 interrupted 时停止轮询并对当前线程补一次刷新；若已有 Viewer 刷新在途则在其结束后补刷。同一轮已经预取的目录不再重复刷新。Workspace 树在重新进入、组件重新激活、保存 artifact 或 Run 终态后刷新。Viewer scope 已位于当前 Workdir，不显示“保存到工作区”操作；runtime artifact 保留复制到 User Data `saved_artifacts` 的操作。

## 替代方案

- 保留双路径身份 + 前端拼接转换：树节点同时携带 scope 与 runtime 两种路径，前端按 `tree_root_dir` 拼接、按前缀分流再由后端反解析，一次请求要转换两次身份，且 `artifact` 布尔残留问题依旧依赖 merge 清理防御。
- 放宽 artifact API 接受 `/outputs/...`：让一个参数同时承载 Workdir scope 与 runtime path，掩盖调用方来源错误。
- 只把轮询间隔从一秒调大：仍会在隐藏、空闲和离页状态持续请求，没有修复生命周期边界。
- 隐藏时卸载整个 AgentPanel：会中止已打开子智能体 Section 的 SSE，破坏现有面板生命周期契约。

## 后果

每种预览来源保持自身路径身份和缓存键，来源切换不再产生"旧来源残留读错接口"的缺陷类。用户可在同一文件树 Section 中浏览和搜索当前对话 Workdir 或个人 Workspace，切换不会重置两棵树的展开状态。空闲、隐藏和离页状态不再持续扫描 Workdir；运行中的 Viewer 文件仍按秒检测元数据变化，Run 结束或中断后文件树读取最终持久字节。AgentPanel 继续保持挂载，子智能体 Section 的 SSE 生命周期不受文件轮询启停影响。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Viewer、Workspace 与 artifact 保持各自 wire identity 和预览协议 | scope/runtime 混用，或原始 JSON artifact 被解释为 envelope | `viewer_filesystem_service.py`、`AgentPanel.vue`、`file_preview.js` | 后端相关 unit；前端目标 unit | Viewer runtime 路径被拒绝；artifact scope 被拒绝；普通 JSON artifact 保留完整正文 | Passed |
| 读取与下载接口由 Workdir、Workspace 或 artifact 来源决定 | 来源标记残留或分流错误导致读错接口 | `AgentChatComponent.vue`、`AgentPanel.vue` | 源码装配检查；前端 lint/build | `workdir`/`workspace` 变化失效缓存；三类下载分别命中 owning API | Inspected |
| 文件树仅在页面可见的运行期文件视图中轮询，所有 Run 终态保证补一次刷新 | 隐藏、空闲或 deactivated 后仍请求，或 interrupted/终态竞态丢失最终刷新 | `AgentChatComponent.vue`、`AgentPanel.vue` | 前端 helper unit；源码装配检查；前端 lint/build | completed 与 interrupted 都触发刷新；在途 Viewer 刷新结束后补刷 | Inspected |
| Workspace 树重新进入后读取最新事实 | 保存 artifact 或离页期间的变化永久停留在旧快照 | `AgentPanel.vue` | 真实页面验证 | 首次加载后新增文件，切回用户目录即可见 | Not run |
| 每轮已预取目录最多读取一次 | 展开的 outputs/uploads 同轮重复请求 | `agentPanelFilesystemPolling.js` | 前端目标 unit | outputs 已预取且展开时加载函数只调用嵌套未加载目录 | Passed |
| 前端静态检查、构建与工程契约有效 | 装配错误或决策生命周期无效 | `web/` 与工程 gate | `pnpm run lint:check`、`pnpm run build`、`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts` | 删除来源判定、轮询 guard 或记录接线后检查失败 | Passed |
| 本地真实页面目录切换、展开与预览行为 | DOM 或请求仍与单元契约不一致 | 本地 Compose 页面 | Playwright 打开 `/agent/{thread_id}` 并保留截图 | 覆盖目录切换、loading、empty、error、预览和窄视口 | Not run |
| 后端 Ruff 与文档构建 gate | Python 风格或文档链接/构建错误只在 CI 暴露 | `backend/pyproject.toml`、`docs/` | `ruff check`、`ruff format --check`、`pnpm run build` | 不适用 | Not run |
