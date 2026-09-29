# 配置系统

Yuxi 的配置分成两类：启动进程时读取的环境变量，以及运行中由管理员在页面维护的系统配置。区分这两类，可以判断修改后需要保存、清缓存还是重启服务。本页拥有配置的生效规则；各主题的具体配置见对应 Owner 页面。

## 配置来源和优先级

对支持运行时修改的字段，生效顺序是：

```text
代码默认值 < 环境变量 < PostgreSQL 中保存的系统配置
```

数据库中保存的非空值优先于环境变量；布尔值和列表即使是 `false` 或空列表，也会被视为管理员明确保存的值。没有数据库值时，系统才读取对应环境变量或代码默认值。

启动期环境变量由 Docker Compose 注入 API、worker、provisioner 和依赖服务。它们决定服务地址、密钥、存储位置和沙盒承载方式。修改环境变量后需要重新创建读取它的容器；只执行 `docker compose restart` 不会更新容器环境：

```bash
docker compose up -d --force-recreate api worker
```

如果改的是 provisioner 变量，把服务名换成 `sandbox-provisioner`；生产环境还要带上 `--env-file .env.prod -f docker-compose.prod.yml`。

## 管理员系统配置

管理员在“设置 → 基本设置”中修改系统配置。当前配置项由 [`options.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/config/options.py) 定义，包含默认对话模型、快速响应模型、嵌入模型、重排模型和默认 OCR 解析引擎。

系统配置保存到 PostgreSQL 的 `config_options` 表。API 和 worker 通过 Redis 保存短期缓存；保存成功后缓存会失效，下一次读取从数据库取得新值。Redis 不可用时，读取路径回源 PostgreSQL，不把缓存当作最终事实。

运行中的已初始化组件不保证热更新。例如模型供应商使用的 API Key 环境变量、OCR 服务地址、沙盒连接和数据库地址变化后，应重新创建实际读取这些变量的 API、worker 或 provisioner。

## 各主题的配置入口

- 模型供应商、聊天模型、嵌入模型和重排模型：[模型配置](../intro/model-config.md)。
- OCR 引擎和各服务凭证：[文档处理与 OCR](./document-processing.md)，其中字段读取规则遵循本页的优先级。
- 沙盒应用层与 provisioner：[沙盒配置与运维](./sandbox-operations.md)。
- 环境变量集中在仓库根 `.env.template`，不另外维护重复配置清单；Agent 并发相关变量的联动核算见 [Agent 并发容量](./agent-concurrency-capacity.md)。

## 修改后的确认方式

修改系统配置后，重新打开配置页面确认保存值，并用一次真实请求确认行为。只看到 HTTP 200 或提示“保存成功”不能证明模型、OCR 或检索链路已经可用：模型在供应商页面执行连接测试后再发起一次真实对话，OCR 查看健康状态后解析一份测试文件，知识库回到文件列表确认状态和检索结果，沙盒检查 provisioner `/health` 后执行一次真实文件操作。

## 历史配置

旧版 `base.toml` 和 `SAVE_DIR` 不属于当前运行时配置来源。受支持的历史数据由一次性 storage migrator 处理；日常部署不要手动把旧文件复制回运行目录。升级步骤见[生产部署指南](./deployment.md)。
