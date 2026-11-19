# PostgreSQL 容器时区固定为 UTC

状态：implemented
类型：bug-fix
Owner：docker-compose.yml

## 问题

后端时间契约（`backend/yuxi/shared/datetime.py` 模块声明）为"按 UTC 存储、ISO 8601 对外"，但 `docker-compose.yml` 与 `docker-compose.prod.yml` 的 postgres 服务未设置 server 时区（容器默认 `TZ=Asia/Shanghai` 还会写入 localtime 日志）。数据库服务器、日志与依赖会话时区的 SQL（`now()::timestamp`、`CURRENT_DATE` 分桶、导出工具）因此可能把本地时间静默混入按 naive 解读的时间读写，部署机 locale 不同则形态不同，事后难以归因。

## 决策

compose 的 postgres 服务启动参数固定 `-c timezone=UTC -c log_timezone=UTC`，容器 `TZ` 默认同步为 UTC；新库自动继承 server 默认。

### 实现方案

两份 compose 的 postgres.command 追加两项 `-c`，`TZ=${TZ:-Asia/Shanghai}` 改为 `TZ=${TZ:-UTC}`，并在 command 上方注释存量库需要的一次性 `ALTER DATABASE yuxi SET timezone = 'UTC'`（幂等，随 datconfig 永久）。应用侧时间读写代码不变。

## 替代方案

- 只在文档要求运维手动 `ALTER DATABASE`：新环境默认仍随容器时区漂移，漏执行即埋雷；compose 单点保证后该 SQL 降级为存量纵深防御。
- 在应用连接层逐会话 `SET timezone`：只影响应用自身，不覆盖日志、psql 排障与导出工具，且每次连接多一条往返。

## 后果

- 已有数据卷重建容器后 server 默认变 UTC，但既有数据库的 `datconfig` 不变：存量库仍需部署者显式执行一次 `ALTER DATABASE ... SET timezone = 'UTC'`（不改数据，只改默认解析）。
- 依赖 PG 容器本地时间的既有自定义报表若曾默认 `Asia/Shanghai`，数值口径随之对齐 UTC；这正是要暴露而非保留的行为。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 容器按 compose 参数启动后 server/日志时区为 UTC，`now()::timestamp` 为 UTC 墙钟 | 默认随容器 TZ 变成 +08 本地时间 | docker-compose.yml postgres 服务 | `docker run -d postgres:16 postgres -c timezone=UTC -c log_timezone=UTC` 后 `psql -Atc "show timezone; show log_timezone; select now()::timestamp(0), extract(timezone from now());"` 输出 `UTC / UTC / extract=0`；两份 compose 经 `yaml.safe_load` 解析，command 与 `TZ=${TZ:-UTC}` 就位 | 移除两项 `-c` 后 `show timezone` 返回 `Asia/Shanghai`（默认随 `TZ`） | Passed |
| compose 配置守卫用例在参数调整后保持绿色 | 容量参数顺序契约漂移 | test/unit/config/test_docker_compose_service_boundaries.py | 时区两项 `-c` 置于 `max_connections` 之前，`pytest test/unit/config -k capacity` 4 passed | 把 `-c` 追加到末位后 `command[-1]` 断言红（CI 实测复现） | Passed |
| 应用启动、就绪与时间显示不受影响 | 时间契约声明与 DB 默认分裂 | backend/yuxi/shared/datetime.py | 部署演练由 CI 与维护者发布检查覆盖；本变更仅触及容器参数 | — | Not run |
