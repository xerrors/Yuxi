# 生产部署

本页说明如何用 Docker Compose 部署 Yuxi、验证服务状态，以及从 v0.7.1 或 v0.7.2 升级到 `v0.7.3`。重要数据上线前请先在备份环境演练恢复。

## 前置条件

- Docker Engine 28.0 或更高版本；
- Docker Compose v2.33.1 或更高版本（provisioner 使用 `gw_priority` 固定默认网关）；
- 能访问所需镜像和模型服务的网络；
- 使用本地 GPU OCR 时准备 NVIDIA Container Toolkit。

生产 Compose 默认不把 PostgreSQL、MinIO、Neo4j 和 Milvus 管理端口发布到公网。维护这些服务时，优先使用 `docker compose exec` 或受控的内网入口。

::: danger 公网部署必须先配置 TLS
生产 Compose 自带的 Web 容器只监听 HTTP 80 端口，不负责证书和 HTTPS。把服务交给公网或接收登录、OIDC、API Key 请求前，必须在前面配置 TLS 反向代理，并只把 HTTPS 地址提供给用户和外部系统。HTTP 仅适合本机或受控内网调试。
:::

## 1. 准备生产配置

复制模板并编辑 `.env.prod`：

```bash
cp .env.template .env.prod
```

至少填写：

```dotenv
POSTGRES_PASSWORD=<strong-postgres-password>
NEO4J_PASSWORD=<strong-neo4j-password>
MINIO_ACCESS_KEY=<strong-minio-access-key>
MINIO_SECRET_KEY=<strong-minio-secret-key>
JWT_SECRET_KEY=<random-value-at-least-32-characters>
API_KEY_DERIVATION_SECRET=<another-random-value-at-least-32-characters>
SANDBOX_PROVISIONER_TOKEN=<another-random-value-at-least-32-characters>
YUXI_INSTANCE_ID=<stable-instance-name>
```

三个安全密钥必须彼此不同、没有首尾空白，并在重建或升级时保留原值。可以用下面的命令生成随机值，再把结果安全地写入 `.env.prod`：

```bash
openssl rand -hex 32
```

模型 API Key 按实际使用的供应商填写。生产 Compose 所有必填项都通过变量校验，缺失时会拒绝启动。

后续命令必须显式使用 `--env-file .env.prod`。Compose 的 `env_file` 负责把变量注入容器，但不会替代 Compose 文件插值所需的 `--env-file`。

### 环境隔离与自定义配置文件

开发配置的容器环境文件默认为 `.env`，生产配置默认为 `.env.prod`。使用其他文件时，同时指定 `YUXI_ENV_FILE` 和 `--env-file`，让容器注入与 Compose 插值读取同一份配置：

```bash
YUXI_ENV_FILE=.env.staging docker compose --env-file .env.staging -f docker-compose.prod.yml config --quiet
YUXI_ENV_FILE=.env.staging docker compose --env-file .env.staging -f docker-compose.prod.yml up -d --build
```

同机并行部署时，在各自的环境文件中设置不同的 `COMPOSE_PROJECT_NAME` 和 `YUXI_STATE_DIR`；项目名隔离容器、镜像、Compose 网络和动态沙盒名称，数据目录隔离持久文件。默认数据目录仍是 `./docker/volumes`，同一目录只允许一套运行中的环境写入。已有部署更换项目名或从固定容器名切换前，先结束任务和沙盒会话，用旧配置执行 `docker compose down`（保留数据，不加 `-v`），再用新配置启动；复用数据时保持状态目录和密钥不变。

生产 Web 端口通过 `YUXI_WEB_PORT` 设置，默认 80；API 默认发布到 `127.0.0.1:6050`，管理服务端口也只绑定回环地址。具体默认值由 `docker-compose.prod.yml` 的 `ports` 定义；多套生产环境还需分别设置端口，启用 `all` profile 时包括 `YUXI_MINERU_PORT` 和 `YUXI_PADDLEX_PORT`。开发环境的端口隔离示例见[并行工作树与隔离运行环境](../develop-guides/parallel-worktree-environments.md)。

