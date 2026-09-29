# 文档首页品牌化与本地资源交付

状态：archived
类型：feature
Owner：docs/.vitepress/theme/components/YuxiHome.vue

## 问题

文档首页已经拥有独立 Vue 页面，但旧版首屏沿用青色视觉与过期产品截图，十余个等权区块让快速开始、产品能力和文档入口互相争夺注意力。正式品牌资产位于被 Git 忽略的 `docs/vibe/design-1.0.0/`；站点构建不能直接依赖被忽略目录，产品截图还可能携带真实用户和业务数据，首页因此缺少稳定、安全、可发布的品牌资源 Owner。

## 决策

保留 VitePress 路由、导航标签和文档信息架构，由 `YuxiHome.vue` 继续拥有首页内容与交互。首页使用 `#f3ba32` 亮黄、结构墨色、纸白和字标，收敛为首屏、文档入口、产品截图、模型供应商、核心能力、快速开始和社区行动七类内容。字标、favicon 和供应商图标由 `docs/public/home/` 本地交付；超过 100 KiB 的 Open Graph 图、首屏角色、文档入口角色图和产品截图由项目 OSS 的 `github/yuxi/docs/home/` 前缀公开交付，站点配置与 `YuxiHome.vue` 拥有稳定 HTTPS 地址。产品区使用 README 已公开的工作台、知识库、多智能体和沙盒界面截图。贡献者头像墙保留 `contrib.rocks` 动态地址，使社区成员变化无需更新仓库资源。

交互只使用 Vue、Canvas、CSS 与 IntersectionObserver：首屏由 `YuxiHome.vue` 直接组合透明原画角色与像素环境。首屏角色由 OSS 上的 `yuxi-mascot-cutout.png` 提供，以独立 img 元素保留平滑边缘，并由 CSS 驱动 7.2 秒摇摆浮动；外围文档、图谱、记忆、交付与信号在 160×160 Canvas 中以 24 FPS 持续循环。产品截图通过符合水平 tablist 键盘约定的选项卡切换，模型供应商使用两行错位滚动展示，进入视口的内容使用一次性淡入，所有非必要动效在 `prefers-reduced-motion` 下停用。浅色与深色模式共享同一结构、单一黄色强调色和统一圆角体系；VitePress 全站链接、按钮和提示块通过主题变量使用同一品牌色及其可访问深色变体。

## 替代方案

- 继续在旧版十余个区块上换色和替换截图：改动较小，但信息层级和重复布局仍然存在，首页无法优先服务“找到入口”和“理解产品”两个任务。
- 把 `docs/vibe/design-1.0.0/` 直接纳入 VitePress 构建：会把临时设计过程与正式站点耦合，而且该目录被明确排除并由 Git 忽略。
- 全部图片继续放入 Git 仓库：资源可随版本原子交付，但十个大文件会持续增加仓库体积和克隆成本。
- 引入动画或组件依赖：可以扩展表现力，但当前交互用原生能力即可完成，新增依赖没有必要。

## 后果

