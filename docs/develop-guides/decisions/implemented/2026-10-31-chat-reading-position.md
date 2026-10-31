# 对话宽度变化时保持阅读位置

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/session/model/scrollController.js

## 问题

对话区域随视口、导航或右侧面板改变宽度时，文字重新换行。固定 scrollTop 或滚动百分比无法保持正在阅读的文字，长消息内部的偏移尤其明显。个人助理入口和文件树标签也使页面导航层级不一致。

## 决策

### 实现方案

ScrollController 记录首个可见消息内的文字字符及其距滚动视窗顶部的距离；没有可见文字时记录消息元素。SessionWorkspace 复用现有 ResizeObserver，在尺寸变化时恢复文字位置，在用户主动滚动后更新锚点。停在底部时沿用贴底行为。普通对话、个人助理及右侧嵌入会话共用该实现；分页历史仍由既有追加补偿负责。锚点仅属于当前 DOM，不持久化。

AppLayout 将个人助理放在对话和智能体之间，沿用现有导航行和能力开关。个人助理的侧栏只保留轨迹、来源和显式文件预览，最后一个标签关闭时收起侧栏。普通对话保留文件树。预设头像保留现有静态 SVG 地址，简化造型与配色；不替换用户上传的头像。

## 替代方案

- 固定像素或百分比：实现较短，但消息前方及消息内部的重新换行仍导致偏移。
- 只锚定消息顶部：长消息内部无法保持当前行。
- 仅依赖浏览器自动锚定：无法同时明确控制底部跟随、分页补偿及长段落内字符位置。
- 重建助理页面或引入滚动库：现有页面和浏览器 DOM API 已满足需要。

## 后果

锚点仅保存在当前视图内存中，不增加存储、配置或依赖。文字被替换、折叠或移除后原 DOM Range 可能失效，零高度 Range 不参与补偿，随后重新采样。重排后总高度不足时，滚动受浏览器边界约束，不能保证完全相同的像素位置。

个人助理继续使用已有独立页面和能力开关。普通对话保留文件树；个人助理关闭最后一个轨迹或预览标签后收起面板，避免回到不可见的默认文件页。头像保留五个原有静态地址，已有选择自然使用对应新造型，用户上传的图片不受影响。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 宽窄切换和侧栏变化保持可见文字 | scrollTop 不变但当前行离屏 | ScrollController / SessionWorkspace | chatReadingPosition.js 真实浏览器读取同一字符坐标，覆盖普通、个人和嵌入轨迹 | 修复前 768px 同一字符偏移 -360px | Passed |
| 底部跟随且用户上滚优先 | 重排被误判为手动上滚，或强制拉回底部 | ScrollController | unit 和浏览器验证底部距离、手动滚动后的字符 | 重排提前产生 scroll；无效 Range、隐藏容器 | Passed |
| 助理使用正常导航且不显示文件树标签 | 底部重复入口、关闭标签落入隐藏文件树 | AppLayout / SessionWorkspace | personalSession.test.js 与真实 DOM，普通文件树仍存在 | 重置面板、关闭最后轨迹 | Passed |

实际浏览器命令为 `playwright-cli -s=reading-ui --raw run-code --filename=frontend/test/browser/chatReadingPosition.js`，固定协议数据装配真实 Vite 页面，检查 1440、1024、768px、左右侧栏开合、浅深主题及最后标签关闭。文字位置容差为一个可见行，底部距离不超过 2px。定向命令为 `docker compose exec -T frontend node --test test/unit/scroll_controller.test.js test/unit/personalSession.test.js`。

`docker compose exec -T frontend pnpm run test:unit`：1002 passed。`pnpm run lint:check` 与 `pnpm run build` 在同一 frontend 容器通过，build 包含类型检查。工程契约检查及其 56 项单测、docs 的 `pnpm run build` 和 `git diff --check` 通过。

`docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow'`：3525 passed、125 skipped；容器缺少仓库根目录的配置测试不作为通过证据，本变更不触及部署配置。

测试不触发模型调用或修改业务记录，不证明真实模型输出质量。完整个人助理执行链未重跑；本变更不改变输入、持久化、授权和 worker。
