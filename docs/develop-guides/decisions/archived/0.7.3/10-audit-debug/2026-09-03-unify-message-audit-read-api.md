# 收敛线程 Message 审计读接口

状态：archived
类型：simplification
Owner：backend/server/routers/chat_router.py

## 问题

审计读取同时存在只返回 Model 审计的 `/api/chat/thread/{thread_id}/model-audits` 和返回 Model/Tool DTO 的 `/api/chat/thread/{thread_id}/audits`。调试面板只使用后者，前者额外保留一套 route、service、repository 查询、Web API wrapper 和重复测试，形成没有当前 consumer 的维护表面。

## 决策

- `/api/chat/thread/{thread_id}/audits` 是唯一的线程 Model/Tool 审计读接口，继续由 `get_superadmin_user` 和线程 Owner 查询执行权限。
- Model-only route、service、conversation 查询和 Web API wrapper 不保留；Model DTO、排序、权限和 metadata 收敛由统一接口测试验证。
- Model/Tool lifecycle 写入、Run lease、事务、显式 DTO、排序和调试面板合并行为保持原有 Owner。

## 替代方案

- 保留两个接口：没有独立 consumer，也没有不同的权限、DTO 或性能边界，只会扩大兼容面。
- 让 `/model-audits` 重定向或转发到 `/audits`：仍保留无 consumer 的 URL 契约，且响应从 Model-only 变为 Model/Tool，语义不再匹配名称。
- 新建通用审计 repository：当前联合查询已经由 Tool 审计 repository 拥有，引入新抽象不会减少运行路径。

## 后果

- 调试面板和当前客户端只维护一个审计请求及一套显式 DTO。
- 直接调用 `/model-audits` 的客户端会收到 404；该接口未发布，也没有仓库内 consumer 或兼容承诺。
- 统一接口返回最新 500 条 Model/Tool 时间线，并用 `truncated` 明示更早事实未包含在当前响应中；分页或增量刷新仍需单独定义现有行终态更新的游标语义。

## 验证

- 旧能力不存在：Model-only route、service、conversation query 和 Web client wrapper 均已删除；重复正向测试已收敛到统一接口，保留旧 URL 返回 404 的负向守卫。
- 统一接口的 backend unit 保留 Model/Tool DTO、时间、usage、排序和 metadata 收敛断言；真实 HTTP integration 保留 401、403、跨用户 404、持久化顺序与 History 隔离，并验证旧 URL 返回 404。
- 重新引入条件：出现需要独立权限、独立字段投影或独立性能边界的真实 Model-only consumer，并为新增公开契约提供对应测试和文档。
- 相关 backend unit、Ruff/format、Web lint/unit、工程契约检查与 docs build 通过；真实 PostgreSQL lifecycle integration 与真实 HTTP audit integration 在隔离数据库和 API 容器中通过。
