# 推理与工具结果的统一展示

状态：archived
类型：feature
Owner：web/src/utils/messageGrouping.js

## 问题

推理内容和相邻工具调用分开显示，用户无法通过同一个容器查看或收起连续处理过程。工具返回结果既可能是成功也可能是错误；将结果对象存在视为成功会遮蔽 ToolMessage 和业务 JSON 中的错误，图标、摘要、计数和详情也可能出现不一致。

## 决策

`messageGrouping.js` 将相邻推理和工具调用按原始顺序归组，正文、错误和停止提示保持独立。正文前后的分组使用不同的稳定 key。`ToolCallsGroupComponent.vue` 拥有统一折叠交互：活跃过程默认展开，正文开始或处理结束后收起，用户可以手动展开；只有推理时也显示容器。容器内的推理条目复用 `BaseToolCall.vue`，摘要行显示脑图标、思考标签、分隔线和单行内容预览；默认收起，点击或按 Enter/空格后展开完整推理，各条目的展开状态独立。主聊天、线程消息列表、历史处理过程和单条消息入口复用该组件。

`getToolCallStatus` 统一读取调用对象、ToolMessage 和解析后结果顶层的 status；error/failed 优先于成功和结果存在判断。`BaseToolCall`、容器摘要和子智能体错误标签消费同一判断，专用工具的运行态覆盖不能隐藏明确调用错误。`getToolCallDisplayStatus` 在调用状态上补充子智能体运行状态，使行内图标和外层失败数量一致；子运行失败仍保留专用运行详情，与调用本身失败分开展示。

`BaseToolCall` 使用统一错误详情区保留原始错误内容，避免错误进入专用成功结果模板。知识库调用失败时，标题显示执行失败，不显示空结果摘要。有效的 0 和 false 结果可以展示。时间线保留错误颜色，折叠行支持键盘操作。该修复只改变展示层，不改变原始消息、后端协议、持久状态或执行行为。

流式消息的 provider adapter、首块合并、同一 Run 与 tool-call 的绑定仍由原有消息处理链负责；本决定只消费其规范化结果，不改变消息协议、流式归一化或跨 Run 关联不变量。

## 替代方案

将推理面板留在外层容器之外，无法随整个处理过程一起收起；直接在容器内平铺推理内容会丢失原有点击展开交互，因此通过工具基础组件的图标、标题和结果插槽展示推理，不将推理伪装为持久化工具调用。把全部推理移到工具列表前方会改变跨消息的展示顺序，因此使用有序展示条目。

逐个专用组件补错误解析会重复维护判断，并让分组摘要继续与详情不一致。根据文本中的 error 或递归搜索结果判断错误，会把工具读取的文件或数据行误判为调用失败，因此只读取明确的状态字段。

## 后果

推理不计入工具数量。展示条目仅服务前端渲染，原始消息和后端协议保持原状；同一消息的正文仍能打断过程分组。外层 ToolMessage 成功但返回顶层业务错误时，界面显示失败并保留原始错误。正常结果继续使用专用展示，子智能体独有的运行过程仍由对应组件负责。Markdown 或 HTML 形式的任意文本不参与状态判断。

## 验证

- `docker compose exec web node --test test/unit/agentRequestQueue.test.js`：覆盖纯推理、相邻推理与工具的顺序、正文和错误独立、原始消息不变，以及同消息不同分组的稳定唯一 key。旧实现因多出独立推理消息而失败。
- `docker compose exec web node --test test/unit/toolRendering.test.js`：覆盖错误优先级、JSON/对象结果、分组失败计数、专用错误详情、子智能体覆盖、子运行失败与外层计数一致、0/false 结果及普通文本负例；旧实现的错误图标、文案、计数和详情断言失败。
- `docker compose exec web pnpm run lint:check`、`docker compose exec web pnpm run test:unit`、`docker compose exec web pnpm run build` 验证前端回归与编译。
- 当前已执行上述两个 Node/Vite 真实组件渲染测试，以及 Web lint、unit 和 build；尚未执行 Playwright 浏览器探针，因此不同视口、主题和真实模型聊天链路仍未验证。
