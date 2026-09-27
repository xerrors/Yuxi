# MinIO 镜像改为仓库内构建

状态：proposed
类型：architecture
Owner：docker/minio/Dockerfile

## 问题

两份 Compose 的 MinIO 服务引用 `quay.io/minio/minio:RELEASE.2023-03-20T20-16-18Z`，该镜像的三个来源都已不可用：Docker Hub 的 `minio/minio` 在 2026-09-11 前后被移除，quay.io 上 `minio/minio` 这个仓库自 2026-09-24 前后起不再对匿名用户公开（同一 registry 上的其他镜像仍可匿名拉取，例如 `quay.io/coreos/etcd` 的匿名 token 仍授予 `pull` 并返回 manifest），`dl.min.io` 返回 410。

影响分两层。部署侧：新机器执行 `docker compose up` 在拉取 MinIO 时失败，已有机器依赖本地镜像缓存才能继续启动。CI 侧：`system-tests.yml` 的两条 job 都停在 `docker compose up` 这一步，其后 32 个 E2E 步骤整体 skipped；`main` 自身的运行结果相同，因此所有 PR 的这两条检查恒定失败，无法再用它们判断改动的正确性。

镜像内容仍有官方来源：MinIO 的 GitHub Release 保留各版本的 `linux-amd64` / `linux-arm64` 二进制，并附 `.sha256sum` 与 `.minisig`。下架前镜像内的 `/opt/bin/minio` 与该 Release 的 amd64 资产逐字节相同（两者 sha256 均为 `df0de9982c4ae440d2c9617bc1da805cf71eb7d9ce5b106b3fdb036fa27ba163`），镜像可以由 Release 内容重建。

## 提案

新增 `docker/minio/Dockerfile` 与 `docker/minio/docker-entrypoint.sh`。Dockerfile 基于 `alpine:3.20`，构建参数固定 MinIO 版本与两个架构各自的 sha256；RUN 阶段按 `TARGETARCH`（缺失时回落到 `apk --print-arch`）选择对应 Release 资产，用带 `--retry-all-errors` 的 curl 下载后以 `sha256sum -c` 校验，校验不通过即构建失败。二进制落在 `/opt/bin/minio` 并加入 `PATH`；入口脚本保留“命令首项不是 `minio` 时自动前置”的语义，使 `CMD ["minio"]` 与两份 Compose 现有的 `command: minio server /data …` 都能工作。

`docker-compose.yml` 与 `docker-compose.prod.yml` 的 MinIO 服务增加 `build` 段，镜像名改为 `<项目名>-minio:RELEASE.2023-03-20T20-16-18Z`（项目名取 `COMPOSE_PROJECT_NAME`，默认 `yuxi`，与仓库其他自建镜像一致）；环境变量、卷、健康检查与 `command` 保持不变，因此既有数据卷和凭据无需调整。`docker/save_docker_images.sh|.ps1` 在导出前构建该镜像，并从 `docker compose config --images` 解析其镜像名：`.env` 里的 `COMPOSE_PROJECT_NAME` 不进 shell 环境，按它拼装出的名字会与 Compose 实际使用的名字漂移，目标机器上只能现场构建而离线环境无法构建。两个脚本同时切到仓库根执行并对构建失败显式退出。`scripts/init.sh|.ps1` 的预热步骤改为 `docker compose build minio`。

`scripts/ci_build_topology_images.sh` 把 MinIO 纳入预构建（新增第三个缓存目录参数），`system-tests.yml` 的两个 job 各增加一个恢复 `/tmp/yuxi-buildkit-cache/minio` 的 `actions/cache` 步骤；层缓存按 `docker/minio/` 下文件哈希失效，命中后不再重复下载二进制。

`docs/advanced/deployment.md` 的组件表把 MinIO 标注为本地构建并说明校验方式。该组件仍是 AGPL-3.0，再分发义务不因构建位置改变。

## 替代方案