- 首页从能力清单转为任务入口加产品截图的叙事；保留供应商滚动墙和贡献者证明，重复截图墙与等权营销卡片不再保留。
- 首屏原画角色由透明 PNG 拥有外观，`YuxiHome.vue` 拥有图片动效和外围像素 Canvas；图片不经过低分辨率 Canvas 采样。
- `docs/public/home/` 承载小型品牌与供应商图标；十个超过 100 KiB 的 Open Graph 图、角色和产品截图由项目 OSS 公开交付，本地不保留副本。首页吉祥物场景由 `YuxiHome.vue` 直接拥有，不替代产品实景。
- 文档入口角色图保留原始固有尺寸，由零预取边距的 `IntersectionObserver` 在进入视口时绑定 `src`，并保留浏览器原生懒加载与异步解码作为补充；移动端初始导航不请求角色图，滚动到入口区域后再加载。
- 首页新增少量客户端行为，需要继续维护键盘选项卡、供应商滚动、装饰图视口观察器、深浅主题、375 px 小屏和 reduced motion 语义；不支持 `IntersectionObserver` 的浏览器直接加载装饰图作为显式回退。
- 贡献者头像墙依赖 `contrib.rocks` 的动态响应；外部服务不可用时不会阻断文档主体，但社区头像区域会缺图。
- Open Graph 与 Twitter 分享图片使用项目 OSS 的稳定绝对地址；发布前资源回读同时验证状态、MIME 和字节数。
- VitePress 1 与当前 Rolldown Vite 组合仍会产生已有的兼容性 warning，并把不存在的 `.lean.js` 写入 modulepreload；本次首页没有修改构建工具链。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 负向案例 | 结果 |
|---|---|---|---|---|
| 首页优先呈现快速开始、文档路径与产品截图 | `YuxiHome.vue` | Playwright 在 1440×1000 与 375×812 视口回读 DOM 与截图 | 首屏 CTA、产品截图不可见或产生横向溢出时页面检查失败 | 通过；两种宽度均无横向溢出 |
| 原画角色与像素环境在深浅主题、小屏和 reduced motion 下正常展示 | `YuxiHome.vue`、`yuxi-mascot-cutout.png` | Playwright 在 1440 px 与 390 px 开发页面回读图片加载、CSS transform、Canvas 数据与截图 | 图片丢失时 naturalWidth 不符合预期；任一动画停滞时连续采样相同；reduced motion 下继续动画时静态检查失败 | 通过；图片自然宽度 1172，角色 transform 与环境帧持续变化，reduced motion 下均静止，小屏无横向溢出 |
| 小型品牌资源由站点本地交付，大型图片由项目 OSS 交付 | `docs/public/home/`、`config.mts` 与 `YuxiHome.vue` | 生产构建；逐个回读十个 OSS 对象的状态、类型和字节数；浏览器回读角色与四张产品截图 | 远端对象缺失、类型错误、字节数不一致或页面图片加载失败时资源检查失败 | 通过；十个对象返回 200，类型与上传前字节数一致 |
| 产品选项卡支持鼠标与键盘，并暴露当前选择语义 | `YuxiHome.vue` | Playwright 使用点击、Home、End 操作并回读 `aria-selected`、`tabindex` 与图片 URL | 移除键盘处理或选择语义后 DOM 断言失败 | 通过；四个选项依次切换工作台、知识库、多智能体与沙盒截图 |
| 页面在浅色、深色和小屏下保持可读 | `YuxiHome.vue` 样式 | Playwright 独立检查浅色、深色、375 px 和 768 px 页面 | 恢复固定双栏后，小屏 overflow 检查失败 | 通过；暗色字标切换正确，页面无横向溢出 |
| 非必要动效尊重 reduced motion，截图具有稳定比例 | `YuxiHome.vue` 与产品截图尺寸属性 | reduced-motion 媒体模拟和浏览器尺寸回读 | 删除 reduced-motion 覆盖或图片尺寸后检查失败 | 通过；四张截图均声明 2940×1670 尺寸 |
| 供应商滚动墙持续运行并提供无按钮暂停语义 | `YuxiHome.vue` 与 `docs/public/home/providers/` | 浏览器回读 44 个渲染项、焦点暂停和 reduced motion 状态 | 移除焦点暂停或 reduced-motion 覆盖后，状态检查失败 | 通过；普通模式自动滚动，区域聚焦后暂停，reduced motion 下 animation 为 none |
| 贡献者头像墙反映动态社区成员 | `YuxiHome.vue` 中 `contrib.rocks` 地址 | 滚动到社区区后回读图片自然尺寸 | 外部响应失败时 `naturalWidth` 为 0 | 通过；加载为 812×268，链接指向 GitHub contributors |
| VitePress 全站主题使用 `#f3ba32` 品牌色并保持文字对比 | `docs/.vitepress/theme/custom.css` 与 `config.mts` | 首页和普通文档页回读主题变量、按钮文本色与 `theme-color` | 恢复 Web 青色变量后，主题值检查失败 | 通过；主背景色为 `#f3ba32`，浅色链接使用可访问深色变体 |
| 文档构建、链接和工程契约不回归 | `docs/` 与工程契约脚本 | `pnpm --dir docs run build`；7 个内部路由逐一请求；`python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts`；`git diff --check` | 删除资源、破坏链接或 decision 字段后对应 gate 失败 | 通过；工程契约 61 项测试通过 |
| 仓库基础 Python unit 未受影响 | `backend/` 与 `test/unit/` | `docker compose exec api uv run --no-sync pytest test/unit -m "not slow"` | 任一既有 unit 回归时 pytest 失败 | 1770 passed，50 skipped；标准 `uv run --group test` 因容器内 root-owned editable `.pth` 权限在收集前失败，未据此记为测试失败 |
| 入口角色图保持延迟加载与稳定尺寸 | 生产 VitePress 页面 | 375×812 全新浏览器冷导航与滚动后的资源记录 | 首屏立即请求角色图、缺少固有尺寸或进入入口区后仍不加载时检查失败 | 冷导航请求 0/4 张角色图；滚动到入口区后四张均加载且固有尺寸正确；当前资源版本未运行 Lighthouse，不声明性能分数 |

原画角色资源为 1172×1342 RGBA PNG，alpha 范围 0–255；深浅背景截图确认外围、手臂内侧与双腿之间透明。`pnpm --dir docs run build` 通过（保留已有 Rolldown 兼容警告），工程契约检查与 61 项脚本 unit 通过，`git diff --check` 通过。此项纯视觉替换未重跑后端 unit；同任务前次标准命令在收集前因容器 editable `.pth` 文件权限失败。
