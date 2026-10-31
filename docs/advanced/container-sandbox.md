# Container 沙盒（轻量执行后端）

本页覆盖默认的 `SANDBOX_BACKEND=container`。需要 VM 隔离时选择 Cube，见[沙盒配置与运维](sandbox-operations.md)。Container 模式用独立 `sandbox-runner` 为每个 Run 懒创建一个临时容器，Run 结束后销毁；它不改变 Agent 的文件工具、`execute` 工具、审批、审计和 Run 生命周期。

## 安全定位

普通 Docker/Podman 与宿主共享内核，**不是**多租户强隔离。Container 模式只适合单租户部署、可信代码或本地开发；不可信用户代码应继续使用 Cube 的 Linux/KVM 隔离。本模式不引入 gVisor、Kata 或 Firecracker，也不承诺与 Cube 相同的隔离等级。

API、worker 和 Agent 进程都不持有 Docker socket。只有 `sandbox-runner` 挂载 socket，并独占镜像校验、挂载解析、资源限制和容器清理。API/worker 只通过内网 HTTP 与共享 Bearer token 调用 runner。runner 使用同一 `POSTGRES_URL` 只读 Run 的执行归属，不修改业务状态；数据库不可达时拒绝新执行。

## 配置

```dotenv
SANDBOX_BACKEND=container
# 至少 32 个字符；初始化脚本自动生成
SANDBOX_RUNNER_TOKEN=<random-secret>
# 预构建或预拉取的单一镜像；建议使用 digest
SANDBOX_CONTAINER_IMAGE=registry.example/yuxi-sandbox@sha256:<digest>
SANDBOX_CONTAINER_IMAGE_DIGEST=sha256:<digest>
# none 为默认；bridge 会削弱网络隔离
SANDBOX_CONTAINER_NETWORK=none
```

启动时显式启用 profile：

```bash
docker compose --profile container up -d
```

生产 compose 的 `sandbox-image` 服务负责预拉取镜像；`sandbox-runner` 在镜像缺失、digest 不匹配、Docker 不可用或 token 未配置时健康检查失败，API/worker 不会在未就绪时接流量。镜像、资源与最大存活时间配置只传给 runner；业务进程仅使用后端选择、runner 地址和认证，并通过认证健康检查证明执行节点就绪。

开发环境可以先构建本地镜像：

```bash
docker build -f docker/sandbox.Dockerfile -t yuxi-sandbox:0.8.0.beta6 .
docker image inspect yuxi-sandbox:0.8.0.beta6 --format '{{.Id}}'
```

`SANDBOX_CONTAINER_IMAGE` 保留可拉取的 `name:tag` 或 `name@sha256:<manifest-digest>`；把本机镜像 ID（`sha256:...`）写入 `SANDBOX_CONTAINER_IMAGE_DIGEST` 用于校验。生产初始化要求 image 与 digest 非空，runner 在 `YUXI_ENV=production` 时拒绝未固定 digest 的镜像；本地开发允许只配置 tag，runner 会在启动时固定当前镜像 ID，但重启后不会继承该固定值。

runner 直接运行在宿主机（不在 Compose 内）时，需要把 `SANDBOX_CONTAINER_DATA_MOUNT`、`SANDBOX_CONTAINER_SKILL_MOUNT` 设为本地实际路径，并显式设置 Docker daemon 视角的 `SANDBOX_CONTAINER_HOST_DATA_DIR`、`SANDBOX_CONTAINER_HOST_SKILL_DIR`。Compose 拓扑使用默认 `/sandbox-data`、`/sandbox-skill-projections`，由 runner 从自身挂载反查宿主 Source。

## 生命周期

