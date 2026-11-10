<script setup>
import { ref } from 'vue'
import SandboxSetup from '../.vitepress/theme/components/SandboxSetup.vue'

const sandbox = ref('container')
</script>

# 快速开始

准备好 Git、Docker 和 Docker Compose v2，按下面三步启动 Yuxi：下载代码、选择沙盒、启动服务并验证。本文使用仓库根目录的开发 Compose；正式上线请参考[生产部署](../advanced/deployment.md)。

## 1. 下载代码

本文固定使用 `v0.8.0`。

```bash
git clone --branch v0.8.0 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi
```

后续命令都在这个目录执行。

::: warning 使用全新数据目录
0.8 不会自动升级旧数据库与索引。首次部署请使用独立数据目录，保留旧环境；版本差异见[0.8.0 更新说明](../develop-guides/releases/0.8.0.md)。
:::

## 2. 选择沙盒并初始化

沙盒用于智能体执行命令。选择下面一种方式，完成对应初始化即可。

| 方式 | 适用场景 | 环境要求 |
| --- | --- | --- |
| **Container（默认）** | 本地试用、单租户可信代码，需要基本命令执行 | Docker，无需 KVM 或额外执行节点 |
| **Cube** | 需要 VM 隔离和持久用户环境 | 已准备好的 Linux/KVM 执行节点 |

Container 与宿主共享内核，不适合不可信多租户代码。后端由配置显式选择，Cube 不可用时不会自动切换。

初始化脚本会提示填写 `SILICONFLOW_API_KEY`，生成应用安全密钥和实例标识，并准备基础镜像。其他模型供应商可在登录后配置。已有 `.env` 时直接编辑并保留密钥，不要用模板覆盖。

::: info Windows 用户
Windows 下的 Docker Desktop 不支持启动 Cube。可以选择 Container；如果需要 Cube，必须另备 Linux/KVM 执行节点，Windows 只运行 Yuxi 应用并连接该节点。
:::

<SandboxSetup v-model="sandbox">
<template #container>

**初始化 Container**

按操作系统选择初始化命令：

::: code-group

```bash [Linux / macOS]
./scripts/init.sh
```

```powershell [Windows PowerShell]
.\scripts\init.ps1
```

:::

两种初始化脚本都默认选择 Container 并自动生成 `SANDBOX_RUNNER_TOKEN`。已有 `.env` 会保留原后端；如需切换，先将 `SANDBOX_BACKEND` 改为 `container` 再初始化。Docker Desktop 使用 Linux 容器模式，并允许项目目录共享。

初始化完成后，直接进入步骤 3。沙盒镜像会在启动时构建，不需要填写 Cube 配置。容器默认禁止联网，每个执行段（Run）使用独立临时环境；工作目录文件保留，Home 和临时安装的软件随容器清理。更多限制见[Container 沙盒](../advanced/container-sandbox.md)。

</template>
<template #cube>

**先启动 Cube 执行节点**

在 Linux/KVM 节点的仓库目录执行。安装会将 SELinux 设为 permissive，请使用专用节点：

```bash
test -e /dev/kvm && echo "KVM available"
sudo bash docker/cube/setup-node.sh
sudo cp docker/cube/Dockerfile.user docker/cube/build-template.sh /opt/yuxi-cube/
sudo bash /opt/yuxi-cube/build-template.sh
curl --fail http://127.0.0.1:3000/health
```

脚本安装并启动 Cube v0.7.3，构建 `yuxi-user` 模板。应用主机与执行节点可以同机，也可以分开部署。

::: details Cube 节点准备完成的条件

- 执行节点具备 `/dev/kvm`，Cube 控制面和数据代理可从应用主机的私网访问。
- 已构建 `yuxi-user` 模板。
- 应用主机的 `YUXI_STATE_DIR/yuxi/threads` 和 `YUXI_STATE_DIR/yuxi/skill-projections` 已共享到节点的对应目录；两端看到相同文件。`YUXI_STATE_DIR` 默认是 `./docker/volumes`，只填写配置不会创建共享挂载。

