# 参与贡献

感谢你对 Yuxi 的兴趣。我们欢迎 Issue、文档改进、Bug 修复、测试补充以及新功能贡献。

如果你只是想快速了解仓库入口信息，可以先看根目录的 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

<a href="https://github.com/xerrors/Yuxi/contributors">
    <img src="https://contributors.nn.ci/api?repo=xerrors/Yuxi" alt="贡献者名单">
</a>

## 开始之前

提交前建议先完成以下检查：

- 搜索已有 [Issues](https://github.com/xerrors/Yuxi/issues) 和 [Discussions](https://github.com/xerrors/Yuxi/discussions)
- 对较大的功能改动，先发 Issue 讨论设计和边界
- 保持一次 PR 只解决一个明确问题，避免把无关重构混在一起

## 开发原则

本项目默认遵循以下开发原则：

- 避免过度设计，只做当前需求直接需要的改动
- 不额外添加“顺手优化”、兼容层或未来需求抽象
- 尽量复用现有实现，保持代码简单、聚焦、可维护
- 只在系统边界做必要校验，不为不可能发生的内部场景增加复杂度

## 开发环境

Yuxi 基于 Docker Compose 管理开发环境。开发、调试、测试都应尽量在运行中的容器中完成。

### 启动项目

```bash
docker compose up -d
```

### 常用检查命令

```bash
docker ps
docker logs api-dev --tail 100
```

`api-dev` 和 `web-dev` 默认支持热重载。通常情况下，本地修改代码后不需要重启容器。

如需进一步了解服务定义，可查看 [docker-compose.yml](../../docker-compose.yml)。

## 贡献流程

### 1. Fork 仓库

在 GitHub 上 Fork 本仓库到你的个人账户。

### 2. 创建分支

请使用语义明确的分支名，例如：

```bash
git checkout -b feature/amazing-feature
git checkout -b fix/chat-stream-interrupt
git checkout -b docs/update-contributing-guide
```

### 3. 开发与验证

按项目规范完成代码、测试与文档更新。开发完成后，至少完成：

- 检查
- 测试
- Lint
- 必要的端到端验证

如果现有测试脚本不足以覆盖你的改动，应补充对应测试，测试脚本优先放在 `backend/test`。

### 4. 提交代码

```bash
git commit -m "feat: add knowledge graph import flow"
```

### 5. 推送并发起 Pull Request

```bash
git push origin feature/amazing-feature
```

创建 PR 时，请写清楚：

- 修改内容
- 修改原因
- 影响范围
- 验证方式

如果涉及 UI 改动，建议附上截图或录屏。

## 前端贡献规范

前端目录位于 `web/`，提交前请遵循以下约束：

- 包管理器使用 `pnpm`
- 所有 API 接口定义统一放在 `web/src/apis`
- Icon 优先使用 `lucide-vue-next`
- 样式使用 `less`
- 非特殊情况必须优先复用 [web/src/assets/css/base.css](../../web/src/assets/css/base.css) 中的颜色变量

界面设计和样式约束可参考 [design.md](./design.md)。

## 后端贡献规范

后端目录位于 `backend/`，提交时请注意：

- Python 风格尽量保持 pythonic
- 优先使用较新的语法，兼容目标为 Python 3.12+
- 优先在容器内运行调试和测试命令

示例：

```bash
docker compose exec api uv run python test/your_script.py
```

测试脚本建议放在 `backend/test` 下。

## 质量检查

提交前请至少完成以下检查：

### 格式化与静态检查

```bash
make format
make lint
```

如果测试依赖管理员账户，可从项目根目录的 `.env` 中读取相关配置。

## 文档维护要求

代码改动后，请同步检查是否需要更新文档。

- 通用开发文档位于 `docs/`
- 文档导航定义在 `docs/.vitepress/config.mts`
- 若本次改动值得记录，请更新 [roadmap.md](./roadmap.md)
- 若确需新增仅开发者可见的说明文档，放在 `docs/vibe/`

## 提交信息规范

推荐使用清晰、可检索的提交前缀：

```text
feat: 添加新功能
fix: 修复 bug
docs: 更新文档
refactor: 代码重构
test: 添加测试
chore: 构建过程或辅助工具的变动
```

## Beta 后临时 dev 分支流程

当已经打出 beta tag，并决定当前版本不再接收新功能时，`main` 进入 feature freeze：

- `main` 只接收当前版本必要的 bugfix、文档修正、测试补充、版本号和发布配置调整
- 已开发但不进入当前版本的新功能，不再合入 `main`
- 从 `main` 创建临时 `dev` 分支，用于接收冻结期间已经完成的新功能
- 冻结期间，`feature/*` 分支合入 `dev`，不要直接合入 `main`
- 冻结期间，合入 `main` 的 release bugfix 需要同步到 `dev`，避免正式发布后丢失修复
- 正式版发布并打 tag 后，再将 `dev` 合回 `main`，然后删除临时 `dev` 分支

示例流程：

```bash
# 已经在 main 打出 beta tag 后
git checkout main
git checkout -b dev

# 冻结期间：新功能合入 dev，发布修复合入 main

# 正式发布后，将 main 上的发布修复同步到 dev，再合回 main
git checkout dev
git merge main

git checkout main
git merge --no-ff dev

git branch -d dev
```

这个 `dev` 不是长期分支，只在当前版本 feature freeze 期间作为下一版本的临时集成线。发布完成并回合后，应删除该分支，恢复日常 `feature/* -> main` 的开发方式。

## Bug 修复发布流程

当版本发布后发现 Bug，需要按实际分支状态处理。

### 情况 1：`main` 上没有未完成的新功能

直接在 `main` 修复并发布：

```bash
git commit -m "fix: resolve config parser crash"
git tag -a v0.3.1 -m "Hotfix v0.3.1"
git push origin main --tags
```

### 情况 2：`main` 上已有未完成的新功能

从上一个 tag 创建 hotfix 分支：

```bash
git checkout -b hotfix/0.3.1 v0.3.0

# 修复问题
git commit -m "fix: resolve config parser crash"
git push origin hotfix/0.3.1

# 测试后合并回 main 并打 tag
git checkout main
git merge --no-ff hotfix/0.3.1
git tag -a v0.3.1 -m "Hotfix v0.3.1"
git push origin main --tags

# 删除临时分支
git branch -d hotfix/0.3.1
git push origin --delete hotfix/0.3.1
```

## 测试配置

首次运行部分测试前，需要准备测试环境变量：

```bash
cp test/.env.test.example test/.env.test
```

如果你的改动涉及认证、知识库、文件系统或智能体流程，建议补充对应集成测试，避免只覆盖单元逻辑。

## 反馈渠道

- Bug 反馈：<https://github.com/xerrors/Yuxi/issues>
- 功能讨论：<https://github.com/xerrors/Yuxi/discussions>

感谢每一位贡献者的投入。