- **改用另一个第三方 registry 上的现成镜像**。实测：`quay.io/bitnami/minio` 与 `quay.io/bitnamilegacy/minio` 对匿名拉取授予空权限，`quay.io/opendatahub/minio` 只有 2019 年的 tag，`alpine/minio` 仅有 `RELEASE.2025-10-15T17-29-55Z`，Docker Hub 的 `ghostwritten/minio` 虽标明同版本，其 tag 详情接口（`/v2/repositories/ghostwritten/minio/tags/<tag>`）的 `images[].digest` 为 `sha256:95f4a901…`，与本仓库在用的镜像 ID `sha256:400c20c8…`（`docker inspect` 报告）不一致；本机拉不到 Docker Hub，该比较未能同维度复核，只能作为不采用第三方重打包镜像的辅证。没有可用的同内容第三方来源。
- **用 quay.io 凭据拉取该仓库的现成镜像**。匿名 token 被拒，但 auth 端点仍正常签发 token，说明该仓库可能只是转为非公开而仍对授权用户开放。这要求每个部署环境与 CI 都持有并轮换凭据，而项目其余组件都是匿名可拉；在分发渠道明确收缩的前提下，把凭据变成部署前置条件的长期成本高于自建。
- **升到仍可获取的较新版本**。MinIO 在 2023 之后的社区版移除了管理控制台，`--console-address` 与 9001 端口的既有用法失去对应界面；`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` 等变量名在版本间的兼容性也需重新确认。这类变更改变所有既有部署的访问方式与数据，超出恢复镜像供给的范围。
- **改用其他 S3 兼容存储**（RustFS、SeaweedFS 等）。磁盘格式不同，既有数据卷需要迁移，代价高于恢复供给。
- **把镜像推送到项目自己的 registry**。托管位置可以独立于内容来源选择，但构建配方仍然必需，因为没有任何现成镜像可推。本提案先让构建发生在使用现场，避免引入一次必须人工完成、且源镜像只存在于推送者本机缓存的前置步骤。
- **把该版本的二进制提交进仓库**。仓库体积增加约 97 MB，二进制资产不适合随源码分发。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 构建出的镜像与原镜像运行同一二进制 | 下载到的资产与下架前镜像内的可执行文件不同 | `docker/minio/Dockerfile` 的 sha256 校验 | 自建镜像内 `/opt/bin/minio` 的 sha256 为 `df0de998…`，与下架前镜像同名文件、以及 Release 的 `.sha256sum` 声明三方一致；`minio --version` 给出同一 commit-id `05444a0f6af8389b9bb85280fc31337c556d4300` | 把 `MINIO_SHA256_AMD64` 改成其他值，构建在 `sha256sum -c` 处返回非零 | Passed |
| 自建镜像可以直接接管既有数据卷 | 磁盘格式或凭据语义变化导致既有对象不可读；本行无法用负向案例区分，只能由版本一致性与往返成功共同支持 | MinIO 自身的磁盘格式 | 同一数据目录：下架前镜像写入 `probe.txt` → 自建镜像读出该对象并写入 `probe2.txt` → 下架前镜像读出 `probe2.txt`，三步都返回预期内容，`/minio/health/live` 在每个阶段均可达 | 无可执行的负向案例：MinIO 读取旧盘格式是设计保证，换版本或伪造 `format.json` 的版本号都不会让它拒绝启动（后者已实测仍能启动） | Passed |
| 两份 Compose 与离线脚本不再引用外部 MinIO 镜像 | 仍有拉取路径在运行时失败 | `docker-compose.yml`、`docker-compose.prod.yml`、`docker/save_docker_images.*`、`scripts/init.*` | 对 `quay.io/minio` 与 `minio/minio` 做全仓符号搜索，只命中构建配方中的说明与下载地址；`docker compose config --images` 给出的 MinIO 镜像名跟随 `.env` 的 `COMPOSE_PROJECT_NAME`（实测设为 `alpha-dev` 时解析为 `alpha-dev-minio:…`），与 Dockerfile 产出名一致 | 项目名被改写时，按常量拼装的写法会与 Compose 实际使用的名字不符；两者取自同一个解析来源时不存在这个漂移 | Inspected |
| CI 的两条 job 恢复运行 | 拓扑仍起不来，或层缓存让构建被跳过而用了错误内容 | `scripts/ci_build_topology_images.sh`、`system-tests.yml` | 本 PR 的 workflow 运行结论，以及失败 job 的 step 编号（此前两条都停在 step 8 `Start … runtime topology`） | 缓存命中后仍执行 `docker compose up -d` 并进入其后的验证步骤 | Not run |

## 风险

构建需要在有网络的环境下载约 97 MB 二进制，并在 CI 冷缓存时增加该耗时；本地 Docker 层缓存与 buildkit 层缓存都能让后续构建命中。GitHub Release 是 MinIO 二进制当前唯一的官方来源，若它同样消失，构建配方需要新的下载地址与校验值，此时该记录应重新评估。

基础镜像从下架前镜像的 UBI 8 minimal 变为 `alpine:3.20`。MinIO 是静态链接的 Go 程序，不依赖基础镜像的 C 运行库；健康检查依赖的 `curl` 由 `apk` 显式安装。这两处差异已由数据卷读写与健康检查两项验证覆盖。未覆盖的是原镜像入口脚本中借助 `useradd`/`setpriv` 切换运行用户的分支——两份 Compose 都不使用 `MINIO_USERNAME`/`MINIO_GROUPNAME`，新入口脚本据此不再提供该能力；原镜像另附带 `mc`、`minisig` 与 `MINIO_*_FILE` 系列变量的默认值，新镜像不含这些内容，全仓搜索确认没有 consumer。

离线部署需要在有网络的机器上先构建再导出，`docker/save_docker_images.sh|.ps1` 已按此顺序调整；只用 `docker load` 导入他处 tar 的流程不受影响。

arm64 资产的下载与校验路径已用 `--build-arg TARGETARCH=arm64` 构建验证通过；该次构建走的是 legacy builder（本机未安装 buildx），`TARGETARCH` 为手工传入，CI 用 BuildKit 由平台自动提供该参数。本机为 amd64，未在 arm64 主机上运行该镜像。

镜像 tag 固定为 MinIO 的版本号、不随 `YUXI_VERSION` 变化，因此只改 `docker/minio/Dockerfile` 而不改版本号时，`docker compose up` 不会重建已有镜像；改动配方后需要显式 `--build`。

Dockerfile 中的版本号与校验值是外部事实的固化副本，MinIO 发布新版本时需要一并更新；不更新不影响既有部署。
