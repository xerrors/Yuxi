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

- `NEO4J_PASSWORD`：修改默认密码
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：修改默认密钥
- `SILICONFLOW_API_KEY` 等模型密钥
- `YUXI_SUPER_ADMIN_NAME` / `YUXI_SUPER_ADMIN_PASSWORD`：建议显式配置初始管理员

不要把开发或测试环境里的占位值带到生产环境，例如：

- `SILICONFLOW_API_KEY=dummy-for-tests`
- `TEST_USERNAME`
- `TEST_PASSWORD`

生产环境必须使用真实可用的模型 provider key，否则聊天链路虽然能启动，实际调用模型时仍会失败。

### 2. 校准默认智能体模型

升级旧实例时，这一步是必须的。

如果数据库中的默认智能体配置仍然指向已经不存在的 provider，例如 `openai_local/gpt-5.4`，容器启动后会在聊天阶段报错：

```text
Unknown model provider: openai_local
```

可先进入数据库检查当前配置：

```bash
docker exec postgres psql -U postgres -d yuxi_know -c \
  "select id, name, config_json->'context'->>'model' as model, config_json->'context'->>'subagents_model' as subagents_model from agent_configs order by id;"
```

如果仍是旧 provider，请改成当前系统内置且你已经配置了密钥的模型。以 `siliconflow` 为例：

```bash
docker exec postgres psql -U postgres -d yuxi_know -c "
update agent_configs
set config_json = jsonb_set(
    jsonb_set(config_json::jsonb, '{context,model}', '\"siliconflow/Pro/deepseek-ai/DeepSeek-V3.2\"'),
    '{context,subagents_model}',
    '\"siliconflow/Pro/deepseek-ai/DeepSeek-V3.2\"'
)::json
where id = 1;
"
```

如果你的默认配置不是 `id = 1`，请按上一步查询结果替换目标记录。

### 3. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose -f docker-compose.prod.yml up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose -f docker-compose.prod.yml --profile all up -d --build
```

### 4. 验证部署

- Web 访问：http://localhost（直接通过 80 端口）
- API 健康检查：`curl http://localhost/api/system/health`
- Docker 状态：`docker ps`
- API 日志：`docker logs -f api-prod`

建议至少完成一次真实业务验证：

1. 登录系统。
2. 上传一个 `docx` 文件，确认附件状态为 `parsed`。
3. 发送“请读取我刚上传的文档并总结”。
4. 确认智能体能继续读取转换出的 `.md` 文件，而不是只在文件树中显示。

## 维护与更新

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动生产容器
docker compose -f docker-compose.prod.yml up -d --build
```

### 从旧版本升级到新版本

如果机器上已经跑着旧版容器，推荐按下面的顺序切换，避免混用旧网络和旧镜像：

```bash
# 进入新代码目录
cd /path/to/Yuxi

# 准备并检查生产环境变量
cp .env.template .env.prod

# 停掉旧的生产容器
docker compose -f docker-compose.prod.yml down

# 构建并启动新版本
docker compose -f docker-compose.prod.yml up -d --build
```

如果你是从开发 compose 切到生产 compose，也应确保当前运行中的容器来自同一套 compose 文件，不要让旧的 `api-dev` 和新的 `api-prod` 同时接管同一份生产数据目录。

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod
```
