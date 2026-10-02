# 依赖工具链与锁文件刷新

状态：archived
类型：process
Owner：web/pnpm-workspace.yaml

## 问题

仓库需要让本地、CI 与 Docker 使用一致的当前稳定 pnpm 和 uv，并刷新 Dependabot 所覆盖的 npm 与 uv 依赖。pnpm 11 不读取 `package.json` 中旧的 `pnpm` 配置，因此版本升级同时需要迁移安全 override 和构建脚本许可，避免工具升级削弱供应链约束。

## 决策

本地、package manifest、Docker 与 CI 固定使用 pnpm 11.24.0；本地、Docker uv 镜像和 setup-uv 固定版本使用 uv 0.12.6。Web `pnpm-workspace.yaml` 是 pnpm 11 安全配置的首要 Owner；docs 的同名文件、两个 package manifest、Dockerfile 与 workflows 分别拥有各自装配事实。Web 与 docs 的 overrides 移入各自 workspace 配置，pnpm 11 只允许当前依赖图需要的 `esbuild` 和 Web `core-js` 执行安装脚本。Docker dependency layer 复制 workspace 配置，dependency-audit 的路径 Owner 同时覆盖这两个文件。

Web、docs、backend 与 CLI 锁文件按当前 manifest 约束刷新。后端锁文件获得兼容的 PyTorch 2.13 CPU 依赖后，漏洞审计删除不再命中的两个历史 ignore。Dependabot 常规版本 PR 按仓库策略关闭；本次通过受管 manifest 的 outdated/lock upgrade 重建更新范围。唯一 open security alert 属于故意脆弱的 Node 审计负控 fixture，因此保留其漏洞版本。历史上已关闭的 PostgreSQL、Milvus、Redis、Python、Node 等跨主版本镜像更新不在缺少迁移与真实拓扑验证时重新引入。

## 替代方案

- 只改版本字符串、不迁移 pnpm overrides：pnpm 11 会忽略原配置，因此不采用。
- 将所有 Docker 镜像升级到最新主版本：会改变数据库格式、服务协议和部署兼容，超出工具链与锁文件刷新范围。
- 修复审计 fixture 的 open alert：会破坏证明审计 gate 能检测真实漏洞的负控，因此不采用。
- 保留已失效的 uv audit ignore：产生无匹配警告并扩大未来审计盲区，因此删除。

## 后果

pnpm 11 frozen install 在本地、CI 和 Web Docker build 中读取同一 workspace 安全配置。后端与 CLI 使用 uv 0.12.6 生成的锁文件，API Docker build 从同版本镜像复制 uv。工具链刷新时 Web 没有可更新的正常依赖；当时 deprecated 的 `lucide-vue-next` 不存在更高版本，替换包被留给后续独立迁移，现已由[迁移 Lucide Vue 官方包](../14-frontend-ux/2026-08-26-lucide-vue-package-migration.md)完成。docs 和直接 Python 依赖没有剩余 outdated 项。

版本查询与 Dependabot 状态是本次更新时间点的证据，之后会随上游漂移。真实 PostgreSQL integration、完整 E2E、外部 provider 和 Windows PowerShell 7 初始化脚本未由本次验证覆盖。

## 验证

- `pnpm --version` 与 `uv --version`：分别为 11.24.0 和 0.12.6；构建出的 Web/API 镜像回读版本一致。
- Web `pnpm install --frozen-lockfile`、`pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`：通过，136 tests passed。
- `cd docs && pnpm install --frozen-lockfile && pnpm run build`：通过；保留既有 VitePress/Rolldown 兼容警告。
- `uv run --project backend --python 3.13 --group test pytest backend/test/unit -m 'not slow' -q`：1590 passed。
- `cd packages/yuxi-cli && uv run --python 3.13 --group test pytest`：90 passed。
- `docker build -f docker/web.Dockerfile --target build-stage -t yuxi-web:pnpm11-test .` 与 `docker build -f docker/api.Dockerfile -t yuxi-api:uv012-test .`：通过。
- `make audit-dependencies`：四个生产依赖集合无已知漏洞，Python/Node 漏洞 fixture 负控均按预期失败并命中目标 advisory。
- `make audit-licenses`：backend 与 CLI 许可证清单成功生成。
- `python3 scripts/verify_engineering_contracts.py && python3 -m unittest scripts.test_verify_engineering_contracts scripts.test_dependency_update_policy`：通过，66 tests passed。
- `uv lock --check`（backend、CLI）、npm/uv outdated 检查和 `git diff --check`：通过；工具链刷新时 Web 仅报告无更新版本的 deprecated `lucide-vue-next`，随后已独立迁移。
- 独立 Reviewer：No blocking findings；未审查工作区同时存在的文件夹上传并发变更。
