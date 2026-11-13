# 收起侧边栏后的对话列表入口

状态：implemented
类型：feature
Owner：frontend/src/modules/session/ui/SessionNavigation.vue

## 问题

侧边栏收起时对话列表被卸载，用户必须展开整条导航才能查看项目与最近对话。用户需要在图标栏右侧临时查看列表，也可以将列表固定。

## 决策

### 实现方案

[应用布局](https://github.com/xerrors/Yuxi/blob/main/frontend/src/app/layouts/AppLayout.vue)在原导航展开时展示项目与最近对话。[SessionNavigation](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/SessionNavigation.vue)复用导航控制和列表展示，独立面板实例由 AppLayout 在主导航收起时统一挂载，避免缓存的多个 AgentView 各自保留面板。SessionNavigation 依据当前路由选择对话，只在对话页显示外壳与触发区；离开对话页立即隐藏外壳，不等待内部列表的退出动画。AgentView 只拥有固定列的正文让位，其他页面不继承宽度动画或显示触发区域。两种展示通过 ChatUI 共享分组展开、可见批次、滚动位置及项目操作等待状态；缓存页面停用后关闭搜索与临时展示，隐藏实例不写入滚动位置。导航线程与常驻偏好由[刷新恢复决策](2026-10-23-session-navigation-persistence.md)说明。

收起导航时默认隐藏列表，在图标栏右上方提供无可见按钮的触发区域，支持悬停、点击和键盘操作；仅在键盘聚焦时显示入口提示。鼠标实际移入区域才打开，布局宽度动画经过静止指针不会触发展开；键盘或点击打开后焦点进入面板搜索，鼠标悬停不抢焦点。临时列表在图标栏右侧以 8px 圆角、无阴影卡片覆盖内容，离开后延迟关闭，Escape 和点击外部也可关闭。列表内有焦点，或操作菜单与确认弹窗可见时保留卡片；Escape 优先交给这些上层浮层。面板顶部仅显示品牌名称，复用原导航的文字样式和高度，不重复展示 Logo，不保留图标间距；收起状态的搜索入口位于面板内，与侧栏展开/收起图标并列，图标栏不重复展示搜索。全局“对话”导航使用普通对话图标，并读取账号已保存的导航目标返回线程；没有目标时进入空白对话页。展开的主导航在“对话”右侧提供独立的新建图标，默认隐藏，悬停整行或键盘聚焦行内时显示；图标默认及悬停均无背景，悬停图标只强化颜色；图标与返回线程的导航链接并列，支持键盘操作。主导航不重复展示整行新建入口。独立面板仍保留整行“新建对话”，两处新建入口都进入空白页且不自动提交会话。浮层距图标栏和视口边缘约 4px，临时列表通过移出、外部点击或 Escape 关闭。

固定后列表成为图标栏右侧的独立列，为正文让出空间；取消固定直接收起列表并将焦点交还入口。显式展开或折叠主导航清除独立面板的临时状态与常驻偏好。列表可见期间继续同步对话状态。用户菜单始终位于图标栏底部。固定列左侧有明确分隔线，与图标栏区分；卡片进入和退出使用短淡入与轻位移，固定切换时面板边界和正文让位同步过渡，减少动态效果设置会关闭这些动效。

当前路由位于项目内对话时，SessionNavSection 在路由与异步列表就绪后展开所属项目，把可见批次扩展到当前项并滚动定位；最近对话同步展开最近分区。手动折叠不改变路由与所属位置，普通列表刷新也不强制重新展开。项目标题的背景仅在悬停时改变，键盘聚焦仍显示操作按钮。

## 替代方案

- 悬浮完整导航：重复图标栏功能，且不符合仅展示项目与对话的目标。
- 在 AppLayout 挂载独立面板并仅按路由隐藏：占位动画仍作用于新页面，页面职责与视觉结果不一致。

## 后果

列表展示与导航折叠解耦，普通导航与对话页面共享一份项目展开及滚动状态；独立面板生命周期由对话页面拥有。卡片关闭判定识别 Ant Design 的 body 菜单与弹窗；升级组件库时需要重跑浏览器回归。后端接口、授权和会话业务状态不受影响。

## 验证

[浏览器回归脚本](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/sessionSidebar.js)在真实 Vite 页面中使用固定协议 fixture，断言真实 DOM 归属、共享展开状态和布局坐标。它覆盖悬停开关、点击外部、Escape、品牌名称样式一致、搜索入口移动及搜索框操作、主导航新建图标在浅深主题下默认隐藏、悬停整行及键盘聚焦时显示、无背景及悬停变色、键盘激活新建、独立面板的新建整行入口、跨页面返回原线程并保留消息和草稿、显式新建清除旧线程选择、跨页面限定独立面板及保留原展开导航的历史列表、取消固定立即收起、portal 菜单、弹窗及 Escape 优先级、项目展开状态保留、长列表滚动、底部菜单、减少动态效果，以及 1440px、1024px、768px 的浅深主题。恢复卸载列表、遗漏固定列让位、取消固定后仍保留浮层或移除底部对齐，均会使对应断言失败。

实际执行：

- `playwright-cli -s=session-sidebar --raw run-code --filename=frontend/test/browser/sessionSidebar.js`：Passed；截图覆盖浅色悬浮、固定、深色悬浮及 768px 边界。
- `docker compose -p yuxi-container-e2e exec -T frontend pnpm run lint:check`：Passed。
- `docker compose -p yuxi-container-e2e exec -T frontend pnpm run test:unit`：Passed，830 项。
- `docker compose -p yuxi-container-e2e exec -T frontend pnpm run build`：Passed，保留既有大包警告。
- `pnpm --dir docs run build`：Passed。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：Passed，后者 66 项。
- `docker compose -p yuxi-container-e2e exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow'`：3119 Passed、110 skipped，7 个 subtests Passed；跳过项不计为已验证。

独立 Reviewer 在另一浏览器会话完整执行回归并检查截图。另在静止鼠标下连续折叠五次验证不误开，检查 Escape 后不重开，以及键盘打开后的焦点位置；逐帧采样确认卡片约 160ms 淡入位移、固定面板与对话正文约 200ms 同步过渡；独立浏览器从固定对话切到 Dashboard 后采样的 27 帧均从 x=52 开始，面板 DOM 数为 0。减少动态效果时侧栏、面板与对话正文过渡均为 0s。浏览器 fixture 不证明真实后端授权或会话执行；这两项不属于本次 UI 变更的验证范围。已有重命名弹窗取消后焦点返回 body 的行为未在本次修复，不声称弹窗焦点恢复已通过。

- `playwright-cli -s=ui-commit run-code --filename=frontend/test/browser/sessionSidebar.js`：Passed；浅深主题及 1440、1024、768px 覆盖唯一面板、项目第六条自动展开选中、手动折叠后刷新、跨页隐藏、固定和悬浮、账号隔离及刷新恢复。Dashboard 28 帧均无可见面板且正文横坐标稳定；移除外壳的路由可见性后，该断言因退出动画残留而失败。API 使用固定协议数据，只证明前端行为。