MinIO 将同一宿主数据目录挂载到容器 `/data`，Neo4j 将日志目录挂载到 `/logs`；这两个容器内路径的调整不要求移动宿主文件。

## 2. 首次启动

新部署直接启动核心服务：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

需要本地 MinerU 或 PaddleX OCR 时，再启用 `all` profile：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile all up -d --build
```

`storage-migrator` 是启动依赖的一部分。迁移器成功后会退出，退出码为 0 是正常结果；API、worker 和 provisioner 会等待它成功。

## 3. 升级到 v0.7.3

### 从 v0.7.2 升级的变化

`v0.7.2` 正式版的 Schema 基线是 business=2、knowledge=1。迁移器一次补齐业务结构并记录 business=7，知识域升级到 knowledge=2；无需逐级执行 3～6。业务版本号保留开发期间的 revision，不与产品版本号一一对应。未发布的中间 Schema 会被明确拒绝；不要手工修改版本表绕过结构校验。

- LITE 模式已移除，原 LITE 部署须补齐 Milvus、etcd、Neo4j 等完整拓扑资源，并清理失效的 LITE 配置。
- Sandbox 默认规格为 `SANDBOX_RUNTIME_PROFILE=core`，不启动浏览器、browser MCP、VNC、Jupyter、code-server 或 NodeJS REPL 服务。依赖网页自动化的部署在 `.env.prod` 设置 `SANDBOX_RUNTIME_PROFILE=browser`；需要完整交互式开发环境时设置 `full`。升级时重新创建 provisioner，规格对随后创建的沙盒生效；能力范围见[沙盒配置](../agents/sandbox-architecture.md)。
- 通用后台任务改由独立 worker 执行。迁移后须用同一版本协调启动 API 与 worker；混用旧 worker 不能满足新版本的就绪条件。
- 旧知识文件中没有执行 owner 的 `parsing` / `indexing` 状态会收敛为 `error_parsing` / `error_indexing`，升级后检查失败文件并显式重试，不假定旧任务会自动续跑。
- 新建托管 Project 使用可读的时间戳目录名；既有 UUID 目录继续有效，无需重命名。

### 停机、备份与迁移

升级前安排停机窗口，先结束运行中的任务与沙盒会话，再使用旧部署的 Compose 配置停止 API、worker 和 provisioner，确认没有业务写入后再做同一时点的备份：

- PostgreSQL 数据目录；
- MinIO 数据目录；
- Milvus、etcd、Neo4j 等已启用服务的持久数据；
- `docker/volumes/yuxi` 中的历史文件、UserWorkspace 和 Skill 数据；
- 当前 Compose、`.env.prod` 和目标版本代码。

使用各存储服务支持的一致性备份方式；直接复制数据目录时，先停止对应存储服务，备份完成后按原配置启动存储依赖，业务服务保持停止。备份后至少做一次成套恢复演练。只恢复数据库或只恢复文件卷，会让数据库记录与文件字节不一致。

目标 tag 发布后检出该版本，再运行仓库提供的迁移入口；脚本会停止 API、worker 和 provisioner：

```bash
git checkout v0.7.3
docker compose --env-file .env.prod -f docker-compose.prod.yml build storage-migrator
bash scripts/migrate-storage.sh \
  --env-file .env.prod \
  -f docker-compose.prod.yml
