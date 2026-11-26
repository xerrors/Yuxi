# 收敛本地开发与初始化入口

状态：implemented
类型：simplification
Owner：Makefile

## 问题

`reset` 将删除 Compose 默认状态目录和创建固定密码测试账号绑定在一起，`verify-trust` 重复封装已有工程检查。造数脚本及其容器挂载、Langfuse Python 任务上传脚本没有现行消费者，前端开发还存在多条 Vite 启动命令。

## 决策

### 实现方案

Makefile 保留开发文档和问题模板使用的启动、停机、日志、格式化、lint、单测和审计入口。快速开始通过网页初始化管理员，CI 使用初始化 API。造数脚本、专用测试、开发 Compose 中的脚本挂载和 Langfuse Python 任务上传脚本统一退役。工程契约与版本验证由各自脚本和工作流维护。

开发 Compose 与宿主开发共用前端 `pnpm run dev`。监听配置由 `frontend/vite.config.js` 维护。

## 替代方案

保留脚本及别名可兼顾仓库外的手工调用，但仍要维护多套入口，一键清库造数也没有已知的仓库调用者。移除整个 Makefile 会让贡献指南、问题模板和本地审计失去现有的简短入口。

## 后果

仓库外直接调用旧目标、脚本或 Vite 别名的流程需要改用现有入口。开发者可通过[独立状态目录](../../parallel-worktree-environments.md)启动全新环境。

## 验证

- 逐项比对变更前后的 `make -n` 输出。默认入口、八个保留目标、Compose 覆盖和测试参数覆盖共 11 组结果一致。
- 隔离的 Compose 前端容器使用 `pnpm run dev` 提供应用 HTML 和经 Vite 转换的入口模块。
- 旧能力不存在：`make -n reset`、`make -n seed` 和 `make -n verify-trust` 均因目标不存在而退出，退出码为 2。旧脚本、专用测试、挂载和 Vite 别名已从现行入口移除。
- 重新引入条件：存在现有入口无法满足的具体调用方，并能明确其数据和运行边界。
