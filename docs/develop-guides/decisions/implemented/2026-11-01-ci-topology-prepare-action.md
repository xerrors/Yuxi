# 拓扑准备基建收敛为共享 composite action

状态：implemented
类型：testing
Owner：.github/actions/prepare-topology/action.yml

## 问题

五个系统 job 中有四个拓扑 job 手抄同一份 9 步基建块（Buildx、环境准备、三层缓存恢复、构建、三份缓存保存），沙盒 job 另有 5 步变体。这些块由[五个并行 job](2026-11-01-ci-parallel-runtime-jobs.md)的拆分手工复制而来：约 200 行重复 YAML 让"改一处、漏三处"成为结构性风险，且逐 job 守卫必须对每个副本重复断言。

## 决策

新增 composite action `.github/actions/prepare-topology`：封装 Buildx、`prepare_system_tests_env.sh`、三层缓存恢复、按 `sandbox-only` 输入切换的构建命令（完整拓扑或仅 sandbox 镜像）与缓存保存。四个拓扑 job 各以一行 `uses: ./.github/actions/prepare-topology` 调用；沙盒 job 传入 `sandbox-only: 'true'`。挂载准备（`install -d`）仍留在各 job 内——它依赖 build 产物且只有运行 pytest 的 job 需要，顺序约束由守卫保持"动作 < 挂载准备 < compose up"。

守卫改为两层：动作文件断言环境准备先于构建、三次保存后于全部构建、`--sandbox-only` 存在；job 断言共享动作的使用位置、`sandbox-runner` 进入 `compose up`、沙盒 job 无挂载准备。`system-tests.yml` 的 push 路径加入 `.github/actions/**`，动作自身的变更必须触发系统测试。

## 替代方案

- 同时把 buildkit 本地缓存换成 `type=gha` 直连缓存：拒绝。远端日志显示现有键精确命中，恢复耗时主要是整包下载，gha 分层导入收益不确定，而本轮已经重构同一区域，一次只换一个机制。
- 连挂载准备一起收进动作（再加一个输入区分）：拒绝。挂载准备与 `compose up` 的顺序是逐 job 的运行语义，留在 job 内让"动作 < 挂载 < 启动"的守卫在两份文件间保持清晰。

## 后果

动作内 run 步骤显式 `shell: bash` 带 `pipefail`，与 workflow 裸 `run:` 的默认 `bash -e` 存在微差；现有三步无管道，未来在动作步骤里引入管道时按动作语义执行。`system-tests.yml` 从 831 行降至 632 行；基建变更（换缓存键、加镜像）今后只改动作文件一处。动作多了一层间接（job 不再直接可见构建步骤），由两层守卫补偿；`actions/cache` 在 composite 内的 `steps.<id>.outputs` 传递是 GitHub 支持的用法。墙钟与 runner 分钟预期不变。

## 验证

- `python3 -m unittest scripts.ci.test_resource_boundaries scripts.ci.test_release_workflows`：30 项通过；负控覆盖删除任一拓扑 job 的动作调用或 `sandbox-runner`、删除动作内的环境准备/缓存保存/`--sandbox-only`、把 api 保存移到恢复之前、删除沙盒 job 的输入或真实执行入口，均使检查失败。
- `python3 scripts/ci/verify_engineering_contracts.py` 通过；`scripts/ci` 全套除既有环境相关错误外通过；工作流与动作 YAML `safe_load` 通过；`git diff --check` 通过。
- 远端 CI 结果以本 PR 对应运行为准；五个 job 的镜像构建、缓存命中与测试结果需在远端复现后才算闭合。
