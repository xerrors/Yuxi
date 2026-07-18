# 生产部署指南

本文档介绍如何在生产环境中部署 Yuxi。

## 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

::: warning 注意事项
1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突
2. 虽然名为「生产环境」，但这只是基本配置，真正上线需要根据实际情况调整
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭
:::

## 部署步骤

### 1. 准备配置文件

为避免与开发环境冲突，生产环境建议使用 `.env.prod` 文件：

```bash
cp .env.template .env.prod
```

编辑 `.env.prod`，设置强密码和必要的 API 密钥：

```sh
POSTGRES_PASSWORD=
NEO4J_PASSWORD=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
JWT_SECRET_KEY=
YUXI_INSTANCE_ID=
SANDBOX_PROVISIONER_TOKEN=
SILICONFLOW_API_KEY=
```

生产 Compose 会在前七项配置缺失或为空时拒绝启动，并提示具体变量名。`JWT_SECRET_KEY` 和 `SANDBOX_PROVISIONER_TOKEN` 均应至少使用 32 字节随机值并持久保存，可分别使用 `openssl rand -hex 32` 生成；两者不能复用。`YUXI_INSTANCE_ID` 应是每套部署稳定且唯一的实例标识。模型 API 密钥按实际使用的供应商配置。

### 2. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose -f docker-compose.prod.yml up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose -f docker-compose.prod.yml --profile all up -d --build
```

### 3. 验证部署

- Web 访问：http://localhost（直接通过 80 端口）
- API 健康检查：`curl http://localhost/api/system/health`

公开头像和 Agent 图片通过前端同源路径 `/minio/public/...` 读取，由 Nginx 只读代理到 MinIO 的 `public` bucket。无需也不应向公网开放 MinIO 的 `9000` 对象 API 或 `9001` 管理控制台；知识库等私有 bucket 不经过这个代理。需要使用独立静态资源域名时，可在 `.env.prod` 中设置 `MINIO_PUBLIC_URL=https://assets.example.com`，并在该域名侧保持同等的只读 bucket 限制。

历史 PDF 解析 Markdown 中已经写入的 `http://localhost:9000/public/...` 或其他 `<host>:9000/public/...` 图片地址，会在前端渲染时自动转换为同源路径，不需要重新解析 PDF。

## 跨域（CORS）配置

`docker-compose.prod.yml` 默认把 `YUXI_ENV` 设为 `production`，后端在该环境下会按 `YUXI_CORS_ORIGINS` 显式声明允许的来源。**未配置时返回空列表，浏览器跨域请求会被拒绝**。生产部署前请根据前端与 API 的相对位置选择策略：

| 部署形态 | 推荐配置 |
|----------|----------|
| 前端与 API 同源（Nginx 同端口反代） | 不需要设置，留空即可 |
| 前端与 API 跨域部署 | `YUXI_CORS_ORIGINS=https://your-frontend.example.com` |
| 多个前端域名 | 逗号分隔，如 `https://a.example.com,https://b.example.com` |
| 完全放开（不推荐） | `YUXI_CORS_ORIGINS=*`，会自动关闭 credentials，登录态/JWT 无法跨域携带 |

开发环境（`YUXI_ENV=development` 且未设置该变量）默认允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`，方便本地前后端独立启动调试。从 0.7.0 升级到 0.7.1 时，如果此前是跨域部署但未显式声明来源，必须补上 `YUXI_CORS_ORIGINS`，否则前端跨域请求会被拒绝。

## 维护与更新

### 从使用默认凭据的版本升级

如果部署曾使用仓库历史默认的 PostgreSQL、Neo4j 或 MinIO 凭据，升级 Compose 文件本身不会保证已有数据卷中的服务凭据已经改变。升级前应分别通过对应服务的管理命令真实修改凭据，再把新值写入 `.env.prod`；完成后重建相关服务，并使用旧凭据验证登录已被拒绝。

PostgreSQL 可以在数据库容器内使用交互式命令修改，避免新密码出现在 shell 历史和进程参数中：

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U postgres -d yuxi -c '\password postgres'
```

Neo4j 应使用 `cypher-shell` 的当前用户密码修改流程；MinIO 应使用 `mc admin` 或部署所采用的密钥管理流程。不要把真实密码写入文档、测试脚本或命令历史。完成凭据轮换并配置 `SANDBOX_PROVISIONER_TOKEN` 后，再执行下面的重建命令。

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动
docker compose -f docker-compose.prod.yml up -d --build
```

生产 Compose 不再向宿主机发布 PostgreSQL 和文档解析服务端口。确需从宿主机维护时，优先使用 `docker compose exec`；不要为了临时调试把这些端口重新暴露到公网。

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod
```
