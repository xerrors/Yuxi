# 仅展示当前流式思考段

状态：implemented
类型：feature
Owner：frontend/src/modules/session/model/agentItems.js

## 问题

会话把已经完成的思考段保留在工具时间线和历史消息中，连续工具调用后积累的推理文本干扰结果阅读。界面需要在模型正在产出思考时提供反馈，并在该段结束后隐藏；历史读取、终态回读和断线恢复不能让已结束的思考重新出现。

## 决策

### 实现方案

前端 Thread 状态记录从当前 Run 接受的非空 reasoning.delta 对应的 item_id 和 content_index。正文开始、新工具调用或新的思考段出现时，事件消费者把旧段记入展示专用的隐藏集合，防止迟到增量重新激活它；重置运行展示时清理标记和集合。段结束、item 终态和恢复快照的完成索引共同限制可见性。运行结束、取消或进入等待状态时不展示思考。

[消息展示投影](../../../../frontend/src/modules/session/model/agentItems.js)仅接收当前可见思考段，其余 reasoning_content 为空。[事件消费者](../../../../frontend/src/modules/session/model/useAgentStreamHandler.js)拥有当前片段的切换规则；实时主视图和进行中消息复用同一个 runtime 投影。完整原始思考仍由既有 item reducer、协议和持久化拥有；隐藏集合不关闭协议内容块，缺失 done 的迟到增量仍完整写入原始 item。历史快照不会主动创建当前思考标记。不增加后端字段、配置项或定时判断。

## 替代方案

- 按最后一个列表元素判断：无法识别明确的段结束、状态通知和终态快照。
- 完成后折叠思考：仍会积累历史条目，不满足隐藏要求。
- 删除原始 reasoning：破坏既有协议事实和模型消息语义，展示需求无需修改持久化。

## 后果

SSE 中断期间保留已观察的当前片段，恢复后以快照完成索引和运行状态校正；只读取历史不能推断某段仍在产出，需要新的有效增量。浏览器合成事件验证前端装配与 DOM，不能替代真实 provider 的事件时序探针。

## 验证

[runtime 测试](../../../../frontend/test/unit/sessionRuntime.test.js)通过实际 SSE 消费入口验证段结束、正文与工具切换、下一段替换、重复与跨 Run 事件、迟到增量、恢复快照以及完成、失败、取消和等待状态，同时分别断言展示结果与原始 item。相关 agentItems、sessionRuntime、messageGrouping 共 49 项通过。

负向验证：恢复历史思考拼接逻辑时，agentItems 测试因历史 reasoning_content 非空而失败；移除已隐藏片段限制时，正文后的旧段重新显示、旧段抢回新段展示两项断言失败。

[浏览器回放](../../../../frontend/test/browser/liveReasoning.js)使用合成 HTTP/SSE 数据装配真实聊天页面，检查思考节点移除、正文和工具保留、断线恢复与历史重新打开，覆盖浅色/深色以及 1440、1024、768 宽度。外部模型、真实 worker 与后端持久化未执行，本改动不变更其协议或数据。

验证命令与结果（Compose `main` 项目绑定当前工作区）：

- `docker compose -p main exec -T frontend pnpm run test:unit`：1007 项通过。
- `docker compose -p main exec -T frontend pnpm run lint:check` 与 `pnpm run build`：通过，后者包含 TypeScript 检查。
- 当前分支页面执行 `playwright-cli -s=reasoning-display run-code --filename=frontend/test/browser/liveReasoning.js`：全部 DOM 与原始 item 断言通过。
- `python3 scripts/ci/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.ci.test_verify_engineering_contracts`：检查通过，56 项测试通过。
- 在 `docs` 执行 `pnpm run build`：通过；本页 4 个相对链接目标均存在。
- `git diff --check`：通过。
