# Demo 暂停时仍发送公开消息

状态：implemented
类型：bug-fix
Owner：packages/agent-demo/src/useDemo.js

## 问题

独立 Demo 在 Session 的 `yuxi.queue_paused` 为真时直接丢弃发送，并禁用输入框和发送按钮。公开协议是：用户向已暂停会话发送一条新的公开消息时，接收事务解除目标队列暂停，并优先单独执行这一条，之后才恢复旧队列。该行为没有第三种 `mode`；现有 `yuxi.mode=follow_up` 的 `agent.session.input.message` 就是这条公开消息。服务端在公开来源且队列已暂停时自行写入恢复意图。

同一 Demo 把回答等待点的每个问题都渲染成文本框。问题对象的非空 `options` 是选择题，提交值应是选项 `value`；没有 `options` 或空列表才是文本。

## 决策

### 实现方案

`packages/agent-demo` 的 `useDemo.send` 拥有公开消息提交。队列暂停不再作为发送条件。已连接、已选择 Agent、不在回答或审批等待、没有在途命令且没有上传时，已有 Thread 仍 POST 原来的请求体：`agent.session.input.message`，`yuxi.mode` 为 `follow_up`，并带上本次 `attachment_file_ids`。Demo 不新增 mode，也不在请求体写入 `resume_queue`。输入框、发送按钮和附件按钮不再因 `queue_paused` 禁用。`yuxi.session.input.continue` 仍只由「继续队列」提交，并在取消清理未完成时保持禁用。

等待回答或审批、未连接、缺少 Agent、上传中和在途命令继续阻止普通发送。取消清理尚未结束时，服务端拒绝普通消息；清理完成且队列仍暂停时，客户端可以发送这条 `follow_up`。

回答控件由 `protocol.js` 的 `questionHasOptions` 区分，选项界面在 `App.vue`。`options` 为非空数组时渲染选项：`multi_select === true` 用复选框，resume 的 `answer` 是所选 `value` 数组；否则用单选，`answer` 是所选 `value` 字符串。`allow_other` 未关闭时可自行填写，提交 `{type:"other",text,selected}`；多选保留已选择的选项。没有 `options` 或空列表保留文本框，`answer` 仍是非空文本。选项展示 `label`，有 `description` 时一并展示。审批调用继续使用原来的 `select`，决定仍是 `approve` 或 `reject`。空选择、空白文本和空的多选数组不提交 resume。

主链路是：暂停会话中的发送按钮调用 `send`，公开 API 接收 `follow_up` 并解除暂停、单独调度该 Input；用户改点继续队列时才领取原优先队头。选择题从等待点 `questions[].options` 读到 `label`/`value`，经 `yuxi.session.input.resume` 的 `response.answers` 按问题顺序提交。

消息内容按协议中的文字与图片块顺序显示。相邻文字合并后经 Markdown 解析与清理，图片只展示 `data:image/` 地址。

## 替代方案

暂停时改发 `steer` 会与尚未消费的旧引导合批，和「只运行这一条」相反。增加客户端 mode 或 `resume_queue` 字段会超出公开事件 schema；恢复标记属于服务端接收事务。只保留继续队列按钮则无法优先单独执行新消息。选择题继续用文本框会提交展示文案而不是 `value`，多选也无法提交数组。

## 后果

暂停时发送会由服务端取消目标队列暂停，并优先单独执行这条 `follow_up`；该轮成功后才按原规则恢复旧队列。附件未就绪时旧队列继续等待。继续队列的语义不变。

选择题可以提交所选 `value` 或自行填写的答案。审批控件和决定值不变。

## 验证

- `packages/agent-demo` 的 unit 和浏览器测试覆盖暂停时发送 `follow_up`、选择与自填答案、空值拒绝和审批决定；浏览器同时核对实际 resume 请求与多模态内容顺序。
