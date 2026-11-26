# 按智能体、会话与用户区分头像

状态：implemented
类型：feature
Owner：frontend/src/shared/lib/avatar/generator.js

## 问题

智能体定义与对话中创建的会话共用头像风格，用户难以区分两种对象。用户头像需要彩色背景，并在深浅主题下保持一致。

## 决策

### 实现方案

共享生成器把 agent、session、user 默认映射到 Lorelei、Gaze、Notionists。`FallbackAvatar` 接收 session 类型，会话树、工具结果和面板标签传入会话身份。智能体目录、选择器及提及继续使用智能体身份，缺省采用 soft 背景预设；Lorelei 只替换背景，脸色、头发和线条保留原作颜色。显式 preset 继续覆盖缺省选择。Gaze 不设置背景；会话树、工具结果与面板标签保持 18px；输入框、正文和候选列表的会话引用使用描边对话气泡加标题，直接复用普通引用图标。Notionists 的背景进入现有四套色卡，整张头像不随主题变化。保留自定义图片优先、稳定 seed、现有显式风格和失败恢复。

不修改持久化、线程生命周期、权限、上传接口或主题选择行为，不增加依赖和设置入口。

## 替代方案

只在目录显式指定 Lorelei 会使同一智能体在选择器与目录的头像不一致。将会话继续标成 agent 并覆盖 style 会保留错误的身份和备用图标语义，因此增加一个对应现有会话实体的 kind。

## 后果

默认头像发生视觉变化，本地包体积增加；已有显式风格与自定义图片继续有效。人物风格使用预设淡彩背景，保证线条在深色界面可辨认；Lorelei 的脸色与线条不随背景预设改变。Gaze 使用会话身份生成，与智能体定义分开。背景颜色属于生成预设，不增加用户设置或持久配置。

本决定取代[本地头像系统](2026-10-06-agent-user-avatar-system.md)中的默认风格和会话身份规则，并补充[头像主题配色](2026-10-11-theme-auto-avatar-colors.md)中的人物风格策略。

## 验证

- 头像 unit 5 项通过：覆盖六种风格与四套预设的原生几何、稳定身份、透明 Gaze、人物背景和深浅主题相同输出，保留作者与许可元数据；Lorelei 各套预设与仅设置背景的上游原生输出完全相同。
- 在临时副本恢复旧默认映射，身份断言失败；删除 Notionists 背景配置，原生输出及用户背景断言失败；恢复 Lorelei 纯色底纸或人物着色覆盖，背景与原作颜色断言失败。
- [浏览器探针](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/avatarEntityStyles.js)在当前 checkout 的真实 Vite 页面验证智能体目录 Lorelei，并用合成会话数据装配会话树、协作工具、面板标签、提及与候选五个入口。回读已解码 SVG、透明像素、同一会话的图片来源、四套用户背景、主题一致性、备用图标与空状态；1440、1024、768px 无横向溢出，保存浅深色截图。DOM 断言确认会话引用使用描边气泡且没有头像图片，会话树、协作结果和面板标签仍为 18px。
- 当前 checkout 的 Compose 一次性前端容器执行 `pnpm run lint:check`、`pnpm run build` 通过。相关 `pnpm exec node --test test/unit/avatar.test.js test/unit/mention_utils.test.js` 为 9 passed、0 failed、0 skipped。`python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`（56 项）、`pnpm --dir docs run build` 与 `git diff --check` 通过。

浏览器使用 Chromium。会话使用合成数据，没有创建模型任务；上传持久化、权限和 worker 不属于头像展示验证范围。主开发前端在 25173 端口挂载当前 checkout；5173 前端挂载其他 checkout，不作为当前源码证据。
