# Yuxi 沙盒架构说明

::: tip info
本文档是由 Codex 联合撰写，开发者审阅，尽管已经多次校对，但仍可能存在不准确或过时的描述。如果你发现任何问题，欢迎提交 issue 或 PR 来帮助我们改进文档。
:::

我们在 Yuxi 里引入沙盒，不是为了让架构更“重”，而是因为 Agent 一旦从纯文本对话进入真实执行阶段，就一定会碰到一组很具体的运行时需求：执行命令、读写文件、处理用户上传附件、产出可下载结果，以及在受控目录里保留中间过程文件。如果把这些能力直接放进 API 进程本身，权限边界、租户隔离、环境一致性和后续运维成本都会迅速恶化。

从设计目标上看，沙盒这一层主要解决三件事。第一，给 Agent 一个可写、可执行、可回收的独立运行空间，而不是让它直接操作应用主进程。第二，把模型可见文件系统整理成稳定的命名空间，例如 `/home/gem/user-data` 和 `/home/gem/skills`，这样 prompt、工具、viewer 和 artifact 下载接口可以共享同一套路径语义。第三，让这套能力既能在本地 Docker 开发环境里稳定工作，也能在需要时切到 Kubernetes 这类更适合多实例部署的承载方式。

这份文档说明当前项目中“沙盒”这一层到底是什么、为什么同时会看到 Docker 和 Kubernetes、默认开发环境实际启用的是哪一种模式，以及沙盒如何和 `skills`、附件、工作区文件系统组合在一起工作。内容以当前仓库实现为准，我们重点解释真实调用链、配置入口、路径语义和运维边界，而不是抽象地介绍容器技术。

## 一、先说明白：Docker 和 K8s 在这里是什么关系

Docker 和 Kubernetes 不是互斥关系。Docker 解决的是“把一个进程放进容器里运行”这个问题，Kubernetes 解决的是“如何在一组机器上批量调度、暴露、重建和管理这些容器”这个问题。可以把 Docker 理解成容器运行时和镜像分发方式，把 Kubernetes 理解成容器编排平台。

放到 Yuxi 里，这个关系更具体一些。Yuxi 本身并不直接决定“沙盒一定跑在 Docker 还是一定跑在 K8s 上”，它只要求后端拿到一个可访问的沙盒地址，然后通过 `agent-sandbox` 的 HTTP API 去执行命令、读写文件。真正负责创建和回收沙盒实例的是 `sandbox-provisioner` 这个单独的服务。也就是说，Yuxi 的应用层只依赖 “provisioner”，而 provisioner 的后端可以选择用本机 Docker 去起容器，也可以选择向 Kubernetes 集群创建 Pod 和 Service。

所以项目里看到的概念其实分成两层。第一层是应用层的 `SANDBOX_PROVIDER`，当前代码只支持 `provisioner`。第二层是 provisioner 内部的 `SANDBOX_PROVISIONER_BACKEND`，它决定具体用哪种底层实现去创建沙盒。当前真正应该对外理解和配置的是 `docker`、`kubernetes`，测试或占位场景可以使用 `memory`。

## 二、当前项目的真实沙盒调用链

当前仓库里，后端只支持 `SANDBOX_PROVIDER=provisioner`。当某个对话线程第一次需要执行文件操作或命令执行时，后端会基于文件线程与 skills 线程生成稳定的 `sandbox_id`，然后请求 `sandbox-provisioner` 创建或复用对应沙盒；普通 Agent 的文件线程和 skills 线程都回退为当前 `thread_id`。应用层拿到返回的 `sandbox_url` 之后，才会真正通过 `agent-sandbox` 客户端去调用远程沙盒的文件 API 和 shell API。

调用链可以概括为：Web/API 请求进入 Yuxi 后端，后端构造 `ProvisionerSandboxBackend`，再经由 `ProvisionerClient` 调用 `sandbox-provisioner` 的 `/api/sandboxes` 接口。`sandbox-provisioner` 根据 `SANDBOX_PROVISIONER_BACKEND` 选择内存占位实现、Docker 容器实现或 Kubernetes 实现。沙盒真正启动后，对外暴露一个 HTTP 地址，Yuxi 再使用这个地址完成执行命令、上传文件、下载文件、目录遍历等操作。

