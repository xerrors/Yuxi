# 内置 Skill 初始化保留读取授权

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/extensions/skills/builtin/sync.py

## 问题

旧版内置 Skill 同步写入 flat 分享值，普通用户的 v2 权限解析无法读取带有旧默认值的内容。已有记录的安装信息刷新还无条件覆盖分享配置，无法保留自定义 v2 分享。

## 决策

### 实现方案

内置同步复用 Skill 域现有 `normalize_skill_share_config` 创建 v2 读取配置。已有行仅在分享值严格等于旧系统默认 flat 值时修复；其他 v2 或未知旧值原样保留。版本和内容哈希更新的 repository 方法不再拥有分享策略。

同步沿用现有事务、锁及代码来源更新流程。内置 Skill 的读取权限控制内容发现和预加载。运行工具由 Plugin 配置与 MCP 授权单独控制。

## 替代方案

- 放宽 resolver 识别旧格式：把单一 producer 的缺陷扩散到全部共享资源，拒绝。
- 每次同步重置为全局 v2：覆盖已存自定义分享，拒绝。
- 仅修复新建行：依赖再次初始化时的无条件分享覆盖，不保留自定义配置，拒绝。

## 后果

未知旧分享值保持拒绝读取，需要部署侧单独处理。下一次正式 app-init 同步会修复可识别记录。持久分享在同步完成时更新，历史 Run 保留其资源快照。

## 验证

`test/integration/api/test_builtin_skill_access.py` 使用真实 HTTP、隔离 PostgreSQL Schema、普通用户、Skill 同步和 Context 资源刷新。用例回读默认共享配置、自定义授权和启停状态，验证普通用户能发现内置 Skill 并预加载正式发布的内容。
