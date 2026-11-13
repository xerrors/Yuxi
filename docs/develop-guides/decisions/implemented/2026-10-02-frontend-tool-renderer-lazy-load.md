# 前端工具 Renderer 按需加载与共享

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/ui/tools/toolRenderers.js

## 问题

工具详情静态导入会扩大聊天首屏 chunk；在每个卡片实例中创建异步包装会重复定义组件。协作分组另建入口时，加载与失败提示容易偏离单工具入口。固定图标的动态 VNode 包装和恒返回 null 的 Mention 样式接口也没有独立消费价值。

## 决策

### 实现方案

session UI 的 `toolRenderers.js` 在模块级拥有固定工具映射与 Vue 异步组件定义，`ToolCallRenderer.vue` 和协作分组复用同一个 CooperationTool。基础卡片同步可用，低频详情按需导入；异步组件共用基础加载卡片、局部失败提示、十秒超时与 Vue 原生失败语义，不增加缓存、重试或插件注册机制。

加载失败只显示“工具详情暂时不可用”，不吞掉原始调用或影响消息列表；未知工具继续显示基础卡片。协作分组加载期间以首个调用显示基础卡片，完成后展示完整分组。工具名称、状态和隐藏规则由既有 model 拥有，分组入口拥有合并展示规则。内部 `tools/index.js` 不承诺无人消费的 renderer 专用导出。

`FileTable.vue` 的固定图标使用直接组件标签，保持尺寸和属性；消费 VNode 的 icon prop 继续使用 h()。Mention 删除空样式函数及调用；图案和交互颜色的当前归属见[前端图标一致性与工具状态反馈](./2026-11-13-tool-icon-interaction.md)。文件、品牌、头像和 Widget 的渲染边界保持独立。

## 替代方案

- 保留静态导入与逐实例包装：功能可用，但首屏包含低频详情且协作加载提示重复维护。
- 只使用构建分包：改变产物分组，无法按运行时工具需求加载。
- 仅移到 SFC 普通 script：可共享单工具定义，协作分组仍需另一套定义。
- 建立通用插件或图标注册中心：没有当前消费者需要版本、权限或生命周期扩展，扩大维护面。

## 后果

低频 renderer 保持按需 chunk，多个卡片和协作分组共享组件定义与加载结果，不声称未经测量的性能收益。动态 chunk 在网络异常或部署切换时可能失败，局部失败提示不改变工具执行事实。固定图标无需动态包装，Mention 无空样式接口。

## 验证

- `docker compose exec -T frontend node --test test/unit/toolRendererLoading.test.js test/unit/toolRendering.test.js`：Passed，32 项；真实组件装配检查共享定义、未知工具回退与隐藏工具。恢复旧的逐实例定义后，共享引用断言失败，负控有效。
- `playwright-cli -s=render-cleanup run-code --filename=frontend/test/browser/toolRendering.js`：Passed，21 项回执场景，浅深主题及 1440、1024、768px；真实浏览器挂载实际工具入口，合成回执不证明后端工具执行。
- `playwright-cli -s=render-cleanup run-code --filename=frontend/test/browser/toolRendererSharing.js`：Passed，两入口的延迟、成功和 chunk 失败，以及未知工具、Mention 图标 DOM。
- 真实浏览器挂载 FileTable 并打开目录操作菜单：Passed，三个固定图标的 DOM 尺寸均为 14×14px；不执行文件变更。
- `docker compose exec -T frontend pnpm run test:unit`：Passed，1084 项，无失败或跳过。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`：Passed，3766 项通过、127 项跳过、7 项子测试通过；跳过项不计为已验证。
- `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run build`：Passed；构建保留已有的大 chunk 提示，未声称性能测量收益。
- `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`、`pnpm --dir docs run build`：Passed。

旧能力不存在：工具消费入口中不再创建异步包装；FileTable 不再用 component 包装固定图标 VNode；getMentionIconStyle 的定义与消费者全部删除。tools/index.js 不再重新导出低频 renderer；静态 import 检查仍由 toolRendererLoading.test.js 拒绝低频 renderer 回到同步入口。

重新引入条件：只有真实 consumer 需要同步入口，或测量证明按需加载存在不可接受的回归，才恢复对应静态导入或导出并补充构建及浏览器证据；只有入口需要不同的组件生命周期或明确不同的加载展示，才单独定义异步组件；只有真实样式 consumer 出现，才恢复样式接口。