当前仓库的默认配置和默认开发环境都应该理解为 `docker`。正常情况下运行中的 provisioner 健康检查应返回 `backend=docker`。这意味着我们用 `docker compose up -d` 启动项目时，应用并不是直接把代码跑在宿主机上，而是通过 `sandbox-provisioner` 再去用 Docker 启一个真正的沙盒容器。

## 三、`memory`、`docker`、`kubernetes` 分别是什么

当前实现里，`memory`、`docker`、`kubernetes` 是三种需要区分的语义。

`memory` 是一个纯内存登记实现。它不会真正创建容器，也不会提供真实隔离，主要适合测试或极轻量的占位场景。它只是记录一个 `sandbox_id -> sandbox_url` 的映射，因此不能把它理解成生产可用的沙盒。

`docker` 是当前默认也是推荐的本机容器后端。`sandbox-provisioner` 会使用 `LocalContainerProvisionerBackend` 通过宿主机 Docker daemon 动态创建沙盒容器。

`kubernetes` 则是另一条实现路径。它不会再去调用本机 Docker 起容器，而是使用 Kubernetes API 在指定 namespace 中创建一个 Pod 和一个 NodePort Service，然后把这个 Service 对应的可访问地址回传给 Yuxi 后端。

因此，如果在界面、文档或者环境变量里看到 “docker / k8s” 这几个词，最准确的理解应该是：Yuxi 的应用层只有一种 provider，也就是 `provisioner`；provisioner 下面有多种 backend；其中 `docker` 是默认的本机 Docker 后端，`kubernetes` 是另一种远程集群后端。

## 四、默认开发模式到底是什么

默认开发模式是 Docker Compose 启动整个项目，再由 `sandbox-provisioner` 按 `docker` 后端去创建沙盒容器。也就是说，项目本身跑在 Compose 里，沙盒也跑在 Docker 里，只不过沙盒不是 Compose 静态声明的长期服务，而是 provisioner 按需动态拉起和回收的短生命周期容器。

这也是为什么在 `docker-compose.yml` 中既能看到 `api`、`worker`、`sandbox-provisioner` 这样的常驻服务，又能看到 `sandbox-provisioner` 挂载了 `/var/run/docker.sock`。这不是重复设计，而是为了让 provisioner 有能力继续调用宿主机 Docker daemon 去创建新的“每线程沙盒容器”。

换句话说，当前项目不存在单独的 “纯宿主机 local 模式”。本机开发和单机部署应显式使用 `docker` 后端。

这里还需要把 Compose 里的环境变量分两层看。`api` 和 `worker` 关注的是应用层变量，例如 `SANDBOX_PROVIDER`、`SANDBOX_PROVISIONER_URL`、`SANDBOX_PROVISIONER_TOKEN`、`SANDBOX_VIRTUAL_PATH_PREFIX`、`SANDBOX_EXEC_TIMEOUT_SECONDS`、`SANDBOX_MAX_OUTPUT_BYTES`。`sandbox-provisioner` 自己则有另一组变量，负责决定具体如何创建沙盒实例。两层不要混看，否则很容易误以为改了 API 环境变量就能切换底层承载方式。

## 五、Docker 本机后端是如何工作的

当 `SANDBOX_PROVISIONER_BACKEND=docker` 时，`sandbox-provisioner` 会进入 `LocalContainerProvisionerBackend`。它会检查 Docker 是否可用，解析自身容器里 `/app/saves` 这个挂载点在宿主机上的真实路径，并据此推导出线程数据目录。随后它为每组文件线程与 skills 线程准备一个稳定的 `sandbox_id`，把容器命名为类似 `yuxi-sandbox-<id>` 的形式，并在 Docker 网络中启动真正的沙盒镜像。