节点安装、共享挂载和验证要求由[沙盒配置与运维](../advanced/sandbox-operations.md)说明。同机嵌套开发使用其中的[嵌套节点方案](../advanced/sandbox-operations.md#当前机器的嵌套开发节点)，控制端口和代理端口需按该方案填写。

:::

**再初始化 Yuxi 应用**

回到应用主机的仓库目录，先复制配置模板：

::: code-group

```bash [Linux / macOS]
cp .env.template .env
chmod 600 .env
```

```powershell [Windows PowerShell]
Copy-Item .env.template .env
```

:::

编辑 `.env`，按 Cube 所在位置选择配置：

::: code-group

```dotenv [同一台主机]
SANDBOX_BACKEND=cube
CUBE_API_URL=http://host.docker.internal:3000
# 未启用认证时留空
CUBE_API_KEY=
CUBE_PROXY_NODE_IP=host.docker.internal
```

```dotenv [独立节点]
SANDBOX_BACKEND=cube
CUBE_API_URL=http://<Cube节点的内网IP>:3000
# 未启用认证时留空
CUBE_API_KEY=
CUBE_PROXY_NODE_IP=<Cube节点的内网IP>
```

:::

保存后运行初始化：

::: code-group

```bash [Linux / macOS]
./scripts/init.sh
```

```powershell [Windows PowerShell]
.\scripts\init.ps1
```

:::

</template>
</SandboxSetup>

## 3. 启动并验证

下面的命令跟随步骤 2 的沙盒选择。

<div v-show="sandbox === 'container'">

**Container 启动命令**

```bash
docker compose --profile container config --quiet
docker compose --profile container up --build -d
```

Container 的启动命令会构建沙盒镜像并启动 runner；`SANDBOX_BACKEND=container` 和 `--profile container` 缺一不可。

</div>
<div v-show="sandbox === 'cube'">

**Cube 启动命令**

```bash
docker compose config --quiet
docker compose up --build -d
```

此命令启动 Yuxi 应用服务；Cube 节点需要先完成步骤 2 的启动和配置。

</div>

首次启动需要下载镜像并构建应用。查看服务状态，等待就绪接口返回 `status: ready`：

```bash
docker compose ps
curl --fail http://localhost:5050/api/system/ready
```

打开 **<http://localhost:5173>**，按提示创建超级管理员账号。在「智能体 → 模型供应商」配置模型，然后选择已启用命令执行工具的智能体，发送：

> 请执行 `pwd`，并在当前工作目录创建 `hello.txt`，内容为 `hello Yuxi`。

如出现审批，批准后继续。确认命令执行成功，并在工作区打开 `hello.txt` 核对内容，至此完成部署验证。就绪接口只证明服务可以接流量，不能代替这一步。

## 常见配置

以下配置保存在 `.env`。首次启动前按需调整；模型、Skills 和用户设置可在登录后配置。

| 需要调整什么 | 配置或入口 |
| --- | --- |
| 模型供应商 | 初始化脚本要求 `SILICONFLOW_API_KEY`；其他供应商见[模型配置](./model-config.md) |
| Web 端口 | `YUXI_FRONTEND_PORT`，默认 `5173` |
| 数据目录 | `YUXI_STATE_DIR`，默认 `./docker/volumes`；更换目录不会自动迁移已有数据 |
| 应用安全密钥 | `JWT_SECRET_KEY`、`API_KEY_DERIVATION_SECRET`，由初始化脚本生成，需独立且至少 32 字符；重新部署时保留 |
| 容器镜像、联网与资源 | 见[Container 沙盒](../advanced/container-sandbox.md)；默认禁止联网 |
| Cube 节点、模板与休眠 | 见[沙盒配置与运维](../advanced/sandbox-operations.md) |

::: details 手动配置或无人值守初始化

可以复制 `.env.template` 为 `.env`，填写模型 Key、两个独立安全密钥、`YUXI_INSTANCE_ID` 和所选沙盒配置，跳过初始化脚本。Container 还需要独立随机 `SANDBOX_RUNNER_TOKEN`，建议至少 32 个字符。

Linux/macOS 初始化脚本接受环境变量覆盖 `.env` 中的同名配置。无人值守时先提供 `SILICONFLOW_API_KEY` 和所选沙盒配置，再设置 `YUXI_INIT_NON_INTERACTIVE=1`；仅生成配置而暂不准备镜像时可设置 `YUXI_INIT_SKIP_IMAGES=1`。已有安全密钥应保留，不要用临时环境变量覆盖。PowerShell 脚本不提供这些开关，也不会把进程环境中的 `CUBE_*` 写入 `.env`。

:::

## 启动遇到问题

::: details API 或 worker 无法就绪

```bash
docker compose logs --tail=100 app-init api worker
```

`storage-init` 和 `app-init` 是一次性初始化服务，成功退出属于正常状态。API 和 worker 等待初始化完成后启动。

日志出现 `sandbox_provider` 时，按所选后端检查：

- **Container**：确认 `.env` 中选择了 `container`、runner token 已配置，并使用 `--profile container` 启动。执行 `docker compose --profile container logs --tail=100 sandbox-image sandbox-runner` 查看镜像和 runner 错误，详细诊断见[Container 沙盒](../advanced/container-sandbox.md)。
- **Cube**：检查控制面、认证、模板和代理端口，以及节点的共享目录。执行 `curl --fail http://<node-private-ip>:3000/health` 验证原生节点控制面可达；嵌套节点使用实际配置的地址和端口。

:::

::: details 镜像拉取或构建失败

先确认 Docker 能访问镜像仓库。可单独重试拉取：

```bash
bash scripts/pull_image.sh python:3.13-slim
```

需要代理时，先配置 Docker daemon 和构建环境使用的代理，再重试步骤 3 的启动命令。离线镜像导出可使用 `docker/save_docker_images.sh`，导出前核对所需镜像列表。

:::

::: details 知识库依赖未就绪

```bash
docker compose ps milvus etcd minio graph
docker compose logs --tail=100 milvus etcd minio graph
```

这些服务属于默认拓扑。以各服务健康状态和 API 就绪接口为准，初始化脚本退出不代表所有依赖已就绪。

:::

## 下一步

- [知识库入门](./knowledge-base.md)：上传文档并验证检索。
- [命令行工具](./cli.md)：从终端连接实例和进行对话。
- [生产部署](../advanced/deployment.md)：使用生产 Compose、配置凭据并验证部署。
