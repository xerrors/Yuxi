# 配置沙盒与 provisioner

Yuxi 通过 `sandbox-provisioner` 为 Agent 提供文件和命令执行环境。本页面向部署和运维人员，说明如何选择 Docker 或 Kubernetes、配置连接参数以及排查沙盒问题。

沙盒的身份、文件 Owner、虚拟路径和恢复语义见[沙盒与文件系统机制](../mechanisms/sandbox.md)。

## 先明确两层配置

应用层负责“API/worker 怎样找到 provisioner”；provisioner 层负责“用什么方式创建实际沙盒”。两层变量名称不同：

| 配置目标 | Compose/.env 入口 | provisioner 容器变量 |
| --- | --- | --- |
| 应用连接 provisioner | `SANDBOX_PROVIDER`、`SANDBOX_PROVISIONER_URL`、`SANDBOX_PROVISIONER_TOKEN` | 同名变量 |
| 选择承载后端 | `SANDBOX_PROVISIONER_BACKEND` | `PROVISIONER_BACKEND` |
| provisioner 对外代理地址 | `SANDBOX_PROVISIONER_URL` | `PROVISIONER_PUBLIC_URL` |
| Docker/Kubernetes 参数 | `SANDBOX_*` 或对应宿主变量 | `DOCKER_*`、`K8S_*`、`NODE_HOST`、PVC 变量 |

Compose 会把宿主变量映射为右侧的 provisioner 变量。直接运行 provisioner 时，设置右侧变量即可。

## 选择承载后端

应用层当前固定使用：

```bash
SANDBOX_PROVIDER=provisioner
```

provisioner 支持：

| `PROVISIONER_BACKEND` | 用途 | 隔离能力 |
| --- | --- | --- |
| `docker` | 开发和单机部署的默认后端 | 为每个运行时创建独立容器和网络 |
| `kubernetes` | 将沙盒 Pod 交给目标集群承载 | 取决于集群、Pod 和网络安全策略 |
| `memory` | unit 测试和占位测试 | 不创建真实隔离环境 |

生产环境不要使用 `memory`。切换后端只改变动态沙盒的承载位置，API 和 worker 仍通过同一个认证的 provisioner 代理访问。

## 应用层配置

API 和 worker 至少需要：

```bash
SANDBOX_PROVIDER=provisioner
SANDBOX_PROVISIONER_URL=http://sandbox-provisioner:8002
SANDBOX_PROVISIONER_TOKEN=<random-value-at-least-32-characters>
SANDBOX_VIRTUAL_PATH_PREFIX=/home/gem/user-data
SANDBOX_EXEC_TIMEOUT_SECONDS=180
SANDBOX_MAX_OUTPUT_BYTES=262144
```

`SANDBOX_PROVISIONER_TOKEN` 必须至少 32 个字符，并且只提供给 API、worker 和 provisioner。它不能进入 `sandbox.env`、用户 Agent 环境、Skill 或模型上下文。

`SANDBOX_VIRTUAL_PATH_PREFIX` 是 Agent 使用的虚拟用户数据根。API/worker 不直接取得动态容器地址，只使用 provisioner 返回的认证代理 URL。

## provisioner 通用配置

Compose 中的 `sandbox-provisioner` 使用以下变量：