这个沙盒镜像默认来自 `SANDBOX_IMAGE`，容器内部监听的端口默认是 `8080`。provisioner 会为每个动态沙盒创建独立的 Docker bridge 网络，只把 provisioner 和该沙盒接入其中；沙盒之间不能互访，也不能访问承载 PostgreSQL、Redis、Neo4j、MinIO 等服务的 `app-network`。沙盒端口不发布到宿主机，provisioner 通过对应的独立网络访问真实容器，再以需要 Bearer token 的代理地址向 API/worker 提供文件和命令接口。API/worker 不直接持有沙盒容器地址。

这个拓扑把沙箱按“其中代码可能被完全控制”处理。`SANDBOX_PROVISIONER_TOKEN` 只配置给 API、worker 和 provisioner，绝不能写进 `sandbox.env` 或用户级 Agent 环境变量，否则沙箱会重新获得 provisioner 管理权限。

Docker 后端在启动沙盒时，会挂载三类关键目录。第一类是用户级 workspace，挂载到容器内的 `/home/gem/user-data/workspace`。第二类是文件线程级 uploads/outputs，分别挂载到 `/home/gem/user-data/uploads` 和 `/home/gem/user-data/outputs`。第三类是 skills 线程可见的 skills 目录，挂载到 `/home/gem/skills`，而且是只读挂载。除此之外，容器的 `/home/gem` 本身还会额外挂一个 `tmpfs`，原因是当前沙盒镜像启动时要求 `/home/gem` 可写，但 Yuxi 希望真正持久化的只有 `user-data` 下面的内容。

为了避免长期空闲的沙盒一直占资源，provisioner 还带了一个 idle reaper。它会记录每个沙盒最近一次被 touch 的时间，超过 `SANDBOX_IDLE_TIMEOUT_SECONDS` 之后自动删除。当前默认空闲超时是 120 秒，但如果这个值小于命令执行超时，系统会自动把它提高到“命令超时 + 30 秒”，以免执行中的任务被误回收。

对应到 `docker-compose.yml` 和 `docker-compose.prod.yml`，当前 `sandbox-provisioner` 实际会读取的 Docker 后端相关变量主要是这些：

- 通用变量：`PROVISIONER_BACKEND`、`SANDBOX_IMAGE`、`SANDBOX_CONTAINER_PORT`、`SANDBOX_HEALTH_TIMEOUT_SECONDS`、`SANDBOX_IDLE_TIMEOUT_SECONDS`、`SANDBOX_IDLE_CHECK_INTERVAL_SECONDS`、`SANDBOX_EXEC_TIMEOUT_SECONDS`、`MEMORY_SANDBOX_URL_TEMPLATE`
- Docker 后端变量：`DOCKER_NETWORK_PREFIX`、`DOCKER_THREADS_HOST_PATH`、`DOCKER_SANDBOX_PREFIX`
- 容器代理变量：`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`

`DOCKER_NETWORK_PREFIX` 用于生成每个沙盒的独立网络名称。`DOCKER_THREADS_HOST_PATH` 也是 Docker 后端专用；如果不显式传入，provisioner 会尝试根据自身容器挂载反推出宿主机路径。

## 六、Kubernetes 后端是如何工作的

当 `SANDBOX_PROVISIONER_BACKEND=kubernetes` 时，`sandbox-provisioner` 会改用 Kubernetes Python 客户端。它会先加载 kubeconfig 或集群内配置，然后在指定的 namespace 中创建一个沙盒 Pod，再创建一个同名的 NodePort Service，把这个 Service 的 `nodePort` 暴露给 Yuxi 后端使用。

