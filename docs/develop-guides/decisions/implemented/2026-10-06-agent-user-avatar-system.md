# Agent 与 User 共用本地 DiceBear 头像

状态：implemented
类型：feature
Owner：frontend/src/shared/ui/FallbackAvatar.vue

默认风格、会话身份与人物背景策略由[按实体区分头像](2026-10-31-avatar-entity-styles.md)取代；本记录保留本地生成、自定义图片优先与错误恢复的决定。

## 问题

Agent 与 User 的默认头像需要共享风格、完整色卡、明暗版本和图标备用语义，并在列表、选择器、账户、统计及子智能体面板中保持实体身份一致。外部默认头像 API 会使头像展示依赖网络。

## 决策

### 实现方案

共享 `FallbackAvatar` 保留自定义图片优先、实体 seed、尺寸、名称和装饰语义，接入本地 DiceBear 生成器。Agent 默认使用 Clay，User 默认使用 Glyphs。四种风格与四套完整色卡由 [共享生成器](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/lib/avatar/generator.js) 和 [色卡](https://github.com/xerrors/Yuxi/blob/main/frontend/src/shared/lib/avatar/presets.js) 拥有，每套色卡都有日间与夜间版本。组件接收 style、preset、shape、dark，默认 preset 为 default、dark 为 auto；auto 跟随 Yuxi 主题 Store，on/off 独立覆盖。circle、square、rounded 分别提供圆形、直角方形和现有圆角方形。

调用方只传真实实体身份与自定义图片，不构造默认图片地址。子智能体标签使用 Agent 身份。没有自定义图片时在浏览器本地生成，Gaze 保持透明。自定义图片加载失败或生成失败时显示本地 Lucide Bot/User 图标及浅色背景，发出包含原因的 fallback 事件；更换来源或网络恢复后重新尝试。图片 DOM 身份检查隔离迟到的旧请求错误，空图片字段和数字身份 0 均保留正确语义。

依赖固定为 DiceBear core 10.7.0、styles 10.6.0，只导入实际支持的四种定义。Glyphs 夜间仅开放底纸颜色，不改蒙版和几何，发布包含 [署名许可](https://github.com/xerrors/Yuxi/blob/main/frontend/public/avatar-licenses.txt)。组件参数、主题与失败语义由 [设计规范](../../design.md#头像组件) 说明。MCP、模型供应商、品牌头像与后端持久化保持现有职责；本阶段没有头像配置数据库或新上传接口。

## 替代方案

- 外部 DiceBear API：需要可达网络，不能直接复用 Glyphs 的夜间底纸适配。
- 静态 SVG 集合：减少运行依赖，但失去按实体身份稳定生成的能力。
- 两套生成器只共享备用图标：不能形成 style、preset、dark 的共同契约。

## 后果

本地生成增加前端包体积。库版本与同名色卡改动会影响头像输出，固定依赖并比较 default 的原生结果。Glyphs 使用 CC BY 4.0，SVG 保留署名元数据，发布保留许可与修改说明；其他三种美术使用 CC0 1.0。不同实体不承诺头像零碰撞。组件 auto 与 Yuxi 的明暗设置一致，由主题 Store 拥有最终偏好。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| Agent/User 当前入口共享稳定的本地头像 | 页面保留远程默认地址或以 Thread 当 Agent 身份 | 共享组件与调用点 | 头像 unit、负向搜索、浏览器工作区与管理目录 DOM | 阻断外部网络仍显示本地图像；null src 页面可渲染 | Passed |
| 配色与夜间保持身份几何；Gaze 透明 | 色卡混入几何或背景 | avatar 生成器 | 原生输出、独立几何比较、4 styles × 4 presets × 2 modes 浏览器像素 | 日夜路径变化或 Gaze 角落填色会失败 | Passed |
| 上传优先，失败可恢复且可观测 | 吞错误、旧错误覆盖新来源、无限请求失败资源 | FallbackAvatar | 浏览器请求 abort、换源、online、ARIA 与事件 | 无效图像、非法 style、迟到 error、空值与 seed 0 | Passed |
| 生产装配、工程边界与许可有效 | 未装配、遗漏依赖或署名 | frontend 与规范 | 前端 lint/unit/build、信任检查、docs build、独立 Review | 非法参数显式拒绝并报告 generation 原因 | Passed |

实际命令：`pnpm --dir frontend run test:unit` 为 480 passed；头像最小集合 `pnpm --dir frontend exec node --test test/unit/avatar.test.js` 为 3 passed。独立 Compose frontend 的 `pnpm run test:unit` 同样为 480 passed；前端 `lint:check`、`build` 在本地和独立 Compose frontend 均通过。信任检查及其 64 项 unittest 通过，`cd docs && pnpm run build` 通过，`git diff --check` 通过。浏览器执行 [avatarSystem.js](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/avatarSystem.js)，无页面脚本错误、无外部默认头像请求；验证实际 Agent/User 入口、夜间覆盖、透明像素、加载失败和恢复。真实页面截图覆盖管理目录的浅/深色、编辑头像、账户头像及 390px 移动布局；独立 Reviewer 完整审查无阻塞问题。

后端代码未变。已有开发 API 容器执行默认 `uv run --group test pytest test/unit -m "not slow"` 时，依赖同步因 `yuxi.egg-info` 权限失败；使用 `uv run --no-sync --group test pytest test/unit -m "not slow"` 实际运行同一已安装测试环境，结果为 2564 passed、55 skipped、7 subtests passed。55 个 skip 不作为头像功能证据。没有执行上传持久化、权限、worker 或外部 provider E2E，这些边界不属于头像展示变更。
