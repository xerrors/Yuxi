# 登录页默认背景是一张山茶折枝 SVG

状态：implemented
类型：feature
Owner：frontend/src/pages/LoginView.vue

## 问题

登录卡片左侧需要一张背景。它必须是单一 SVG，不能按主题换成另一张图。外链图片也读不到页面上的 `.dark` 类。山水层次靠位图，不适合这张图要承担的矢量画面。

## 决策

### 实现方案

`LoginView` 在 `login_bg` 为空时渲染内联山茶折枝；其他地址优先加载图片。包不再提供历史默认图片，因此 `/login-bg.jpg` 无法加载时也使用内置图，同一路径上可用的自定义图片则继续显示。画布 3:4，`preserveAspectRatio="xMidYMid slice"` 盖住左栏。纸色、墨色、花瓣和叶子写在这张图自己的 CSS 变量上，`:root.dark` 只改这些变量。枝条以根部为轴缓慢摆动，落瓣、光斑与花朵使用轻微动画。`prefers-reduced-motion: reduce` 时停止动画。品牌字段继续由 `info` YAML 拥有，模板中的 `login_bg` 为空字符串。

## 替代方案

白天和夜里各放一张位图可以保留水墨层次，但那是两张图切换，不是一张 SVG。几何色块能跟随主题，但不是一幅花。外链 SVG 文件无法读取页面主题类，所以图留在 `LoginView` 模板里。

## 后果

自定义登录图仍是单张图片，不随主题改色，也没有摆动和落瓣。768px 及以下左栏继续隐藏。

## 验证

- `frontend/test/browser/loginBackdrop.js` 验证空配置的内置图、历史路径的自定义图片与缺图回退，并检查减少动效和窄屏登录入口。
