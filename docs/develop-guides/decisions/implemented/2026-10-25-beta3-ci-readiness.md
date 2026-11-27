# 0.8.0 beta3 的 CI 验收与 main 切换边界

状态：implemented
类型：process
Owner：scripts/bump-version.sh

## 问题

开发分支需要一个可追溯的 beta3 候选，但已有远端检查暴露了初始化、补投和 MCP 故障测试的契约漂移。文档仍未完成，候选标签不能表示正式发布或生产升级可用。

## 决策

### 实现方案

该候选的版本脚本同步应用、锁文件、Compose、sandbox runner 与集成测试的默认镜像及容器沙盒文档示例至 `0.8.0.beta3`，源码获取入口指向 `develop/0-8-0`。初始化集成测试明确回读 business 1、knowledge 2；补投替身返回实际投递接口的成功值；MCP 故障 E2E 使用注册的 `ocr_parse_file` 依赖验证工具隔离、普通聊天及持久告警。

正式入口与候选引用并存时，`scripts/bump-version.sh` 仍同步候选的应用和镜像版本。继续发布候选时，公开文档只替换完整的当前候选版本号，正式安装命令和稳定版沙盒示例保留原值。从正式版开始首个候选时，维护者按需添加候选说明。`--dev` 跳过公开文档同步。正式发布只定向更新安装命令、镜像示例等版本入口，维护者按[贡献指南](../../contributing.md#候选版本与正式发布)定稿。

保留现有 workflow、路径过滤和结果断言。开发分支推送后的检查必须归属同一个候选提交，全部通过才创建并推送 `v0.8.0.beta3`，同时核对 tag 触发的检查。不创建 GitHub Release、不上传 npm。维护者指定 main 直接替换为最终验收的开发分支提交，不采用合并方式；候选准备阶段只核对切换目标、远端保护和文档部署边界，尚未更新 main。

## 替代方案

- 降低断言或跳过失败测试：不能验证当前初始化、队列和 MCP 故障语义。
- 仅对 beta2 提交新增 beta3 标签：应用版本和标签不一致。
- 合并开发分支到 main：不符合维护者指定的直接替换策略，额外引入合并结果与冲突处理。
- 候选升级同步所有克隆命令：会让稳定版安装入口提前指向候选，导致命令和部署说明的版本不一致。

## 后果

候选版本与正式发布分离。main 切换后的源码以最终验收提交为准，旧 main 的独有提交不通过合并带入。准备阶段读取的旧 main 为 `928141ce`；实际替换前仍需重新读取远端 SHA，并按该值使用 lease 保护，避免覆盖并发提交。远端 force-push 保护的允许范围须在执行替换前核对。

只读检查确认仓库规则 `Protect main from force push`（21189549）对 `refs/heads/main` 启用 non-fast-forward 与删除保护，当前没有 bypass actor；当前凭据为 ADMIN 也不等于已配置绕过。直接替换前需要单独处理该规则的允许范围；候选准备没有修改保护规则或 main。

直接替换不需要处理合并预演中的 13 个冲突。文档发布页仍明确截至 `97e04174`，其中历史 Schema 说明不覆盖当前 knowledge v2；正式 release 前需补齐文档。main push 会部署文档站，切换准备需要考虑这一外部副作用。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 初始化和补投测试遵循当前契约 | 旧 Schema 预期或空成功返回值 | PostgreSQL schema、scheduler、对应 integration | 真实 PostgreSQL 目标测试 | 修改前目标集合 4 failed / 1 passed，失败原因分别为版本预期与补投返回值 | Passed，本地 37 项 |
| MCP 故障仍隔离 Skill 工具并保留聊天 | 无效依赖导致创建提前失败 | Skill 校验、MCP runtime、故障 E2E | 真实 HTTP、worker、模型协议、PG 和事件回读 | 故障 MCP 三种模式及有无 Skill 的六种组合 | Passed，本地 6 项 |
| 候选与 tag 对应已验证提交 | 版本错位、CI 缺失或使用旧 SHA 结果 | 版本脚本、manifest、Git refs、Actions | 版本脚本单测、锁文件校验；远端 headSha、job conclusion 和 tag 解引用由 Actions 与 Git refs 拥有 | 既有版本与发布工作流负控 | 本地 Passed；远端 CI 和 tag 在提交时 Not run |

实际执行：

- `python3 scripts/verify_engineering_contracts.py`：Passed。
- `python3 -m unittest scripts.test_verify_engineering_contracts scripts.test_bump_version scripts.test_release_workflows`：Passed，79 项。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_app_initialization.py test/integration/services/test_schema_migration_version.py test/integration/services/test_agent_input_concurrency.py -q --tb=short`：Passed，37 项；独立 Schema、临时 Skill 目录及持久结果回读。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_mcp_failure_isolation.py -q --tb=short`：Passed，6 项；模型使用确定性 HTTP 重放。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：Passed，3273 项、122 skipped；容器缺少仓库根文件的部署配置范围由宿主补跑。
- `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`：Passed，204 项，无跳过。
- `uv lock --project backend --check --offline`、`pnpm --dir docs run build`：Passed。
- `uv run --project backend --no-sync ruff check backend/yuxi`、`uv run --project backend --no-sync ruff check --select I backend/yuxi`、`uv run --project backend --no-sync ruff format backend/yuxi --check`：Passed，441 个源码文件；检查发现的一处显式导出 import 排序已修正。
- `pnpm --dir frontend run lint:check`、`pnpm --dir frontend run test:unit`、`pnpm --dir frontend run build`：Passed，849 项 unit、无跳过，build 包含 typecheck。旧开发容器内的同名 unit 命令为 840 passed / 9 failed；文件哈希核对确认其未挂载的 `eslint-boundaries.js` 仍是旧规则，当前 checkout 的 19 项边界目标测试和完整集合通过。旧容器结果不作为候选通过证据，未为此放宽断言或修改产品配置。

版本脚本的扩展夹具在修复前为 2 failed / 1 passed，明确复现普通升级与 dev 升级遗漏 sandbox 镜像默认值；修复后 3 项通过，仍验证 dev 模式保留公开文档和历史记录。

`python3 -m unittest scripts.ci.test_bump_version` 的当前 5 项覆盖候选同步、首个候选保留正式入口、正式升级保留历史起点、开发模式及非法版本。恢复全篇替换会改写旧迁移版本，移除版本边界会误改较长的历史候选号。

远端 CI 在提交时尚未运行，本地证据不能代替对应提交的 Actions 结果；标签创建以该结果为前置条件。

## 风险

CI 的 container 拓扑不证明 Cube/KVM、外部供应商或真实数据迁移可用。文档构建成功不代表文档完整，main 切换与正式 release 保持独立。版本同步改变沙盒镜像默认值，不改变业务流程；本地 MCP E2E 使用当前开发服务，候选镜像构建与完整 container 拓扑仍由远端系统检查验证。