Kubernetes 后端下，沙盒还是同一套镜像，还是暴露同样的 HTTP API，但存储方式和暴露方式变了。它不会依赖宿主机 Docker bind mount，而是要求有一个可写的 PVC。当前实现里真正使用的是 `THREAD_PVC`，Pod 会把这块共享存储挂到 `/mnt/shared-data`，然后用 `subPath` 的方式把 `threads/shared/<uid>/workspace` 挂到 `/home/gem/user-data/workspace`，把 `threads/<file_thread_id>/user-data/uploads` 与 `threads/<file_thread_id>/user-data/outputs` 分别挂到 uploads/outputs，把 `threads/<skills_thread_id>/skills` 挂到 `/home/gem/skills`。这样做的好处是目录结构仍然可以和 Docker 模式保持一致，同时允许子智能体共享父对话文件但隔离 skills。

需要特别说明的是，代码里虽然读取了 `SKILLS_PVC` 这个环境变量，但当前 Pod 规格实际没有使用单独的 skills PVC，而是统一从 `THREAD_PVC` 中切 `threads/<thread_id>/skills` 这个子路径。因此，如果看到环境变量里同时出现 `SKILLS_PVC` 和 `THREAD_PVC`，应当以 `THREAD_PVC` 的真实挂载语义为准，`SKILLS_PVC` 目前更像一个预留字段。

Kubernetes 后端还需要一个 `NODE_HOST`。这是因为当前实现使用的是 NodePort Service，而不是 Ingress，也不是 ClusterIP。provisioner 创建完 Service 后会通过 `http://<NODE_HOST>:<nodePort>` 访问目标沙箱，但返回给 Yuxi 后端的仍是 provisioner 认证代理地址。所以 `NODE_HOST` 必须从 provisioner 可达，不需要直接暴露给 API/worker。

当前 Compose 中与 Kubernetes 后端对应的变量主要是：

- `K8S_NAMESPACE`
- `KUBECONFIG_PATH`
- `NODE_HOST`
- `THREAD_PVC`
- `SKILLS_PVC`

其中真正决定运行时挂载的是 `THREAD_PVC`。`SKILLS_PVC` 目前只保留为代码层读取字段，并没有进入实际 Pod 挂载。

## 七、如果要使用“远程 K8s”，应该怎么接

这里最容易误解的一点是，所谓“选择远程 K8s”，并不是在 Yuxi 页面里点一个开关，然后系统自动发现一个集群。当前实现没有内建集群选择器，也没有多集群管理界面。它的工作方式很直接：我们把 `sandbox-provisioner` 配置成 `kubernetes` 后端，并让它能拿到目标集群的 kubeconfig 或者运行在集群内即可。对 provisioner 来说，只要 Kubernetes 客户端能连上 API Server，这个集群就是它要操作的“远程 K8s”。

如果 Yuxi 部署在 Docker Compose 里，而 Kubernetes 集群在另一台机器或云厂商托管环境中，那么最常见的做法是把本地 kubeconfig 文件挂载进 `sandbox-provisioner` 容器，然后设置 `KUBECONFIG_PATH`。同时把 `SANDBOX_NODE_HOST` 改成一个从 `api` 容器也能访问的节点公网 IP、负载均衡域名，或者已经做过反向代理的地址。

一个典型的 Compose 覆盖配置会长这样：

```yaml
services:
  sandbox-provisioner:
    environment:
      - PROVISIONER_BACKEND=kubernetes
      - K8S_NAMESPACE=yuxi-know
      - KUBECONFIG_PATH=/root/.kube/config
      - THREAD_PVC=yuxi-thread
      - SKILLS_PVC=yuxi-skills
      - NODE_HOST=203.0.113.10
    volumes:
      - ~/.kube/config:/root/.kube/config:ro
```

这段配置表达的意思不是“把整个应用迁到 K8s”，而是“仍然用 Compose 跑 Yuxi 主服务，但沙盒实例改为由远程 Kubernetes 集群承载”。这是当前代码最自然的混合部署方式。

如果 `sandbox-provisioner` 本身就运行在 Kubernetes 集群内部，那么通常不需要显式提供 `KUBECONFIG_PATH`。它会优先尝试 `incluster_config`，也就是使用 Pod 的服务账号权限直接访问 Kubernetes API。此时更需要关注的是 namespace、PVC 和 NodePort 的可达性，而不是 kubeconfig 文件本身。

