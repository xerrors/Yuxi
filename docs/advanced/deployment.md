# 生产部署

本页说明如何用 Docker Compose 全新部署 Yuxi 并验证服务状态。重要数据上线前请先在备份环境演练恢复。

0.8 的功能与不兼容范围见[0.8.0 更新说明](../develop-guides/releases/0.8.0.md)。试用环境使用独立 PostgreSQL、对象存储、向量索引和用户文件目录，保留旧部署及其成套备份。

## 前置条件

- Docker Engine 28.0 或更高版本；
- Docker Compose v2.33.1 或更高版本；
- 能访问所需镜像和模型服务的网络；
- 使用本地 GPU OCR 时准备 NVIDIA Container Toolkit。

生产 Compose 默认不把 PostgreSQL、MinIO、Neo4j 和 Milvus 管理端口发布到公网。维护这些服务时，优先使用 `docker compose exec` 或受控的内网入口。

::: danger 公网部署必须先配置 TLS
生产 Compose 自带的 Web 容器只监听 HTTP 80 端口，不负责证书和 HTTPS。把服务交给公网或接收登录、OIDC、API Key 请求前，必须在前面配置 TLS 反向代理，并只把 HTTPS 地址提供给用户和外部系统。HTTP 仅适合本机或受控内网调试。
:::

## 1. 准备生产配置

