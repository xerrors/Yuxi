# 前端交互恢复与输入保护

状态：implemented
类型：bug-fix
Owner：frontend/src/app/router/index.js

## 问题

用户在[前端审查报告](../../audits/frontend-ui-ux-audit-2026-11-17.md)中排除第 4、5、6、7、8 项，授权修复其余 11 项。列表失败、筛选无匹配、临时身份读取失败和未保存输入需要明确反馈与恢复入口；键盘用户需要完成与鼠标用户相同的操作。

目标是让用户能识别状态、恢复失败、保留输入并完成键盘操作。非目标是调整被排除的布局、配色、登录校验和技能顺序，改变后端权限、凭据 API 或业务存储，或新增草稿持久化系统。保留优雅动效，只减少不必要的位移。

## 决策

### 实现方案

列表组件分别拥有 loading、error、empty 和筛选无匹配状态，知识库查询条件通过当前路由 query 保存。InfoCard 使用独立原生标题按钮，与卡片其他操作保持同级；设置分组改用原生按钮。智能体卡片保留描述，将内部 ID 移到复制操作。

路由身份验证在 `beforeResolve` 执行，使 App 的导航跟踪先记录目标，再区分 401 与其他请求失败：401 沿用登出，其他错误保留凭据并阻止目标路由完成，复用 App 的加载失败与重试界面。用户信息校验仍在页面挂载前完成，后端继续拥有授权边界。

新建智能体与知识库在各自组件比较初始草稿与当前输入，关闭时使用现有确认弹窗；取消放弃保持原输入，并把焦点返回创建表单。搜索弹窗限定输入框快捷键，约束键盘焦点并恢复触发位置。网络搜索设置的空白保存不发起覆盖写入，显式勾选清除才通过现有 API 写入空值；后端协议不变。两个公共加载条改用轻柔明暗过渡，保留加载语义。

## 替代方案

维持短暂提示无法让用户区分空列表与读取失败。将草稿保存到服务端会引入不必要的生命周期与持久化责任，因此只保护当前弹窗输入。为临时身份错误增加新路由或状态系统会重复已有失败界面，优先复用 App。直接修改后端空值语义会影响其他配置消费者，因此将“保留还是清除”的决定留在表单提交端。

## 后果

共享卡片通过标题按钮提供键盘入口，菜单与独立操作保留自己的事件边界；只读智能体不提供无效的编辑入口。卡片菜单的键盘入口等待浮层可见后聚焦操作，方向键选择，Escape 返回触发按钮，Tab 关闭后继续页面导航。草稿保护仅覆盖当前创建弹窗，不持久化草稿，保存期间的关闭限制继续由各组件拥有。

临时身份故障保留凭据，但目标页面必须在身份验证成功后才能挂载。后端权限边界不变。网络搜索设置由表单明确区分保留、替换和清除，配置 API 的空值协议不变。

## 验证

- `docker compose exec frontend pnpm run test:unit`：1156 项通过，覆盖实际路由守卫、创建草稿取消/放弃、保存期间关闭限制，以及凭据空白、清除、替换和失败重试。测试中存在 Pinia 上下文提示，不影响断言结果。
- `playwright-cli -s=ui-fixes --raw run-code --filename=frontend/test/browser/uiAuditFixes.js`：真实页面通过 MCP 加载、失败、空列表和恢复，知识库无匹配、筛选名称与返回恢复，创建草稿保护，搜索焦点循环、Enter/Escape 和设置键盘导航。卡片菜单通过纯键盘选择、退出与复制验证，剪贴板边界记录对应 ID 仅写入一次，不改变真实剪贴板。覆盖浅色、深色与 768px；同一加载周期实测加载条位置和宽度保持不变，透明度持续变化。
- `playwright-cli -s=ui-fixes --raw run-code --filename=frontend/test/browser/uiRecoveryBoundaries.js`：隔离浏览器上下文中验证身份 502、网络中断和 401，前两者恢复真实请求后进入原目标；凭据保留、显式清除和替换通过浏览器拦截的合成配置验证，不写真实凭据。
- `docker compose exec frontend pnpm run lint:check` 与 `docker compose exec frontend pnpm run build`：通过；构建保留现有大 chunk 提示。
- `docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"`：3874 项通过、128 项跳过，另有 7 项 subtest 通过；存在依赖弃用与容器缓存目录权限警告。
- `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`：通过，后者共 56 项。两份文档的 4 个相对链接存在性检查、`cd docs && pnpm run build`、`git diff --check` 通过；文档构建保留现有大 chunk 提示。

故障由浏览器 HTTP 边界注入；未创建真实资源、提交真实凭据或测试外部搜索供应商。浏览器脚本与组件测试拥有回归断言，截图仅作为视觉证据。
