# 审批失败保留当前运行状态

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/model/useThreadTurnAction.js

## 问题

审批提交期间，状态同步可以推进到新 Run、新等待点或终态。提交请求随后失败时，恢复提交前保存的等待点会让旧审批重新出现；无条件清除运行反馈也会遮蔽新 Run 的执行状态。

## 决策

### 实现方案

单 Thread 的人工操作绑定当前 Thread、Turn、Run 与等待点。审批恢复命令由 `sessionCommands.js` 负责。收到接收回执且目标仍有效时，操作控制器消费原审批并把后续执行交给共享运行内核。失败处理保留有效等待点的答案和错误提示，提交标记随本次操作结束。

接口接收前失败时，原审批尚未清除，可以继续重试。请求期间收到的新等待点或运行进展保留；审批已接收后，历史或连接错误也不能复活已消费的等待点。共享运行内核继续拥有运行状态同步和断线恢复，本决定不改变后端审批协议或持久化边界。

## 替代方案

为失败回滚补充版本、Run 和等待点校验，可以阻止过期写入，但仍保留没有必要的回滚路径。由于接口接收前原审批始终存在，删除回滚即可满足重试和并发状态保留的要求。

## 后果

审批失败不再把本地旧快照当成当前事实。正常失败仍显示原审批和错误提示；若状态已经推进，页面保留新状态。该局部修复没有需要先裁决的新机制或外部行为取舍，因此直接记录为 implemented。

## 验证

- [组件提交回归测试](https://github.com/xerrors/Yuxi/blob/main/frontend/test/unit/sessionApprovalSubmission.test.js) 执行真实提交函数和审批状态投影，验证原审批可重试、新 Run、终态、新等待点以及已接收后的连接失败。
- 使用修复前的组件执行相同测试，正常重试用例通过，四个过期回滚场景失败。
- [浏览器回归](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/sessionApprovalFailure.js) 在真实会话页面使用确定性 HTTP fixture 检查延迟失败后的 DOM、当前 Run、等待点与运行反馈。新状态由外部恢复触发，真实后端、worker 以及审批响应丢失后的自主恢复超出回放范围。