先按[下载代码](../intro/quick-start.md#_1-下载代码)获取 `v0.8.0.beta5` 测试版本源码，后续命令均在仓库根目录执行。该版本用于独立环境试用。

Linux/macOS 可以使用初始化脚本准备 `.env.prod`。默认使用 Container；先构建固定的沙盒镜像，再通过安全环境注入模型 Key。首次部署使用空状态目录和独立项目名：

```bash
export YUXI_ENV_FILE=.env.prod
export YUXI_ENV=production
export COMPOSE_PROJECT_NAME=production
export YUXI_STATE_DIR=./.yuxi/production/state
export YUXI_INSTANCE_ID=Production
export SILICONFLOW_API_KEY='<siliconflow-api-key>'
export SANDBOX_BACKEND=container
export SANDBOX_CONTAINER_IMAGE=yuxi-sandbox:0.8.0.beta5
docker build -f docker/sandbox.Dockerfile -t "$SANDBOX_CONTAINER_IMAGE" .
export SANDBOX_CONTAINER_IMAGE_DIGEST="$(docker image inspect "$SANDBOX_CONTAINER_IMAGE" --format '{{.Id}}')"
YUXI_INIT_NON_INTERACTIVE=1 ./scripts/init.sh
```

初始化脚本当前无条件要求 `SILICONFLOW_API_KEY`；即使生产使用其他供应商，也需提供该变量完成初始化，其他供应商在启动后于「智能体 → 模型供应商」接入。

Container 共享宿主内核，适用于单租户可信代码；生产必须固定镜像 digest。需要强隔离时显式设置 `SANDBOX_BACKEND=cube`，按[沙盒配置与运维](./sandbox-operations.md#原生执行节点)准备节点、模板和共享挂载，并配置 `CUBE_*` 连接与路径；Cube 启动命令去掉 `--profile container`。Docker 项目名只接受小写字母、数字、连字符和下划线，因此显示名称 `Production` 对应项目名 `production`。

脚本接受环境变量覆盖，并生成缺失的 PostgreSQL、Neo4j、MinIO 凭据、两个独立应用安全密钥及 runner 认证密钥；已有非空安全密钥无效时明确失败，不自动轮换。重建时保留配置文件及原有密钥，避免用临时环境变量覆盖已有生产凭据。开关、优先级和模型 Key 输入见[常见配置](../intro/quick-start.md#常见配置)。

也可以复制模板，手动编辑 `.env.prod` 并设置文件权限：

```bash
cp .env.template .env.prod
chmod 600 .env.prod
```

至少填写：

```dotenv
POSTGRES_PASSWORD=<strong-postgres-password>
NEO4J_PASSWORD=<strong-neo4j-password>
MINIO_ACCESS_KEY=<strong-minio-access-key>
MINIO_SECRET_KEY=<strong-minio-secret-key>
JWT_SECRET_KEY=<random-value-at-least-32-characters>
API_KEY_DERIVATION_SECRET=<another-random-value-at-least-32-characters>
SANDBOX_BACKEND=container
SANDBOX_RUNNER_TOKEN=<another-random-value-at-least-32-characters>
SANDBOX_CONTAINER_IMAGE=<prebuilt-sandbox-image>
SANDBOX_CONTAINER_IMAGE_DIGEST=sha256:<image-id>
YUXI_INSTANCE_ID=<stable-instance-name>
```

JWT 与 API Key 派生这两个应用安全密钥必须彼此不同、没有首尾空白，并在重建时保留原值。可以用下面的命令生成随机值，再把结果安全地写入 `.env.prod`；runner 使用独立随机密钥，镜像准备见[Container 沙盒](./container-sandbox.md#配置)：

```bash
openssl rand -hex 32
```

模型 API Key 按实际使用的供应商填写。生产 Compose 所有必填项都通过变量校验，缺失时会拒绝启动。

后续命令必须显式使用 `--env-file .env.prod`。Compose 的 `env_file` 负责把变量注入容器，但不会替代 Compose 文件插值所需的 `--env-file`。

### 环境隔离与自定义配置文件

开发配置的容器环境文件默认为 `.env`，生产配置默认为 `.env.prod`。使用其他文件时，同时指定 `YUXI_ENV_FILE` 和 `--env-file`，让容器注入与 Compose 插值读取同一份配置：

```bash
YUXI_ENV_FILE=.env.staging docker compose --env-file .env.staging -f docker-compose.prod.yml --profile container config --quiet
YUXI_ENV_FILE=.env.staging docker compose --env-file .env.staging -f docker-compose.prod.yml --profile container up -d --build
```

同机并行部署时，在各自的环境文件中设置不同的 `COMPOSE_PROJECT_NAME` 和 `YUXI_STATE_DIR`；项目名隔离应用容器、镜像与 Compose 网络，数据目录隔离持久文件。Cube 用户 VM 的引用保存在各自 PostgreSQL，节点共享路径需要另行隔离；Compose 项目名不会创建隔离的执行节点或共享挂载。默认数据目录为 `./docker/volumes`，同一目录只允许一套运行中的环境写入。已有部署更换项目名前，先停止接收任务、等待 Run 和命令清理并核对原执行环境，再用旧配置执行 `docker compose down`（保留数据，不加 `-v`），用新配置启动；复用数据时保持状态目录、节点挂载、实例身份和密钥不变。

生产 Web 端口通过 `YUXI_FRONTEND_PORT` 设置，默认 80；API 默认发布到 `127.0.0.1:6050`，管理服务端口也只绑定回环地址。具体默认值由 `docker-compose.prod.yml` 的 `ports` 定义；多套生产环境还需分别设置端口，启用 `all` profile 时包括 `YUXI_MINERU_PORT` 和 `YUXI_PADDLEX_PORT`。开发环境的端口隔离示例见[并行工作树与隔离运行环境](../develop-guides/parallel-worktree-environments.md)。

MinIO 将同一宿主数据目录挂载到容器 `/data`，Neo4j 将日志目录挂载到 `/logs`；这两个容器内路径的调整不要求移动宿主文件。

## 2. 首次启动

构建应用与存储初始化镜像，再启动完整拓扑：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container build api frontend minio storage-init
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build
```

Compose 自动创建缺失的挂载目录，`storage-init` 在无网络、无平台凭据的轻量容器中设置以下挂载根的权限。API、worker 与 Milvus 必须等待其退出码为 0 才启动；初始化失败时，先检查目录是否只读、宿主文件系统是否允许修改属主，以及部署使用的 UID 映射。

| 目录 | UID:GID | 模式 |
| --- | --- | --- |
| `yuxi/threads`、`yuxi/skill-sources`、`yuxi/skill-projections` | `1000:1000` | `0700` |
| `milvus/milvus`、`milvus/logs` | `999:999` | `0755` |

以上路径均相对 `YUXI_STATE_DIR`。初始化可重复运行，仅调整五个目录本身；已有内部文件和目录的内容、数字属主与模式保持原值。恢复备份时保留原数字属主，历史内部权限错误由部署者离线处理。PostgreSQL、Redis、MinIO、Neo4j 继续遵循各自镜像的存储初始化契约。

API、Web 与 MinIO 的项目文件在构建时明确权限：源码和配置归 root 持有，普通文件可读、目录可进入、入口脚本可执行；API 与 worker 仍以 `1000:1000` 运行，仅在专用数据与运行目录写入。构建输入的 `600/700` 模式不会使镜像内的项目文件失去可读性。开发 Compose 的源码 bind mount 使用宿主文件实际权限，宿主 checkout 仍须允许容器用户读取与遍历；镜像权限不会覆盖挂载内容。实现与回归入口见[权限决策](../develop-guides/decisions/implemented/2026-10-17-container-storage-permissions.md)。

需要本地 MinerU 或 PaddleX OCR 时，再启用 `all` profile：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container --profile all up -d --build
```

`app-init` 以 UID/GID 1000 运行，等待 PostgreSQL 健康与 `storage-init` 成功后，在数据库锁内完成 Schema 和共享内容初始化。它为确认空库建立 business 版本 2 与 knowledge 版本 2 基线，包含当前表、索引、约束和 LangGraph checkpoint 表；当前版本跳过建表，其他版本不匹配会明确失败。随后同步系统配置定义、内置模型供应商、Agent、MCP 和 Skill 来源文件，API 与 worker 等待全部步骤成功。应用启动不自动迁移历史数据。[版本 1 基线决定](../develop-guides/decisions/implemented/2026-10-19-schema-baseline-one.md)说明当前结构和历史升级路径的收敛；编号重置不代表旧库已完成结构变更，历史 `1/1` 标记也不能作为结构完整的证明。

### 可选启用个人助理

个人助理默认关闭。部署方在环境文件中设置 `YUXI_PERSONAL_ASSISTANT_ENABLED=true`，并重新创建 API 和 worker 后，主导航在“对话”下方、“智能体”上方显示“个人助理”入口。超级管理员在系统设置的 Options 中选择“个人助理模型”；该值用于新建会话或尚未选择模型的主会话首次输入，不覆盖已有会话模型。实际选用的模型不可用时，用户仍可创建助理和查看对话，但不能提交新的工作。专用默认值不回退到普通默认对话模型。

已有 business v1、knowledge v2 数据库需要先备份并停止 API、worker，再使用待部署版本执行一次显式升级。无论是否启用个人助理，当前程序都要求 business v2；升级增加登记表、更新版本，保留既有数据。已存在 business v2 登记且仍使用普通 Backend 的部署也执行同一命令，切换到专用 Backend；旧助理的待处理输入、未结束轮次及清理责任必须先处理。完成后可安全重跑：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml stop api worker
docker compose --env-file .env.prod -f docker-compose.prod.yml build app-init frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm --no-deps app-init uv run --no-sync python -m yuxi.migrations.personal_assistants
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm --no-deps app-init
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build api worker frontend
```

这些命令在目标版本源码目录执行；`app-init`、API 和 worker 共享同一后端镜像，构建步骤确保迁移与运行使用相同版本。开发环境使用相同顺序，将 Compose 文件替换为 `docker-compose.yml`，环境文件使用对应的开发配置。空数据库由 `app-init` 直接建立当前基线，不执行旧库升级命令。其他历史版本仍需按其对应升级路径处理，不能手动修改版本号跳过结构校验。

每位用户拥有一个助理和固定主对话，工作记录位于用户空间 `agents/assistant`。助理页面明确发送 steer，后端按普通输入协议处理。会话保留模型和审批选择，资料编辑只同步主会话名称与个性化说明；已接收输入及运行中的 steer 保留原快照。助理通过 `send_to_user` 显式沟通，普通正文仅在完整轨迹保留。委派创建普通 Agent 的独立 root，默认不选择项目，使用独立 managed Workdir，不继承助理个性化内容；进度、结果和失败可从右侧完整轨迹查看。任务完成不自动唤醒助理。

关闭功能时，先等待正在执行的任务结束，再把环境值改为 `false` 并重新创建 API 和 worker。入口与专用助理接口不可用，执行 Backend 不再注册，已有数据保留。普通会话历史仍按既有权限读取；遗留执行因 Backend 不可用而明确失败。已有 API Key 沿用普通用户与 APP 作用域访问已知会话，没有新增专用对外接口或定时触发入口。

### 可选关闭知识库

知识库默认开启。需要停用知识库时，在同一项目叠加关闭配置；PostgreSQL、Redis、MinIO、代码和数据保持原位：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container -f docker-compose.no-knowledge.yml up -d --no-build
```

开发环境使用 `docker compose --profile container -f docker-compose.yml -f docker-compose.no-knowledge.yml up -d`。关闭配置将 Milvus、etcd、Neo4j 的副本数设为 0，并为 API、worker 和初始化器设置 `YUXI_KNOWLEDGE_ENABLED=false`。同项目已有的三个容器也会停止，无需删除数据卷。切换前停止提交知识库任务，并等待正在执行的任务结束；意外中断的任务仍按已有租约恢复规则收敛，尚未执行的知识库任务和投影清理意图保留，重新开启后继续处理。

关闭时，API 不初始化知识库执行器，不注册知识库管理、图谱、评估、公开知识库与 `/api/mcp` 路由；这些路径返回 404。系统能力发现报告知识库不可用，Agent、MCP 配置和 Skill 定义仍保留。Agent 实际使用知识库工具时会报告 MCP 不可用，普通对话不依赖知识库后端。初始化器仍建立、校验原有两域 Schema 并同步内置内容，不清理知识库记录。

重新开启时使用原有完整命令，不叠加关闭配置：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build
```

确认环境文件中的 `YUXI_KNOWLEDGE_ENABLED` 为 `true` 或未设置，然后检查就绪状态并检索已有文档。该开关不是热切换；修改后需要重新创建 API 和 worker。仅设置环境开关不会停止存储容器，停启本地容器应使用上面的 Compose 叠加配置。

API 与 worker 必须共享持久化的 `JWT_SECRET_KEY` 和 `YUXI_INSTANCE_ID`，用于内部 MCP 短期用户委托。启用 Knowledge 时，两种进程在连接数据库前校验显式委托配置，不使用各进程临时生成的身份；worker 还会在派发知识任务前连接已有知识库后端与 Neo4j，因此完整部署必须启动 Milvus、etcd 和 Neo4j。Knowledge 是平台自有服务，容器内 MCP 地址固定为 `http://api:5050/api/mcp`，不提供环境变量覆盖；直接在宿主运行后端时无法访问该容器内地址，知识工具与引用读取需要在容器拓扑内执行。

### 共享内容更新与失败重试

应用初始化每次部署都需要执行；当前 Schema 不变也会同步内置内容。系统配置值、Agent 名称与配置、供应商凭据与地址、MCP 和 Skill 启用状态按各业务域规则保留；代码拥有的定义和内置 Skill 文件按当前镜像刷新。初始化只挂载 Skill 来源目录，不访问用户工作区或 Milvus 数据。

更新镜像或开发环境的内置定义后，在没有活动任务时停止 API 和 worker，再重新运行应用初始化并启动消费者：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container stop api worker
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up --no-deps --force-recreate --exit-code-from app-init app-init
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build
```

上述更新步骤要求 PostgreSQL 已健康、`storage-init` 已成功且目标镜像已构建或拉取。每条命令成功后再执行下一条。初始化失败时查看 `docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container logs app-init`，修正问题后重跑；不要直接启动消费者绕过失败。新空库建表失败会清理该初始化器的半成品；共享内容同步失败保留已完成步骤和已有数据，通过幂等重跑完成剩余内容。多个阶段包含独立事务和文件操作，不承诺整体原子回滚。

API 和 worker 在各自进程内继续校验密钥及 Schema，创建连接、缓存并检查运行依赖。外部模型 Token 与可选集成按实际启用和使用验证，不要求所有供应商配置齐全。职责与验证见[应用初始化决策](../develop-guides/decisions/implemented/2026-10-18-app-initialization.md)。

## 3. 数据库与文件目录

新部署使用空 PostgreSQL 数据库和独立的 Yuxi 文件目录。已有当前版本数据库可以直接重启同版本服务。其他版本和包含未版本化 Yuxi 表的数据库会被明确拒绝，初始化器不执行历史升级、回填或自动清库。除下述知识域 v1 显式转换入口外，其他历史结构由部署者独立备份和处理，不通过手改版本标记绕过结构校验。

知识域 v1 的路径型目录需要在维护窗口显式转换。先备份 PostgreSQL 与对象存储，停止 API、worker 及其他知识库写入方，在同一部署环境运行 `uv run --no-sync python -m yuxi.migrations.knowledge_directories` 进行只读审计。活动摄取任务、重复真实目录、真实/虚拟同名目录、普通文件占用目录前缀及无效父关系会阻止转换，必须先明确处理，不能通过手改版本跳过。

审计无阻断后运行 `uv run --no-sync python -m yuxi.migrations.knowledge_directories --apply`。转换逐条提交，可在中断后重跑；文件 ID、原件地址、Markdown 和索引身份保留，叶子重名按稳定顺序加后缀。只有全部数据收敛且约束建立成功，才发布 knowledge 版本 2。转换后重新执行审计、回读文件和对象，再运行 app-init 并启动 API/worker。跨版本回退需要恢复维护前整套备份，不能只回退代码。只读连接器不执行目录转换。设计与验证见[目录与导入决策](../develop-guides/decisions/implemented/2026-08-24-knowledge-folder-materialization.md)。

应用不写入身份操作日志，初始 Schema 不创建 `operation_logs` 表。

默认执行环境由 Container runner 提供，Run 结束后清理容器并保留工作文件。选择 Cube 时，节点准备与 Workspace、Skills 共享目录要求见[沙盒配置与运维](./sandbox-operations.md)。

## 4. 验证部署

先看容器状态：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container ps
```

生产 Web 入口默认是 `http://<host>/`。部署在反向代理后并配置 TLS 后，应使用 HTTPS。

```bash
curl --fail http://localhost/api/system/health
curl --fail http://localhost/api/system/ready
```

- `/api/system/health` 只表示 API 进程存活；
- `/api/system/ready` 表示启动完成、PostgreSQL/Redis 可用，并且兼容 worker 正在提供健康租约。

worker 的 Compose 健康检查通过 `python -m yuxi.workers.health` 轻量读取 `REDIS_URL` 中的 ARQ 心跳，不加载业务执行依赖。心跳缺失、过期、没有 TTL、TTL 超过约定上界或 Redis 连接失败时检查失败。该心跳表达共享队列的消费健康，多副本部署不能用它判断单个 worker 进程是否失活。

就绪接口返回 `ready` 后，再用浏览器完成登录和一次真实对话。健康或就绪状态不能证明知识库、模型、沙盒或外部服务的业务链路正确。

公开头像和智能体图片通过同源 `/minio/public/...` 只读代理访问。不要把 MinIO 的 9000 对象 API 或 9001 控制台暴露到公网；知识库等私有 bucket 不经过该代理。需要单独的静态资源域名时，设置 `MINIO_PUBLIC_URL`，并在域名侧保持同样的只读限制。

## 开发环境端口与入口

开发 Compose 发布到宿主机的端口如下；生产 Compose 对外提供 Web 80 入口，同时将 API 与存储维护端口绑定到宿主 `127.0.0.1`。浏览器、CLI 和外部系统通过 Web 同源代理访问 API 与公开图片，无需开放这些维护端口。生产默认端口由 [docker-compose.prod.yml](https://github.com/xerrors/Yuxi/blob/main/docker-compose.prod.yml) 拥有。

| 端口 | 服务 | 用途 |
| --- | --- | --- |
| 5173 | Web | 开发 Web 界面 |
| 5050 | API | API 和 Swagger 文档 |
| runner 8090（容器内网） | sandbox-runner | 默认 Container 执行，仅 API/worker 访问 |
| Cube 私网控制面/代理 | 专用 Linux/KVM 节点 | 显式选择 Cube 时由部署提供 |
| 7474 / 7687 | Neo4j | HTTP 管理界面 / Bolt |
| 9000 / 9001 | MinIO | 对象 API / 管理控制台 |
| 19530 / 9091 | Milvus | gRPC / 健康检查 |
| 5432 | PostgreSQL | 本机数据库维护 |
| 6379 | Redis | 本机缓存和队列维护 |

`all` profile 下还有两个可选 OCR 服务：`mineru-api`（30001，`/file_parse` 接口）和 `paddlex`（8080，PP-Structure-V3）。etcd 只在 Compose 网络内供 Milvus 使用，没有发布到宿主机。

PostgreSQL、Redis、MinIO、Milvus 和 Neo4j 的端口只绑定 `127.0.0.1`，不要把它们暴露到公网；Web 与 API 发布到所有接口。各端口可用环境变量覆盖（`YUXI_FRONTEND_PORT`、`YUXI_API_PORT`、`YUXI_NEO4J_HTTP_PORT`、`YUXI_MINIO_API_PORT`、`YUXI_MILVUS_PORT`、`YUXI_POSTGRES_PORT`、`YUXI_REDIS_PORT`），完整映射以 [docker-compose.yml](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml) 为准。

常用入口：Web <http://localhost:5173>，API 文档 <http://localhost:5050/docs>，Neo4j <http://localhost:7474>。用户执行环境状态在“设置 → Agent 环境”查看；health 与 ready 接口的语义见上方「验证部署」。

## 跨域（CORS）

生产环境不会默认允许浏览器跨域请求：

```dotenv
YUXI_CORS_ORIGINS=https://frontend.example.com
```

多个来源用逗号分隔：

```dotenv
YUXI_CORS_ORIGINS=https://a.example.com,https://b.example.com
```

前端与 API 同源时留空即可。设置为 `*` 会关闭 CORS credentials 支持，依赖跨域 Cookie 的请求无法携带该登录状态；生产浏览器入口应列出明确来源。开发环境在 `YUXI_ENV=development` 且未设置该变量时，默认允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`；生产环境不会采用这个默认值。修改环境文件后重新创建 API 容器。

## 维护与故障排查

### 查看日志

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container logs --tail=200 api worker
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container logs -f api worker
```

### PostgreSQL 共享内存配额

开发与生产 Compose 为 PostgreSQL 配置 `shm_size: 1g`，这是 `/dev/shm` 的使用上限，不是启动时预占内存。并行查询共享内存不足时，可能出现 `could not resize shared memory segment ... No space left on device`；该错误不等同于数据盘已满。

已有部署修改该配置后需要重建 PostgreSQL 容器，`restart` 不会更新配额。重建会中断 API 与 worker 的数据库连接；在维护窗口停止接收任务、等待活动 Run 和后台作业收敛后执行：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-deps --force-recreate postgres
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container exec postgres df -h /dev/shm
```

确认 `/dev/shm` 容量为 1.0G、PostgreSQL 健康且 `/api/system/ready` 恢复后，再开放入口。开发环境使用同样的服务命令并去掉生产配置参数。

### Redis 重建后恢复 worker

ARQ worker 不会在 Redis 容器重建后自动恢复连接。重建 Redis 后重启 worker：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d redis
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container restart worker
```

再次检查 `/api/system/ready`，确认 worker 健康租约恢复。

### 停机备份与恢复演练

本节适用于同一版本、同一组组件版本的整套状态恢复。备份必须同时包含 PostgreSQL、MinIO、Milvus/etcd、Neo4j、Redis、用户 Workspace、Skills 来源及投影，并单独安全保存环境文件、两个应用安全密钥和镜像版本或 digest。Container 的 Home 与临时软件不跨 Run 保留。显式选择 Cube 时，应用目录不包含节点磁盘和 VM Home；需要保留用户安装的软件与配置时，按[沙盒运维](./sandbox-operations.md#运维与验证)另行备份节点，保留实例身份与原挂载路径。

1. 关闭外部入口，等待正在执行的 Run、待派发 Input、后台作业和沙盒命令收敛；暂停定时任务的触发来源。确认用户 VM 已空闲，按执行节点的停机规程保存节点状态。
2. 停止 Web、API 和 worker，再停止全部本次部署的存储服务。不要复制仍在写入的 PostgreSQL、etcd、Neo4j 或对象目录。
3. 对配置中的实际状态目录执行冷备份，保留数字属主、文件模式、符号链接和必要的扩展属性；外置共享目录或符号链接目标必须独立纳入同一批备份。保留原数据与原环境文件。
4. 把备份解到新的空目录，核对文件摘要与数字属主。恢复环境使用同一组安全密钥；所有 bind mount、节点共享路径和实例身份必须与恢复方案一致，不能让新旧实例同时写同一目录。
5. 用相同镜像启动存储、`app-init`、API、worker 和 Web。核对 Schema、readiness、原用户登录、会话最终结果、反馈、知识文件正文与检索、原始对象字节、图谱及用户文件；再提交一条新对话，确认恢复后的写入链路可用。

以下示例要求 `STATE_DIR` 是实际本地状态根，且其内部数据都已纳入备份。按现场环境设置绝对路径；环境文件含凭据，备份目录只允许运维身份读取。

```bash
STATE_DIR=/absolute/path/to/yuxi-state
BACKUP_DIR=/absolute/path/to/backup
RESTORE_DIR=/absolute/path/to/empty-restore-state

docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container stop frontend api worker
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container stop
sudo install -d -m 0700 "$BACKUP_DIR"
sudo tar --numeric-owner --acls --xattrs -cpf "$BACKUP_DIR/state.tar" -C "$STATE_DIR" .
sudo install -m 0600 .env.prod "$BACKUP_DIR/env.prod"
sudo sha256sum "$BACKUP_DIR/state.tar"

sudo install -d -m 0700 "$RESTORE_DIR"
sudo tar --numeric-owner --acls --xattrs -xpf "$BACKUP_DIR/state.tar" -C "$RESTORE_DIR"
```

恢复前复制出独立环境文件并把 `YUXI_STATE_DIR` 指向 `RESTORE_DIR`，使用 Cube 时同步准备节点共享路径；通过 `YUXI_ENV_FILE` 和 `--env-file` 使用该文件。已有 VM 的挂载引用不会随环境变量自动改变，换路径或换节点时必须先验证其映射与节点恢复，不用重建空 VM 冒充保留原环境。完成上述业务回读前，不切换真实入口。合成数据的小规模演练只证明该版本的恢复链路，生产数据量下的恢复时间仍需现场测量。

### 轮换历史默认凭据

更换 `.env.prod` 中的 PostgreSQL、Neo4j 或 MinIO 凭据，不会自动修改已经写入数据卷的服务凭据。请先使用对应服务的官方管理流程修改数据卷内的凭据，再更新 `.env.prod`，重新创建相关服务，并用旧凭据验证登录已被拒绝。不要把真实密码写进命令历史、日志或文档。

PostgreSQL 可以在数据库容器内使用交互式命令修改，避免新密码出现在 shell 历史和进程参数中：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container \
  exec postgres psql -U postgres -d yuxi -c '\password postgres'
```

Neo4j 使用 `cypher-shell` 的当前用户密码修改流程；MinIO 使用 `mc admin` 或部署采用的密钥管理流程。完成轮换后，把新值写入 `.env.prod`，再重建依赖这些凭据的服务：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container \
  up -d --force-recreate postgres graph minio api worker
```

最后分别用新凭据和旧凭据执行一次受控登录验证；API/worker 的 `API_KEY_DERIVATION_SECRET` 也必须保持为持久、独立且至少 32 个字符的值。

### 常用检查顺序

1. `docker compose ps`：确认 Schema 初始化成功、API 与 worker 在运行。
2. `docker compose logs`：从最先失败的服务开始看，不只看最后一条 API 错误。
3. `/api/system/ready`：确认接流量前置条件。
4. 真实登录、对话和文件操作：确认业务链路。
5. 知识库、OCR、Langfuse 等可选能力：单独检查其配置和外部服务。

### Milvus 3 全新部署

Milvus 3.0.2 与 PyMilvus 3.0.2 配套部署，知识库与图向量通过 `MILVUS_DB` 使用同一个数据库，默认 `yuxi`。Compose 使用 Woodpecker 的对象存储模式，MinIO 固定到支持条件写入的版本；`storage-init` 在 API、worker 和 Milvus 启动前设置各自绑定目录的 UID/GID，初始化失败会阻止启动。

允许丢弃全部数据时，停掉当前 Compose project，在对应环境文件中把 `YUXI_STATE_DIR` 指向一个新的空目录，例如 `/srv/yuxi/state-milvus3`，然后重新构建并启动完整拓扑。开发使用 `docker-compose.yml`，生产使用本页的 `.env.prod` 与 `docker-compose.prod.yml`。新目录同时重置 PostgreSQL、对象存储、图谱、工作区及向量索引；需要重新初始化管理员、模型供应商和知识库，再上传、解析、索引文档。不要把旧的 PostgreSQL 文件记录与空的 Milvus 索引混用。

检查 `storage-init` 和 `app-init` 已成功退出，并确认 `/api/system/ready` 返回就绪。再按[知识库教程](../intro/knowledge-base.md#_3-验证检索)确认文件为 `indexed`，三个检索模式返回目标文件的片段。集合的模型或 schema 不匹配时会显式报错，查询不会自动删除集合；需要修正部署状态并重新建立知识库。

## 第三方组件和许可证

Yuxi 本体使用 MIT License。Compose 依赖以独立进程运行，Yuxi 通过公开协议访问它们；第三方组件的许可证不会因为使用 Compose 就变成 MIT。

当前 Compose 引用的主要组件如下。表中的版本是镜像 tag；只有明确写死的 tag 才能提供对应的版本预期，`postgres:16`、`mineru-vllm:latest` 和 `paddlex:latest` 仍可能随重新拉取而变化：

| 组件 | 镜像引用 | 许可证 |
| --- | --- | --- |
| Neo4j Community | `neo4j:5.26.29` | GPL-3.0-only |
| MinIO | 本地构建：`<项目名>-minio:RELEASE.2024-12-18T13-15-44Z`（`docker/minio/Dockerfile`；项目名取 `COMPOSE_PROJECT_NAME`，默认 `yuxi`） | AGPL-3.0 |
| Milvus | `milvusdb/milvus:v3.0.2` | Apache-2.0 |
| etcd | `quay.io/coreos/etcd:v3.5.25` | Apache-2.0 |
| PostgreSQL | `postgres:16` | PostgreSQL License |
| Redis | `redis:7.4.10-alpine` | RSALv2 / SSPLv1（均非 OSI 许可证） |
| MinerU / PaddleX（可选） | `mineru-vllm:latest` / `paddlex:latest` | 以各自 Dockerfile 和上游声明为准 |

MinIO 的镜像由本仓库构建：MinIO 在 Docker Hub 与 quay.io 上的镜像已不再公开分发（同一 registry 上其他镜像仍可匿名拉取），`dl.min.io` 返回 410。Compose 按 `docker/minio/Dockerfile` 构建该镜像，构建时从官方 GitHub Release 下载固定版本的二进制并校验 sha256；它运行与下架前镜像逐字节相同的 MinIO 二进制，基础镜像与镜像内附带文件则不同（不再包含 `mc`、`minisig` 与 `*_FILE` 变量默认值）。

这张表只覆盖 Compose 的主要镜像本体，不是完整的软件物料清单，也不承诺 `latest` 镜像的内容固定。镜像还可能包含各自的基础系统和传递依赖，离线交付前要按实际 digest 核对许可证、版权声明和对应源码。

如果通过 `docker/save_docker_images.sh` 或其他方式向第三方再分发包含 GPL/AGPL 软件的镜像，需要保留许可证文本和上游声明，并按对应许可证第 6 节提供匹配的完整对应源码或有效的书面源码要约。通过网络提供服务、修改 AGPL 组件或把组件集成进同一程序时，义务可能不同，不能只附一个上游链接就视为完成。

商业部署可以评估 Neo4j Enterprise、MinIO 商业订阅或其他兼容替代品，但这会带来新的协议、迁移和运维条件。

需要 Neo4j 企业版功能或商业支持时，用 Compose override 同时替换图谱服务镜像并注入许可确认；只把变量写进 `.env.prod` 不会生效，因为 `graph` 服务使用显式 `environment` 列表。请自行新建 `docker-compose.enterprise.yml`：

```yaml
services:
  graph:
    image: neo4j:5.26.29-enterprise
    environment:
      NEO4J_ACCEPT_LICENSE_AGREEMENT: "yes"
```

启动或重建图谱服务时合并该文件，并先验证合并后的配置可解析：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container -f docker-compose.enterprise.yml config --quiet
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container -f docker-compose.enterprise.yml up -d graph
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container -f docker-compose.enterprise.yml logs --tail=100 graph
```

同时按 Neo4j 官方订阅协议确认许可范围；设置该变量表示部署方接受 Neo4j 商业许可，许可获取与合规由部署方负责。替换镜像不会自动迁移或改变现有数据卷。以上是工程侧边界，不构成法律意见；再分发、修改组件或对外托管前请让法务按具体版本和交付方式确认。