## 八、当前项目的沙盒文件系统是如何设计的

从模型和工具调用的视角看，Yuxi 主要向 Agent 暴露两类路径：`/home/gem/user-data` 和 `/home/gem/skills`。其中 `user-data` 是可写的用户工作区，`skills` 是只读的技能目录。知识库不再映射为沙盒文件系统路径，模型应通过知识库工具检索和打开文档。

在宿主机侧，和线程相关的数据主要放在 `saves` 目录下。当前可读的目录结构可以概括为下面这样：

```text
saves/
├── skills/
│   ├── <skill-slug>/
│   └── ...
├── threads/
│   ├── <thread_id>/
│   │   ├── user-data/
│   │   │   ├── uploads/
│   │   │   ├── outputs/
│   │   │   └── ...
│   │   └── skills/
│   │       ├── <skill-slug>/
│   │       └── ...
│   ├── shared/
│   │   └── <uid>/
│   │       └── workspace/
│   └── ...
```

这里要重点理解 `workspace` 和 `uploads/outputs` 的区别。按照当前宿主机路径解析逻辑，`workspace` 被定义为用户级共享目录，位置是 `saves/threads/shared/<uid>/workspace`；而 `uploads` 和 `outputs` 属于文件线程目录，位置分别是 `saves/threads/<file_thread_id>/user-data/uploads` 和 `saves/threads/<file_thread_id>/user-data/outputs`。普通 Agent 的 `file_thread_id` 就是当前对话 `thread_id`，子智能体运行时则使用父对话作为 `file_thread_id`，因此可以读取父对话附件并把产物写回父对话 outputs。

与此同时，运行时 provisioner 在创建 Docker 容器或 Kubernetes Pod 时，会把用户级 `saves/threads/shared/<uid>/workspace` 单独挂到 `/home/gem/user-data/workspace`，再把文件线程的 `uploads/outputs` 分别挂到 `/home/gem/user-data/uploads` 和 `/home/gem/user-data/outputs`。因此在排查文件问题时，需要先明确一个前提：当前项目里同时存在“宿主机侧目录组织”和“容器内统一虚拟路径”两层概念。对外接口和 viewer 语义与底层挂载实现现在是一致的，workspace 是用户共享空间，而 uploads/outputs 跟随文件线程隔离或共享。

## 九、路径暴露规则是什么

Yuxi 不会把整个容器文件系统都开放给 Agent 或 viewer。当前 viewer 根目录只会列出几个命名空间入口，而不会直接暴露 `/` 的真实文件树。这样做是为了避免只看文件树就触发沙盒冷启动，也为了让权限边界更稳定。

`/home/gem/user-data` 是主要工作区。它允许模型和工具写入，但推荐语义并不相同。内置 prompt 中已经明确说明，`workspace` 应当放中间文件，`outputs` 应当放最终产物，`uploads` 是用户上传文件的位置。对于普通对话 Agent，文案甚至提示“非必要不要写 workspace，而优先写 outputs”。

`/home/gem/skills` 是只读目录。它不是简单地把 `saves/skills` 整个暴露进去，而是先根据当前运行时的 `_readable_skills`，把这些技能从全局 skills 根目录同步复制到 `saves/threads/<skills_thread_id>/skills`，再把这个 skills 线程目录只读挂进沙盒。这样做的结果是，不同主/子 Agent 看到的 skill 集可能不同，而且模型永远不能在运行时修改 skills 内容。

知识库访问不属于沙盒文件系统暴露规则。当前 Agent 可见知识库仍由用户权限和 Agent 配置共同决定，但只通过 `query_kb`、`open_kb_document` 等工具访问，不提供沙盒目录投影。

## 十、skills、知识库、附件是怎么和沙盒结合的

