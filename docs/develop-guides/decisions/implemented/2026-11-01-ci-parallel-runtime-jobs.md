# 按实测耗时把系统测试拆分为五个并行 job

状态：implemented
类型：testing
Owner：.github/workflows/system-tests.yml

## 问题

[缩短 CI 验证等待并恢复真实供应商探针](2026-11-01-ci-feedback-time.md)把检索 E2E 移入后台作业 job 后，PR 的完整 CI 仍需约 30 分钟。远端运行数据显示关键路径完全在主系统 job「PostgreSQL, readiness and Agent lifecycle」（29 分 45 秒）：`e2e` 生命周期批次 661 秒中 445 秒集中在 `test_session_cooperation_e2e.py` 一个文件；宿主机沙盒容器隔离占 192 秒；其余约 25 个 HTTP 契约与权限步骤合计约 446 秒全部串行。后台作业 job 只用 11 分 43 秒，能力明显闲置。该记录曾以"额外构建和维护一整套拓扑"为由拒绝继续拆分，但实测固定开销（缓存恢复约 53 秒 + 镜像构建约 154 秒）远小于可并行化的测试耗时，此理由不再成立。

## 决策

`system-tests.yml` 由两个运行时 job 拆为五个并行 job，测试集合不变，按实测耗时与语义归属分组：

- `background-job-worker-path`（后台作业与检索）：不变，仍执行后台作业恢复、HTTP、文档回滚与 Milvus 检索。
- `cooperation-e2e`（会话协作 E2E）：只执行 `run_tests.sh e2e-cooperation`（`test_session_cooperation_e2e.py`），拥有独立拓扑与共享模型重放。
- `lifecycle-worker-e2e`（生命周期与 worker E2E）：执行 `run_tests.sh e2e-core`（生命周期批次其余九个文件）与权限撤销、知识 MCP Agent、MCP 故障隔离、容器 Agent 执行四组 worker 链路 E2E；这些步骤全部依赖共享模型重放。
- `runtime-contracts`（运行时契约与知识开关）：Knowledge 开关、worker 健康检查和全部 HTTP 契约、权限、OCR、web 工具步骤。其中附件 HTTP 契约（`test_public_agent_files.py`）与专属 Skill E2E（`test_agent_bound_skill_e2e.py`）真实消费共享模型重放，因此该 job 也启动重放；Knowledge 开关最先执行（会重建 api 容器，重放必须在它之后启动），web 工具叠加拓扑最后执行（同样重建 api，重放此后不再被使用）。OCR 使用测试内自建 MinerU 替身，web 工具使用 `WEB_TOOLS_REPLAY_URL`；OCR 步骤相对原顺序后移到 web 配置之后，它与前后步骤无共享状态。
- `sandbox-isolation`（沙盒容器隔离）：宿主机直接执行 `test_container_sandbox.py`，复用拓扑镜像构建产物中的 sandbox 镜像，不启动 Compose 拓扑。

`run_tests.sh` 保持 E2E 用例集合的唯一 Owner：新增 `e2e-cooperation` 与 `e2e-core` 两个子集入口，`e2e` 仍等于二者之和，本地一次性运行入口不变。每个拓扑 job 继续通过 `prepare_system_tests_env.sh` 生成独立临时密钥并初始化独立 E2E 管理员；`result` job 要求五个 job 全部成功。

本次未单独创建 `proposed` 记录：这是对同一 PR 内 CI 反馈时间问题的继续迭代，用户明确要求以"推送—测量远端 CI—再优化"的循环直接验证，替代方案在本记录内完整权衡。

## 替代方案

- 删除部分系统测试以缩短时间：拒绝。每个重型步骤都对应真实契约（Run/FIFO/SSE 与恢复须 E2E、权限须真实 HTTP、沙盒须容器隔离、Knowledge 关闭须专项验证），没有可安全删除的冗余集合；耗时大头是时序等待断言，不是重复覆盖。
- 在 `test_session_cooperation_e2e.py` 内部按用例再拆分：拒绝。文件内存在共享的会话协作状态与时序假设，按用例拆拓扑可能改变断言语义，收益（约 3 分钟）不足以承担该风险。
- 构建一次镜像后经 GHCR 或 tarball 在 job 间共享：拒绝。fork PR 无包推送权限，tarball 上传下载与现有 buildkit 层缓存收益相近，却新增一套失败面。
- 维持两个 job 并只把沙盒测试移出主 job：接受过（本 PR 第一版），但主 job 仍有约 26 分钟，无法达到按并行度平衡后的约 12 分钟。

## 后果

- 各 job 的 JUnit artifact 重新归属：`e2e-core/system-09/15/20/31` 进入 `lifecycle-worker-e2e-junit`，`system-06..08/10..14/16..18/21..30/33/34/system-ocr` 进入 `runtime-contracts-junit`，`system-32` 进入 `sandbox-isolation-junit`，`e2e-cooperation` 进入 `cooperation-e2e-junit`；仓库内没有按旧位置消费这些 artifact 的代码，仓库外精确依赖旧位置的消费者需要调整。
- 每个 PR 的 runner 总分钟数上升（五个拓扑 job 各付出一次镜像构建），换约 2.5 倍墙钟时间收益；job 并行度仍低于 GitHub 托管执行器并发上限。
- `scripts/ci/test_resource_boundaries.py` 的结构守卫改按"四个拓扑 job + 沙盒 job"断言；重放与 Knowledge 开关的接线约束改写为"三个运行 pytest 的拓扑 job 各启动一次共享重放，后台作业与沙盒 job 不启动；开关只在契约 job 出现一次且先于该 job 的重放启动"。`scripts/ci/test_release_workflows.py` 的挂载准备守卫改按四个拓扑 job 断言；冷构建预算守卫继续要求所有 job `timeout-minutes` 不低于 60。
- `verify_engineering_contracts.py` 的 `WORKFLOW_ENTRIES` 登记两个子集入口，防止任一子集失去实际执行步骤。

## 验证

- `python3 scripts/ci/verify_engineering_contracts.py` 通过；`python3 -m unittest discover -s scripts/ci -p 'test_*.py'` 共 105 项，除既有的 `test_pytest_xml_distinguishes_skipped_and_failure` 在基线提交同样报错（环境相关，与本变更无关）外全部通过。结构守卫覆盖：删除任一拓扑 job 的 runner/缓存/构建/挂载准备接线、删除沙盒 job 的实际执行入口或报告归属步骤、删除 Knowledge 开关或任一重放启动、把开关移到重放启动之后、把任一 job 超时降到 60 以下，均使检查失败。
- 负控复核：`e2e-cooperation` 与 `e2e-core` 的选择器集合之并等于原 `e2e` 列表，`run_tests.sh` 以 `bash -n` 通过语法检查。
- 远端 GitHub runner 的实际总耗时、拆分后各拓扑 job 的测试结果（特别是生命周期批次拆分是否引入跨文件状态依赖）以本 PR 的 CI 运行为准；本地未完整执行五个拓扑 job。
