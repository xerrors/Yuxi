# 可选择的企业资料入口

状态：implemented
类型：feature
Owner：web/src/views/WorkspaceView.vue

## 问题
可访问知识库按创建人分组不等于企业共享范围。强制企业默认会改变个人用户现有体验。

## 决策
企业资料视图只消费服务端授权后明确 global/department 共享标记，不改变资源权限、不增加租户模型。首次保持个人默认，用户可将当前个人或企业视图设为此浏览器默认。偏好按 uid 隔离，无 uid 不持久化；存储不可用时继续工作并提示保存失败。文件深链始终优先个人目录，企业空态不自动回退、不请求个人树。

knowledge router和现有权限模块拥有可见性与标记，WorkspaceView拥有入口状态，workspace_sources拥有严格标记消费和浏览器偏好读写。个人入口、知识库浏览和原删除行为保持。

## 替代方案
部署级默认配置需要后台配置/API及管理员界面，超过用户自选入口所需。现有系统选项没有空间默认字段，本地偏好满足交互而无需新数据库配置链。硬编码企业默认改变上游个人体验，因此不采用。

## 后果
单部署全员/部门共享不等于多租户隔离，偏好不跨设备同步。此变更不含统一回收站、记忆管理或私有部署配置。浏览器端只使用uid分键偏好而不保存知识库内容；服务端可见性仍是权限边界。

## 验证
- `node --test web/test/unit/workspace_enterprise_entry.test.js web/test/unit/workspace_action_semantics.test.js`：15项通过，覆盖首次个人、按uid重建偏好、企业不读个人树、空uid/非法值/存储错误、严格共享标记、深链与服务端失败。
- `pytest --confcutdir=test/integration/services test/integration/services/test_enterprise_workspace.py -q`：独立fixture PostgreSQL、真实JWT及TCP HTTP的1个综合用例通过。普通成员只见global/本部门/定向库，私有与跨部门库不返回；superadmin可见私有库但不标企业，定向共享不标企业；匿名401。测试不读取生产密钥。
- 定向ESLint、Vite build、后端Ruff和工程契约检查通过。全后端unit2092项通过；全Web在Windows有1个上游既存PDF资源URL路径测试失败，不以该平台差异冒充全Web通过。
- 真实WorkspaceView/Sidebar/FileList在浏览器以合成API验证个人默认、企业设置默认后刷新、不同uid、个人文件深链、企业空态、错误重试和知识库点击。未用复刻HTML替代组件；API/user被fixture替换，预览与全局搜索未在此范围验证。只覆盖标准桌面浅色，不声称窄屏/深色或生产登录E2E通过。

目录请求使用单一版本保护成功、错误和finally，切入企业立即使旧请求失效；返回个人明确重新读目录。真实SFC延迟Promise覆盖知识库→企业→个人、个人→知识库、A→B及旧finally；移除guard的变异对照失败。独立Reviewer复审原串源时序及相邻路径通过。浏览器截图展示交互布局，不作为请求竞态证据。