| 变量 | 作用 | Compose 默认值 |
| --- | --- | --- |
| `PROVISIONER_BACKEND` | `docker`、`kubernetes` 或测试用 `memory` | `docker` |
| `PROVISIONER_PUBLIC_URL` | 返回给 API/worker 的代理基地址 | `http://sandbox-provisioner:8002` |
| `SANDBOX_IMAGE` | 动态沙盒镜像 | AIO Sandbox `1.11.0` |
| `SANDBOX_RUNTIME_PROFILE` | 动态沙盒启用的服务规格 | `core` |
| `SANDBOX_CONTAINER_PORT` | 沙盒内部 HTTP 端口 | `8080` |
| `SANDBOX_HEALTH_TIMEOUT_SECONDS` | 创建后的健康检查上限 | `300` |
| `SANDBOX_PROVISIONER_DELETE_TIMEOUT_SECONDS` | API/worker 等待一次 Sandbox 删除响应的上限 | `120` |
| `SANDBOX_DELETE_CONCURRENCY` | provisioner 同时执行的 Docker Sandbox 销毁数 | `32` |
| `SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS` | Docker stop 发出 `SIGTERM` 后等待强制终止的秒数 | `2` |
| `SANDBOX_IDLE_TIMEOUT_SECONDS` | 空闲实例回收时间 | `120` |
| `SANDBOX_IDLE_CHECK_INTERVAL_SECONDS` | idle reaper 扫描间隔 | `10` |
| `SANDBOX_EXEC_TIMEOUT_SECONDS` | 命令超时，也用于计算安全回收下限 | `180` |
| `SANDBOX_MEM_LIMIT` | 单个沙盒容器的内存上限，只接受纯字节数或整数加 `k/m/g` 后缀，不是完整 Docker 内存格式 | `2g` |
| `SANDBOX_CPUS` | 单个沙盒容器的 CPU 上限（核），至少 `1e-9` 核 | `2` |
| `SANDBOX_PIDS_LIMIT` | 单个沙盒容器的进程数上限，必须 ≥ 1 | `512` |

`SANDBOX_MEM_LIMIT`、`SANDBOX_CPUS`、`SANDBOX_PIDS_LIMIT` 只在 `SANDBOX_PROVISIONER_BACKEND=docker` 时生效；非法值在 provisioner 启动时抛错，不回退默认值。

当空闲回收时间小于等于命令超时时，provisioner 会把它提高到“命令超时 + 30 秒”，避免回收正在执行的任务。直接运行 provisioner 且没有 Compose 默认值时，代码默认的 idle timeout 是 600 秒；以实际 `/health` 响应为准。

Docker backend 对同一 Sandbox generation 的创建和删除保持串行，不同 Sandbox 的删除由 `SANDBOX_DELETE_CONCURRENCY` 有界并行。容器收到 `SIGTERM` 后最多等待 `SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS` 秒，仍未退出则由 Docker 强制终止。删除请求可能等待并行槽和容器停止，`SANDBOX_PROVISIONER_DELETE_TIMEOUT_SECONDS` 必须覆盖该等待时间；扩大并行数会增加 Docker daemon、CPU 和文件系统的瞬时压力。

`SANDBOX_RUNTIME_PROFILE` 只接受以下值：

| 规格 | 启用的能力 | 使用场景 |
| --- | --- | --- |
| `core` | Shell、Python、文件操作和 Shell 中的 Node 命令 | 默认 Agent 任务 |
| `browser` | `core` 加浏览器、browser MCP 和 VNC | 需要网页自动化的 Agent |
| `full` | `browser` 加 Jupyter、code-server 和 NodeJS REPL 服务 | 需要完整交互式开发环境的任务 |

修改规格后重新创建 `sandbox-provisioner`；该设置只影响之后创建的动态沙盒。未知值会阻止 provisioner 启动。规格对应的镜像服务开关由部署拥有，Agent 请求和 `sandbox.env` 不能覆盖。

## Docker 后端

Docker 后端需要 provisioner 能访问 Docker daemon，并能看到 API/worker 挂载的两处宿主目录：

| Compose 变量 | provisioner 变量 | 作用 |
| --- | --- | --- |
| `SANDBOX_DOCKER_NETWORK_PREFIX` | `DOCKER_NETWORK_PREFIX` | 每个沙盒独立网络的名称前缀 |
| `SANDBOX_DOCKER_ADDRESS_POOL` | `DOCKER_ADDRESS_POOL` | Sandbox 专用 IPv4 地址池，Compose 默认 `10.253.240.0/20` |
| `SANDBOX_DOCKER_SUBNET_PREFIX` | `DOCKER_SUBNET_PREFIX` | 每个 Sandbox 网络的子网前缀，Compose 默认 `28` |
| `SANDBOX_DOCKER_USER_DATA_HOST_PATH` | `DOCKER_USER_DATA_HOST_PATH` | UserWorkspace 在宿主机上的路径 |
| `SANDBOX_DOCKER_SKILL_PROJECTIONS_HOST_PATH` | `DOCKER_SKILL_PROJECTIONS_HOST_PATH` | Skill 投影在宿主机上的路径 |
| `SANDBOX_DOCKER_SANDBOX_PREFIX` | `DOCKER_SANDBOX_PREFIX` | 动态容器名称前缀 |

