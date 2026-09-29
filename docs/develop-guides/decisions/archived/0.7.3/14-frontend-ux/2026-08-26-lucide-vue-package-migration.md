# 迁移 Lucide Vue 官方包

状态：archived
类型：process
Owner：web/package.json

## 问题

`lucide-vue-next@1.0.0` 已被 Lucide 官方标记为 deprecated，并明确要求 Vue 用户改用 `@lucide/vue`。Web 有 96 个源码文件直接导入旧包，继续保留会产生安装警告并失去正常版本更新路径。

## 决策

直接依赖替换为当前稳定版 `@lucide/vue@^1.34.0`，所有静态命名导入的 module specifier 从旧包机械替换为 `@lucide/vue`。不建立本地 wrapper，不批量重命名图标，也不改变组件属性、尺寸、样式或页面布局。`web/AGENTS.md`、设计规范和贡献指南同步使用当前包名，避免继续引入旧包。

新版包的真实 ESM exports 是导入兼容性的 oracle。迁移脚本从 `web/src` 和 `web/test` 收集到 167 个实际使用的命名导出，全部存在于新版包；生产 build 同时证明 Vue 组件编译和 tree-shaking 链路可用。现有 CSS 只依赖新版继续提供的 `.lucide` 稳定类，没有依赖图标专属类名。

## 替代方案

- 继续使用 deprecated 的旧包：失去维护和更新路径，不采用。
- 固定到旧包最后一个 0.x 版本：只能推迟迁移，并非最新版适配，不采用。
- 建立统一图标 wrapper：当前 consumer 都使用稳定的命名导入，额外间接层没有独立业务语义，不采用。
- 借迁移统一图标名称或视觉：会扩大 UI 回归面，不属于依赖迁移。

## 后果

依赖和 96 个 import source 发生变化，但所有现有图标名称、props、尺寸和样式保持原样。新包继续声明 Vue 3 peer dependency、`sideEffects: false` 并输出 `.lucide` class；主 bundle 大小基本不变。

上游 SVG path 可能随图标版本演进而发生细节变化。编译和测试能证明 API、DOM 类名与打包兼容，不能替代全部真实页面的像素级视觉对比；本次未执行全页面截图回归。

## 验证

- 官方 npm 元数据：`lucide-vue-next@1.0.0` deprecated 并指向 `@lucide/vue`；当前稳定版为 1.34.0，仓库均为 `lucide-icons/lucide`。
- `pnpm install --frozen-lockfile`：通过；`pnpm list` 只包含 `@lucide/vue@1.34.0`，旧 package/import 搜索为空。
- 命名导出 oracle：167 个实际导入全部存在于新版 6101 个 ESM exports。
- `cd web && pnpm run lint:check && pnpm run test:unit && pnpm run build`：通过，136 tests passed。
- `pnpm --dir web audit --prod --audit-level=moderate`：无已知漏洞；`pnpm outdated` 为空。
- `docker build -f docker/web.Dockerfile --target build-stage -t yuxi-web:lucide-vue-test .`：frozen install 和生产 build 通过。
- 工程契约、相关策略测试、docs build 与 `git diff --check`：通过。
- 独立 Reviewer：No blocking findings；指出活动开发文档仍引用旧包，已同步修复。
