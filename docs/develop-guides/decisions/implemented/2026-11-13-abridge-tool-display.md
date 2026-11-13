# aBridge 桌面、浏览器与命令工具展示

状态：implemented
类型：feature
Owner：frontend/src/modules/session/model/abridgeToolResult.js

## 问题

aBridge 的桌面、浏览器和命令工具返回结构化文本与截图。通用卡片无法直接表达动作、目标和操作结局，传输完成也不代表远程操作成功。用户需要直接查看图片和命令输出，并保留参数与完整回执供核对。

## 决策

### 实现方案

session 的 `abridgeToolResult.js` 拥有 aBridge 工具名称与混合回执解析，`toolRenderers.js` 为当前设备、连接、命令、桌面和浏览器工具装配同一个 `AbridgeTool.vue`。未知工具继续使用通用卡片，不增加执行入口。

`toolCallProjection.js` 从回执读取错误、超时和非零退出码；`outcome_unknown` 使用现有 incomplete 状态，卡片显示“执行结果未知”，分组计入“未完成”。投影不改写持久记录，回执未验证实际效果的事实仍可在原始结果中核对。

`AbridgeTool.vue` 拥有动作、目标与结果展示。标题优先使用当前浏览器回执的页面标题或地址，不使用填写内容；session_open 明确声明 browser 能力时显示“连接浏览器”。点击和填写复用现有图标及中文动作。

图片通过既有 `getToolResultParts` 分离，并复用 `ToolResultContent` 与图片查看器。command_execute 的 stdout／stderr 直接投影为纯文本，保留换行并标明标准错误、截断和超时。其他文字不另行加工为设备列表或网页正文；原始文字置于默认关闭的 details，入口沿用 12px 字号和 20px 行高，参数沿用 `BaseToolCall`。

`BaseToolCall` 的可选 error-result 插槽允许带执行回执的 aBridge 命令在失败时显示输出与折叠回执；没有执行回执的命令及未提供插槽的其他工具保持默认错误结果和真实错误原因。插槽只接管错误展示，错误状态仍由原投影决定。

## 替代方案

- 继续仅展示通用 JSON：维护成本低，但动作、图片和命令输出不便阅读。
- 每个工具增加独立组件：共享的回执信封、截图协议和布局会重复实现。
- 把所有文字加工为设备列表或网页正文：重复原始信息，超出展示需求。
- 隐藏参数或原始回执：删除核对与排障入口。
- 修改后端执行状态，或让所有专用结果插槽接管错误：超出展示职责，并影响其他工具的错误语义。

## 后果

一个专用渲染器和结果解析模块覆盖当前已观察到的 aBridge 工具及回执结构，不增加依赖、配置、后端权限或持久化变更。图片、命令输出直接可读，参数与完整回执保持可查；无图片时仍能展开查看原始结果。远程执行事实继续由后端与设备回执拥有。

## 验证

- `docker compose exec -T frontend node --test test/unit/toolRendering.test.js test/unit/toolResultContent.test.js test/unit/toolRendererLoading.test.js`：Passed，45 项。覆盖三个 browser 工具的实际装配、实时／历史混合信封、点击／填写、能力声明、拒绝和未知结果、普通工具负例、命令成功／失败输出、超时、截断及无回执错误兜底。
- 恢复旧的 Abridge 名称注册后，浏览器工具中文动作断言失败；原始结果入口恢复默认字号时，浏览器 computed style 断言因 15px／24px 而失败，恢复未保护的命令错误插槽后，无回执错误用例因真实错误原因丢失而失败，负控有效。
- `playwright-cli -s=abridge-browser run-code --filename=frontend/test/browser/abridgeBrowserRendering.js`：Passed，10 组工具场景，浅深主题及 1440、1024、768px。回读图片 decode、放大和关闭、参数、原始 JSON 默认折叠与展开、12px 入口、命令成功／失败输出及 hover。
- Inspected：用户的实际会话链接跳转登录，未验证该会话完整历史；浏览器测试以合成回执装配真实组件，不连接设备或触发工具执行。
- `docker compose exec -T frontend pnpm run test:unit`：Passed，1084 项，无失败或跳过；包含无回执命令错误回归。
- `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run build`：Passed，build 保留已有大 chunk 提示。
- `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`、`pnpm --dir docs run build`、`git diff --check`：Passed。