skills 的结合方式分成两层。第一层是提示词层，`prepare_agent_runtime_context` 会先根据当前 Agent 配置的 `context.skills` 展开依赖闭包，`SkillsMiddleware` 再把 `_prompt_skills` 注入到系统提示里，让模型知道哪些 skill 存在、它们的入口文件一般在 `/home/gem/skills/<slug>/SKILL.md`。第二层是文件系统层，运行时会调用 `sync_thread_readable_skills`，把 `_readable_skills` 对应的 skill 目录复制到当前 `skills_thread_id` 的 `saves/threads/<skills_thread_id>/skills` 下，再由沙盒只读挂载到 `/home/gem/skills`。也就是说，skill 既是 prompt 中的能力说明，也是文件系统中的只读知识目录。

附件的结合方式更偏向“先落盘，再把路径告诉模型”。用户上传文件后，系统会先把原始文件写入 `saves/threads/<file_thread_id>/user-data/uploads`。如果该文件可以被解析，系统还会额外生成一个 Markdown 副本，写到 `saves/threads/<file_thread_id>/user-data/uploads/attachments/<name>.md`。普通 Agent 的文件线程就是当前对话线程；子智能体沿用父对话文件线程，所以能访问父对话附件。随后，LangGraph state 中会维护一份 `uploads` 列表，`AttachmentMiddleware` 会把这些可读路径注入系统提示，告诉模型优先用 `read_file` 去读取这些路径。因此，附件并不是“作为消息大段内联塞给模型”，而是被转换成沙盒文件系统中的路径对象。

知识库不再与沙盒文件系统结合。它不会被复制到每个线程目录，也不会生成虚拟目录；模型通过专门的知识库工具检索，并在需要更完整上下文时用 `open_kb_document` 按 `kb_id` 和 `file_id` 打开文档内容。

## 十一、当前推荐如何使用 Docker 沙盒

如果只是正常开发、调试或单机部署，最简单也是当前默认的方式就是保留 `SANDBOX_PROVIDER=provisioner`，同时把 `SANDBOX_PROVISIONER_BACKEND` 设为 `docker`。这会让整个项目继续由 Docker Compose 管理，而沙盒实例由 provisioner 动态创建。通常不需要手工 `docker run` 沙盒镜像，也不需要在 Compose 文件里静态声明每一个沙盒容器。

最小必要配置通常就是下面这几项：

```env
SANDBOX_PROVIDER=provisioner
SANDBOX_PROVISIONER_URL=http://sandbox-provisioner:8002
SANDBOX_PROVISIONER_TOKEN=<至少 32 个随机字符>
SANDBOX_PROVISIONER_BACKEND=docker
SANDBOX_VIRTUAL_PATH_PREFIX=/home/gem/user-data
SANDBOX_DOCKER_NETWORK_PREFIX=yuxi-know-sandbox
```

然后用常规方式启动即可：

```bash
docker compose up -d
curl http://localhost:8002/health
```

如果健康检查返回 `backend: docker`，就说明 provisioner 已经处于默认的 Docker 本机后端。真正的沙盒容器不会在系统启动时立即全部出现，而是在你第一次创建线程并触发需要文件系统或命令执行的操作后才会被创建。

升级已有开发环境时可以重新运行初始化脚本，让脚本补生成 `SANDBOX_PROVISIONER_TOKEN`。历史共享沙盒网络中的容器会在下次发现时被重建到各自独立的网络。

## 十二、如何理解文件管理与暴露边界

从产品行为上看，viewer 文件系统和 artifact 下载接口优先走的是宿主机路径解析，而不是无条件透传到沙盒容器内部。这么设计有两个直接收益。第一，浏览 `/` 或 `/home/gem/user-data` 这样的树形入口时，不需要为了只读查看而冷启动沙盒。第二，权限边界更好做，因为 `resolve_virtual_path` 会把用户可见路径严格限制在预定义的 `user-data` 和 `skills` 命名空间内。

从工程上看，当前实现更像“双层文件系统”。对 Agent 执行来说，真正工作的对象是远程沙盒进程暴露的文件 API；对 viewer、附件下载和一部分 artifact 查看来说，系统会优先在宿主机侧解析虚拟路径，再用本地文件读取或只读 backend 下载内容。这也是为什么你会看到既有 `ProvisionerSandboxBackend`，又有 `viewer_filesystem_service`、`SelectedSkillsReadonlyBackend` 这样的配套实现。

