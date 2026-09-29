# 用户管理使用服务端分页

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/repositories/user_repository.py

## 问题

设置弹窗的用户管理表格虽然显示分页控件，但前端 Store 会循环请求 `/api/auth/users` 的所有批次，再在浏览器中执行搜索、筛选和切片。用户量增长后，打开设置页会加载全部有效用户；后端数组响应也没有 `total`，无法证明分页边界。

## 决策

保留现有 `/api/auth/users` 数组契约供旧调用方使用，新增 `/api/auth/users/page` 管理表格契约。Repository 在有效用户范围内统一完成关键词、部门和角色过滤，并返回当前页与总数；Service 装配部门名称和分页元数据；Router 只校验 HTTP 参数和当前管理员可见部门。设置页仅保存当前页数据，翻页、修改每页数量和筛选时重新请求服务端。

## 替代方案

- 直接修改 `/api/auth/users` 返回对象：契约更少，但会破坏现有 Store、Debug 页面和集成测试对数组响应的依赖。
- 保留前端假分页：改动最小，但继续加载全部用户，无法满足按页加载。
- 只分页、不下沉筛选：搜索只能覆盖当前页，结果错误。

## 后果

- 新旧用户列表契约同时存在：`/api/auth/users/page` 服务设置页表格，原数组接口继续服务需要完整列表的旧调用方。
- 搜索、部门与角色筛选在数据库中先执行，再计算总数和分页；已删除用户不进入管理列表。
- 前端使用递增请求序号拒绝晚到响应，并在筛选、翻页、修改每页数量、创建、编辑和删除后重载当前服务端页。
- 当前页在数据变化后越界时，页码回退到最后有效页并重新加载。

## 验证

- `uv run --group test pytest test/unit/repositories/test_user_repository.py -q`：2 passed；负向数据包含已删除用户，并证明过滤先于分页。
- `docker compose exec api pytest test/integration/api/test_auth_router.py::test_admin_user_page_filters_before_pagination_and_excludes_deleted test/integration/api/test_auth_router.py::test_admin_can_create_and_delete_user`：2 passed。
- `docker compose exec api pytest test/integration/api/test_auth_router.py::test_admin_user_page_filters_before_pagination_and_excludes_deleted test/integration/api/test_auth_router.py::test_admin_can_create_and_delete_user test/integration/api/test_auth_router.py::test_department_admin_is_limited_to_own_department_users`：3 passed；同时证明普通管理员传入其他部门 ID 也不能扩大可见范围。
- 真实 HTTP 探针创建 25 个临时用户，接口返回第一页 20 条、第二页 5 条、两页 `total=25`；探针结束后通过正常删除接口清理，查询总数回到 0。
- 浏览器实际设置页首次请求 `offset=0&limit=20`，第二页请求 `offset=20&limit=20`；搜索“张文杰”时重置为 `offset=0` 并由服务端返回 1 条；切换到 50 条/页时请求变为 `offset=0&limit=50`。Console 为 0 warning，截图为 `/tmp/user-management-server-pagination.png`。
- 前端全量 Node 测试：92 passed；`pnpm run lint:check` 与 `pnpm run build` 通过。
- 后端非 slow unit：1549 passed。
- 合并运行认证、Dashboard 与部门 API integration 时 24 项产品断言均通过，但 3 个既有认证测试在 fixture teardown 中使用已经锁定或删除的 token 清理会话而报错；分页定向 integration 和真实 HTTP 证据不受影响。