```

迁移脚本会使用同一组 Compose、env file 和 profile 参数建立停机证明，阻止新的沙盒创建，等待现有沙盒清空，然后运行 storage migrator。迁移成功前不要启动新的 API 或 worker。

迁移按 PostgreSQL、对象存储和文件卷分别提交，不是跨存储的单事务。命令失败时保持服务停止并保留日志；修复冲突后使用完全相同的参数重跑，迁移器会校验已提交的确定性目标并继续。

迁移成功后，用相同的配置启动目标版本，再完成下节的就绪与真实对话验证：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

使用可选 profile 的部署在迁移和重启时保持相同参数。

需要放弃升级时，保持服务停止，检出旧版本，并从同一停机时点的成套备份恢复。不要只恢复其中一个存储域。

### 从 v0.7.1 升级的额外影响

迁移还会处理历史 Conversation 的 Project/Workdir 绑定、附件和产物路径、系统配置、共享 Skill 以及持久目录的所有权；未完成的历史 AgentRun 会被收敛为可观察失败，旧 SQLite checkpoint 不会迁移。

历史知识库 Markdown 中的 `http://localhost:9000/public/...` 或其他 `<host>:9000/public/...` 图片地址会在前端转换为同源 `/minio/public/...`，无需仅为更新 URL 而重新解析 PDF。对象仍在公开的 `public` bucket 中；敏感知识库需要重新解析、核对图片权限并清理旧公开对象，URL 转换不改变访问权限。

### Kubernetes 存储

当前仓库只提供沙盒 provisioner 的 Kubernetes backend，不提供完整的应用 Deployment、StorageClass、Secret 或旧 PVC 原地迁移工具。新部署需要由集群运维预先创建：

- `USER_DATA_PVC`：承载每个用户的 UserWorkspace，必须提供部署所需的共享读写能力；
- `SKILLS_PVC`：承载按用户投影的共享/内置 Skill。

旧版 `THREAD_PVC` 的目录形状与当前 `shared/<uid>/workspace/projects/<workdir-id>` 不同，不能只改变量名升级。请离线导出、校验并导入新布局，再启动新 provisioner。

## 4. 验证部署

先看容器状态：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

生产 Web 入口默认是 `http://<host>/`。部署在反向代理后并配置 TLS 后，应使用 HTTPS。

```bash
curl --fail http://localhost/api/system/health
curl --fail http://localhost/api/system/ready
```

- `/api/system/health` 只表示 API 进程存活；
- `/api/system/ready` 表示启动完成、PostgreSQL/Redis 可用，并且兼容 worker 正在提供健康租约。

就绪接口返回 `ready` 后，再用浏览器完成登录和一次真实对话。健康或就绪状态不能证明知识库、模型、沙盒或外部服务的业务链路正确。

公开头像和智能体图片通过同源 `/minio/public/...` 只读代理访问。不要把 MinIO 的 9000 对象 API 或 9001 控制台暴露到公网；知识库等私有 bucket 不经过该代理。需要单独的静态资源域名时，设置 `MINIO_PUBLIC_URL`，并在域名侧保持同样的只读限制。

## 跨域（CORS）

生产环境不会默认允许浏览器跨域请求：

```dotenv
YUXI_CORS_ORIGINS=https://frontend.example.com
```

多个来源用逗号分隔：

```dotenv
YUXI_CORS_ORIGINS=https://a.example.com,https://b.example.com
```

前端与 API 同源时留空即可。设置为 `*` 会关闭 credentials，浏览器不会携带登录态，因此不适合需要 JWT Cookie/凭证的前端。开发环境在 `YUXI_ENV=development` 且未设置该变量时，默认允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`；生产环境不会采用这个默认值。修改后重启 API。

## 维护与故障排查

### 查看日志

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs --tail=200 api worker sandbox-provisioner
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f api worker
```

### Redis 重建后恢复 worker

ARQ worker 不会在 Redis 容器重建后自动恢复连接。重建 Redis 后重启 worker：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d redis
docker compose --env-file .env.prod -f docker-compose.prod.yml restart worker
```

再次检查 `/api/system/ready`，确认 worker 健康租约恢复。

### 轮换历史默认凭据

更换 `.env.prod` 中的 PostgreSQL、Neo4j 或 MinIO 凭据，不会自动修改已经写入数据卷的服务凭据。请先使用对应服务的官方管理流程修改数据卷内的凭据，再更新 `.env.prod`，重新创建相关服务，并用旧凭据验证登录已被拒绝。不要把真实密码写进命令历史、日志或文档。

PostgreSQL 可以在数据库容器内使用交互式命令修改，避免新密码出现在 shell 历史和进程参数中：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml \
  exec postgres psql -U postgres -d yuxi -c '\password postgres'
```