## 十三、环境变量配置与传递链

sandbox-provisioner 的环境变量传递分**两层**，需要分别理解：

### 第一层：应用层 → sandbox-provisioner

`api` 和 `worker` 服务通过 `SANDBOX_*` 前缀的环境变量告诉后端如何连接 provisioner。这些变量定义在 `docker-compose.yml` 的 `x-api-worker-env` 锚点中：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SANDBOX_PROVIDER` | 提供者类型，固定为 `provisioner` | `provisioner` |
| `SANDBOX_PROVISIONER_URL` | provisioner 服务地址 | `http://sandbox-provisioner:8002` |
| `SANDBOX_PROVISIONER_TOKEN` | provisioner 管理与代理接口 Bearer token，至少 32 个字符 | 无，必填 |
| `SANDBOX_VIRTUAL_PATH_PREFIX` | 虚拟路径前缀 | `/home/gem/user-data` |
| `SANDBOX_EXEC_TIMEOUT_SECONDS` | 命令执行超时时间 | `180` |
| `SANDBOX_MAX_OUTPUT_BYTES` | 最大输出字节数 | `262144` |

### 第二层：sandbox-provisioner 内部配置

`sandbox-provisioner` 服务本身读取另一组环境变量，决定如何创建沙盒容器。这些变量直接写在 `docker-compose.yml` 的 `sandbox-provisioner.environment` 中：

**通用配置：**

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `PROVISIONER_BACKEND` | 底层后端类型，`docker` 或 `kubernetes` | `docker` |
| `SANDBOX_IMAGE` | 沙盒容器镜像 | 详见 compose 文件 |
| `SANDBOX_CONTAINER_PORT` | 沙盒容器内部端口 | `8080` |
| `SANDBOX_IDLE_TIMEOUT_SECONDS` | 空闲回收时间 | `120` |
| `SANDBOX_HEALTH_TIMEOUT_SECONDS` | 健康检查超时 | `300` |

**Docker 后端专用：**

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DOCKER_NETWORK_PREFIX` | 每沙盒独立网络的名称前缀 | `yuxi-know-sandbox` |
| `DOCKER_SANDBOX_PREFIX` | 沙盒容器名前缀 | `yuxi-sandbox` |
| `DOCKER_THREADS_HOST_PATH` | 线程数据宿主机路径 | 自动推断 |

**Kubernetes 后端专用：**

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `K8S_NAMESPACE` | Kubernetes namespace | `yuxi-know` |
| `NODE_HOST` | Kubernetes 节点地址 | `host.docker.internal` |
| `KUBECONFIG_PATH` | kubeconfig 文件路径 | 空（使用 incluster 配置） |
| `THREAD_PVC` | 线程数据持久化卷 | `yuxi-thread` |
| `SKILLS_PVC` | 技能目录持久化卷（预留） | `yuxi-skills` |

### 环境变量传递链

```
宿主机 .env / 系统环境变量
         ↓
    docker-compose.yml
         ↓
    ┌────────────────────────────────┐
    │  api/worker 服务               │  应用层变量 (SANDBOX_*)
    │    SANDBOX_PROVISIONER_URL     │
    │    SANDBOX_PROVISIONER_TOKEN   │
    └────────────┬───────────────────┘
                 ↓  带 Bearer token 的 HTTP 调用
    ┌────────────────────────────────┐
    │  sandbox-provisioner 服务       │  沙盒层变量 (PROVISIONER_BACKEND, DOCKER_*, K8S_*)
    │    PROVISIONER_BACKEND         │
    └────────────┬───────────────────┘
                 ↓  Docker API / K8s API + 认证 HTTP 代理
    ┌────────────────────────────────┐
    │  动态创建的沙盒容器              │
    └────────────────────────────────┘
