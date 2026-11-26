# Thread Input 观察生命周期收敛

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/model/useAgentInputQueue.js

## 问题

Thread 队列观察已共用一个轮询器，每个 Input 仍保存重复 controller 与空 timer，清理同时维护两层资源。getThreadInput 的调用方传入 signal，但 API 封装忽略第三个参数。Session 读取和等待恢复仍传递未被消费的 Agent ID；当前 Thread 状态组件还暴露没有生产消费者的当前 Thread fallback 与停止入口。

Input 领取后，轮询等待当前执行恢复。如果会话在等待期间重置并安装新的观察，旧轮询的完成或异常可能清理替代观察，或发布已经失效的输入错误。

## 决策

### 实现方案

每个 Thread 的 inputQueueMonitor 单独拥有 controller、timer 和 Input ID 集合。停止最后一个 Input 或释放 Thread 时只清理该 Owner。API 直接转发请求选项，取消实际到达 fetch。状态、历史和审批按明确 Thread 身份读取，删除无效 Agent 参数、无消费者停止入口与当前 Thread fallback。

执行重置取消旧观察的 controller。领取后的恢复完成或拒绝时，轮询检查自己的信号，只有仍有效的观察才移除本次 Input、发布错误或继续轮询。当前执行由持久 Turn 的恢复入口选择。

## 替代方案

- keep：维持两层监视资源和无效参数，拒绝。
- narrow：只删除空 timer，仍保留重复 controller 与取消信号断路，拒绝。
- replace：新增轮询服务或兼容适配器，没有必要，拒绝。
- remove：保留唯一 Thread 轮询 Owner，删除旧层及参数，采用。
- 另设观察版本也能辨别旧轮询，执行重置已经会取消原 controller，沿用其信号即可。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 同 Thread 的输入共享一个观察生命周期 | 重置误杀后续输入、遗留计时器 | Input queue / Thread state | 前端 unit | reset/cleanup 后 signal 和重试结果回读 | Passed：frontend unit 620 passed，含重置、交接、取消与在途返回隔离 |
| 领取后的执行恢复只影响自己的观察 | 旧恢复结束清理替代观察或发布过期错误 | Input queue | `cd frontend && node --test test/unit/agentInputQueue.test.js` | 重置后启动替代观察，再让旧恢复成功或拒绝，两种情况均回读新 Input 对应的消息条目 | Passed |
| 取消信号到达 HTTP 请求 | 封装丢弃 signal | agent_api | API unit | 恢复旧封装后请求仍存活 | Passed：旧封装未抛 AbortError；转发 signal 后精确负控通过 |
| Thread 身份独占读取路由 | 残留 Agent 参数和 fallback | sessionRuntime / stream / approval | 调用搜索与 unit | 旧能力不存在负向搜索 | Inspected：旧入口无消费者；frontend unit、lint 与 build Passed |

旧能力不存在：每 Input 的 controller/timer、getCurrentThreadId/onStopThread/stopThreadStream，以及状态读取的旧 Agent ID 参数无生产入口或 fixture。

重新引入条件：出现独立于 Thread 轮询的业务操作与清理责任，再以当前消费者和验证定义生命周期。

## 后果

Thread 拥有并发版本检查、Input 顺序、重连和视图释放隔离。执行重置后，替代观察继续处理新 Input 的领取结果。

验证命令：`docker compose exec -T frontend pnpm run test:unit`（620 passed）、`pnpm run lint:check` 和 `pnpm run build` 通过。`publicAgentApi.test.js` 以真实 AbortController 配合受控 fetch，验证 signal 透传和取消导致的 Promise 拒绝，不以 mock 调用次数代替取消结果。
