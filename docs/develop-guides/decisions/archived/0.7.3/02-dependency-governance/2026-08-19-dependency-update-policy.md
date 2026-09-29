# 依赖更新降噪策略

状态：archived
类型：process
Owner：.github/dependabot.yml

## 问题

Dependabot 对七类依赖源每周独立创建常规版本 PR，每类允许五个开放 PR。一次扫描可以产生数十个互相修改同一锁文件的 PR，并把数据库、缓存、向量库、运行时和模型服务的大版本迁移表现为普通单行镜像更新。依赖审计 workflow 同时在所有 PR 上运行，即使变更不涉及依赖或审计逻辑，也会下载完整 Python 与 Node.js 生产依赖。

## 决策

所有生态在 `.github/dependabot.yml` 中保留 update entry 和 weekly schedule，但统一使用 `open-pull-requests-limit: 0` 关闭常规 version update PR。该限制只作用于 version updates；对支持 security updates 且仓库已启用该能力的 uv、npm 与 GitHub Actions 依赖，它不计入、也不阻止 security update PR。Docker 与 Docker Compose 不支持 Dependabot security updates；镜像风险当前依赖人工上游生命周期检查和显式升级任务，容器镜像扫描仍是未覆盖范围。普通依赖升级由明确的安全、兼容、生命周期或功能需求触发，并按受影响语义边界人工更新。

依赖审计 workflow 只在 shipping manifest、锁文件、审计 workflow、Makefile 或固定脆弱 fixture 变化时触发，并取消同一分支已经过期的运行。手工触发和 main 上对应变更的 push 审计保持可用；漏洞与许可证 gate 的完整决定由[依赖供应链审计门禁](./2026-08-18-dependency-supply-chain-gates.md)拥有。

## 替代方案

- 保持每个依赖一个 PR：隔离清楚，但持续制造没有当前需求的审查队列。
- 聚合 patch/minor 更新：减少 PR 数量，但锁文件解析会把大量传递依赖捆绑，仍会产生难以归因的失败和周期性维护工作。
- 自动合并绿色 Dependabot PR：普通 CI 不能证明框架契约、文档解析、持久数据迁移、GPU 镜像、运行时大版本或真实 provider 兼容。
- 完全关闭 Dependabot：会失去安全更新入口和低风险版本漂移提示。
- 为所有 Docker 镜像维护逐项 ignore 规则：规则随镜像和版本增长，形成第二份需要人工同步的迁移清单。

## 后果

- 仓库不再自动收到新版本提示；维护者在出现漏洞、上游生命周期、功能需求或兼容问题时主动升级，并使用对应项目的 lint、unit、build、integration 或 E2E 证明结果。
- Docker 与 Compose 的非安全版本漂移不再自动形成 PR，需要在有兼容性、生命周期或功能需求时显式规划升级。
- 路径过滤遗漏新的 manifest 会让依赖审计不自动触发；新增依赖 Owner 时必须同步更新 workflow paths。

## 验证

`scripts/test_dependency_update_policy.py` 逐个断言七类生态保留 update entry 与 weekly schedule、常规版本 PR 上限为零，并拒绝没有 consumer 的 group、allow、ignore 和 cooldown 规则；负向案例恢复 Docker 常规 PR、删除 schedule 或重新加入分组时会失败。测试同时验证 dependency audit 的 manifest/lock 路径过滤与并发取消，`trust.yml` 在每个 PR 运行该测试。提交前运行工程契约、对应单元测试、YAML 解析和 `git diff --check`，并由独立 Reviewer 核对 security update 支持边界。
