# 前端按模型目录别名展示模型名称

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/settings/model/modelDirectory.js

## 问题

模型配置支持为已启用模型设置展示别名（`display_name`），模型选择器的下拉目录已展示别名，但选中态触发器仍从 `model_spec` 截取供应商原始模型名，消息底部引用栏直接读取 `response_metadata.model_name`。别名设置无法在用户实际看到模型名的地方生效。

## 决策

### 实现方案

模型设置模块新增共享 composable `model/modelDirectory.js`（经 `@/modules/settings` 公开入口导出）：按 `modelType` 维护模块级单飞缓存（`modelProviderApi.getV2Models`），暴露 `load`、`resolveDisplayName({spec | modelId})` 与 `invalidateModelDirectory`；解析先按完整 `spec` 精确匹配，再退化为唯一 `model_id` 匹配，跨供应商同名不解析。`load` 带失效纪元守卫：失效前发起的在途旧响应不回写缓存，改为等待新一代结果。展示接入两处：选择器在挂载与 `model_spec` 变化时解析别名用于选中态文本，标题取别名或完整 spec，加载期间 spec 切换时放弃过期结果；引用栏按 `response_metadata.model_name` 解析唯一别名，原始名变化时放弃过期结果，目录未就绪/歧义/失败回退原始名称。

## 替代方案

- 后端在 `response_metadata` 直接写入别名：拒绝。会在仅用于展示的位置改变模型响应协议，并把别名快照固化进历史消息。
- 每个引用栏各自请求目录：拒绝。产生按消息数量的重复请求，改为模块级共享缓存。
- 把 composable 放 `shared/lib`：拒绝。前端分层 lint 禁止 shared 依赖 apis；模型目录的 Owner 是 settings 模块。

## 后果

别名展示的 Owner 收敛到模型配置目录投影，前端不再从 spec 推断展示名。目录按类型在首次需要时请求一次并全应用复用，别名变更通过选择器刷新动作或页面重载生效。消息引用按 `model_id` 唯一匹配，同名歧义时保留原始名称。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 选中态与引用栏按目录别名展示，未命中/歧义/失败回退原始名称 | 展示继续从 spec/`response_metadata` 推断原始名 | `useModelDirectory.resolveDisplayName` 与两处接入 | `test/unit/modelDirectory.test.js`、`modelSelectorAlias.test.js`、`refsModelAlias.test.js`；`pnpm run test:unit` → 879 passed / 0 failed；`npx eslint` 干净；`pnpm run build` 通过 | 实现改为"首个匹配即用"或去掉回退分支时相应用例失败 | Passed |
| 失效后乱序完成的在途旧响应不污染共享目录 | epoch 缺失时旧响应覆盖新缓存 | `load` epoch 守卫 | `modelDirectory.test.js`「失效后乱序完成的在途旧响应不回写共享目录」 | 移除 epoch 守卫后用例失败于 `旧别名 !== 新别名`（实测）；恢复后通过 | Passed |
| 加载期间切换 spec 不回写过期别名 | 过期结果覆盖新选中态 | `syncResolvedModelAlias` 捕获序检查 | `modelSelectorAlias.test.js`「慢目录返回期间切换 spec 时不回写过期别名」 | 移除捕获序检查后用例失败（实测 2 failed）；恢复后通过 | Passed |

浏览器验证：独立页面装配真实选择器与消息引用组件，所有业务请求由浏览器合成响应；浅/深色、768/1440 像素宽度及中文长别名的 DOM 与截图已核对，目录未命中与 503 错误均回退原始名称，`Passed`。

未验证范围：完整登录会话中的真实后端模型目录链路 `Not run`；引用栏"加载期间原始名变化放弃过期结果"与选择器同型，因 SSR 单实例内切换 props 受限，组件级负向用例 `Not run`（composable 层 epoch 与选择器层已覆盖同型缺陷）。
