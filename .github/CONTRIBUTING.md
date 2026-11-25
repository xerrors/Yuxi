# 参与贡献

感谢你关注 Yuxi。欢迎提交 Issue、修复 Bug、补充测试、改进文档或贡献新功能。

## 开始前

- 先搜索 [Issues](https://github.com/xerrors/Yuxi/issues)，避免重复工作。
- 较大的功能、公开接口、权限或架构变化，先在 Issue 或 [Discussions](https://github.com/xerrors/Yuxi/discussions) 讨论范围和方案。
- 一个 PR 只解决一个明确问题，不混入无关重构、格式化或顺手优化。
- 修改不熟悉的模块前，先阅读 [ARCHITECTURE.md](../ARCHITECTURE.md)。

完整的 Fork、开发、测试、Review 和 PR 说明见[开发贡献指南](../docs/develop-guides/contributing.md)。文档改动请同时阅读[文档编写与维护规范](../docs/develop-guides/documentation-guidelines.md)。

## 开发环境

先按[快速开始](../docs/intro/quick-start.md)初始化本地 Container 沙盒环境，再用 Docker Compose 启动开发环境：

```bash
docker compose --profile container up --build -d
docker compose ps
docker compose logs --tail=100 api
```

前端保留 Vite 热更新。API 与 worker 挂载源码，修改后执行 `docker compose restart api worker` 生效；环境变量变化需要重新创建容器。共享定义与数据库初始化按[开发贡献指南](../docs/develop-guides/contributing.md#_2-开发环境)处理，完整服务拓扑以 `docker-compose.yml` 为准。

## 常用检查

```bash
make lint
make test
python3 scripts/ci/verify_engineering_contracts.py
python3 -m unittest scripts.ci.test_verify_engineering_contracts
git diff --check
```

涉及 API、数据库、worker、SSE、沙盒、对象或浏览器时，按[测试规范](../docs/develop-guides/testing-guidelines.md)补充真实 integration 或 E2E。未执行的检查要在 PR 中说明原因，不要把未验证写成通过。

## Pull Request

PR 标题直接说明目标，正文写清背景、影响范围和验证命令。UI 改动附真实页面截图或录屏；接口、配置或行为变化同步更新文档。非平凡改动还要记录事实 Owner、决策记录、oracle、负向案例和未验证范围。

提交信息使用中文 Conventional Commit，例如：

```bash
git switch -c docs/improve-guides
git add <changed-files>
git commit -m "docs: 完善项目文档"
git push -u origin docs/improve-guides
```

## 问题反馈

- Bug 和功能建议：[GitHub Issues](https://github.com/xerrors/Yuxi/issues)
- 方案讨论：[GitHub Discussions](https://github.com/xerrors/Yuxi/discussions)
