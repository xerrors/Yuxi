# 交付物保存目标路径

状态：archived
类型：feature
Owner：backend/package/yuxi/services/artifact_service.py

## 问题

用户从线程交付物卡片保存文件时需要选择个人工作区目录，同时保留 `/saved_artifacts` 作为默认入口。保存目标属于不可信路径输入，必须在当前用户的 UserWorkspace 边界内解释和校验。

## 决策

后端服务拥有目标路径校验与文件写入；`web/src/components/AgentArtifactsCard.vue` 拥有保存交互。保存按钮打开目标目录弹窗，默认选中 `/saved_artifacts`，并复用 `WorkspacePathPicker` 浏览和新建工作区目录。

保存 API 接受可选的 `destination_path`，以 Workspace scope 的绝对路径表示。字段缺省或显式选择 `/saved_artifacts` 时保持默认入口可按需创建的兼容行为；其他显式目标必须是已存在的真实目录。服务端拒绝父目录跳转、runtime 路径、URL、反斜杠路径、普通文件和不可访问目标，并通过 Workspace no-follow 文件能力复制内容。显式目标写入禁止创建父目录，因此目录在校验后被删除时保存失败。目标同名文件存在时沿用原有自动改名语义。

## 替代方案

- 固定保存到 `/saved_artifacts`：无法满足选择保存路径的需求。
- 让前端提交宿主机或 runtime 路径：混淆路径边界并削弱服务端校验，因此不采用。
- 新建独立文件选择组件：与现有工作区目录语义重复，因此复用 `WorkspacePathPicker`。

## 后果

旧调用方无需提交新字段。除默认入口外，显式目录在保存前被读取校验，目录已删除或不可访问时请求失败，不回退到默认目录。前端目录列表继续服从 Workspace API 的可见性投影；后端 no-follow Workspace capability 是最终隔离边界。

## 验证

- `UV_PYTHON=python3.12 uv run --project backend --group test pytest backend/test/unit/services/test_artifact_service.py backend/test/unit/workspace/test_filesystem.py -q`：32 passed；负向案例覆盖跳转、两类 runtime 目标、不存在目标、普通文件中间路径和目录校验后的删除竞态。
- `UV_PYTHON=python3.12 uv run --project backend --group test pytest backend/test/unit -m 'not slow' -q`：1579 passed。
- 独立临时 API 进程的相关 HTTP integration：目录写入主路径 3 passed；最终重复执行因 session 级历史资源清理超时，Not run to completion。
- `pnpm run lint:check && pnpm run test:unit && pnpm run build`：130 passed，lint 和 build 通过。
- `python3 scripts/verify_engineering_contracts.py && python3 -m unittest scripts.test_verify_engineering_contracts`：通过，61 passed。
- `cd docs && pnpm run build`：通过，存在既有 VitePress/Rolldown 兼容警告。
- 真实浏览器交互截图：Not run；当前运行中的 Web 容器挂载主工作树，不是该独立工作树。
