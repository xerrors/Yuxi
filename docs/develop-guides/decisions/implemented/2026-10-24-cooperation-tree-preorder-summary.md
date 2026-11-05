# 协作树摘要按父子关系先序遍历

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/repositories/cooperation.py

## 问题

`CooperationRepository.summary` 原按 `cooperation_path` 字符串排序返回整树成员。`-`（0x2D）小于 `/`（0x2F），`/root/a-b` 排在 `/root/a/child` 之前；前端协作树按返回顺序渲染、按路径深度缩进，子会话在视觉上挂到错误的父节点。数据关系本身正确，仅展示层级错误。

## 决策

### 实现方案

`summary` 查询不再按 `cooperation_path` 排序，改由 `order_tree_members` 在内存中按 `parent_thread_id` 分组、从根会话先序遍历；兄弟按 `cooperation_name` 排序（与原路径序在兄弟间等价）。树的形状约束（单根、父必须在同树内）由 `ck_sessions_tree_shape` 与外键保证；当过滤条件（`status != "deleted"`）使某个后代不可达时，摘要显式抛出 ValueError 失败，不静默丢弃成员。输出顺序服务 HTTP 协作摘要与前端按序缩进渲染；模型工具采用独立的[项目与历史目录查询](./2026-11-05-session-directory-scope.md)。

## 替代方案

- 前端按 `parent_session_id` 自行构建树：多个消费端重复实现，且工具返回给模型的顺序仍由字符序决定。
- 数据库递归 CTE 按树遍历排序：成员集合一次全部载入，内存遍历更直接，无需 SQL 方言绑定。

## 后果

成员超过原分页上限时仍一次返回，仅排序位置改变。混合大小写兄弟名的相对顺序由数据库 collation 变为 Python 码点序，仅影响展示顺序。未来若引入会话软删除且不级联后代，摘要将显式失败而不是展示残缺树，该边界由负向测试锁定。

## 验证

- 真实 PostgreSQL 集成测试：创建 `a`、`a-b` 与 `a` 的子会话后，摘要顺序为 root、a、a 的子会话、a-b（旧实现返回 a-b 在 a 的子会话之前，负向验证确认）。
- 不可达成员负向测试：直写隐藏父成员后 `tree_snapshot` 抛 ValueError，不静默丢行。
