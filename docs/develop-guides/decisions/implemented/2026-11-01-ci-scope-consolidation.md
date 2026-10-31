# 按"有用性与归属"收敛 CI 检查

状态：implemented
类型：testing
Owner：.github/workflows/trust.yml

## 问题

[按实测耗时把系统测试拆分为五个并行 job](2026-11-01-ci-parallel-runtime-jobs.md)解决了"怎么跑"，但没有回答"该不该跑、在哪跑"。逐项审计全部检查的用途与执行位置后发现三处错配：

- PowerShell 安全契约是唯一的 windows-latest job，但 `test_init_security.ps1` 与 `scripts/init.ps1` 不含任何 Windows 专有语义（无注册表、ACL 或盘符），只通过 pwsh 子进程验证 `-ValidateSecurityEnv` 行为；windows runner 按 2 倍计费且多数 PR 上它只是随 backend 检查陪跑。
- 沙盒隔离 job 只需要 sandbox 镜像，却完整构建并保存 api、minio、storage-init 镜像及缓存，每次 PR 白付约 150 秒构建。
- 前端三个质量门禁串联在同一个 run 步骤里（`lint && test && build`），任一失败都会掩盖后续门禁的真实状态，诊断时只能看日志文本。

## 决策

- PowerShell 安全契约合并进 `trust.yml`（ubuntu 执行器，预装 pwsh），作为工程信任契约的一个步骤；`test.yml` 删除 windows job 及其 ps1 路径触发。契约覆盖反而扩大：trust 在所有 PR 与 push 上无条件运行，不再依赖 backend 选择结果。
- `build_topology_images.sh` 新增 `--sandbox-only`：只构建 sandbox 镜像并只使用其缓存目录；沙盒 job 移除 api/minio 缓存的恢复与保存。拓扑 job 的完整构建路径不变。
- 前端三个门禁拆为三个独立步骤，失败定位直达具体门禁；执行命令与顺序不变。

## 替代方案

- 为 PowerShell 契约新增独立 workflow 并在 `gate.py` 增加路径级 `powershell` 选择键：拒绝。脚本在 ubuntu 上验证已通过，18 秒的契约不值得一套新的选择键、ci.yml 接线与守卫夹具；并入 trust 后覆盖更保守。
- 把 OIDC/MCP 代理检查（`verify_oidc_proxy.py`）从运行时契约 job 移入 trust：拒绝。该脚本需要真实 docker 启动 Nginx 容器并回读日志，不是静态契约检查，与 trust 的纯工具链定位不符。
- 把 ruff 并入 trust、三个 Node 依赖审计矩阵合并为单 job：拒绝。收益都只有约 15-30 秒的 job 启动开销，却要动选择键与守卫夹具；快速 lint 信号独立成 job、矩阵并行失败可见，各有真实价值。
- 为四个拓扑 job 的重复基础设施（checkout/buildx/缓存恢复/构建/保存）抽 composite action：推迟。收益是维护性而非运行时，且需要重写 `assert_hosted_jobs` 等逐 job 守卫为动作文件守卫；刚完成的结构守卫不宜在本 PR 内二次翻搅，留作独立后续。
- 把 runtime-contracts 中不依赖 worker 的 integration 步骤迁到独立服务 job：拒绝。两处语义 Owner 不同（真实部署拓扑 HTTP 对独立进程内数据库验证），且契约 job 已不是关键路径，迁移不减少墙钟时间，还需逐用例核对依赖。

## 后果

- 每个 PR 省去一个 windows runner job（2 倍计费）和沙盒 job 约 150 秒的无关镜像构建；信任 job 增加 pwsh 执行（约 18 秒）。
- `test.yml` 不再拥有 PowerShell 契约入口，其 push 路径过滤移除两条 ps1 条目；`verify_engineering_contracts.py` 的入口登记随 Owner 同步移动。[Container 默认部署](2026-10-28-container-default-deployment.md)中"原生执行交由 Backend Unit Tests 的 Windows job"的归属声明自此由本记录取代。
- 沙盒 job 不再顺带预热 api/minio 缓存；四个拓扑 job 继续承担全部镜像缓存预热。

## 验证

- `pwsh 7.4 (ubuntu-22.04 容器)` 实际执行 `scripts/ci/test_init_security.ps1`：安全校验矩阵与负控全部通过，证明脚本不依赖 Windows 执行器。
- `python3 -m unittest discover -s scripts/ci -p 'test_*.py'`：新增 `--sandbox-only` 模式守卫（只产生 1 条 sandbox 构建命令；配置遗漏时仍在副作用前失败）与沙盒 job 的 `--sandbox-only`/无 api 缓存断言；全套除既有环境相关错误外通过。
- 远端 runner 的实际结果以本 PR 对应 CI 运行为准；本地未复跑完整拓扑。
