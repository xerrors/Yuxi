# 前端图标一致性与工具状态反馈

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/ui/tools/BaseToolCall.vue

## 问题

普通 read_file 图标的颜色由基础卡片的 tool-loader 固定，查看图片的图标经 icon 插槽继承标题颜色。相同状态和外观下，两者在默认与 hover 时表现不同。工具专用图标不应另行决定通用交互反馈。MCP 目录已按连接器用途选择图案，提及候选和插入后的 token 却固定使用插头，同一连接器在不同入口无法保持一致。

## 决策

### 实现方案

BaseToolCall 在默认图标和 icon 插槽外提供共同的状态图标容器，颜色由容器按现有 effectiveStatus 与 appearance 维护，内部 SVG 继承颜色。保留原有默认状态图案和加载动画，工具插槽只负责自定义图案。

时间线中的已完成可交互工具在 hover 和 focus-visible 时由 gray-600 加深到 gray-700；仅摘要工具没有展开交互，不显示该反馈。卡片保留成功、错误、加载的语义色；时间线保留已有中性风格及错误红色。状态颜色规则只覆盖 BaseToolCall 的标题图标，不把品牌、文件类型或整站导航纳入同一颜色规则。

连接器图案由 `shared/lib/skill_icon_utils.js` 的现有名称与 slug 映射拥有。`mention_icon_utils.js` 对 MCP 使用与 `McpCardList.vue` 相同的 slug 加显示名称选择方式，未知连接器回退到 Plug；`MentionResourceAvatar.vue` 和 `MentionToken.vue` 传入显示名称。Skill 保持既有 slug 映射，候选尺寸、正文尺寸与颜色保持各入口原有职责。这与工具卡片的状态颜色是独立规则，不把连接器身份色纳入工具状态配色。

## 替代方案

- 给 ReadFileTool 补 class：依赖调用方了解基础组件 CSS，不能统一后续插槽图标。
- 为所有 SVG 添加全局 hover 样式：会误改品牌、状态、文件及其他控件图标。
- 删除自定义图标插槽并增加配置：现有专用图标消费者需要保留图案能力，不新增替代 API。
- 在提及处复制连接器图案映射：会产生两个可独立修改的事实源；复用目录已有映射即可满足一致性。

## 后果

默认图标和查看图片、Abridge 的自定义图案共用状态容器，工具无需复制基础卡片 CSS。已完成的时间线工具统一加深图标以反馈 hover 和键盘焦点；错误状态保持红色，摘要没有可展开反馈。容器保持 15px 图标尺寸，加载动画、展开事件和工具结果不变。

MCP 目录、提及候选与插入后的 token 共用连接器图案映射，不新增依赖或图标注册机制。

## 验证

- `playwright-cli -s=render-cleanup run-code --filename=frontend/test/browser/toolIconInteraction.js`：Passed。真实 Vite 页面经 ToolCallRenderer 挂载读取文件、查看图片、生成图片、错误、中断、等待、运行和摘要；在浅深主题及 1440、1024、768px 下回读 SVG computed color、尺寸、真实 hover 与 focus-visible。原实现因普通读取图标 hover 后仍是 gray-600 而失败，修复后与图片图标统一为 gray-700。卡片保留成功绿、等待和运行蓝、错误及中断红；时间线等待与运行维持既有中性色。
- `playwright-cli -s=render-cleanup run-code --filename=frontend/test/browser/toolRendering.js`：Passed，21 项合成回执场景，浅深主题与三个支持宽度，工具摘要和状态样式回归检查通过。两份浏览器证据只证明展示，不证明后端工具执行。
- `docker compose exec -T frontend pnpm run test:unit`：Passed，1084 项通过，无失败或跳过。
- `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run build`：Passed；build 保留已有的大 chunk 提示。
- `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`、`pnpm --dir docs run build`：Passed。

未修改后端、持久化或工具执行链路。交互颜色的事实 Owner 是 BaseToolCall；专用工具组件继续拥有图案及业务文案。

MCP 提及修复的独立证据：

- `docker compose exec -T frontend node --test test/unit/mentionIconComponents.test.js`：Passed，覆盖 aBridge、WeKnora、知识库、显示名称匹配、未知 MCP 回退和 Skill 负例。
- Playwright 实际登录页面核对目录与候选 SVG，检查插入后图案：Passed；浅深主题及 1440、1024、768px 无候选横向溢出。只检查展示，不触发远程工具执行。
- `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run build`：Passed。
- `docker compose exec -T frontend pnpm run test:unit`：Passed，1084 项，无失败或跳过，包含 MCP 提及图案回归。