1. Run 调用 `execute`（包括 Skill 中的 shell）时，provider 将可信 Run/attempt 上下文传给 runner。runner 在同一 Run 的创建/删除锁内只读 PostgreSQL，核对 uid、running 状态、attempt owner、lease 和清理标记，再创建或复用容器；清理完成后才到达的旧请求也会被拒绝。
2. runner 用 Docker 宿主数据根派生稳定的部署 scope，并按 scope 与 `run_id` 生成确定性容器名，幂等创建并启动容器；同名容器标签不匹配时返回 409，不静默复用。
3. Run 内所有命令复用该容器。不同 Run、协作会话和用户各自独立，不共享容器；Project Workdir 通过宿主挂载共享文件。
4. Run 终态（包括 yielded）、取消、失败、lease 失效或 worker 重试清理时删除容器。删除幂等；容器不存在视为成功。删除未确认时保留 PostgreSQL 中的 `runtime_cleanup_pending`，阻止同 Thread 接续 Run 领取；恢复沿用现有清理与补投循环。清理边界见[沙盒机制](../mechanisms/sandbox.md#执行后端)。
5. 容器异常退出后，下一次执行删除并新建。Home 和容器内安装的软件随容器消失，Workspace 保留。
6. worker 每 30 秒按 Docker label 和 `agent_runs` 的归属、状态与 lease 对账；最大存活时间只由 runner 兜底。worker/runner 重启不会复用上一进程的内存状态。

枚举、并发计数、直接删除、TTL 与关闭清理都限定到当前数据根的 scope。不同数据槽位可共享 Docker daemon；同一数据根仍只允许一个 runner。worker 对账只删除列表快照中的具体容器 ID，避免迟到的清理影响同 Run 的新容器。采用 scope 标签前，先等待该部署已有 Run 收尾并确认旧容器已清理；没有 scope 标签的容器不会被自动接管。

没有活动 Run 的 `execute`（例如设置页的环境变量验证、独立远程 Skill CLI 安装）会显式失败，不会创建无主容器。

## 挂载与镜像

| 容器内路径 | 来源 | 权限 |
|---|---|---|
| `/home/user/workspace/<workdir>` | 当前用户 Workspace 中已绑定的 Project Workdir | 读写 |
| `/home/user/workspace/agents/skills` | 当前用户个人 Skill 持久目录 | 读写 |
| `/home/user/skills` | 当前用户获授权的共享 Skill 投影 | 只读 |
| `/home/user` | tmpfs | 读写及执行 CLI 缓存，随容器销毁 |
| `/tmp` | tmpfs | 读写，随容器销毁 |
| 根文件系统 | 镜像 | 只读 |

runner 从自身容器的 `/sandbox-data`、`/sandbox-skill-projections` 挂载反查 Docker daemon 视角的宿主路径；API/worker 不传宿主路径，模型也不能选择挂载。Workdir 与个人 Skill 目录逐段以 `O_NOFOLLOW` 打开，拒绝 `..`、符号链接、非目录和越界。Skill 投影不存在时跳过该挂载，不创建空宿主目录。

容器固定以 `SANDBOX_CONTAINER_UID:GID`（默认 1000:1000）运行；宿主 Workspace 和 Skill 投影必须允许该身份读写/读取。镜像由 [docker/sandbox.Dockerfile](https://github.com/xerrors/Yuxi/blob/main/docker/sandbox.Dockerfile) 构建，包含 Node 24、npm/npx、bash、python3、pip、uv、git、curl、jq、ripgrep、unzip 和常用 Python 库。镜像不包含 sudo，且 root 文件系统只读。

## 资源与网络

默认限制：

| 配置 | 默认值 |
|---|---|
| `SANDBOX_CONTAINER_MEMORY_BYTES` | 1 GiB |
| `SANDBOX_CONTAINER_CPUS` | 1.0 |
| `SANDBOX_CONTAINER_PIDS_LIMIT` | 256 |
| `SANDBOX_CONTAINER_HOME_BYTES` | 256 MiB |
| `SANDBOX_CONTAINER_TMP_BYTES` | 256 MiB |
| `SANDBOX_CONTAINER_MAX_CONTAINERS` | 32 |
| `SANDBOX_CONTAINER_MAX_EXECS` | 8 |
| `SANDBOX_CONTAINER_MAX_LIFETIME_SECONDS` | 7200 |

容器使用 `--network none`、非 root、`cap-drop ALL`、`no-new-privileges`、只读根、独立 PID/IPC/network namespace，不挂载宿主 PID 或 Docker socket。超过预算的输出被截断；内存、pids 超限由 cgroup 显式失败，CPU 超限由 cgroup throttling 限制。设置 `SANDBOX_CONTAINER_NETWORK=bridge` 才能联网，但这会扩大 SSRF 与数据外传面，只应在受控部署中启用。

每条命令的包装父进程保留执行身份与独立进程组，直接负责请求期限。正常结束或超时时，包装进程在退出前停止同组后台进程并确认收尾，保留原退出码或返回 124；Docker 取消请求不可用也不延长普通命令期限。子命令使用 `exec env -i` 清空环境后，收尾仍按进程组执行。主动取消先固定目标进程组，发送 TERM，必要时发送 KILL，并确认没有活进程；同 Run 的其他命令使用独立进程组。

执行流故障且局部停止无法确认时，runner 强制删除该 Run 容器并显式报错，同 Run 的其他命令也会失败。Docker 删除也失败时不会返回普通超时结果，恢复后的 Run 清理与 TTL 继续负责回收。

## 能力差异

保留：Workspace 文件读写/编辑/搜索、共享 Skill 读取、`execute`、依赖当前 Workdir 或 `/home/user/skills` 的 Skill、附件与 OCR、成果登记/预览/下载、摘要、Memory、MCP、网络搜索、深度研究、协作和定时任务。

Shell 命名空间包含当前 Run 的 Workdir、当前用户个人 Skill 目录与只读共享 Skill 投影。runner 在启动后建立 `~/.agents/skills → /home/user/workspace/agents/skills`，完成后才接受命令。个人文件跨 Run 保留；其他 Workdir 和其他 Workspace 路径仍只能由文件工具通过宿主边界访问。

在 Container 模式下不可用或语义变化：

- 没有持久 Home 或跨 Run 软件安装；容器内 Home 与 `/tmp` 随 Run 销毁。
- `--network none` 下，需要联网的 Skill（如图片生成、在线安装依赖）会失败。
- 设置页的“重建环境”和磁盘用量不适用；状态卡显示任务级临时环境。
- 环境变量仍会在 Run 内注入，但设置页的“验证配置”没有活动 Run，会显式失败。
- 对话中的个人安装可执行 `npx -y skills add <source> -g -y`，联网安装需要 `bridge`；管理页远程安装（个人或共享）没有活动 Run，仍不可用。网页上传不依赖沙盒。
- 不提供多镜像、模型自建沙盒、持久 Home 或多租户强隔离。

## 运维与验证

```bash
# runner 就绪状态；同时检查 Docker、镜像和挂载根
docker compose --profile container exec sandbox-runner curl -fsS http://localhost:8090/healthz

# 只查看受管容器
docker ps -a --filter label=yuxi.managed=true --format '{{.Names}}\t{{.Status}}\t{{.Label "yuxi.run_id"}}'

# 查看 runner 生命周期日志（不记录命令正文或环境变量值）
docker compose --profile container logs --tail=100 sandbox-runner
```

手工清理只能是运维兜底；正常回收责任属于 Run 终态和 worker 对账。删除容器后，下一次执行会按同一 Run 重新创建。

真实 Docker 验证：

该测试必须在能访问 Docker daemon、Docker CLI 和宿主数据路径的机器上运行；API 容器不持有 Docker socket，不能通过 `docker compose exec api` 执行：

```bash
docker build -f docker/sandbox.Dockerfile -t yuxi-sandbox:0.8.0.beta6 .
cd backend
SANDBOX_CONTAINER_TEST=1 \
SANDBOX_CONTAINER_IMAGE=yuxi-sandbox:0.8.0.beta6 \
SANDBOX_CONTAINER_IMAGE_DIGEST="$(docker image inspect yuxi-sandbox:0.8.0.beta6 --format '{{.Id}}')" \
uv run --no-sync --group test pytest test/integration/sandbox/test_container_sandbox.py -q
```

完整 API/worker/runner 链路可在已部署的 container 栈上运行：

```bash
TEST_BASE_URL=http://127.0.0.1:5050 \
POSTGRES_URL='postgresql://<user>:<password>@127.0.0.1:<published-port>/<db>' \
E2E_USERNAME=<admin> E2E_PASSWORD=<password> \
CONTAINER_E2E_REPLAY_URL_TEMPLATE='http://host.docker.internal:{port}/v1' \
uv run --no-sync --group test pytest test/e2e/test_container_agent_execution_e2e.py -q
```

该测试启动确定性 OpenAI replay，经真实 Session/Input/Run/Turn 调用 `execute`，用公开 Viewer API 回读 Workdir 文件，并直接查询 PostgreSQL 核对 Run 终态与清理责任。`POSTGRES_URL` 必须指向同一部署，测试进程需要读取 Run 表；API 容器必须能访问 replay 地址。生产 Compose 默认不发布 PostgreSQL 端口，需先建立受控访问或端口转发。

Docker 集成测试读取真实 Docker 事实：镜像、用户、挂载、网络、capabilities、pids/memory/CPU 限制、stdout/stderr/退出码、Workdir 双向字节与 SHA-256、超时/取消后的进程和容器清理、幂等创建/删除，以及工作区外、其他用户、`.env`、Docker socket 和云元数据不可达。具体断言由测试文件拥有；该文件在未设置 `SANDBOX_CONTAINER_TEST=1` 时跳过。

## 已知限制

- 单个 runner 实例；多副本、跨节点调度和水平扩容需要额外设计。
- 容器启动成本高于复用 Cube VM；高并发时受 `SANDBOX_CONTAINER_MAX_CONTAINERS` 限制。
- `stdin` 文件临时写入当前 Workdir，命令启动后立即删除；runner 崩溃时由下一次容器清理移除。
- Podman 需要让 `sandbox-runner` 挂载 Podman 的 Docker 兼容 socket，并把 `SANDBOX_CONTAINER_SOCKET` 指到该路径；本页的默认 Compose 使用 Docker socket。