Compose 默认把 `/var/run/docker.sock`、UserWorkspace 和 Skill projection 挂载到 provisioner。只有 provisioner 持有 Docker socket；API 和 worker 不直接操作 Docker。

每个动态沙盒只加入自己的 bridge 网络，网络中包含 provisioner 和该沙盒，不加入承载 PostgreSQL、Redis、MinIO、Milvus 或 Neo4j 的 `app-network`，也不向宿主机发布沙盒端口。provisioner 会在复用前检查容器的用户、Workdir、挂载和网络身份，发现不匹配时拒绝复用。

provisioner 会扫描 Docker 已占用网段，从专用地址池中为新 Sandbox 选择不重叠的独立子网；并发分配冲突时重新选择，池耗尽时返回 503。`SANDBOX_DOCKER_SUBNET_PREFIX` 留空时 provisioner 使用 `/28`，最大只能设置为 `/29`，确保网络有足够地址容纳网关、provisioner 和 Sandbox。网络删除后子网可再次使用。部署者必须确认地址池不与宿主机路由、VPN 或其他 Docker 网络重叠，并在冲突时通过 `SANDBOX_DOCKER_ADDRESS_POOL` 覆盖 Compose 默认值。

运行时挂载：

- `/home/gem/user-data`：当前用户 UserWorkspace，读写；
- `/home/gem/skills`：当前用户获授权的共享/内置 Skill 投影，只读；
- `/home/gem/user-data/<workdir_path>`：当前 Project 的工作目录。

`uploads/` 和 `outputs/` 在首次使用时创建。沙盒被 idle reaper 或 Run 终态回收时，UserWorkspace 中的持久文件不会被删除。

## Kubernetes 后端

Kubernetes 后端由 provisioner 创建沙盒 Pod 和 NodePort Service。Compose/宿主变量与容器变量如下：

| Compose/.env 变量 | provisioner 变量 | 作用 |
| --- | --- | --- |
| `SANDBOX_K8S_NAMESPACE` | `K8S_NAMESPACE` | Pod 和 Service 所在 namespace |
| `KUBECONFIG_PATH` | `KUBECONFIG_PATH` | 容器内 kubeconfig；集群内运行可留空 |
| `SANDBOX_NODE_HOST` | `NODE_HOST` | provisioner 访问 NodePort 的节点地址 |
| `USER_DATA_PVC` | `USER_DATA_PVC` | UserWorkspace 共享卷 |
| `SKILLS_PVC` | `SKILLS_PVC` | Skill 只读投影卷 |

User Data PVC 必须提供跨节点部署需要的共享读写能力。Pod 将 `shared/<uid>/workspace` 挂载为 `/home/gem/user-data`，将 `skill-projections/<uid>` 只读挂载为 `/home/gem/skills`。Pod 默认不自动挂载 ServiceAccount token；使用 kubeconfig 或集群内 ServiceAccount 时，都应只授予目标 namespace 所需的 Pod、Service 操作权限。

当前实现使用 NodePort，不使用 Ingress、ClusterIP 或多集群选择器。`NODE_HOST` 只需要从 provisioner 可达，API/worker 不需要直接访问 NodePort；它们仍访问 `PROVISIONER_PUBLIC_URL` 返回的代理地址。

一个 Compose 覆盖示例：

