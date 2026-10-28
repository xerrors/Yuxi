# 单位目录用户计数改为单次聚合查询

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/identity/repositories/departments.py

## 问题

`GET /api/departments` 返回每个单位的 `user_count`，`DepartmentRepository.list_with_user_count` 先取全部部门，再对每个部门单独执行一次 `COUNT`（N+1）。查询次数随部门数线性放大，用户量大时单次请求耗时显著（6k 用户实测 ~200ms）。该接口被智能体"指定可见用户"等共享弹窗高频拉取，用户感知为弹窗打开慢。

## 决策

### 实现方案

`DepartmentRepository.list_with_user_count` 保持部门列表查询与 `created_at desc` 排序不变，改为追加一次 `SELECT department_id, COUNT(id) ... WHERE is_deleted = 0 AND department_id IS NOT NULL GROUP BY department_id` 聚合，在内存中按部门 id 回填 `user_count`（无用户部门计 0）。总计固定 2 次查询，与部门数无关。真实 HTTP 回归由 `system-tests.yml` 的部门 HTTP 步骤运行，回读新增与删除用户后的部门计数。计数口径不变：仅统计有效用户；响应字段、结构与排序不变，前端与管理端无需同步变更。

## 替代方案

- 数据库侧 LEFT JOIN + GROUP BY 单条 SQL：查询次数更少，但部门行与计数在同一条结果里重复序列化，与现有 `to_dict` 投影分叉更大；两次固定查询已满足目标。
- Redis 缓存计数：引入失效一致性成本，计数正确性 Owner 仍在 PostgreSQL，收益不匹配复杂度。

## 后果

接口耗时不再随部门数放大；`user_count` 口径（`is_deleted = 0`）与本文件其他用户计数查询保持一致，三处谓词调整需同步。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 计数不随部门数放大（固定 2 次查询） | 恢复逐部门 COUNT 循环 | `list_with_user_count` | `backend/test/unit/repositories/test_department_repository.py::test_list_with_user_count_uses_bounded_queries`（`_CountingSession` 计 execute 次数并断言各部门取值 1/0） | 恢复 N+1 循环后 `execute_count == 2` 断言失败（实测 1 failed） | Passed |
| `user_count` 增量等于新增有效用户数，删除用户不计入 | 聚合漏掉 `is_deleted` 过滤 | `GET /api/departments` 投影 | `backend/test/integration/api/test_department_router.py::test_departments_list_user_counts_aggregate_active_users`（独占 PostgreSQL、真实 TCP 路由与 JWT 鉴权；建 3 用户删 1 后断言 baseline+2） | 移除 `is_deleted == 0` 过滤时相同 HTTP 用例在最终计数断言失败（实测） | Passed |
| 回归不破坏既有部门行为 | 排序/字段投影漂移 | 部门路由与仓储既有单测 | `test_department_repository.py` 6 passed；`ruff check`/`format` 干净 | 不适用 | Passed |

未验证范围：完整 Compose 部门 HTTP 步骤的新增部门用例尚待 CI 执行，`Not run`；独立 HTTP 与 PostgreSQL 验证不替代完整拓扑回归。
