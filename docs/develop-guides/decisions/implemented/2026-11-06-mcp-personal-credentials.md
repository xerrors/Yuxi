# MCP 个人静态凭证与鉴权表单

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/mcp/service.py

## 问题

共享 HTTP 请求头只能表达一个服务身份，无法让使用同一 MCP 的用户分别提供自己的 API Key 或 Token。个人 OAuth 已有隔离连接，但不能替代只提供静态凭证的服务。

## 决策

### 实现方案

管理员维护服务目录，每名用户只配置自己的凭证。`auth_mode` 区分无认证、个人 Bearer、个人 API Key、个人 OAuth、管理员共享请求头和内置平台委托。Bearer 固定认证头及前缀；API Key 由管理员声明一个请求头名，用户只填原始值。填写矩阵及操作由 [MCP 集成](../../../agents/mcp-integration.md#选择接入方式)维护。

MCP 模块复用连接表、Fernet 加密、配置修订、世代和行锁；静态凭证不经过 OAuth 协议或自动刷新。保存接口以当前用户及表单修订发布密文，每次出站重新读取本人凭证。工具发现、真实调用与个人测试使用同一身份；未配置时不回退共享值。前端复用现有配置表单与卡片，保存只显示已配置，独立测试只执行初始化与发现。状态和权限由 [MCP 机制](../../../mechanisms/mcp.md)维护。

个人凭证组件统一拥有预览弹窗和详情页的标题、状态、表单与操作布局，预览弹窗保留基础高度并随内容展开。右侧按钮组以测试连接为主操作，替换和清除收进下拉菜单，测试成功直接更新主按钮；编辑替换草稿时只展示保存和取消，取消后保留已保存的连接。

管理员表单以“接入方式”区分个人与共享凭证，选项按提供凭证的人分组。共享请求头直接在主表单填写，并提供 Bearer 与 API Key 示例；个人方式的公共协议头与超时保留在高级连接选项中。

离开共享方式时，表单清空原请求头；选择无需认证时同样执行清空。管理员可重新填写服务要求的公共协议头。更新用例在修改配置前拒绝省略 `headers` 而隐式沿用旧共享头的切换；显式提交的完整请求头由管理员声明，再按新方式校验认证头冲突。迁移检查使用原配置与更新请求，不从已经替换的新请求头推测旧凭证。请求头内容相同不能证明沿用秘密，旧配置只有公共协议头时也允许重填相同值。

个人静态凭证的 worker E2E 进入现有核心生命周期集合，由 `run_tests.sh e2e-core` 与 CI 系统测试执行。

## 替代方案

仅使用共享凭据不能表达个人身份；强制 OAuth 无法接入仅有静态 Key 的服务。开放用户自建 URL 和任意请求头扩大了已确认范围。独立凭证库或通用凭证框架重复现有连接与加密事实，因此继续在 MCP 模块扩展两种静态方式。

## 后果

保留明确标注的管理员共享模式，迁移由管理员逐服务决定，不自动复制共享秘密。第一阶段支持一个原样发送的 API Key 请求头与固定 Bearer，不增加 Basic、查询参数、多字段凭证或前缀模板。

新增可空 `api_key_header` 字段，业务 Schema 为 v2。显式幂等升级保留已有数据；空字段保持旧 OAuth 修订不变。API/worker 仍只读校验 Schema，部署操作见 [已有部署升级](../../../agents/mcp-integration.md#已有部署升级个人凭证字段)。

修改服务配置要求重新连接，启停保留凭证。停用服务保留本人未清除连接的脱敏卡片，清除后隐藏；管理员也不能通过个人测试使用别人的凭证。清除约束后续出站检查，已经发出的请求不回滚。

## 验证

| 证据 | 观察结果 | 状态 |
| --- | --- | --- |
| PostgreSQL / HTTP integration，含 OAuth 回归、个人静态凭证、匿名访问、Schema 与初始化 | 98 项通过；两名用户实际返回不同身份，替换与清除后回读连接和远端请求，清除后改配置仍隐藏停用服务，升级重复执行保留旧值 | Passed |
| 后端 unit `test/unit -m "not slow"` | 3656 passed、127 skipped；包括旧 OAuth 修订的固定兼容值 | Passed |
| 宿主环境 config unit | 209 项通过，覆盖容器未挂载的仓库根配置 | Passed |
| 前端 unit | 1046 项通过，覆盖方式字段、保存请求与失败草稿；宿主并行运行有两项受文件监听额度限制而失败，容器完整批次无失败或跳过 | Passed |
| 真实浏览器加载生产组件 | 固定 HTTP 响应验证保存失败重试、替换取消、独立测试、停用后清除、管理员方式切换；浅色、深色、失败状态截图，1024px / 768px 无溢出 | Passed |
| 个人 Bearer / API Key worker E2E | 两项通过；独立 MCP 核对秘密，模型只消费实际工具返回，最终输出与 result Run 一致，秘密未进入模型请求 | Passed |
| 腾讯会议真实服务探针 | 个人 API Key 配合官方版本协议头发现 33 项工具；预约结果按会议 ID 回读一致。其他供应商未覆盖 | Passed |
| 共享认证切换的 HTTP / PostgreSQL 回归 | 12 项通过；省略请求头时拒绝并保留原配置，显式清空或重新填写公共协议头可保存，包含与原协议头相同的值。旧实现有 7 项按目标原因失败 | Passed |
| 表单切换与真实浏览器回归 | 相关组件 21 项通过；旧实现提交残留 Key 的负控失败。浏览器保存无需认证配置的请求不含旧共享 Key，并核对失败草稿、个人方式及浅深色与窄屏，共 7 张截图 | Passed |

切换回归与 Schema、初始化的真实集成批次合计 98 项通过，个人静态凭证 worker E2E 两项通过。新增 E2E 文件接入现有核心集合，接线后的远端 CI 尚未执行。最终正式候选的生产镜像冷启动及成套备份恢复不由上述结果证明。

关键命令：

```bash
docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow" -q
docker compose exec api uv run --no-sync --group test pytest test/integration/services/test_mcp_oauth.py test/integration/services/test_schema_migration_version.py test/integration/services/test_app_initialization.py -q
docker compose exec api uv run --no-sync --group test pytest test/e2e/test_mcp_personal_credentials.py -q
uv run --project backend --no-sync --group test pytest backend/test/unit/config -q
(cd frontend && pnpm run test:unit)
playwright-cli -s=mcp-credentials run-code --filename=frontend/test/browser/mcpPersonalCredentials.js
```

worker E2E 直接登记一次性 HTTP MCP fixture，避免修改部署的实验来源白名单；它验证凭证与结果的跨进程链路，HTTPS 配置与来源拒绝由 integration 单独证明。完整 lint、build 与测试层级见 [测试规范](../../testing-guidelines.md)。