```yaml
services:
  sandbox-provisioner:
    environment:
      PROVISIONER_BACKEND: kubernetes
      K8S_NAMESPACE: yuxi
      KUBECONFIG_PATH: /root/.kube/config
      NODE_HOST: 203.0.113.10
      USER_DATA_PVC: yuxi-user-data
      SKILLS_PVC: yuxi-skills
    volumes:
      - ~/.kube/config:/root/.kube/config:ro
```

在 Kubernetes 内运行 provisioner 时，通常省略 `KUBECONFIG_PATH`，让客户端使用集群内配置。PVC、namespace、NodePort 可达性和 kubeconfig 权限需要由集群运维验证；仓库不提供完整的应用 Deployment 或旧 PVC 自动迁移工具。

## 沙盒内环境变量

动态沙盒环境由两部分合并：

1. provisioner 只读挂载的 `docker/sandbox_provisioner/sandbox.env` 全局变量；
2. 当前用户为 Agent 配置的变量。

开发和生产 Compose 都把该文件挂载到 provisioner 的 `/app/sandbox.env`。仓库当前默认文件只有 `CHECK_YUXI_SANDBOX_ENV_EXISTS=True`；如果需要全局变量，应在部署侧维护该文件并重新创建 provisioner。用户变量覆盖同名全局变量，运行规格的镜像服务开关最后应用且不能被前两者覆盖。全局与用户变量都会对沙盒内代码可见，应按“不可信代码可以读取和外传”处理。只注入任务所需的低权限变量，禁止注入 provisioner token、数据库凭据、对象存储管理凭据和云平台管理员密钥。

远程 Skill 安装使用 `inherit_env=False` 的一次性 Sandbox，不继承全局或用户 Agent 环境，也不挂载持久用户目录。Kubernetes 沙盒默认关闭 ServiceAccount token 自动挂载。

## 开发环境启动和验证

开发 Compose 已默认配置 Docker provisioner。初始化 `.env` 后启动：

```bash
docker compose up -d
```

provisioner 只在第一次文件或命令操作时创建动态沙盒，刚启动时看不到沙盒容器是正常的。先检查 provisioner：

```bash
curl --fail http://localhost:8002/health
docker compose logs --tail=100 sandbox-provisioner
```

健康响应应包含 `backend`、`runtime_profile`、`idle_timeout_seconds` 和 `tracked_sandboxes`。然后用真实线程执行一次文件读写或命令，并分别核对：

- Docker：动态容器、独立网络、UserWorkspace 读写挂载和 Skill 只读挂载；
- Kubernetes：Pod、NodePort Service、PVC 子路径和 `NODE_HOST` 可达性；
- Viewer：通过 Workdir 读取同一个文件，确认它与沙盒看到的是同一份持久字节。

等待超过 idle timeout 后，实例应被回收，但下一次操作可以重新创建，持久文件仍然存在。

## 排障顺序

1. 检查 API/worker 的 `SANDBOX_PROVISIONER_URL` 和 token 是否一致。
2. 检查 `/health` 报告的后端和超时是否符合预期。
3. Docker 后端检查两处 host path 是否为 provisioner 能看到的真实路径；Kubernetes 后端检查 kubeconfig、namespace、PVC 和 NodePort。
4. 查看 provisioner 的创建、复用、健康检查和回收日志。
5. 如果 Viewer 能看到文件但 Agent 不能，核对 Conversation 绑定的 `project_id`、Project 的 `workdir_path`、根 runtime scope、uid 和挂载路径；不要把 Viewer scope `/foo`、Agent 路径或宿主机路径混在一起。

健康接口只能证明 provisioner 进程和后端初始化，不能证明某个沙盒已经创建或文件权限正确。最终结论要通过真实文件、容器/PVC 和 API 响应回读确认。

## 相关入口

- [沙盒机制详解](../mechanisms/sandbox.md)
- [Docker Compose](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
- [生产 Compose](https://github.com/xerrors/Yuxi/blob/main/docker-compose.prod.yml)
- [provisioner 实现](https://github.com/xerrors/Yuxi/blob/main/docker/sandbox_provisioner/app.py)
