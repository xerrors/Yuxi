# 参与贡献

感谢你对 Yuxi 的关注。我们欢迎 Bug 修复、功能改进、测试补充、文档更新以及其他能够让项目变得更好的贡献。

本文面向通过 Fork 参与开发的贡献者，介绍从领取任务到提交 Pull Request（以下简称 PR）的完整流程。如果你只需要快速了解仓库入口，可以先阅读根目录的 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

<a href="https://github.com/xerrors/Yuxi/contributors">
    <img src="https://contributors.nn.ci/api?repo=xerrors/Yuxi" alt="贡献者名单">
</a>

## 开始之前

开始开发前，请先完成以下确认：

- 搜索已有 [Issues](https://github.com/xerrors/Yuxi/issues)，避免重复提交相同问题。
- 如果任务来自 GitHub Project，阅读任务描述、关联 Issue、验收标准和已有讨论，并确认任务已经分配给你。
- 对影响范围较大、需求边界不明确或会改变现有架构的改动，先通过 Issue 或 [Discussions](https://github.com/xerrors/Yuxi/discussions) 对齐方案。
- 一个 PR 只解决一个明确问题，不混入无关重构、格式化或“顺手优化”。

修改不熟悉的模块前，请先阅读 [ARCHITECTURE.md](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md)，了解前后端边界、主要运行链路和架构不变量，再通过代码搜索定位具体实现。

## 贡献流程概览

一次完整贡献通常包括：

1. Fork 并克隆仓库。
2. 配置上游仓库并同步最新 `main`。
3. 从最新 `main` 创建独立分支。
4. 完成开发、测试和文档更新。
5. 使用独立上下文的 Reviewer Agent 完成提交前 Code Review，并处理 Review 结论。
6. 提交改动并将分支推送到自己的 Fork。
7. 从 Fork 分支向 `xerrors/Yuxi:main` 发起 PR。
8. 根据 CI 和 Review 反馈继续修改，直至合并。

如果任务来自 GitHub Project，开始开发时将任务状态更新为进行中；PR 创建后关联对应 Issue 或 Project 条目。任务只有在必要测试通过并完成合并后，才应标记为完成。

## 1. Fork 与克隆仓库

先在 GitHub 上 Fork [xerrors/Yuxi](https://github.com/xerrors/Yuxi)，然后克隆自己的 Fork：

```bash
git clone https://github.com/<your-username>/Yuxi.git
cd Yuxi
```

克隆完成后，默认的 `origin` 应指向你的 Fork。将官方仓库配置为 `upstream`：

```bash
git remote add upstream https://github.com/xerrors/Yuxi.git
git remote -v
```

推荐保持以下远程仓库关系：

```text
origin    你的 Fork，用于推送开发分支
upstream  xerrors/Yuxi，用于获取主仓库更新
```

不要把开发分支直接推送到 `upstream`。

## 2. 同步最新主分支

每次开始新任务前，先同步官方仓库的最新 `main`：

```bash
git fetch upstream
git switch main
git merge --ff-only upstream/main
git push origin main
```

`--ff-only` 可以避免在本地 `main` 上意外产生额外的合并提交。如果该命令失败，说明本地 `main` 已经包含独立修改；请先检查分支状态，不要直接覆盖或删除未确认的工作。

## 3. 创建任务分支

必须从同步后的 `main` 创建独立分支，不要直接在 `main` 上开发：

```bash
git switch -c feat/knowledge-graph-import
```

分支名应简短、明确，并能表达改动目的。推荐格式：

```text
feat/<topic>      新功能
fix/<topic>       Bug 修复
docs/<topic>      文档更新
refactor/<topic>  重构
test/<topic>      测试改进
chore/<topic>     工程或辅助任务
```

示例：

```bash
git switch -c fix/chat-stream-interrupt
git switch -c docs/update-contributing-guide
```

## 4. 开发环境

Yuxi 使用 Docker Compose 管理开发环境。开发、调试和测试应在运行中的容器环境中完成。

首次启动前，根据 `.env.template` 准备项目根目录下的 `.env`，然后启动服务：

```bash
docker compose up -d
```

确认容器状态和后端日志：

```bash
docker ps
docker logs api-dev --tail 100
```

Compose 服务 `api` 和 `web` 对应的容器名分别为 `api-dev` 和 `web-dev`，默认支持热重载。修改本地代码后通常不需要重启容器。

服务定义和挂载方式见 [docker-compose.yml](../../docker-compose.yml)。

## 5. 实现原则

提交代码时请遵循以下原则：

- 使用满足验收标准的最小实现，不增加当前任务未要求的功能、配置或扩展点。
- 保持主流程简单、线性、易读；不要为了单次使用的逻辑创建多层抽象。
- 修改范围应能直接追溯到当前任务，不格式化或重构无关代码。
- 预设条件不成立时应明确失败，不使用静默回退或吞异常掩盖问题。
- 修复 Bug 时，优先增加能够稳定复现问题的回归测试，再修复实现。
- 行为、接口或配置发生变化时，同步更新相关文档。

### 前端改动

前端代码位于 `web/`，请遵循以下约束：

- 使用 `pnpm` 管理依赖。
- API 接口统一定义在 `web/src/apis`。
- Icon 优先使用 `lucide-vue-next`，并保持尺寸一致。
- 样式使用 `less`。
- 非特殊情况使用 [base.css](../../web/src/assets/css/base.css) 中已有的颜色变量。
- 遵循 [界面设计规范](./design.md)，保持现有交互和视觉语言一致。

不要在没有必要的情况下引入新的前端依赖。如果确实需要新增依赖，请在 PR 中说明用途和替代方案。

### 后端改动

后端代码位于 `backend/`，请遵循以下约束：

- 使用 Python 3.12+ 支持的现代、Pythonic 写法。
- 保持路由、服务、仓储和领域逻辑的现有边界。
- 新增测试应放入 `backend/test/unit`、`backend/test/integration` 或 `backend/test/e2e` 对应目录。
- 不在测试或文档中写入 `.env` 中的账号、密码、Token 或其他敏感值。

测试分层、fixture 和 skip 规则见 [测试规范与工作流](./testing-guidelines.md)。

## 6. 检查与测试

提交前按照“检查 → 测试 → Lint”的顺序验证改动。测试范围应与改动风险匹配：

- 纯逻辑改动：运行相关单元测试。
- API、权限或持久化改动：补充并运行相关集成测试。
- 关键用户链路：补充并运行对应 E2E 测试。
- 前端改动：运行 Lint、相关单元测试和构建检查。

### 后端测试

```bash
# 单元测试
docker compose exec api uv run --group test pytest test/unit -m "not slow"

# 集成测试
docker compose exec api uv run --group test pytest test/integration

# E2E 测试
docker compose exec api uv run --group test pytest test/e2e -m e2e
```

也可以使用项目脚本：

```bash
backend/test/run_tests.sh unit
backend/test/run_tests.sh integration
backend/test/run_tests.sh e2e
backend/test/run_tests.sh all
```

优先运行与改动直接相关的最小测试集，再根据影响范围扩大回归测试。不要只因为本地缺少默认数据就跳过测试，应通过 fixture 显式准备需要的资源。

### 格式化与静态检查

应用项目格式化规则：

```bash
make format
```

如需在不修改文件的情况下核对后端 Ruff 检查，可运行：

```bash
docker compose exec api uv run ruff check package
docker compose exec api uv run ruff format package --check
```

前端改动还应运行：

```bash
docker compose exec web pnpm run lint
docker compose exec web pnpm run test:unit
docker compose exec web pnpm run build
```

最后检查补丁是否包含空白错误：

```bash
git diff --check
```

如果某项检查因环境或外部服务不可用而无法执行，请在 PR 的测试说明中明确记录原因和未验证范围，不要把未执行写成已通过。

## 7. 提交前独立 Agent Code Review

所有包含代码变更的任务，在开发和测试完成后、执行 `git commit` 前，必须使用 Codex、Claude Code 或同等工具完成一次独立 Agent Code Review。

这里的“独立”是指 Reviewer Agent 必须运行在一个全新的会话和上下文中：

- 不继承当前开发会话的对话历史、推理过程或实现结论。
- 不使用继承当前上下文的 SubAgent 代替独立 Review。
- Reviewer 应根据需求、完整 diff、相关代码、测试和项目规范独立判断，而不是只检查开发者指定的局部代码。

Review 重点检查以下内容：

1. **功能正确且完整**：实现满足需求和验收标准，主路径、关键边界、错误处理和测试可信，没有遗漏主要使用场景或引入明显回归。
2. **实现简单且低认知负担**：优先复用现有能力，减少重复开发和重复代码；避免过度设计、过度防御、不必要抽象、细碎 helper、冗余 fallback 和过长调用链；主流程应直接、清晰、易读。
3. **风格一致且位置合理**：新代码与相邻实现保持一致，Python 代码符合 Python 3.12+ 和 Pythonic 风格；路由、服务、仓储、前端 API、测试等内容位于正确边界，并符合 `AGENTS.md`、[ARCHITECTURE.md](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md)、[测试规范](./testing-guidelines.md) 和相关开发文档。

发现影响功能、代码边界或明显增加冗余和认知负担的问题时，应在提交前修正。这个阶段用于提升代码质量，不要求在 PR 中记录 Review 过程或问题清单。

独立 Agent Review 不能替代贡献者本人的 Human Review。代码作者仍需对最终实现、测试结果和维护成本负责。

## 8. 提交改动

提交前先检查本次变更范围：

```bash
git status
git diff
```

不要提交 `.env`、密钥、运行数据、构建产物或与当前任务无关的文件。

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)；标题应简洁说明改动内容。项目提交信息推荐使用中文：

```bash
git add <changed-files>
git commit -m "docs: 完善 Fork 与 PR 贡献流程"
```

常用类型：

```text
feat      新功能
fix       Bug 修复
docs      文档更新
refactor  不改变行为的代码重构
test      测试新增或调整
chore     构建流程或辅助工具变更
```

保持提交历史易于 Review。不要为了“整齐”而修改已经共享的提交历史；如果 Reviewer 要求整理提交，再根据具体情况处理。

## 9. 推送分支并创建 PR

将任务分支推送到自己的 Fork：

```bash
git push -u origin docs/update-contributing-guide
```

创建 PR 时确认目标和来源：

```text
base repository: xerrors/Yuxi
base branch:     main

head repository: <your-username>/Yuxi
compare branch:  当前任务分支
```

不要向自己 Fork 的 `main` 创建 PR，也不要把多个任务分支合并后再统一提交。

PR 标题应直接表达目标，正文按照 [PR 模板](https://github.com/xerrors/Yuxi/blob/main/.github/PULL_REQUEST_TEMPLATE.md) 填写，并至少包含：

- 改动解决了什么问题。
- 采用了什么实现方式，以及必要的设计取舍。
- 影响了哪些模块、接口或用户行为。
- 实际执行过哪些测试，结果如何。
- 哪些内容没有验证，以及原因。
- UI 改动的截图或录屏。

如果 PR 对应仓库 Issue，请在正文中使用：

```text
Closes #123
```

这样 PR 合并后可以自动关闭对应 Issue。准备好接受 Review 时提交普通 PR；仍需讨论方案或 CI 尚未完成时，可以先提交 Draft PR。

## 10. 处理 CI 和 Review

PR 创建后，贡献者负责持续跟进 CI 和 Review：

1. 查看失败检查的具体日志，定位与本次改动有关的问题。
2. 在原任务分支继续修改、测试和提交。
3. 将新提交推送到同一远程分支，PR 会自动更新。
4. 回复 Review 时说明如何处理；如果不采纳建议，应解释具体原因和取舍。
5. 所有必要检查通过、Review 意见解决后再请求合并。

不要为同一个任务重复创建 PR。除非维护者明确要求，也不要关闭原 PR 后重新提交来隐藏讨论记录。

来自 Fork 的 PR 默认无法读取主仓库 Secrets。不要通过修改工作流、打印环境变量或扩大权限来绕过这一限制；如果验证必须依赖受保护凭证，请在 PR 中说明并由维护者执行对应检查。

如果开发期间 `main` 有更新，先获取最新上游代码：

```bash
git fetch upstream
```

只有在出现冲突、CI 明确要求更新，或维护者要求时，再将任务分支更新到最新 `upstream/main`。涉及 rebase 和强制推送时，应使用 `--force-with-lease`，并确保该分支没有其他贡献者共同开发。

## 11. 合并后的清理

PR 合并后，可以删除已经完成的远程和本地任务分支：

```bash
git push origin --delete docs/update-contributing-guide
git switch main
git branch -d docs/update-contributing-guide
```

然后重新同步 `upstream/main`，再开始下一个任务。不要复用已经合并过的任务分支提交新的需求。

如果任务来自 GitHub Project，确认关联 PR 已合并、验收结果已记录，再将任务更新为完成状态。

## 文档维护

代码改动后，请检查是否需要同步更新文档：

- 正式文档位于 `docs/`。
- 文档导航定义在 `docs/.vitepress/config.mts`；新增正式页面时需要同步加入导航。
- 已完成的用户可见变更或发布说明更新到 [changelog.md](./changelog.md)。
- 未来规划和未完成事项更新到 [roadmap.md](./roadmap.md)，不要将已完成变更继续保留为路线图事项。
- 仅开发者可见且确有必要的临时设计记录放在 `docs/vibe/`。

## AI 辅助贡献

使用 Codex、Claude Code 等 AI 工具辅助开发时，贡献者仍需对代码、测试、文档和安全性承担完整责任。

- AI 生成的代码必须经过人工阅读和验证后才能提交 PR。
- 不允许让 Agent 在无人审阅的情况下直接提交、回复 Review 或合并代码。
- PR 标题末尾添加 `🤖`，并在正文中说明使用的工具、人工检查内容和实际测试结果。
- 不要将仓库 Secrets、`.env` 内容、用户数据或其他敏感信息发送给不受信任的服务。

可以在 PR 正文中使用以下说明：

```markdown
<details>
<summary>贡献说明</summary>

本 PR 使用 [工具名称] 辅助完成。贡献者已人工审阅全部改动，并完成以下验证：

- [ ] 检查实现与任务范围
- [ ] 检查敏感信息和安全风险
- [ ] 运行相关测试与静态检查
</details>
```

## 获取帮助

- Bug 反馈：[GitHub Issues](https://github.com/xerrors/Yuxi/issues)
- 功能和方案讨论：[GitHub Discussions](https://github.com/xerrors/Yuxi/discussions)

感谢每一位贡献者的投入。
