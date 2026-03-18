# 参与贡献

感谢你对 Yuxi 项目的兴趣！我们欢迎任何形式的贡献，包括但不限于代码提交、功能建议、问题反馈和文档改进。

<a href="https://github.com/xerrors/Yuxi-Know/contributors">
    <img src="https://contributors.nn.ci/api?repo=xerrors/Yuxi-Know" alt="贡献者名单">
</a>

## 贡献流程

### 1. Fork 项目

在 GitHub 上点击 Fork 按钮，将项目复制到你的账户。

### 2. 创建功能分支

```bash
git checkout -b feature/amazing-feature
```

### 3. 开发并提交

```bash
git commit -m 'feat: 添加新功能'
```

### 4. 推送代码

```bash
git push origin feature/amazing-feature
```

### 5. 创建 Pull Request

在 GitHub 上创建 PR，详细描述你的更改内容和动机。

## 代码规范

项目对代码质量有一定要求，提交前请确保：

- Python 代码使用 `make format` 格式化
- 使用 `make lint` 检查代码质量
- 添加必要的测试用例
- 更新相关文档

## 提交信息规范

使用清晰规范的提交信息：

```
feat: 添加新功能
fix: 修复 bug
docs: 更新文档
style: 代码格式调整
refactor: 代码重构
test: 添加测试
chore: 构建过程或辅助工具的变动
```

## Bug 修复发布流程

当发布后发现 bug 需要修复时：

### 情况 1：main 上没有未完成的新功能

直接在 main 修复并发布：

```bash
git commit -m "fix: 解决配置解析器崩溃问题"
git tag -a v0.3.1 -m "Hotfix v0.3.1"
git push origin main --tags
```

### 情况 2：main 上已有新功能未完成

从上一个 tag 建立 hotfix 分支：

```bash
git checkout -b hotfix/0.3.1 v0.3.0
# 修复问题
git commit -m "fix: 解决配置解析器崩溃问题"
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

## 测试指南

### 运行测试

```bash
# 全量路由测试
make router-tests

# 运行特定测试
make router-tests PYTEST_ARGS="-k knowledge_router"

# 直接运行 pytest
uv run --group test pytest test/api -vv
```

### 测试配置

首次运行测试前，需要配置测试凭据：

```bash
cp test/.env.test.example test/.env.test
```

---

感谢每一位贡献者的付出！
