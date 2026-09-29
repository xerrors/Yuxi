# Beta2 版本升级与脚本覆盖收敛

状态：archived
类型：bug-fix
Owner：scripts/bump-version.sh

## 问题

`bump-version.sh` 声明同步所有当前硬编码版本，但正式版本模式此前只更新 clone 命令，没有更新 README、快速开始和部署指南中的“当前版本”文案及部署 checkout 目标。运行脚本从 `0.7.2.beta1` 升级到 `0.7.2.beta2` 后，这些活动文档仍指向 beta1，而 changelog 中的 beta1 发布记录又必须保持历史事实。

## 决策

正式版本模式在已知活动文档位置更新当前版本说明、clone 分支和部署 checkout 目标；`--dev` 继续跳过这些发布文档。changelog 与迁移说明中的历史版本不参与替换。隔离文件树回归测试覆盖真实数量的重复 Compose 标签、正式版本更新、dev 跳过语义、非法版本输入和历史记录保留，并接入 Make 与 Engineering Trust workflow。

本次通过脚本将后端包、工作区、Web、Compose 镜像默认标签、锁文件和发布文档统一升级到 `0.7.2.beta2`，同时新增 beta2 发布记录。版本格式沿用仓库现有 `0.7.2.betaN` 约定；uv 接受该 PEP 440 版本并保持锁文件可复现。

## 替代方案

- 每次手工补改文档：不能闭合脚本“自动同步”的承诺，拒绝。
- 全仓字符串替换 beta1：会篡改 changelog 和迁移历史，拒绝。
- `--dev` 模式升级 beta2：会故意跳过发布文档，不符合 Beta 发布目标。

## 后果

活动版本入口现在由脚本定点同步，历史版本仍可独立保留。新增活动文档时仍需同时扩展脚本与回归 fixture。`make format` 还收敛了两个 Lucide import 的 Prettier 格式，没有改变运行时行为。

Tag、远端推送和 CI 属于提交后的外部状态；tag 只能在本地门禁与独立 Review 通过后创建，不覆盖同名 tag。

## 验证

- `make format`：通过；backend 233 files unchanged，Web 只格式化两个 `@lucide/vue` import。
- `make lint`：通过。
- `make test`：1590 passed。
- `cd web && pnpm run test:unit && pnpm run build`：136 passed，build 通过。
- `cd packages/yuxi-cli && uv run --python 3.13 --group test pytest`：90 passed。
- `cd docs && pnpm run build`：通过；保留既有 VitePress/Rolldown 警告。
- `make verify-trust`、`scripts.test_dependency_update_policy`：通过；版本脚本 3 个隔离案例全部通过。
- `make audit-dependencies`：backend、CLI、Web、docs 生产依赖无已知漏洞，Python/Node 漏洞负控按预期命中。
- backend/web/docs frozen lock、dev Compose 和带临时非敏感占位变量的 production Compose config：通过。
- 版本 Owner oracle、uv lock check、脚本幂等检查和 `git diff --check`：通过。
- 独立 Reviewer 指出的决策状态、重复入口/dev/非法输入测试覆盖和审计证据均已收敛。
