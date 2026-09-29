# 按请求读取 Agent 调用结果

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/agent_request_service.py

## 问题

普通调用可以先排队为 Request，之后才有 Run。现有查询分别按 Request 和 Run 读取，agent-call 的结果入口只接受 Run ID。调用方在排队、SSE 断开或失败时需要自行拼接状态，容易把相邻 Run 的结果错配。

## 决策

`GET /api/agent/request-result?request_id=...` 从当前用户的持久 Request 读取排队、拒绝和取消状态；派发后只从 `dispatched_run_id` 指向的 Run 读取执行状态、输出与 usage。查询同时返回 Request 与 Run 的原始状态，并把执行中的 Run 投影为 `in_progress`、中断 Run 投影为 `waiting`。没有完整 usage 时返回 `null`。提交回执携带查询地址，Web 与 CLI 在 Request SSE 断开后回读结果。现有路由和响应字段继续可用；审批恢复继续作为独立 Run 保留父子关系。

## 替代方案

- 各客户端分别查询 Request 与 Run：重复拼接并继续承担错配风险。
- 同时统一输入、SSE 与 session/turn 表：会牵动上传、worker 和客户端，超出当前查询收益的范围。

## 后果

状态视图只在读取时计算，不保存第二份业务状态。Run 缺失或与 Request ID 不符时显式返回冲突，跨用户请求返回 404。现有 `request_id` 去重不能证明同键同输入，完整幂等需单独实现。当前结果只投影普通请求绑定的 Run；审批 resume 的独立 Run 仍按原有父 Run 关系读取。

## 验证

- `backend/test/unit/services/test_agent_request_result.py` 覆盖排队不读取相邻 Run、跨用户拒绝、绑定 Run 及错配拒绝。
- `backend/test/integration/api/test_agent_request_result.py` 通过真实 HTTP 和 PostgreSQL 覆盖同线程相邻请求、排队空 Run、派发后输出及跨用户 404。
- `backend/test/e2e/test_deterministic_agent_path_e2e.py::test_deterministic_agent_path_reaches_persisted_result` 通过真实 worker、SSE 和持久结果核对两次顺序调用。
- `web/test/unit/agentRequestQueue.test.js` 与 `packages/yuxi-cli/tests/test_chat_web.py` 覆盖 Request SSE 断开后的查询恢复。
