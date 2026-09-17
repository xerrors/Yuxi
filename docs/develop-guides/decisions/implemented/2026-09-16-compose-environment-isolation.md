# Compose 环境隔离与挂载对齐

状态：implemented
类型：feature
Owner：docker-compose.prod.yml

## 问题

生产 Compose 的固定容器名、数据目录和镜像名妨碍同机多环境部署。开发和生产配置需要一致的环境文件选择与 provisioner 默认网关设置。

## 决策

两份 Compose 使用 `YUXI_ENV_FILE` 选择容器环境文件；生产配置通过 Compose 项目名隔离容器、镜像和动态沙盒名称，通过 `YUXI_STATE_DIR` 选择宿主数据目录，通过端口变量选择宿主监听端口。生产 API 和管理端口绑定回环地址，Web 保留公开入口。

provisioner 在应用网络设置 `gw_priority: 1`。MinIO 容器数据目录与启动命令共同使用 `/data`，Neo4j 日志挂载使用 `/logs`，宿主数据位置的默认值保持不变。[生产部署说明](../../../advanced/deployment.md)拥有配置文件选择、版本要求与旧环境切换步骤。

## 替代方案

保留固定生产名称会继续与其他 Compose 项目冲突。直接照抄附件会使 Markdown URL 进入启动命令和健康检查，并将生产 API 默认发布到所有网卡。重复声明 provisioner 镜像已有的启动命令没有当前收益，因此沿用 Dockerfile 的 CMD。

## 后果

同目录并行运行必须显式区分项目、状态目录和端口。已有部署切换项目名或固定容器名时需要先清空运行中任务与沙盒，再用旧配置停机，避免两套进程写入同一状态目录。网关优先级要求 Docker Engine 28.0、Compose 2.33.1 或更高版本。`YUXI_ENV_FILE` 只选择容器注入文件，Compose 插值仍需配套 `--env-file`。

## 验证

- Passed：使用临时假凭据执行两份配置的 `docker compose --env-file <临时文件> -f <配置文件> --profile all config --format json`，回读默认环境文件、默认状态路径、端口覆盖，以及两组不同项目名与状态目录的解析结果；核对共享挂载、网关优先级、迁移依赖、生产回环绑定。生产必填凭据为空时解析拒绝成功。
- Passed：`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（62 项）、修改文档的相对链接检查、`cd docs && pnpm run build` 和 `git diff --check`。
- Not run：`docker compose exec -T api uv run --group test pytest test/unit -m 'not slow'` 因 API 服务未运行而无法执行。未启动或重建环境，未执行沙盒动态网络 E2E 或持久数据恢复演练；配置解析不证明这些运行时行为。