Neo4j 使用 `cypher-shell` 的当前用户密码修改流程；MinIO 使用 `mc admin` 或部署采用的密钥管理流程。完成轮换后，把新值写入 `.env.prod`，再重建依赖这些凭据的服务：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml \
  up -d --force-recreate postgres graph minio api worker
```

最后分别用新凭据和旧凭据执行一次受控登录验证；API/worker 的 `API_KEY_DERIVATION_SECRET` 与 `SANDBOX_PROVISIONER_TOKEN` 也必须保持为持久、独立且至少 32 个字符的值。

### 常用检查顺序

1. `docker compose ps`：确认迁移器成功、API/worker/provisioner 在运行。
2. `docker compose logs`：从最先失败的服务开始看，不只看最后一条 API 错误。
3. `/api/system/ready`：确认接流量前置条件。
4. 真实登录、对话和文件操作：确认业务链路。
5. 知识库、OCR、Langfuse 等可选能力：单独检查其配置和外部服务。

## 第三方组件和许可证

Yuxi 本体使用 MIT License。Compose 依赖以独立进程运行，Yuxi 通过公开协议访问它们；第三方组件的许可证不会因为使用 Compose 就变成 MIT。

当前 Compose 引用的主要组件如下。表中的版本是镜像 tag；只有明确写死的 tag 才能提供对应的版本预期，`postgres:16`、`mineru-vllm:latest` 和 `paddlex:latest` 仍可能随重新拉取而变化：

| 组件 | 镜像引用 | 许可证 |
| --- | --- | --- |
| Neo4j Community | `neo4j:5.26.29` | GPL-3.0-only |
| MinIO | `quay.io/minio/minio:RELEASE.2023-03-20T20-16-18Z` | AGPL-3.0 |
| Milvus | `milvusdb/milvus:v2.5.6` | Apache-2.0 |
| etcd | `quay.io/coreos/etcd:v3.5.5` | Apache-2.0 |
| PostgreSQL | `postgres:16` | PostgreSQL License |
| Redis | `redis:7.4.10-alpine` | RSALv2 / SSPLv1（均非 OSI 许可证） |
| MinerU / PaddleX（可选） | `mineru-vllm:latest` / `paddlex:latest` | 以各自 Dockerfile 和上游声明为准 |

这张表只覆盖 Compose 的主要镜像本体，不是完整的软件物料清单，也不承诺 `latest` 镜像的内容固定。镜像还可能包含各自的基础系统和传递依赖，离线交付前要按实际 digest 核对许可证、版权声明和对应源码。

如果通过 `docker/save_docker_images.sh` 或其他方式向第三方再分发包含 GPL/AGPL 软件的镜像，需要保留许可证文本和上游声明，并按对应许可证第 6 节提供匹配的完整对应源码或有效的书面源码要约。通过网络提供服务、修改 AGPL 组件或把组件集成进同一程序时，义务可能不同，不能只附一个上游链接就视为完成。

商业部署可以评估 Neo4j Enterprise、MinIO 商业订阅或其他兼容替代品，但这会带来新的协议、迁移和运维条件。

需要 Neo4j 企业版功能或商业支持时，可以将图谱服务镜像替换为 `neo4j:5.26-enterprise`，并设置：

```dotenv
NEO4J_ACCEPT_LICENSE_AGREEMENT=yes
```

同时按 Neo4j 官方订阅协议确认许可范围；替换镜像不会自动迁移或改变现有数据卷。以上是工程侧边界，不构成法律意见；再分发、修改组件或对外托管前请让法务按具体版本和交付方式确认。
