# 项目创建时选择个人空间目录

状态：implemented
类型：feature
Owner：frontend/src/modules/workspace/ui/WorkspaceDirectoryDialog.vue

## 问题

创建项目时需要在个人空间中定位或新建目录，目录层级和已选位置需要清晰展示。目录接口使用个人空间相对路径，不能把宿主机路径当作可访问目录。

## 决策

### 实现方案

项目创建入口打开目录弹窗，至多展示父目录及所选目录内容两列。面包屑、可编辑路径、隐藏目录开关和新建文件夹操作复用现有工作区树与目录创建接口；输入路径逐级匹配接口返回的目录，未匹配时显示个人空间根目录并禁用打开。目录接口对不存在路径也返回空列表，因此选择已有目录时由父目录返回的条目确认目标存在。异步请求按最新输入生效。

新建文件夹成功后选中并展示该目录；确认目录后，项目选择入口使用目录末段作为名称调用现有 linked Project 接口，不截短名称，服务端继续规范化首尾空白。同一弹窗内按目录复用创建请求 ID，切换到其他目录使用不同 ID，切回原目录仍保留原 ID。创建成功后选择返回的项目，后续通过现有项目菜单重命名。附件和交付物继续使用原工作区选择器。

## 替代方案

- 在通用文件选择器中叠加多列和路径编辑：会让文件多选和交付物保存入口承担项目目录专属交互。
- 新增宿主机目录接口：突破现有个人空间的路径和授权边界，且无此需求。

## 后果

弹窗展示的是用户个人空间的虚拟路径，所选目录即项目工作区，文件直接保存在该目录中；路径和权限的最终校验仍由后端工作区边界负责。项目名称与目录绑定分离，重命名项目不重命名目录。

## 验证

- 以下是原有实现的验证结果；目录切换后回到原目录时复用请求 ID 的修改尚未运行测试或浏览器验证。
- `docker compose exec -T frontend pnpm run lint:check`：通过。
- `docker compose exec -T frontend pnpm run test:unit`：1043 tests passed；新增目录选择组件测试及相关测试随后单独运行，10 tests passed。
- `docker compose exec -T frontend pnpm run build`：通过；构建报告已有的 CSS 和大文件分包警告。
- `playwright-cli -s=yuxi-project-picker-test --raw run-code --filename=frontend/test/browser/projectDirectorySelection.js`：通过。真实 Vite 页面使用固定 API fixture，覆盖目录层级、隐藏目录、路径未命中、过期响应、失败重试、目录变更后的请求 ID、长名称、长列表中的选中、创建和重命名，并检查浅色、深色及 768/1024 像素布局。
- `python3 scripts/ci/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.ci.test_verify_engineering_contracts`：通过。
- 真实账号下的项目及文件落盘链路：Not run；浏览器回归使用固定接口数据，不能证明服务端持久化。
