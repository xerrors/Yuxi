# 缩短 CI 反馈时间并统一拓扑准备

状态：implemented
类型：testing
Owner：.github/workflows/system-tests.yml

## 问题

CI 在保留完整测试集合、真实服务边界和失败传播的前提下，需要缩短反馈时间。远端基准的完整 CI 约 30 分钟，主系统 job 占 29 分 45 秒：生命周期批次约 661 秒，其中会话协作单文件约 445 秒；宿主沙盒隔离约 192 秒，其余运行时契约步骤约 446 秒。前端逐文件串行测试也增加等待。

重复的拓扑准备 YAML 容易在修改缓存、构建或环境时逐 job 漂移；沙盒隔离只需要 sandbox 镜像，完整构建其他镜像造成额外开销。PowerShell 安全契约没有 Windows 专有依赖，却独占 Windows job。手动真实供应商探针指定 self-hosted runner，与只允许临时托管执行器的环境准备脚本冲突。

## 决策

### 实现方案

[系统工作流](https://github.com/xerrors/Yuxi/blob/main/.github/workflows/system-tests.yml)按耗时和隔离需求运行五个并行 job：

| job | 职责与运行边界 |
|---|---|
| `background-job-worker-path` | 在独立 Compose 拓扑执行后台作业恢复、HTTP、文档回滚与 Milvus 检索 |
| `cooperation-e2e` | 在独立拓扑执行完整会话协作文件，使用共享模型重放 |
| `lifecycle-worker-e2e` | 在独立拓扑执行其余生命周期及 worker 链路 E2E，使用共享模型重放 |
| `runtime-contracts` | 在独立拓扑执行运行时契约、权限、Knowledge 开关、OCR 与 web 工具验证；附件 HTTP 和专属 Skill E2E 同样消费共享模型重放 |
| `sandbox-isolation` | 在宿主验证真实容器隔离，只构建 sandbox 镜像，不启动 Compose 拓扑 |

[测试入口](https://github.com/xerrors/Yuxi/blob/main/backend/test/run_tests.sh)拥有确定性 E2E 集合，`e2e-cooperation` 与 `e2e-core` 的并集等于 `e2e`，保留本地完整运行入口。三个消费共享模型重放的 job 各自启动重放；运行时契约 job 先验证 Knowledge 开关，再启动重放，最后启动 web 工具叠加拓扑，避免重建 API 容器打断仍在使用的重放。每个 job 的环境准备生成独立临时加密密钥，API 与 worker 共用同一份。最终 `result` 要求五个 job 全部成功；job 成功不证明 pytest 内没有 skip。

[共享拓扑 action](https://github.com/xerrors/Yuxi/blob/main/.github/actions/prepare-topology/action.yml)封装 Buildx、环境准备、分镜像缓存恢复、构建与成功后的缓存保存。四个拓扑 job 使用完整构建，沙盒 job 显式传入 `sandbox-only: 'true'`，仅使用 sandbox 构建和缓存。挂载准备与服务启动仍由各 job 拥有。动作内容与 job 接线分别受守卫约束，保持“准备 → 构建 → 缓存保存”及“共享动作 → 挂载准备 → Compose 启动”的顺序，并拒绝拓扑 job 误传 `sandbox-only`。动作路径纳入系统测试的 push 触发范围。

[前端包配置](https://github.com/xerrors/Yuxi/blob/main/frontend/package.json)以两个文件进程运行完整 Node 测试，沿用独立 Vite 临时缓存；前端 workflow 分别显示 lint、unit、build 步骤，保留原有执行顺序与失败阻断。Ruff 检出只取所需历史。

[信任工作流](https://github.com/xerrors/Yuxi/blob/main/.github/workflows/trust.yml)在 Ubuntu 的 pwsh 中运行初始化安全契约；Backend Unit Tests 不再包含 Windows job，工程契约入口登记与初始化配置测试同步归属。此决定取代 [Container 默认部署](2026-10-28-container-default-deployment.md)中将该契约交给 Backend Unit Tests Windows job 的声明。

[真实供应商探针](https://github.com/xerrors/Yuxi/blob/main/.github/workflows/real-provider-probe.yml)使用临时托管执行器、container 沙盒和默认知识能力所需的完整拓扑，保持手动触发与供应商密钥准入。Cube 兼容性由已有独立探针验证，不放宽 self-hosted 保护。

## 替代方案

- 保留两个系统 job：镜像缓存恢复与构建约 207 秒，远小于可并行化的测试耗时，继续串行无法消除关键路径。删除测试也不合适，重型步骤分别证明 Run/FIFO/SSE、权限、恢复、沙盒或 Knowledge 开关契约。
- 在同一拓扑并行执行所有测试，或把会话协作文件按用例拆开：测试会重建服务、修改共享状态，文件内也有协作时序假设，不能以改变断言语义换取并行度。
- 前端使用四进程：本地更快，但重型测试进程占用数 GB 内存；两进程在等待与托管执行器资源风险之间取舍。
- 构建一次镜像后经 GHCR 或 tarball 分发，或改用另一套缓存机制：fork PR 的权限、分发耗时和新失败面缺少足够收益依据，继续复用现有分镜像 BuildKit 缓存。
- 保留逐 job 拓扑准备副本，或将挂载与启动也收入 action：前者保留重复维护，后者需要额外输入混合构建与运行职责；共享准备、逐 job 运行的边界满足当前消费者。
- 将 PowerShell 契约独立为 workflow 和选择键，或合并 Ruff、依赖审计：短检查的启动收益不足以抵消新增接线或失去独立失败信号的代价。
- 把 OIDC 代理检查并入 trust，或把运行时 HTTP 验证迁到 service integration：前者需要真实 Docker/Nginx，后者与独立进程内数据库验证的边界不同，保留当前归属。
- 放宽真实供应商探针的 self-hosted 保护：会影响持久机器，使用临时托管执行器更直接。

## 后果

四套完整拓扑和一个沙盒构建提高 runner 总分钟数，换取更短的墙钟时间；沙盒 job 不再预热 API、MinIO 缓存。共享 action 减少重复 YAML，也增加一层调用间接，动作内容与接线守卫共同检查。动作显式使用带 `pipefail` 的 Bash，未来添加管道时遵循该失败语义。

JUnit artifact 随执行 job 分别上传；检索报告归后台作业 job，其余报告归对应生命周期、协作、运行时契约或沙盒 job。仓库外精确依赖旧 artifact 位置的消费者需要调整。报告命名和测试选择以工作流及脚本为准，不在决策中复制完整清单。

PowerShell 契约在 trust 中执行，删除独立 Windows job；此验证只覆盖安全环境脚本的当前协议，不证明 Windows 平台其他行为。前端三个独立步骤便于定位失败，前序失败仍会阻断后续步骤。

## 验证

以下本地测量来自 PR #1137 的原始验证记录，描述 rebase 前的证据，不代表更新后分支已完成全量验证：

- 前端串行、两进程、四进程基准约 219、143、110 秒，均 1007 passed、0 skipped，逐项测试名一致；独立前端容器 lint、unit、类型检查与构建通过，独占 Vite 缓存清理通过。人为失败断言使并发命令非零退出。
- 独立拓扑执行后台恢复、HTTP、文档回滚、Milvus 检索与重解析，六份 JUnit 共 110 passed、0 skipped。宿主后端 unit 记录为 3619 passed、1 skipped；容器结果不替代宿主部署配置测试。
- 结构与负向检查覆盖缺失动作、runner、缓存、构建、挂载、重放、Knowledge 开关、实际测试入口或报告归属，以及顺序、冷构建预算和 `sandbox-only` 误接线；工程契约与工作流 YAML 检查通过。原本地 `scripts/ci` 全套记录有一项 XML 分类测试的环境相关错误，未计为通过。
- pwsh 7.4 Linux 容器执行安全契约与负控通过。真实供应商探针用本地 OpenAI 协议替身执行并核对 SSE 与持久结果，不能据此宣称外部供应商可用。

可追溯远端证据：

- [并行拆分运行](https://github.com/xerrors/Yuxi/actions/runs/41236963448)记录完整 CI 13 分 18 秒；五份系统 JUnit 与拆分前逐项比较共 449 项，无新增或丢失，全部 passed、0 skipped。
- [最终方案运行](https://github.com/xerrors/Yuxi/actions/runs/41243282197)对应 `c0e99f497`，完整 CI 12 分 51 秒；远端 job 结果均成功，生命周期与 worker 740 秒、会话协作 690 秒、后台作业与检索 667 秒、运行时契约 596 秒、沙盒隔离 227 秒。job 状态与耗时可由 GitHub 运行记录回读，不能替代 pytest 报告检查。

真实供应商调用为 `Not run`：本地缺少 `SILICONFLOW_API_KEY`，外部协议、配额和可用性未验证。流式取消时公开历史偶发缺少已展示文本的缺陷保留原断言，未在此修复；生命周期子集拆到不同拓扑后的跨文件状态依赖仍需持续观察。既有 `test_session_config_e2e.py` 不在 CI 入口选择集合内，此决定不补齐该缺口。Rebase 后的实际验证命令、结果和未验证范围由 PR 描述维护。
