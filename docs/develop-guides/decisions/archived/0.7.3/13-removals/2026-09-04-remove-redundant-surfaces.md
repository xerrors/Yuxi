# 删除无消费者表面并收敛重复协议

状态：archived
类型：simplification
Owner：web/src/apis/base.js

相关事实由下列源码 Owner 分别持有：

- 前端组件可达性：`web/src` 的显式导入、路由和工具渲染注册表
- Web 测试入口：`web/package.json` 与 `.github/workflows/web.yml`
- 对话队列恢复：`web/src/composables/useAgentRequestQueue.js`
- 下载与 Agent 字段归一化：`web/src/utils/file_utils.js`、`web/src/utils/agentConfigUtils.js`
- MySQL 展示与连接：`web/src/components/ToolCallingResult/tools/mysqlResultFormatter.js`、`backend/package/yuxi/agents/skills/buildin/mysql-reporter/scripts/_mysql_common.py`
- 后端运行时能力：各 service、repository、model factory 与持久化模型
- Shipping 配置与 Docs 工具链：`docker-compose.yml`、`docker-compose.prod.yml`、`docs/package.json`、`.github/workflows/deploy.yml`

## 问题

仓库保留了无生产消费者的 Vue 组件、utility export、Python helper、异常和测试专用 service/repository 包装，也同时维护正式 `web/test` 与未被 CI 收集的 `web/src/utils/__tests__`。查询编码、队列恢复、下载文件名、Agent 字段映射、MySQL 结果展示与连接逻辑分别存在多份等价或轻微漂移的实现。五个 Sigma/Graphology 依赖、旧样式和若干 Compose 环境变量已经没有读取方，Docs Pages 仍绕过现有 pnpm lockfile 使用 npm。这些表面没有独立语义，却形成虚假的第二 Owner 并增加排障与修改成本。

## 决策

- 删除经导入、路由、动态加载和注册表搜索确认无消费者的组件、样式、utility export、Python helper、异常与测试专用便利 API。仍有价值的 Web 测试迁入唯一正式根 `web/test/unit`，同主题用例合并；空测试包删除。
- 队列恢复的“同步持久状态、重读最新线程状态、恢复请求流”由 `useAgentRequestQueue` 持有；组件只提供线程和 Agent 身份。消息工具结果 JSON 解析只保留一个私有 helper。
- 查询参数过滤与编码由 `web/src/apis/base.js` 持有，并保留 `0`、`false`、空值和 endpoint 问号语义。六个下载入口统一使用一个 Content-Disposition parser，同时继续拥有各自默认文件名；Agent identity、backend option 和三个 MySQL renderer 分别使用单一纯函数。
- 下载 parser 优先解析 `filename*` UTF-8，失败时告警并回退普通 `filename`；普通文件名解码 percent encoding，失败时保留原值。Agent ID 保持 `agent_id`、`slug`、`id` 优先级并保留已有 slug，MySQL formatter 保持空值、JSON、对象和 primitive 展示。
- 后端测试直接读取持久化模型、能力常量或生产入口，不为测试保留第二套 API。等价 linked workdir 包装由通用规范化函数取代；附件 parser 只在真实解析分支惰性导入。
- MySQL Reporter 保留三个独立 PEP 723 入口，共享同目录配置、异常和连接重试；Skill 投影复制完整目录。
- 删除无消费者的 Sigma/Graphology 依赖及锁文件项、旧样式和 Compose no-op 变量。Docs Pages 使用仓库现有 pnpm lockfile 执行 frozen install 与 build。
- Request/Run 状态、SSE、权限、lease、恢复扫描、持久化边界和各测试层独立 oracle 不因减少行数而合并。

## 替代方案

- 保留旧表面并标记弃用：仍需理解和验证多套入口，不能降低认知负担。
- 让 CI 收集两个 Web 测试根或为删除项保留兼容 wrapper：会永久保留虚假契约。
- 给下载 helper 增加选项以复刻各副本的异常行为：会把偶然漂移固化为配置。
- 新建通用 facade、mapper 框架或一次性重写事件流：会引入新的长期抽象或越过不同语义边界。
- 让 Pages 继续使用 npm 并新增第二份 lockfile：会制造并行依赖 Owner。

## 后果

- 当前维护者只需跟踪可达组件、一个 Web 测试根、一个队列恢复入口和各协议的真实 Owner；生产源码净减少 2762 行。
- 仓库外直接 deep import 被删除内部表面不获得兼容层；这些入口没有已文档化的外部契约。
- 下载异常输入统一为较完整的既有语义组合；评估下载在 malformed `filename*` 时会继续回退普通文件名，早期简单 parser 也会解码普通 filename 的 percent encoding。
- MySQL Reporter 的单文件拷贝不再受支持，必须复制完整 Skill 目录。
- 设置被删除的 Compose 变量不再表现为有效配置；这些变量此前没有读取方。Docs 本地、CI 与 Pages 共用 pnpm lockfile。

## 验证

旧能力不存在：全仓负向搜索确认已删除组件、样式、依赖、export、Python helper、测试专用包装、路径别名、no-op 环境变量、npm Pages 路径、重复协议实现和第二测试根均不存在；没有兼容 re-export、空壳或重复 fixture。

重新引入条件：只有出现仓库内或已文档化的外部消费者，并能证明独立运行时语义及对应负向测试时，才重新引入被删除表面；测试便利性本身不是条件。

- Web frozen install、lint、223 项 unit 和生产 build 通过；覆盖查询边界、队列同步后重读、下载编码与 malformed 输入、Agent 字段优先级、backend label fallback 和 MySQL 展示。
- 后端相关 unit 通过；MySQL Reporter 7 项用例包含三个投影后独立 CLI，并验证缺少连接配置时均显式失败。Run attempt/manifest 与数据集恢复的真实 PostgreSQL/Task integration 共 8 项通过。
- 全部非慢 unit 在一次性 API 容器中为 1699 passed、44 skipped、2 failed；两项失败来自容器临时目录 `0700` 与 runner 断言 `0755` 不符，在长期 API 容器单独重跑均通过。后续受影响 backend 定向 unit 60 项通过。
- auth/identity integration 的 16 个测试主体通过；三个 teardown 因测试主动锁定或删除用户后仍使用失效 token 清理资源而报错，不经过已删除 helper。
- E2E 为 9 passed、2 skipped、7 failed；六项失败来自 deterministic replay 服务不可连接，一项为既有 evaluation metadata 断言缺失。multimodal E2E 共享 helper 后 collect-only 收集 2 项。
- 变更 Python 文件的 Ruff lint 与 format check 通过；package 全量 format check 只报告两个未修改的既有文件。
- 开发 Compose `config --quiet` 通过；生产 Compose 在缺少 `.env.prod` 时以 `--no-interpolate` 验证结构通过，完整生产变量插值未在本地验证。
- 工程契约检查及其 61 项 verifier unit、Docs frozen install/build、workflow YAML、全仓负向搜索和 `git diff --check` 通过。
