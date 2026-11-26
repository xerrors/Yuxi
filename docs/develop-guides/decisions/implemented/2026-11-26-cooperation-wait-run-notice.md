# 协作等待的对话状态提示

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/model/runProcessGrouping.js

## 问题

协作等待会结束当前执行段并在同一轮保留等待点。消息区只读取 Run 的 `interrupted`，因此在「等待协作任务完成」上方显示「本次运行已中断」。执行段留在历史中，恢复期间也可能看到误报。

## 决策

### 实现方案

`ThreadTimeline` 使用 Run 快照的状态和结束原因。Run 为 `interrupted` 且结束原因为 `cooperation_waiting` 时，等待反馈沿用现有协作状态。Turn 详情确认已取消，且 Turn ID 和当前 Run ID 均匹配时，消息区显示「本次运行已取消」。其他执行段沿用既有提示。

`sessionRuntime` 保存单轮详情已确认的协作终态，供当前和历史消息使用。协作等待的持久取消事件可以直接确认它明确绑定的 Turn 和当前 Run，本地 Run 落后或轮次详情和历史暂不可用时仍能显示结果。旧等待段缺少所属 Turn 的终态，且已加载的 Run 中没有通过 `resume_from_run_id` 指向它的续跑时，复用已有单轮详情接口查询。续跑的创建和当前 Run 的切换同事务完成，取消结果归属于当前 Run。消息区也可使用会话详情中的当前 Turn。服务端继续按[协作与恢复契约](../../../mechanisms/agent-request-queue.md#独立委派)维护 Run 和 Turn。

## 替代方案

使用页面的实时等待标记隐藏提示，会在快照先于等待标记到达、等待恢复或读取历史时漏掉旧执行段。仅使用会话详情确认取消，开始新一轮后会丢失旧轮次终态。改变已有 Items 响应来携带 Turn 状态会扩大公开协议。

## 后果

续跑尚无输出时，消息区仍依据旧执行段的等待原因避免误报。历史页只补读仍可能承载取消结果的等待轮次。详情读取失败时仍显示消息，后续读取历史时可重试终态查询。

## 验证

- `playwright-cli run-code --filename=frontend/test/browser/cooperationWaitRunNotice.js` 在打开本地 Vite 的浏览器中回放公开 HTTP 和 SSE，覆盖等待、刷新、续跑尚无输出、恢复后的最终正文，以及详情和轮次历史同时读取失败和进入新轮次后的取消提示。原始缺陷会同时显示等待与中断提示。去掉续跑关联过滤会触发对已确认接续的历史 Turn 的多余请求。
- `node --test test/unit/messageGrouping.test.js` 核对匹配的取消、跨 Turn 或不同 Run 的取消、普通中断及空执行段的文案。
- `node --test test/unit/agentRunEvents.test.js` 验证本地仍记录旧 Run 且快照不可用时，匹配当前 Run 的协作取消事件仍能结束该轮，归属不符的事件被丢弃。
