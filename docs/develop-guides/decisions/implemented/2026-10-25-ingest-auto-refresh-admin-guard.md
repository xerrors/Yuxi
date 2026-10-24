# 入库领取间隙内自动刷新保持（admin）

状态：implemented
类型：bug-fix
Owner：web/src/stores/database.js

## 问题

知识库文件入库任务提交后、worker 领取前，文件状态停留在 `uploaded`（`isProcessingFile` 不计入处理中），`loadDocumentFiles` 的自动刷新判定因此立即关闭轮询；入库完成后列表无人刷新，用户看到的是过期状态。既有 stale 退避兜底同样会在处理计数长时间稳定（等待领取）时关闭轮询，即使任务中心仍有活跃任务。

## 决策

### 实现方案

`database.js` 两处守卫接入 `taskerStore.hasActiveTasks`（tasker 补导出该状态）：自动刷新关闭判定中，admin 且任务中心存在未终态任务时跨过领取间隙保持轮询；stale 退避兜底对同样条件不再关闭轮询。任务到达终态后两处守卫自然失效，轮询按既有条件收敛停止。可见性依据是既成事实：任务登记与轮询终态解析（`createTaskRegistration`/`syncPolling`）本就限定 admin，非 admin 没有可观察的任务事实，维持既有停止行为——其提交后的刷新依赖手动动作，为已知边界，扩展非 admin 任务可见性需要后端 `tasks` 归属与授权演进，不在本变更范围。

## 替代方案

- 把 `uploaded` 计入处理中状态：拒绝。`uploaded` 是允许长期停留的待解析状态，会导致含未入库文件的库永久轮询。
- 非 admin 也登记本地任务并给予有界宽限：拒绝并入后续变更考虑。任务中心对非 admin 无终态来源，宽限窗口只是把无限轮询换成窗口猜测，且需要改动 tasker 的登记 gate（上游有意仅 admin），收益不匹配。

## 后果

admin 提交入库后列表轮询持续到任务终态，无无限轮询风险（终态由任务中心轮询保证）。`hasActiveTasks` 来自 admin 全局任务视图（任务中心列表 + 本地登记），与本库无关的长任务（图谱构建、评估等）活跃期间同样保持该列表轮询（间隔 2x 退避封顶 30 秒，由任务终态收敛），与 tasker 自身"存在活跃任务持续轮询"的既有设计一致。非 admin 行为与基线零差异，并由专项用例钉住守卫不为其延长轮询。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| admin 任务活跃期间（含超过 stale 上限）自动刷新不停止，任务终态后停止 | 领取间隙直接关闭轮询，入库完成后列表过期 | `database.js` 两处 `hasActiveTasks` 守卫 | `web/test/unit/database_auto_refresh.test.js`（vite SSR + fake timers 驱动真实 store）；`pnpm run test:unit` 全量通过、eslint 改动文件无输出、`pnpm run build` 通过 | 去掉任一处守卫后对应用例阶段失败（首轮/超 stale 上限轮次即停止） | Passed |

| 非 admin 不因守卫延长轮询（与基线零差异） | 守卫误对无终态来源的角色延长轮询 | 两处 `userStore.isAdmin` 短路 + tasker admin-only 登记 gate | `database_auto_refresh.test.js` 非 admin 用例（uploaded 间隙后第一轮即停止） | 契约钉界：当前 tasker gate 下移除 `isAdmin` 短路行为不变（实测用例仍过）；若未来放开非 admin 本地任务登记而守卫不设角色条件，该用例失败于轮询永不停止 | Passed |

未验证范围：真实浏览器中入库全流程观察 `Not run`；非 admin 提交后的手动刷新边界属既有行为，本变更不改变。
