# PostgreSQL 容器时区固定为 UTC

状态：implemented
类型：bug-fix
Owner：docker-compose.yml

## 问题

后端时间契约（`backend/yuxi/shared/datetime.py` 模块声明）为"按 UTC 存储、ISO 8601 对外"，但 `docker-compose.yml` 与 `docker-compose.prod.yml` 的 postgres 服务未设置 server 时区（容器默认 `TZ=Asia/Shanghai` 还会写入 localtime 日志）。数据库服务器、日志与依赖会话时区的 SQL（`now()::timestamp`、`CURRENT_DATE` 分桶、导出工具）因此可能把本地时间静默混入按 naive 解读的时间读写，部署机 locale 不同则形态不同，事后难以归因。

## 决策

两份 Compose 的 postgres 服务启动参数固定 `-c timezone=UTC -c log_timezone=UTC`，容器 `TZ` 默认同步为 UTC。没有显式覆盖的新连接继承服务器默认，不取决于数据库或数据卷的创建时间。

### 实现方案

时区参数放在 `max_connections` 之前，两份 Compose 的注释指向[部署时区检查](../../../advanced/deployment.md#检查-postgresql-会话时区)。应用时间读写代码不变。

## 替代方案

- 对每个数据库执行 `ALTER DATABASE … SET TimeZone`：重复设置不受覆盖的库，且无法消除优先级更高的角色、角色与数据库组合或客户端设置。不作为部署步骤。
- 在应用连接层逐会话 `SET timezone`：只影响应用自身，不覆盖日志、psql 排障与导出工具，且每次连接多一条往返。

## 后果

- 保留数据卷并以新参数启动 PostgreSQL 时，无覆盖的新会话直接得到 UTC；不会重写历史数据。已有数据库或角色的显式设置保存在 `pg_db_role_setting`，与客户端/会话设置一样能覆盖默认值。只按应用会话实查结果处理实际覆盖项。
- 依赖 PG 容器本地时间的既有自定义报表若曾默认 `Asia/Shanghai`，数值口径随之对齐 UTC；这正是要暴露而非保留的行为。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 容器按 compose 参数启动后 server/日志时区为 UTC，`now()::timestamp` 为 UTC 墙钟 | 默认随容器 TZ 变成 +08 本地时间 | docker-compose.yml postgres 服务 | `docker run -d postgres:16 postgres -c timezone=UTC -c log_timezone=UTC` 后 `psql -Atc "show timezone; show log_timezone; select now()::timestamp(0), extract(timezone from now());"` 输出 `UTC / UTC / extract=0`；两份 compose 经 `yaml.safe_load` 解析，command 与 `TZ=${TZ:-UTC}` 就位 | 移除两项 `-c` 后 `show timezone` 返回 `Asia/Shanghai`（默认随 `TZ`） | Passed |
| compose 配置守卫用例在参数调整后保持绿色 | 容量参数顺序契约漂移 | test/unit/config/test_docker_compose_service_boundaries.py | 时区两项 `-c` 置于 `max_connections` 之前，`pytest test/unit/config -k capacity` 4 passed | 把 `-c` 追加到末位后 `command[-1]` 断言红（CI 实测复现） | Passed |
| 保留原数据目录重启后，未覆盖的新连接自动继承 UTC | 把旧数据卷误当成显式覆盖，做不必要的 `ALTER` | PostgreSQL 16 会话默认值 | 在隔离 PostgreSQL 16.16 上用同一数据目录、应用登录角色、应用库重启：`SHOW TimeZone` 从 `Asia/Shanghai` 变 `UTC`，日志时区同步变 `UTC`，两类既有时间戳的存储值不变 | 角色与库组合显式覆盖仍优先，不能默认所有新连接都是 UTC | Passed |
| 只清理实际覆盖项，不影响无关连接 | 数据库设置掩盖客户端或更高优先级设置 | PostgreSQL 16 `pg_settings`、`pg_db_role_setting` | 在同一隔离库分别验证数据库、角色、角色与库组合和客户端覆盖；文档中的 SQLAlchemy 探针用实际登录角色、库和连接参数分别读出 `UTC` / `command line` 与 `Pacific/Auckland` / `client` | 按范围逐项重置，未改的覆盖项仍生效，无关角色和库的时区不变 | Passed |
| 应用启动、就绪与时间显示不受影响 | 时间契约声明与 DB 默认分裂 | backend/yuxi/shared/datetime.py | 部署演练由 CI 与维护者发布检查覆盖；本变更仅触及容器参数 | — | Not run |
