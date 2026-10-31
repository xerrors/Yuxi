# 个人助理资料卡片按对话可用空间平铺

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/agents/ui/PersonalAssistant.vue

## 问题

助理资料卡片始终作为弹层显示。宽屏已有空白也会遮挡消息；卡片与页头按钮距离过近，位置缺少明确的空间关系。

## 决策

### 实现方案

SessionWorkspace 使用主对话与轨迹面板真实边界计算可见宽度，包含窄屏最小宽度造成的重叠部分。打开卡片且可见区域至少 1100px 时，在正文右侧预留 352px，提供独立的卡片挂载位置；资料和任务不覆盖正文或输入框。右侧轨迹变化后重新判断。

PersonalAssistant 继续拥有唯一的打开状态、资料和任务内容，通过 Teleport 将同一张卡片放入平铺位置或顶部按钮容器。悬浮位置固定在按钮下方 12px，右边缘对齐，宽度不超过主对话可见范围减去两侧间距，限制最大高度并允许内部滚动。平铺时点击正文不关闭卡片，悬浮时点击外部关闭；两种模式均支持按钮切换与 Escape，编辑和任务导航沿用现有行为。迁移前焦点位于卡片内时，迁移后恢复原有控件；正文输入持焦时不接管。不增加持久化偏好或后端接口。

按钮容器也由 SessionWorkspace 定位到主对话区域右上方，跟随轨迹面板开合，避免与轨迹关闭按钮重叠；最大化轨迹时隐藏主对话操作与资料卡。PersonalAssistantView 只负责装配页面，不再持有覆盖整个页面的外部按钮容器。

## 替代方案

- 继续始终悬浮，仅调整偏移：没有利用宽屏空间，仍然覆盖消息。
- 按整个窗口宽度平铺：主导航和轨迹面板会占宽，容易挤压正文。
- 拆分两张独立卡片：重复内容和焦点状态，同一张卡片转移位置即可满足需求。

## 后果

同一份资料和任务内容随可用空间转移位置，不保留两张独立卡片。宽度以未扣除卡片 padding 的主区域为准，避免平铺后再判定为不足宽度而来回切换。切换布局导致的正文重新换行由现有字符锚定恢复；卡片本身可滚动，不强行同时显示所有任务。卡片仍由用户主动打开，不增加默认展开或持久化偏好。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 宽屏打开卡片时与正文并排 | 卡片仍覆盖正文/输入区 | SessionWorkspace | unit 和真实 DOM 边界坐标 | 修复前新增浏览器断言失败“宽屏资料卡片仍在悬浮” | Passed |
| 窄屏浮层与按钮保持间距且可关闭 | 贴近按钮、越界、失去键盘关闭 | PersonalAssistant | 浏览器测量 12px 间距、Escape、外部点击，320px 高度滚动 | 宽窄切换后焦点离开卡片仍可 Escape 关闭 | Passed |
| 宽度及轨迹变化后布局一致 | 依据窗口宽度错误平铺、阅读位置跳动 | SessionWorkspace / PersonalAssistant | 真实页面宽窄切换、右侧轨迹、同一文字位置 | 主区域不足1100px、关闭卡片、最大化轨迹和普通会话 | Passed |

浏览器命令为 `playwright-cli -s=reading-ui --raw run-code --filename=frontend/test/browser/chatReadingPosition.js`。固定协议数据装配真实页面，覆盖 1440/1024/800/768px、浅深主题、带任务卡片、低高度滚动、外部关闭、Escape 返回按钮及平铺与悬浮切换。800/768px 额外打开轨迹验证按钮可点击、卡片不越过主导航；宽窄切换核对卡片内部焦点与正文输入焦点。定向 `docker compose exec -T frontend node --test test/unit/personalSession.test.js test/unit/sessionHistoryLoading.test.js test/unit/scroll_controller.test.js`：26 passed。前端 lint 与包含类型检查的 build 通过。

`docker compose exec -T frontend pnpm run test:unit`：1003 passed。工程契约检查及其 56 项单测通过。相对文档链接与页面由 docs 的 `pnpm run build` 验证，补丁由 `git diff --check` 检查。

未重跑真实模型或完整个人助理执行 E2E；本变更只调整视图布局和卡片交互，不改任务执行、持久化、权限和 worker。
