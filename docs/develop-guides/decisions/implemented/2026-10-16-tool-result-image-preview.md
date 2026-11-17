# 工具结果图片预览

状态：implemented
类型：feature
Owner：frontend/src/modules/session/ui/tools/ToolResultContent.vue

## 问题

读取图片的工具结果可能被序列化成 JSON 或 Base64 文本，用户无法直接查看。模型回复称截图已嵌入时，实际消息 DOM 仍可能没有图片。

## 决策

### 实现方案

工具结果的 JSON 解析、内容块识别和图片来源规则由 session model 统一维护；`read_file` 的标题判断、图片生成工具与通用结果组件使用相同规则。工具结果组件按顺序渲染文本与图片块，拥有所有调用路径共用的文本换行、滚动和基础排版。

图片接受 HTTP(S)、受限 data-image 与同源根相对 URL。受保护的同源图片复用正文图片的地址校验和统一 API 鉴权读取，取得图片 Blob 后创建对象 URL，缩略图与放大查看共用该 URL；其他来源直接加载，不携带 API 凭据。结果切换或组件卸载时释放对象 URL，迟到响应不能覆盖新结果。请求失败、非图片响应或图片解码失败显示失败提示，不回显 Base64。图片点击入口连接聊天图片查看器。

组件按规范化后的展示内容判断结果是否变化。流式投影重建相同内容对象时复用正在进行的请求、已加载图片与打开的预览；内容实际变化时重新读取并释放旧资源。受保护图片加载期间显示有尺寸的占位，提供加载说明并遵循减少动态效果偏好。

聊天正文中的截图另由 `chat-widgets` Image/ImageGroup 展示；`present_artifacts` 继续用于可下载或交付的文件，不由工具结果图片预览替代。

## 替代方案

- 仅在读取文件工具单独解析图片：生成图片及其他返回图片块的工具仍会泄露编码文本。
- 将所有工具结果转成 Widget 协议：扩大既有结果区的转换与装配范围。
- 工具结果区保留统一块渲染，并复用图片查看器：当前工具结果有多个图片来源，采用此方案。
- 让调用方分别处理鉴权和文本样式：自定义结果插槽容易遗漏相同约束，公共结果组件统一拥有这两项行为。

## 后果

预览依赖实际工具结果包含可读取的图片字节或受支持 URL。受保护图片的对象 URL 只在当前结果组件存活期间有效。SSR 单测证明内容投影和 HTML；浏览器回归证明实际请求、图片解码、放大与关闭、布局和资源释放。合成 HTTP 响应不替代后端权限测试或真实工具执行到聊天 DOM 的整条链路。

## 验证

等值内容重建回归：`playwright-cli -s=remaining-ui run-code --filename=frontend/test/browser/toolResultImages.js` 通过。已加载和加载中的图片分别连续重建内容对象 12 次，实际网络请求数保持不变，已打开的预览保持可见；占位尺寸、减少动态效果、迟到响应隔离与对象 URL 释放均通过。截图与 DOM 检查覆盖浅、深色及 768、1024、1440px。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 图片结果成为 `<img>`，混合文本维持顺序 | Base64 仍落进普通文本 | 工具结果组件 | `docker compose exec -T frontend pnpm run test:unit` | 无效图片与普通对象不输出 `<img>` 或编码文本 | Passed |
| 读取文件和图片生成共用预览 | 两条工具路径显示不一致 | ReadFileTool、ImageTool | 同上 `toolRendering.test.js` | 普通文本及读取失败继续显示原结果 | Passed |
| 图片来源限制在允许协议 | `javascript:` 或伪装 Base64 被注入 DOM | 工具结果组件 | 同上 `toolRendering.test.js` | 不可信来源显示失败提示且不回显 | Passed |
| 受保护同源图片携带认证，外源不携带凭据 | 原生图片请求缺少 Bearer 或把凭据传向外源 | 统一 API、现有图片地址白名单、工具结果组件 | `test/browser/toolResultImages.js` 的实际网络请求与图片 DOM | 无认证头时合成接口返回 403；外源请求头不得包含 Authorization | Passed |
| 长文本在所有结果插槽中换行并有界滚动 | 自定义插槽遗漏父组件样式 | 工具结果组件 | 同一 browser 回归读取 DOM 尺寸和 computed style | 长 URL、连续 token、多行文本；普通和错误结果 | Passed |
| 结果切换与卸载释放资源，原地更新重新读取 | 迟到响应覆盖新内容、Blob 泄漏或数组内来源更新失效 | 工具结果组件 | 同一 browser 回归延迟响应与对象 URL 回读 | 延迟响应、403、非图片响应和解码失败 | Passed |

实际执行：

```bash
docker compose exec -T frontend node --test test/unit/toolResultContent.test.js test/unit/toolRendering.test.js
docker compose exec -T frontend pnpm run lint:check
docker compose exec -T frontend pnpm run test:unit
docker compose exec -T frontend pnpm run build
playwright-cli -s=p2-fixes --raw run-code --filename=frontend/test/browser/toolResultImages.js
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
pnpm --dir docs run build
git diff --check
git diff --cached --check
```

相关 unit 24 项、全量前端 unit 629 项和工程检查器 unit 66 项通过；前端 lint、类型检查、构建、文档构建和改动文件的 Prettier 检查通过。构建保留已有 `:deep`、compiler macro 与大 chunk 提示。

浏览器回归加载 Vite 的真实 Vue 组件和全局样式，使用独立页面的合成认证上下文与 HTTP 图片响应，不修改登录存储或业务数据。恢复缺陷的浏览器结果分别为“受保护图片请求未携带认证头”和长文本 `5707/298px` 横向溢出；修复后的同一文本为 `298/298px`，高度限制为 300px 且纵向内容可滚动。浅、深色与 1440、375px 下的图片、长文本和错误结果均通过 DOM 检查并回读截图。

未验证真实模型选择、工具执行、当前 API 鉴权与最终业务聊天 DOM 的整条链路；上述浏览器结果只证明真实组件、HTTP 请求和页面交互，不把合成响应计为后端权限通过。
