# 智能体编辑中的个人记忆管理

状态：implemented
类型：feature
Owner：frontend/src/modules/agents/ui/AgentMemoryPanel.vue

## 问题

用户希望在编辑智能体时查看和维护已有记忆，不必切换到网盘寻找文件。现有记忆属于当前用户，不能因入口位于智能体编辑中而被误认为智能体专属数据。

## 决策

### 实现方案

在已有智能体编辑弹窗中加入“记忆”分组，以行为约定、用户档案、长期记忆三个文件页展示当前用户 Workspace 的 AGENTS.md、USER.md、MEMORY.md。面板明确共享范围和独立保存语义，提供 Markdown 预览、编辑、保存、刷新及复制。未保存草稿在离开或刷新前确认；保存失败保留草稿。

文件读写复用 `frontend/src/apis/workspace_api.js`，响应通过现有 `normalizePreviewResponse` 解析；截断或不支持的预览不允许编辑。保存后回读文件再展示结果，回读失败仍保留草稿。配置保存期间禁止切入记忆或产生新草稿，记忆保存期间阻止关闭和导航。后端身份、路径检查、文件字节和覆盖保存契约仍归 `modules/workspace`；运行时记忆读取、开关和写入规则仍归 `modules/agents/services/memory.py`。通用文件接口没有版本校验，界面不承诺多入口并发编辑冲突检测。

## 替代方案

- 只跳转网盘：可以复用现有入口，但无法完成弹窗内查看与编辑的目标。
- 复制 Cherry 的 Agent 专属目录及日志工具：会改变记忆归属和模型行为，超出界面需求。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 编辑入口展示当前用户三个既有文件及共享范围 | 把个人记忆误标为 Agent 私有 | AgentEditModal.vue、AgentMemoryPanel.vue | 组件测试和真实页面 | 切换文件展示对应内容，读取失败可重试 | Passed |
| 保存失败不丢失草稿，未保存内容离开前确认 | 关闭、刷新或切换导致数据丢失 | AgentMemoryPanel.vue、AgentEditModal.vue | 组件测试和真实页面 | 拒绝丢弃后保持原文；请求失败后继续编辑；配置与记忆保存互斥 | Passed |
| 不支持或截断的文件预览不能覆盖保存 | 把错误消息或文件前缀保存为完整正文 | AgentMemoryPanel.vue | 组件测试 | unsupported、truncated 响应均不能进入编辑 | Passed |

- `docker compose exec frontend pnpm run lint:check`、`docker compose exec frontend pnpm run build` 通过；构建仍有既有页面的 `:deep` 警告。
- Linux 容器内对当前前端源码、测试和配置的相同副本执行 `node --test --test-concurrency=2 "test/**/*.test.js" "test/**/*.spec.js"`，1089 条通过，无失败或跳过。副本放在容器临时目录，避免 Windows bind mount 上测试模块加载过慢，四个变更源码/测试文件以 SHA-256 对照一致；直接 bind mount 的中途运行不计为通过。
- 定向面板 5 条、编辑弹窗 22 条测试通过。真实本地页面验证三个文件、保存回读、拒绝丢弃保留草稿、浅/深色和 768、1024、1280 像素布局；临时换行保存验证后恢复原文件。
- 工程契约 verifier 通过，负向测试在 Linux API 容器通过 56 条；Windows 原生执行有一条符号链接权限错误，不计为通过。
- 后端源码、接口与运行时行为没有变更，未运行后端全量 unit 或模型 E2E。网络失败、空文件和截断结果由组件边界测试覆盖，真实页面未注入这些故障。

## 后果

编辑入口不改变记忆归属、自动写入策略或 Memory 开关。通用 Workspace 保存沿用覆盖语义，并发编辑仍以最后一次写入为准。