```

两层变量不要混看。改了 `api/worker` 的 `SANDBOX_PROVISIONER_URL` 只是改了后端找 provisioner 的地址；改了 `sandbox-provisioner` 的 `PROVISIONER_BACKEND` 才是改了 provisioner 本身用什么方式创建沙盒。

### sandbox.env 的特殊作用

`docker/sandbox_provisioner/sandbox.env` 文件的用途与上述两层变量不同。它通过 volume 挂载到 provisioner 容器内 (`/app/sandbox.env`)，然后由 `LocalContainerProvisionerBackend` 在创建沙盒容器时读取，解析后的键值对会作为**环境变量注入到每个动态创建的沙盒容器**中。

```yaml
# docker-compose.yml 中 sandbox-provisioner 的挂载
sandbox-provisioner:
  volumes:
    - ./docker/sandbox_provisioner/sandbox.env:/app/sandbox.env:ro
```

也就是说，`sandbox.env` 配置的是沙盒容器内部可见的环境变量，而不是 provisioner 本身的配置。当前该文件内容为：

```env
CHECK_YUXI_SANDBOX_ENV_EXISTS=True
```

如果需要给所有沙盒容器注入额外的环境变量（如代理配置、认证信息等），可以添加到 `sandbox.env` 文件中。

### 配置方式汇总

| 配置目标 | 配置位置 | 示例变量 |
|----------|----------|----------|
| 应用层连接 provisioner | `.env` 或 compose 环境 | `SANDBOX_PROVISIONER_URL`, `SANDBOX_PROVISIONER_TOKEN` |
| provisioner 自身行为 | `.env` 或 compose 环境 | `PROVISIONER_BACKEND`, `DOCKER_*` |
| 沙盒容器内部环境 | `sandbox.env` 文件 | 代理、认证等运行时变量 |

## 十四、和旧版文档相比，今天最重要的理解方式

当前项目不应再按“应用直接管理一个长期存在的本地 sandbox 服务”去理解。更准确的认识应该是：Yuxi 只管理线程和上下文；provisioner 负责创建线程对应的沙盒实例；文件系统不是简单地暴露一个容器根目录，而是把可写工作区、只读 skills 等组合成一个受控命名空间（知识库不再映射为沙盒目录，改由 `query_kb`/`open_kb_document` 等工具访问）。

因此，当你在界面上“启用沙盒”或者在文档里“选择 K8s”时，本质上做的不是切换一段业务逻辑，而是在切换 provisioner 的底层实例承载方式。选择 `docker` 时，沙盒由当前部署机上的 Docker daemon 动态创建；选择 `kubernetes` 时，沙盒由目标 K8s 集群动态创建。Yuxi 自己始终只面对一个 provisioner 服务地址。

## 十五、排障时建议先看什么

如果怀疑是 provisioner 级问题，先看 `http://localhost:8002/health`，确认 backend 类型和 idle timeout 是否符合预期。默认 Docker 部署下这里应看到 `backend=docker`。接着看 `docker logs sandbox-provisioner --tail 200`，因为这里能直接看到创建容器、复用旧实例、健康检查失败和 idle reaper 删除的日志。

如果怀疑是 Docker 地址不可达，先确认每个动态沙箱只连接自己的 `yuxi-know-sandbox-<id>` 网络，provisioner 同时连接该网络，而 API/worker 只在 `app-network`。provisioner 日志中的目标地址应是动态容器名，API/worker 拿到的地址应是 `/api/sandboxes/<id>/proxy`；代理请求必须携带 `SANDBOX_PROVISIONER_TOKEN`。如果怀疑是 Kubernetes 地址不可达，重点检查 `NODE_HOST` 和 NodePort 是否从 provisioner 可达。

如果怀疑是文件看得到但模型读不到，或者模型写了但 viewer 看不到，优先把问题拆成两层：一层是宿主机路径是否存在于 `saves/...` 下，另一层是该路径是否真的被当前线程沙盒挂载并暴露到了 `/home/gem/user-data` 或 `/home/gem/skills`。只要先分清“宿主机侧文件语义”和“沙盒侧运行时挂载语义”，定位问题通常会快很多。
