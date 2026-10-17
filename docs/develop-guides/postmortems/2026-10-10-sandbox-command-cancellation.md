# 长命令取消后 runtime 清理阻塞

日期：2026-10-10
Owner：backend/package/yuxi/agents/backends/sandbox/backend.py
关联决策：[沙盒命令取消与有界清理](../decisions/implemented/2026-10-10-sandbox-command-cancellation.md)

## 影响

一次实际部署发生长脚本停止后其他会话结束显示与队列状态不一致：回答已经结束，新消息继续排队。已确认命令调用在取消后持续到原始超时，Run 的 runtime 清理未完成；provisioner 曾约 30 分钟无法通过健康检查。未发现数据泄露或越权证据。受影响用户总数未知。

## 事实时间线

- 两条命令分别设置 1800 秒和 3600 秒超时，用户在超时之前点击停止；数据库先记录取消，执行调用之后才达到原定超时。
- 第二条调用达到超时后约 1 秒，provisioner 恢复响应；随后同批 pending runtime cleanup 标记清除并发布终态事件。
- 修复后用户在真实浏览器再次执行两次长脚本并停止。终止接口均约 0.2 秒响应，沙盒删除和终态事件均约 3 秒完成；同一对话随后可运行新任务。

## 因果链

`SandboxBackendProtocol.aexecute()` 默认把同步 `execute()` 放入线程。取消协程不会中断线程或远端命令。provisioner 删除等待长代理请求的 pin 排空，新发现等待删除栅栏；这些同步等待没有超时。终态但清理未完成的 Run 仍拥有 FIFO fence，前端轮询只检查终态则提前显示结束。

反复清理和发现请求耗尽同步线程池与现象吻合，且修复增加了线程池占满时健康检查的直接负向测试。故障当时没有捕获线程栈，因此不能把线程池耗尽写成已现场证明的事实。

## 安全网为何漏过

既有取消测试关闭的是异步生成器，没有证明同步线程中的真实 GEM 命令退出；清理测试只覆盖成功排空，缺少超时和重复删除场景。前端未把 durable cleanup 纳入终态投影。

## 修正与验证

修复 Owner、取舍和证据边界见[决策记录](../decisions/implemented/2026-10-10-sandbox-command-cancellation.md)。`test_sandbox_command_cancellation.py` 的旧实现不能进入原生异步取消路径；Web 的两个 cleanup-pending 负向用例在旧实现下提前清空 active Run，修复后保留订阅。

`test_sandbox_cancel_lifecycle.py` 在真实 HTTP、数据库、Redis、worker 执行函数和 GEM 边界回读 PID、文件、清理标记、generation 与 end 事件；模型与身份测试依赖使用固定输入，不能充当完整 assembled-path E2E。当前环境的上游 PostgreSQL 和共享 ARQ E2E 为 Not run。

## 防复发措施

- 命令取消由明确 session 的远端终止操作拥有；负向测试拒绝取消后残留进程或启动与 kill 乱序。
- provisioner 操作锁等待有界；负向测试拒绝忙碌删除假成功、误释放其他删除者栅栏和 pin 泄漏。
- 健康检查独立于同步线程池；真实 HTTP 负向测试在同步池占满时仍要求及时响应。
- API 与 Web 共同投影 runtime cleanup；回归拒绝清理完成前的空闲判断和提前终态。

## 未解决风险

没有复刻生产现场线程栈。Kubernetes、真实外部模型、上游 PostgreSQL 完整运行链路尚未在当前环境执行。已完成的文件写入和外部操作不会随取消回滚。
