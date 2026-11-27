# 应用共享初始化与运行进程分离

状态：implemented
类型：simplification
Owner：backend/yuxi/bootstrap/app.py

## 问题

API 和 worker 启动都同步系统配置、内置 MCP 与 Skills，发布内容的持久化写入随运行进程重复执行。数据库准备与这些共享内容缺少统一完成边界，初始化失败和业务进程启动相互耦合。

## 决策

### 实现方案

无网络的 root 进程 storage-init 准备[部署所需的目录权限](../../../advanced/deployment.md#_2-首次启动)。Compose 的 app-init 复用 API 镜像和普通用户身份，仅挂载内置 Skill 来源存储，等待 PostgreSQL 健康和 storage-init 成功。入口显式装配全部 ORM 模型，在数据库 advisory lock 内顺序完成 Schema、配置定义、内置供应商、Agent、MCP 与 Skill 同步；领域服务继续拥有管理员字段保留和同步规则。Schema 初始化只为空库建表，兼容的现有库直接进入共享同步，历史版本按[部署指南](../../../advanced/deployment.md#_3-数据库与文件目录)离线升级。初始化失败只清理已确认空库的半成品；后续共享内容失败保留已完成数据，重跑幂等收敛。

API 和 worker 等待 app-init 成功，启动保留实际进程配置校验、Schema 版本校验、缓存及客户端创建和恢复检查。共享内容每次部署显式运行，开发修改内置定义后也重新执行 app-init。配置校验与可变外部服务 readiness 继续由消费进程负责。内置 MCP 发布定义同步失败同样阻止部署；此步骤不连接外部 MCP，其连接与调用仍按启用状态执行。

## 替代方案

- 保持 API/worker 重复写入：共享定义的部署完成时点不明确，增加重复文件替换和并发维护成本。
- 合并 storage-init：引入 root、数据库网络及凭据的混合职责，扩大 Milvus 的启动依赖。
- 全部检查集中初始化：不能证明后续 API/worker 的实际配置或动态依赖仍可用。

## 后果

共享同步包含多次数据库提交与 Skill 文件替换，不承诺单事务原子性；部署停止消费者，失败修正后重跑。管理员字段按领域现有契约保留，代码拥有的内置内容会刷新。初始化完成后的动态依赖可用性仍由运行时检查证明。无新增权限配置、初始化插件或全局 Token 探测。

## 验证

旧能力不存在：Compose 无 schema-init 服务，API/worker 启动不调用共享持久定义同步。

重新引入条件：只有内容确实由独立进程私有持有或必须随运行时状态重建，才进入该进程启动生命周期。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 共享内容完成后才启动消费者 | 服务依赖缺失或仅等待 started | Compose、bootstrap/app.py | config unit、真实 Production | 删除完成依赖或恢复运行进程共享写入使 guard 失败 | Passed：159 项聚焦 unit/config |
| 重复执行保留管理员值并收敛内置定义 | 覆盖配置、重复记录、文件不一致 | 各域同步服务 | 隔离 PostgreSQL 与临时 Skills 回读 | 非空管理员值、不同启用状态与损坏内置文件 | Passed：真实 PostgreSQL integration |
| Schema 与共享初始化串行且失败可重试 | 并行写入或失败后误清空现有数据 | app 入口、migrations | 真实 PostgreSQL 故障与并发测试 | 不兼容版本、共享阶段故障、移除冷启动 ORM 装配 | Passed：23 项 integration，包含独立子进程正负控 |
| API/worker 保留配置校验和运行恢复 | 只在初始化检查导致运行期绕过 | bootstrap/api.py、worker.py | unit、真实 Production 新任务和旧数据回读 | 无效密钥、错误 Schema 拒绝启动 | Passed：真实模型、worker、Cube 文件与 PG 回读 |

- `uv run --project backend --no-sync --group test pytest backend/test/unit/config backend/test/unit/services/test_schema_bootstrap.py backend/test/unit/services/test_worker_bootstrap.py backend/test/unit/utils/test_lifespan.py -q`：159 passed。
- `docker compose exec -T api uv run --group test pytest test/integration/services/test_app_initialization.py test/integration/services/test_schema_migration_version.py -q`：23 passed。测试使用隔离 Schema 和临时 Skill 目录；`system-tests.yml` 执行同一集合。
- `docker compose exec -T api uv run --group test pytest test/unit -m 'not slow'`：2727 passed、96 skipped、7 subtests passed；仓库根配置检查的容器 skip 由宿主聚焦集合补跑。工程契约及 66 项检查器单测、docs build、改动源码与新增测试的限定集合 Ruff 检查、`git diff --check` 通过。
- Production 当前数据库冷启动与重复执行 app-init 均退出 0；实际用户为 1000:1000，仅挂载 Skill 来源目录。部署前后数据库回读确认五类共享记录身份、选取的管理员字段摘要与旧 Turn/Run 保持原值；内置 Skill 内容摘要保持原值。原 schema-init 容器已移除。
- Production 登录、readiness、vector/keyword/hybrid 检索和真实模型会话通过。PG 回读证明新 Turn、结果 Run、输出消息关联与完成状态正确；实际工作区回读新文件字节为 `APP_INIT_OK` 加换行，旧验证文件字节保持原值。Milvus 仍为 UID 999、两挂载根 999:999/0755 且可写。

未验证 Windows 原生 PowerShell、公网 TLS、GPU OCR、原生 Cube、rootless/userns/NFS 映射及容量与真实备份恢复。
