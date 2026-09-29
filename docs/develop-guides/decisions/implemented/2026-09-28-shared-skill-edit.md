# 共享 Skill 在线编辑

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/skills/edit.py

## 问题

共享 Skill 的文件、元数据与依赖分开写入。编辑根级 `SKILL.md` 时，名称和描述会更新，依赖声明不会同步；文件写入先于数据库提交，失败时可能留下不同版本。详情页切换文件会丢弃草稿，保存根文件会重载并覆盖同页未保存的配置。

## 决策

### 实现方案

共享 Skill 的文件保存与依赖表单保存沿用原有 HTTP 路径，由 `services/skills/edit.py` 统一处理。路由只转换请求和响应；service 校验管理权限、来源、相对路径、文件类型和依赖。普通文件保存更新共享目录；根级 `SKILL.md` 保存同时更新 `SkillRepository` 中的名称、描述和依赖索引。依赖表单保存反向修改根文件的 frontmatter，并更新同一数据库索引。文件读取、创建、删除和导出也由该 service 处理；共享范围与启停状态由共享索引 service 处理。个人 Skill 的工作区来源独立于共享数据库。

读取文件以原始字节计算 SHA-256 修订值，并在同一 Skill 共享行锁下返回根文件及其数据库索引；两种保存请求都必须提交对应文件的预期修订值。service 对写入取得独占行锁，文件创建和删除也使用同一行锁；编辑在校验修订值后，将临时文件写在 Skill 来源目录之外，再原子替换目标并提交数据库。运行时只锁定已选共享 Skill 及其依赖，用户投影单独锁定当前授权候选；两者都在锁后重新校验权限，直到对应文件读取或复制完成。投影刷新随后取得用户投影锁，与共享范围更新保持同一顺序。新授权若与本次筛选并发，当前读取可暂不包含它，由授权变更后的投影刷新补齐。修订值过期返回 HTTP 409；文件替换后的同步失败或数据库提交失败时恢复旧文件。权限和 no-follow 路径校验在共享文件 service 中执行，前端按钮不构成授权边界。

共享文件用例使用标准 `ExitStack` 统一释放已打开的目录描述符；文件替换与数据库提交共用一个异常补偿出口，恢复旧文件仍由编辑用例显式执行。投影策略用例命名为 `commit_skill_policy_and_refresh_projections`，明确其先撤回投影、提交授权变更再重建投影的事务责任。

根文件解析兼容 `description:` 后未引用的多行文本：仅在标准 YAML 解析失败时，将该字段按折叠文本重试，使预览、安装和保存接受同一格式。其他 YAML 错误继续拒绝。

详情页提供明确的文件编辑入口。编辑器在切换文件、切换页签、离开页面和刷新时保护未保存的草稿；确认放弃时清除草稿，保存失败时保留草稿。删除成功后直接离开已失效的详情页。读取文件及保存期间限制切换文件，并行保存期间锁定依赖选项，避免旧响应覆盖当前页面状态。根文件读取同时更新文件修订值和依赖表单，避免组合不同版本；根文件保存只更新相关元数据及当前文件，不重载并覆盖未保存的其他表单。依赖保存后刷新当前根文件及其修订值。依赖保存遇到修订值冲突时，用户须明确选择加载最新根文件；当前依赖草稿保留，再次保存会覆盖最新依赖选择。

## 替代方案

- 只调整前端、保留独立文件和依赖写入：改动更少，但两个持久来源仍可能分叉。
- 整个 Skill 目录作为单次草稿发布：可以统一多文件版本，但需要目录级发布生命周期，不符合当前的单文件编辑需求。

## 后果

文件系统与 PostgreSQL 没有跨资源原子事务。进程在文件替换与数据库提交之间崩溃仍可能留下不一致；正常提交失败由 service 恢复文件。运行时预加载读取被行锁约束；已准备 Run 的按需文件读取依赖用户投影刷新，可能看到之后发布的新内容。依赖表单经 YAML 序列化回写 frontmatter，可能重排字段或丢失其中的注释，正文保持原样。旧客户端需要先读取文件修订值才能保存。

## 验证

`docker compose exec -T api env SANDBOX_RUNTIME_PROFILE=core uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider --timeout=60` 通过：2454 passed、58 skipped。原样 `uv run --group test` 在 editable 包同步时因挂载目录不可写而失败；`--no-sync` 使用容器现有依赖测试当前挂载源码。

`docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_shared_skill_edit_router.py test/integration/api/test_skill_artifact_authorization.py test/integration/services/test_user_skill_projection.py -q -p no:cacheprovider --timeout=120` 通过：7 passed，覆盖修订值冲突、文件/索引回读、共享锁、个人覆盖与 artifact 授权。`docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_shared_skill_edit_e2e.py -q -p no:cacheprovider --timeout=360` 通过：1 passed，使用确定性 replay provider，回读真实 worker 的投影内容与 PostgreSQL Run 清单。

前端 `docker compose exec -T web pnpm run lint:check`、`pnpm run test:unit` 和 `pnpm run build` 通过，unit 为 394 passed。真实浏览器验证文件保存并从 HTTP 回读、取消切页保留草稿、409 后保留编辑内容。工程契约检查及 63 项测试、锁定版本 Ruff 的 lint/format/import 检查、`pnpm --dir docs run build` 与 `git diff --check` 通过。

完整 integration/E2E 套件、真实外部模型 provider 与进程崩溃恢复未验证；单测跳过项不计入通过结果。
