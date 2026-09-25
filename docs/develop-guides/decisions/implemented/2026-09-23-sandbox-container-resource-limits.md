# 单沙盒执行资源上限

状态：implemented
类型：feature
Owner：docker/sandbox_provisioner/app.py

## 问题

sandbox-provisioner 通过 `containers.run` 创建沙盒时不带 `mem_limit`、CPU 与 `pids_limit`，单次执行可以吃满宿主机内存、CPU 或进程数；实测既有三个 provisioner 栈创建的容器均为 `HostConfig.Memory=0`。

## 决策

Docker backend 由 `sandbox_container_limits()` 解析三个环境变量并在 `containers.run` 时注入：`SANDBOX_MEM_LIMIT`（默认 `2g`，只接受纯字节数或整数加 `k/m/g` 后缀）、`SANDBOX_CPUS`（默认 `2`，转 `nano_cpus`，截断后不足 1 nano-cpu 即拒绝，堵住 docker-py `if nano_cpus` 静默丢弃的 fail-open）、`SANDBOX_PIDS_LIMIT`（默认 `512`）。非法值启动时抛 `RuntimeError` 显式失败，不回退默认。两份 Compose 的 `sandbox-provisioner` 透传同名变量并给相同默认值，参数表记录在 `docs/agents/sandbox-architecture.md`。

## 替代方案

- 只依赖 `SANDBOX_EXEC_TIMEOUT_SECONDS`：兜时长，兜不住内存与进程数，被拒绝。
- 在 API/worker 侧做配额：执行边界是 Docker daemon，Owner 外的校验可被绕过，被拒绝。
- 同步修改 Kubernetes backend：无集群可验证，提交未验证行为违反证据规则；作为已知缺口保留在后果中。

## 后果

2G 默认可能截断重依赖安装类执行；512 进程触顶时容器内 `fork`/`clone` 会以难以定位的资源错误退出，需分别调 `SANDBOX_MEM_LIMIT`、`SANDBOX_PIDS_LIMIT`。已创建的沙盒不回溯上界，重建后生效。Kubernetes backend 创建的 Pod 仍无上限，三个变量只对 Docker backend 生效。

## 验证

- 真实 Docker E2E：以 `1g/1.5/256` 启动 provisioner，创建沙盒后 `docker inspect` 返回 `Memory=1073741824 NanoCpus=1500000000 PidsLimit=256`，容器内 cgroup `memory.max`/`pids.max`/`cpu.max` 数值一致。
- unit：`test_sandbox_provisioner_config.py` 覆盖默认值与环境覆盖、8 个非法输入负向案例（mem 正则、cpus 的 nan/inf/截断为 0、pids 非法）、启动期环境失败、`__init__` 接线失败与创建路径注入；`test_docker_compose_service_boundaries.py` 断言两份 Compose 透传三个变量。
- Not run：Kubernetes backend 的 Pod 资源上限，见替代方案第三条。
