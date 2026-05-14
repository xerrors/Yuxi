# Contributing to Yuxi

感谢你关注 Yuxi。欢迎提交 Issue、改进文档、修复 Bug 或贡献新功能。

更完整的开发文档可参考 [docs/develop-guides/contributing.md](docs/develop-guides/contributing.md)。

## 开始之前

- 提交前请先搜索现有 [Issues](https://github.com/xerrors/Yuxi/issues)
- 对于较大的功能改动，建议先开 Issue 讨论方案
- 保持改动聚焦，避免在一次 PR 中混入无关重构

## 开发方式

本项目通过 Docker Compose 进行开发，推荐直接在容器环境中调试。

```bash
docker compose up -d
docker ps
docker logs api-dev --tail 100
```

项目中的 `api-dev` 和 `web-dev` 默认支持热重载，本地修改代码后通常无需重启容器。

## 提交流程

1. Fork 仓库并创建分支
2. 在对应目录完成开发与测试
3. 提交清晰的 Commit Message
4. 发起 Pull Request，并说明修改内容、原因和验证方式
5. PR 模板 [PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) 中的检查项需要在提交前完成

示例：

```bash
git checkout -b feature/your-change
git commit -m "feat: add knowledge graph import flow"
git push origin feature/your-change
```

## 代码要求

### 通用

- 保持实现简单直接，避免过度设计
- 只修改当前任务所需内容，不顺手做额外重构
- 更新相关文档
- 如有必要，同步更新 [docs/develop-guides/roadmap.md](docs/develop-guides/roadmap.md)
- 设计部分请参考 [docs/develop-guides/design.md](docs/develop-guides/design.md)

### 后端

- 使用 Python 3.12+ 风格
- 提交前运行：

```bash
make format
make lint
docker compose exec api uv run pytest
```

- 测试脚本建议放在 `backend/test`

### 前端

- 使用 `pnpm`
- API 接口统一放在 `web/src/apis`
- 优先使用 `lucide-vue-next` 图标
- 样式使用 `less`
- 非特殊情况优先复用 [web/src/assets/css/base.css](web/src/assets/css/base.css) 中的颜色变量

## Pull Request 建议

- 标题清晰，能说明变更目标
- 描述中包含改动内容、影响范围和验证结果
- 如果涉及 UI，请附截图或录屏
- 如果涉及接口或行为变化，请补充文档

## 提交信息建议

推荐使用以下前缀：

- `feat`
- `fix`
- `docs`
- `refactor`
- `test`
- `chore`

## 问题反馈

- Bug 反馈/功能讨论：<https://github.com/xerrors/Yuxi/issues>

感谢你的贡献 ❤️。
