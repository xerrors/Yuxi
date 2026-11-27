# Project 默认目录按日期分层

状态：archived
类型：feature
Owner：backend/yuxi/modules/workspace/paths.py

## 问题

默认 Project 目录把完整时间戳与八位标识放在同一级，目录名冗长，同一天的项目缺少目录分组。缩短标识后还需避免未物化的数据库绑定发生碰撞。

## 决策

### 实现方案

Workspace 路径 Owner 分配 `projects/YYYYMMDD/<Project UUID 前六位>`，日期使用上海时间；已占用路径按顺序添加 `-1`、`-2` 后缀。分配检查用户目录中的任意已有条目和该用户全部持久 Project 绑定。Project service 使用现有用户级事务锁串行化分配，repository 查询已绑定路径，事务提交后的目录物化继续由现有入口执行。保留已有时间戳目录的校验与读取，不迁移用户文件，不修改显示名称、Project ID 或 linked Project 行为。

## 替代方案

- 保留单层完整时间戳：名字冗长，不采用。
- 截短 UUID 而只检查磁盘：尚未物化的 Project 可能共享目录，不采用。
- 迁移历史目录：引入文件与数据库同步风险，无当前需求，不采用。

## 验证

- `docker compose exec api uv run --group test pytest test/unit/workspace/test_managed_workdir_paths.py test/unit/services/test_project_service.py -q`：42 passed；旧实现运行新增断言为 9 failed、33 passed。
- `docker compose exec api uv run --group test pytest test/integration/services/test_managed_project_allocation.py -q`：3 passed，回读 PostgreSQL 目录绑定与磁盘文件，覆盖未物化、软删除与并发碰撞。
- `docker compose exec api uv run --group test pytest test/unit -m 'not slow' -q`：2644 passed、59 skipped。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：Passed，后者 65 tests。
- 本机 `backend/.venv/bin/ruff` 对受影响 Python 文件的 check 与 format 检查通过。容器未安装 Ruff，容器命令未执行成功。
- HTTP integration：Not run。临时 API 使用 shipping lifespan 启动时因现有数据库 business=1、源码要求 business=2 而退出；未修改数据库或重启现有服务。E2E 同样未执行，运行中的旧进程未验证新目录格式。

## 后果

六位标识只用于可读目录名，不能作为全局身份或授权依据。重名后缀使少数路径长于六位。用户级事务锁延续至 owning transaction 结束；不在锁内创建文件或调用外部服务。
