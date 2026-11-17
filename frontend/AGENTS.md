# Frontend 约定

本目录是 Vue 3 / Vite 前端。先阅读根 [AGENTS.md](../AGENTS.md)。架构与开发指南按根文件的任务范围加载。涉及界面设计、交互或响应式方案时读取[设计规范](../docs/develop-guides/design.md)。

- API 调用统一放在 `src/apis`；组件不直接拼接普通 HTTP 请求。
- 跨模块导入只使用 `@/modules/<domain>`（模型/用例）或 `@/modules/<domain>/ui/**.vue`（组件）；不得直达 `model/` 或其他内部路径。
- 前端权限与路由守卫只提供体验约束，后端始终执行最终授权。
- 复用 `src/assets/css/base.css` 变量和 `@lucide/vue`；不为一次性视觉需求引入新依赖。
- 保持 loading、empty、error、断线恢复和终态投影语义一致；不要用乐观 UI 覆盖 PostgreSQL 返回的最终事实。
- `pnpm run lint:check` 是只读 gate；`pnpm run lint` 才允许本地自动修复。

提交前运行：

```bash
docker compose exec frontend pnpm run lint:check
docker compose exec frontend pnpm run test:unit
docker compose exec frontend pnpm run build
```

UI 改动必须在真实页面验证，并提供最终截图或录屏；适用时覆盖浅/深色、响应式、loading、empty 和 error 状态。适配与验收宽度遵循[设计规范的响应式范围](../docs/develop-guides/design.md#_8-响应式行为)。
