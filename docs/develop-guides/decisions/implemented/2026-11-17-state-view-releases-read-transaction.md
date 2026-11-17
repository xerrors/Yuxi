# /state 读取在 checkpoint 反序列化前结束请求级读事务

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/checkpoint_state.py

## 问题

`GET /api/v1/agents/sessions/{id}/state` 注入请求级 `Depends(get_db)`。`get_agent_state_view`
先执行两条业务 SELECT，再调用 `_read_checkpoint_state`：这一步走 LangGraph saver 的独立连接池，
在 api 进程内拉取并反序列化整条 checkpoint，线程越大耗时越长。请求级会话自第一条 SELECT 起持有事务，直到
响应结束才提交，因此这段非 DB await 期间连接停在 `idle in transaction`：连接池被长读请求占住，
且该快照会阻塞其所覆盖表的 autovacuum；历史线程越多，单次读取越长，占用越久。

## 决策

在进入 checkpoint 读取之前提交请求级只读事务，把连接归还池；响应装配所需的对象在提交前已经读取完毕。

### 实现方案

- `get_agent_state_view` 在 `_read_checkpoint_state` 之前 `await db.commit()`。会话工厂保持
  `expire_on_commit=False`，因此提交后 `agent_session`、`latest_run` 的已加载属性仍可直接读取，
  中断门与 `include_relations` 的关系快照不受影响。
- 提交之后的 `AgentTurnRepository.get_for_scope` 与 `tree_snapshot` 在同一会话上自动开启新事务；
  该读路径不产生业务写入，提前提交不改变可见性承诺。
- 该服务的另一个调用方是 worker 的 token 用量读取，它使用自管短会话且只读，行为不变。

## 替代方案

- 让 `/state` 路由不注入 `Depends(get_db)`、在服务内部自管短会话：把非 DB await 与请求级会话彻底解耦，
  但要改动两个调用方与全部测试替身签名，超出本缺陷所需；若该读路径继续出现更多非 DB await，再按同一
  模式收敛。
- 仅在 PostgreSQL 侧设置 `idle_in_transaction_session_timeout`：会把大线程的合法读取掐成错误，掩盖
  边界而不修复机制。
- 维持现状：线程历史规模增长后，突发打开历史会话会持续占用连接并延迟 autovacuum。

## 后果

- `/state` 的 checkpoint 反序列化不再占用池连接与事务快照；单次请求的连接持有时间缩短为实际 SQL 时间。
- 会话/Run 读取与 Turn/关系读取不再处于同一快照。中断载荷仍受既有门约束（`turn.current_run_id == latest_run.id`
  且 `waitpoint.run_id` 同一 Run），不会把别的 Run 的中断当作当前；跨快照的可见性差异按既有终态语义处理。
- 该读路径的测试替身必须提供 `commit`，不接受 `None` 或裸对象。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| checkpoint 读取期间请求级会话不再持有事务 | 反序列化期间连接停在 idle in transaction | checkpoint_state.get_agent_state_view | `pytest test/integration/services/test_state_view_read_transaction.py`（真实 PG 会话 + 真实 saver 写入的 checkpoint，`include_relations` 两态） | 移除该 `await db.commit()` 后两用例以「checkpoint 读取期间请求级会话仍持有事务」失败 | Passed |
| 提交点位于两次授权读取之后、checkpoint 读取之前 | 把 commit 挪到 SELECT 之前，事务被自然重开、缺陷复活 | 同上 | `pytest test/unit/services/test_checkpoint_state_reader.py::test_state_view_releases_read_transaction_before_checkpoint`（事件序列 `session-read → run-read → commit → checkpoint`） | 摘除 commit 后该用例红；把 commit 移到 SELECT 之前同样红 | Passed |
| 响应语义不变（面板字段、中断门、关系快照、提前提交后同一会话仍可继续查询） | 提前提交改变载荷、可见性或让后续查询失败 | 同上 | `test_state_view_reads_persisted_fields_without_agent_runtime`、`test_get_agent_state_view_returns_interrupt_only_for_waiting_owner`、真实 PG 集成同上 | — | Passed |
| 其余读路径回归 | 替身签名变化引入连带失败 | — | `pytest test/unit/services -m "not slow"` = 1196 passed | — | Passed |
| 门禁 | — | — | `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`、backend 三项 ruff、`git diff --check` | — | Passed |

未执行：真实 HTTP 的 `test/integration/api/test_checkpoint_state_view.py` 与其余需要 api 活体的集成用例，在本地
开发槽位因测试管理员凭据未播种（`/api/auth/token` 401）全部阻塞，与本次改动无关；该面由上述真实 PG 服务层集成
覆盖同一提交点，端到端表现留给 CI 证明。
