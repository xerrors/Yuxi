# 参与贡献

欢迎提交 Bug 修复、功能改进、测试和文档。本文是 Fork → 开发 → 验证 → PR 的完整流程；只想了解仓库入口时，可以先看根目录的 [CONTRIBUTING.md](https://github.com/xerrors/Yuxi/blob/main/CONTRIBUTING.md)。

## 开始前

- 先搜索 [Issues](https://github.com/xerrors/Yuxi/issues)，确认问题没有重复。
- 如果任务来自 GitHub Project，读完任务描述、关联 Issue、验收标准和讨论，并确认任务已经分配给你。
- 需求会改变架构、权限、持久化、运行生命周期、公开接口或模型可见输入时，先在 Issue 或 [Discussions](https://github.com/xerrors/Yuxi/discussions) 对齐方案，并建立 [工程决策记录](./decisions/README.md)。
- 一个 PR 解决一个清楚的问题。无关的格式化、重构和“顺手优化”请另开 PR。

修改不熟悉的模块前，先读 [ARCHITECTURE.md](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md)，再从真实的路由、service、repository、Schema、Compose 和测试中定位实现。源码和可执行测试比旧文档、历史 PR 或自动生成 Wiki 更可靠。

## 1. Fork、同步和建分支

```bash
git clone https://github.com/<your-username>/Yuxi.git
cd Yuxi
git remote add upstream https://github.com/xerrors/Yuxi.git
git fetch upstream
git switch main
git merge --ff-only upstream/main
git push origin main
git switch -c docs/improve-guides
```

`origin` 指向你的 Fork，`upstream` 指向 `xerrors/Yuxi`。不要把开发分支直接推送到 `upstream`，也不要在 `main` 上开发。

如果开发期间 `main` 有更新，只有在出现冲突、CI 明确要求更新，或维护者要求时才同步 `upstream/main`。涉及 rebase 和强制推送时使用 `--force-with-lease`，并先确认该分支没有其他贡献者共同开发。

分支名可以使用：

```text
feat/<topic>      新功能
fix/<topic>       Bug 修复
docs/<topic>      文档更新
refactor/<topic>  重构
test/<topic>      测试改进
chore/<topic>     工程辅助
```

## 2. 开发环境

Yuxi 的开发拓扑以 Docker Compose 为准。首次启动前，根据 `.env.template` 准备 `.env`，再运行：

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 api
```

`api` 和 `web` 服务默认支持热重载。容器名由 Compose project 生成；使用 `docker compose logs api web` 查看当前槽位日志。修改本地代码后通常不需要手动重启。

### Windows + VS Code + WSL

源码位于 Windows 盘并通过 WSL Ubuntu 运行 Docker 时，使用仓库提供的覆盖文件，避免 PostgreSQL 在 Windows 挂载目录初始化时因权限语义不同而失败：

```bash
docker compose -f docker-compose.yml -f docker-compose.wsl.yml up --build -d
```

Windows VS Code 可以直接打开仓库，通过“终端 → 运行任务”调用 `.vscode/tasks.json` 中的启动、状态、日志和停止任务。任务通过 `wsl.exe --cd` 把当前 `${workspaceFolder}` 作为工作目录，默认使用名为 `Ubuntu` 的 WSL 发行版；发行版名称不同的开发者应在个人任务中调整。覆盖文件只改变 PostgreSQL 数据卷，源码仍由原 Compose 挂载并保持热重载。

该命令创建 Docker 命名卷保存 PostgreSQL 数据。停止服务使用 `docker compose -f docker-compose.yml -f docker-compose.wsl.yml stop`；不要对需要保留的数据执行带 `-v` 的 `down`。

## 3. 实现原则

- 用满足验收标准的最小实现，保持主路径线性可读。
- HTTP 路由只做请求解析、认证上下文和响应装配；用例流程放在 `yuxi.services`，持久化查询放在 `yuxi.repositories`。
- 预设条件不成立时明确失败，不用静默回退或吞异常掩盖问题。
- 权限在后端依赖和 repository 可见性查询处执行；前端守卫、prompt、schema omission 和隐藏按钮不是授权边界。
- PostgreSQL 保存业务事实；Redis 只负责投递、短期事件、取消和缓存。LangGraph checkpoint 只使用 PostgreSQL。
- 文件路径、沙盒虚拟路径、对象 URL 和宿主机路径不能混用；在真正产生副作用的 executor 或 repository 处做边界校验。
- 修复 Bug 时先补一个能稳定复现原问题的回归测试，再改实现。
- 新增函数或类使用简洁中文 docstring；注释只说明时序、Owner、安全边界和调用义务。

### 后端

后端代码位于 `backend/`。新增测试放在 `backend/test/unit`、`backend/test/integration` 或 `backend/test/e2e`，按测试实际依赖选择层级。测试分层、fixture 和 skip 规则见[测试规范](./testing-guidelines.md)。

### 前端

前端代码位于 `web/`，使用 `pnpm` 和 LESS：

- API 封装放在 `web/src/apis`；
- 图标优先使用 `@lucide/vue`；
- 颜色优先使用 `base.css` 和 `base.dark.css` 的变量；
- 浅色、暗色、loading、empty、error、focus 和响应式状态都要检查；
- UI 改动遵循[产品体验与界面设计规范](./design.md)。

不要为单次需求引入新的依赖、样式体系或抽象。确需新增依赖时，在 PR 中说明用途、替代方案和许可证影响。

## 4. 检查和测试

先跑与改动最相关的最小集合，再根据风险扩大：

| 改动 | 最低验证 |
| --- | --- |
| 纯 Python/JavaScript 逻辑 | 相关 unit，断言业务结果 |
| API、权限或持久化 | 真实 HTTP integration |
| Run、FIFO、SSE、沙盒、恢复或文件 | E2E，核对最终状态和产物 |
| 前端交互 | lint、unit；重要行为再 build 和真实页面检查 |
| 文档、导航或链接 | 相对链接检查、文档构建和 `git diff --check` |

后端常用命令：

```bash
docker compose exec api uv run --group test pytest test/unit -m "not slow"
docker compose exec api uv run --group test pytest test/integration
docker compose exec api uv run --group test pytest test/e2e -m e2e
```

前端常用命令：

```bash
docker compose exec web pnpm run lint:check
docker compose exec web pnpm run test:unit
docker compose exec web pnpm run build
```

项目统一格式化命令会修改工作树，完成格式化后应重新查看 diff：

```bash
make format
```

提交前至少运行仓库信任检查、其单元测试和后端 unit：

```bash
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
docker compose exec api uv run --group test pytest test/unit -m "not slow"
git diff --check
```

如果环境、凭证或外部服务导致某项检查无法执行，在 PR 中写明命令、原因、未验证范围和剩余风险。没有默认数据不是跳过测试的理由，应由 fixture 创建资源。测试“通过”也不能替代对数据库、文件、对象、DOM 或协议结果的回读。

## 5. 提交前 Review

代码变更在 commit 前必须经过一个不继承当前开发上下文的独立 Reviewer。Reviewer 应读取完整需求、规范、完整 diff、相关源码、测试结果和未验证范围，重点检查：

1. 主路径、边界、错误处理和回归测试是否完整；
2. 路由、服务、仓储、前端 API 和测试是否位于正确边界；
3. 是否引入不必要的抽象、fallback、配置或维护表面；
4. 权限、文件隔离、事务、Run ownership 和外部副作用是否闭合；
5. 文档是否写对当前行为，oracle 是否独立，负向案例是否能让目标缺陷变红。

发现影响功能、边界、证据可信度或认知负担的问题，先修正再提交。独立 Review 不能替代真实测试和最终事实回读。

## 6. Commit 和 Pull Request

提交信息使用中文 Conventional Commit：

```bash
git add <changed-files>
git commit -m "docs: 完善部署与快速开始文档"
git push -u origin docs/improve-guides
```

常用类型是 `feat`、`fix`、`docs`、`refactor`、`test` 和 `chore`。提交前检查：

```bash
git status
git diff --check
git diff
```

PR 标题直接说明目标，正文说明背景、影响范围、验证命令和未验证风险。非平凡变更还要列出受影响主张、事实 Owner、决策记录、oracle、负向案例和观察边界；不要创建中央 claim ID 清单。

- **Bug 修复**：说明触发步骤、错误表现、根因、回归测试、数据影响和未覆盖边界。
- **新功能**：说明用户场景、目标、非目标、设计取舍、接口/配置/迁移影响和测试结果。
- **UI 改动**：附真实页面截图或录屏，覆盖关键状态和适用的浅色、暗色、响应式场景；没有截图就写明未完成视觉验证。

Agent 创建 PR 前，先把拟提交的标题和完整正文展示给用户，得到明确确认后再创建。确认不代表跳过测试、敏感信息检查或 CI。

PR 正文按创建方式选择模板：

- Agent 创建的 PR 使用默认的 [Agent PR 模板](https://github.com/xerrors/Yuxi/blob/main/.github/PULL_REQUEST_TEMPLATE.md)；
- 人工或其他非 Agent 方式创建的 PR 可使用[简化模板](https://github.com/xerrors/Yuxi/blob/main/.github/PULL_REQUEST_TEMPLATE/non-agent.md)。使用 GitHub compare 页面时增加 `template=non-agent.md` 查询参数，使用 GitHub CLI 时传入 `--template .github/PULL_REQUEST_TEMPLATE/non-agent.md`。

模板复杂度不同，不改变非平凡或高风险变更的工程证据要求。来自 Fork 的 PR 默认无法读取主仓库 Secrets；不要通过修改工作流、打印环境变量或扩大权限绕过这一限制。如果验证必须依赖受保护凭证，应在 PR 中说明并由维护者执行对应检查。

### 候选版本与正式发布

维护者在发布前定稿版本号、changelog 和升级说明。功能更新以最近的正式 tag 为基线，例如 0.7.3 使用 `v0.7.2..HEAD`；候选版本之间的修复归并到对应功能，不单独替代完整发布说明。

1. 在已审查的提交上创建候选 tag，例如 `v0.7.3-rc.1`，显式推送该 tag。需要对外试用时创建 GitHub Release 并标记 Pre-release。
2. 在 Actions 核对该 tag 的工程契约、后端单测、Ruff、Web、运行链路、依赖审计和文档构建结果；tag 检查覆盖完整范围。真实 provider 与生产备份恢复演练按[测试规范](./testing-guidelines.md)和[升级指南](../advanced/deployment.md)补充，记录未验证范围。
3. 修复产生新提交时创建下一个候选 tag；已推送的候选 tag 保留原指向。
4. 最终候选通过后，在同一提交新增正式 tag，并发布正式 Release。Release 正文保留相对上一正式版本的完整功能更新及升级注意事项。应用 tag 触发检查，文档站只在 main 分支推送时部署。

CLI 使用 `packages/yuxi-cli/pyproject.toml` 中的独立版本。需要发布 CLI 时先提交包版本和锁文件更新，再对明确的提交或 tag 手动运行 [Publish yuxi-cli](https://github.com/xerrors/Yuxi/actions/workflows/publish-yuxi-cli.yml)；应用 Release 不触发 PyPI 上传。CLI 版本未变时无需重复发布，上传失败须检查版本与 PyPI 状态。

## 7. 文档维护

代码、配置、API、状态、权限或命令变化时，同一 PR 更新对应的 owning page：

- `intro/` 保持完成用户任务的顺序；
- `advanced/` 保持配置、部署和外部集成参考；
- `agents/` 保持 Agent 扩展和操作方法；
- `mechanisms/` 保持运行链路、状态、权限和失败语义；
- 已发布变更写入 [changelog](./changelog.md)，未完成方向写入 [roadmap](./roadmap.md)，不要同时把同一事项写成完成和计划；
- 新增页面或移动页面时同步更新 `.vitepress/config.mts` 和全部入站链接。

文档使用直接、自然的现在时，写明执行者、条件、结果和失败后果。教程要有可观察验收，参考要有默认值和生效时机，机制页要链接源码和验证入口。完整规则见[文档编写与维护规范](./documentation-guidelines.md)。

文档变更至少运行：

```bash
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
cd docs && pnpm run build
git diff --check
```

## AI 辅助开发

AI 可以帮助搜索、实现、测试和整理文档，但贡献者仍对最终代码、文档、安全性和验证结果负责。不要向不受信任的服务提供 `.env`、仓库 Secret、Token、用户数据或内部地址；AI 辅助贡献和人工贡献使用相同的 Review、测试和 PR 要求。

## 获取帮助

- Bug 和功能讨论：[GitHub Issues](https://github.com/xerrors/Yuxi/issues)
- 方案讨论：[GitHub Discussions](https://github.com/xerrors/Yuxi/discussions)
- 贡献入口：[根目录 CONTRIBUTING.md](https://github.com/xerrors/Yuxi/blob/main/CONTRIBUTING.md)
