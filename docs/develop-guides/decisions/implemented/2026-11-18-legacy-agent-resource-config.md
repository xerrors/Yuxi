# 旧版智能体资源配置迁移

状态：implemented
类型：bug-fix
Owner：backend/yuxi/migrations/legacy_v073.py

## 问题

v0.7.3 智能体定义中的资源选择允许 `null`，表示全部可见资源；当前协议使用 `"all"`。Schema 迁移原样复制智能体配置，创建新会话时加载旧值触发 `ValueError` 并返回 500。历史会话快照的转换不能修复智能体定义。

## 决策

### 实现方案

迁移模块在复制智能体定义后转换显式资源 `null`：`tools`、`knowledges`、`mcps`、`skills` 转为 `"all"`，`preload_skills` 转为空列表。显式列表、缺省字段和其他配置保持原值。

已完成迁移的数据库通过 `--repair-agent-config` 显式审计，附加 `--apply` 后写入。修复复用 Schema advisory lock，在事务中锁定与旧归档 ID、slug 一致的智能体，仅转换仍为 `null` 且归档对应字段为 `null` 或缺省的值。没有对应归档的智能体以及用户已经修改的有效选择不变。当前运行协议与权限解析不增加兼容分支。

## 替代方案

- 只修改当前数据库：能恢复服务，但不能防止其他旧库迁移时复现。
- 运行时将所有 `null` 当成 `"all"`：扩大当前输入协议，掩盖持久配置错误。
- 将旧值改成空列表：把全部可见资源变成不选择资源，改变旧配置含义。

## 后果

`"all"` 恢复旧版动态资源选择含义，实际可用资源仍由当前用户授权解析。数据修复不重建 Schema，不修改历史会话、消息或旧归档。

运行时继续拒绝非法资源值；无法从旧归档证明来源的空值不自动修复。模型标识的可用性属于当前模型配置，资源迁移不替换模型或静默回退。

## 验证

- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_legacy_v073_schema.py -q -o cache_dir=/tmp/yuxi-legacy-pytest-cache`：16 passed。冻结旧 DDL 中的 `null` 在修复前触发精确配置断言失败；迁移后通过真实 PostgreSQL 回读和 HTTP 创建验证 Session、Receipt 及资源快照。
- 修复回归覆盖只读审计、重复执行、有效选择保留、归档缺失、错误版本、ID/slug 不匹配、非归档智能体及归档值不支持转换的负向案例。锁后复核用 PostgreSQL `pg_locks` 证明实际等待排他锁后再提交在途状态，不以固定等待猜测时序。
- `scripts/ci/run_service_integration.sh` 将该文件纳入现有 service integration workflow；对应 `ServiceIntegrationTests`：6 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：3823 passed，127 skipped；容器缺少仓库根文件的配置测试通过只读根挂载另行执行，结果为 200 passed、11 skipped、1 failed。失败来自既有 `backend/uv.lock` 中 `filelock` 的镜像源与官方源断言冲突，锁文件不在修复范围。
- 当前部署在数据库备份后显式修复 2 条智能体定义；重复 `--repair-agent-config --apply` 返回 0 个候选。真实 TCP HTTP 创建空会话返回 201，回读 Session、Receipt、资源快照及详情响应，随后清理合成验证资源。
- Ruff 源码与测试检查、工程信任检查及其 56 个单测、文档构建和 `git diff --check` 通过。Windows 缺少符号链接权限时，工程检查单测在只读根挂载的 Linux 容器执行。
- 未验证：真实模型生成、外部 MCP 调用和旧 checkpoint 执行兼容性；空会话创建不产生模型调用。
