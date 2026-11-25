# 移除闲置开发入口与非发布 Pages 产物

状态：implemented
类型：simplification
Owner：docker-compose.yml

## 问题

[Makefile 入口收敛](./2026-11-26-makefile-entrypoints.md)后，旧造数脚本只剩专用测试与开发容器挂载。Langfuse Python 任务上传脚本没有现行调用方。前端存在多个 Vite 启动命令，文档构建在无需部署时也上传 Pages 产物。

## 决策

### 实现方案

开发 Compose 不再挂载造数目录。首次初始化走网页或初始化 API。旧造数脚本及其专用测试和 Langfuse Python 任务上传脚本退役。

开发 Compose 和宿主开发共用前端 `pnpm run dev`，监听配置由 `frontend/vite.config.js` 维护。`.github/workflows/docs-check.yml` 在各调用路径构建文档，上传步骤沿用 `.github/workflows/deploy.yml` 的主分支推送条件。发布工作流回归测试校验上传和部署选择的条件一致。

## 替代方案

保留脚本和命令别名可以照顾仓库外的手工调用，但会留下多个入口。用共享工作流输入控制上传会新增参数，现行两个调用路径可直接使用调用方事件与 Git 引用。

## 后果

仓库外直接调用退役脚本或旧 Vite 别名的流程需要迁移。PR、版本 tag、其他分支推送和手动触发仍可使用原有文档构建，只有 `main` 推送的发布路径上传 Pages 产物。

## 验证

- 隔离的 Compose 前端容器通过新命令提供应用 HTML 和经 Vite 转换的入口模块。
- 发布工作流回归测试检查实际上传与部署条件。恢复无条件上传时，测试因上传条件缺失而失败。本地解析验证 PR、其他分支推送、版本 tag、手动触发和 `main` 推送五种事件条件。GitHub 托管环境的实际上传与部署未运行。
- 旧能力不存在：旧脚本、专用测试、废弃挂载和 Vite 别名均从现行入口移除。
- 重新引入条件：出现现有入口无法满足的具体调用方，且明确其数据或发布范围。
