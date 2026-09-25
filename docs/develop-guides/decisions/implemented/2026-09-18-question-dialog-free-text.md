# 提问弹窗支持纯问答

状态：implemented
类型：feature
Owner：backend/package/yuxi/agents/toolkits/buildin/tools.py

## 问题

`ask_user_question` 过去只向模型说明带选项的问题，前端也把自行填写编码为特殊“其他”选项，因此 Agent 无法直接提出需要用户自由填写的纯问答，问题导航和逐题跳过也不清晰。

## 决策

- 保持 `questions` 与 resume object 的单一协议：规范化后的 `options` 为空时是纯问答，有选项时由 `multi_select` 判别单选或多选。
- `options` 只保存真实业务选项，不注入或识别“其他”选项或 sentinel。`allow_other` 独立控制选择题底部的自行填写入口。
- 纯问答提交去除首尾空白后的字符串；选择题继续提交 string、list 或自行填写 object。
- 弹窗统一展示问题类型、前后题导航、当前位置、关闭和“跳过 / 下一步（或提交）”。跳过只省略当前题，最终 resume object 不包含被跳过或未作答的问题；关闭沿用整组拒绝语义。
- 问题内容按实际高度紧凑排布，不设置占位最小高度；跳过与主操作位于回答条内部，自行填写回答条与业务选项等宽，灰底只用于 hover、focus 或已选择反馈。
- 工具审批弹窗保持原有协议和布局。

## 替代方案

- 新增 `question_type`：它会与 `options`、`multi_select` 形成可冲突的第二事实，当前类型可以从既有字段无歧义派生，因此不采用。
- 保留特殊“其他”选项或 sentinel：这会继续把自行填写混入业务选项并增加兼容分支，按当前要求删除。
- “跳过”直接恢复字符串 `reject`：它会取消整组问题，无法表达逐题跳过，因此不采用。

## 后果

- 模型可以省略 `options` 发起纯问答；遗漏选项也会明确呈现为文本输入，不静默生成伪选项。
- 选择题不默认选中第一项，用户必须明确选择、自行填写或跳过。
- 自行填写状态属于前端当前交互，不写入问题协议；恢复答案保持已有 object 形状。
- 弹窗使用现有 CSS token 与 Lucide 图标，在浅色、暗色和窄屏下共享同一实现，不引入依赖。

## 验证

- `docker compose exec api pytest test/unit/agents/toolkits/buildin/test_ask_user_question.py test/unit/services/test_chat_stream_interrupt.py -q`：26 passed；纯问答测试同时约束模型可见说明和 interrupt payload。
- `docker compose exec web node --test --test-concurrency=1 test/unit/questionUtils.test.js test/unit/humanApprovalModal.test.js`：6 passed；覆盖纯文本、选择、自行填写、内嵌操作区、从自行填写切回多选、选择题焦点与逐题跳过。
- `docker compose exec api pytest test/unit -m 'not slow'`：2181 passed、54 skipped、7 subtests passed。
- `docker compose exec web pnpm run test:unit`：351 passed。
- `docker compose exec web pnpm run lint:check` 与 `docker compose exec web pnpm run build`：通过；构建仅保留既有 chunk size 提示。
- Playwright 在 1440×900 浅色、375×812 暗色和 812×375 横屏检查真实组件；横屏 `scrollWidth` 等于 `clientWidth`，图标按钮有可访问名称，输入获得可见焦点。
