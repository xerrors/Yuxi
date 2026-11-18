# aBridge 设备接入

本页供部署管理员启用 aBridge 统一登录和个人设备访问。需要 Yuxi、Docker Compose，以及浏览器可达的两个公开来源。未启用集成时，Yuxi 的普通聊天与 aBridge 本地账户继续独立工作。

## 配置、启动与访问

先按[部署指南](deployment.md)准备 Yuxi 环境文件。生成集成配置：

```bash
uv run --project backend python scripts/init_abridge.py \
  --env-file .env \
  --yuxi-url https://yuxi.example.com \
  --abridge-origin https://bridge.example.com
```

初始化器生成独立 RSA 签名密钥、OIDC 客户端凭据和 aBridge 管理员 Token，保存到环境文件和旁边的私有 `.yuxi-identity/` 目录。重复运行保留已有凭据。目录为私有访问，密钥以 `0640` 供文件所属组只读；Compose 将该组补充给固定 UID 的 API/worker，避免宿主用户 UID 不同时无法读取。环境文件及密钥需要备份，不能提交到仓库；aBridge 仅取得公开密钥，不取得 Yuxi 登录签名密钥或 Provider 私钥。

```bash
docker compose -f docker-compose.yml -f docker-compose.abridge.yml up -d
```

Container 沙盒按原部署方式附加 `--profile container`；生产环境将基础文件改为 `docker-compose.prod.yml`，并使用对应 `--env-file`。可选 aBridge 文件不替代基础拓扑。它使用 [Compose 固定的官方 0.4.13 镜像摘要](https://github.com/xerrors/Yuxi/blob/main/docker-compose.abridge.yml)，将状态存入独立命名卷，并默认只在回环地址发布端口 `18765`。反向代理需要将 aBridge 的公开来源转发到该端口，并支持设备 WebSocket。调整宿主端口使用 `YUXI_ABRIDGE_PORT`。

公开来源是浏览器和设备使用的地址，远程访问必须使用 HTTPS。回环 HTTP 仅用于本机开发。aBridge 的 OIDC backchannel 在 Compose 中映射到 `http://api:5050`，只改变服务器请求的传输地址，issuer、浏览器跳转和令牌受众仍使用公开来源。浏览器必须能访问 Yuxi 和 aBridge，不能使用 Compose 服务名代替公开来源。

登录 Yuxi 后，在设置 → 设备管理中进入 aBridge。首次统一登录自动建立普通 aBridge 用户，再按 aBridge 原有流程绑定电脑或浏览器。aBridge 0.4.13 首次统一登录创建的平台连接默认允许访问本人全部设备及全部能力（屏幕、输入、命令、文件、浏览器）。需要限制范围时，在 aBridge 的连接设置中选择指定设备和能力；Yuxi 不另设授权副本。返回 Yuxi 刷新后，设备页只显示该平台连接获准访问的设备；Agent 使用相同用户的权限。离线和本机暂停不代表权限被解除。

超级管理员可在同一设置页编辑后端通信的服务 URL；全局用户和设备概况请进入 aBridge 查看。公开来源由部署配置 `ABRIDGE_PUBLIC_ORIGIN` 固定，缺失时设备入口与 MCP 均不可用，不回退到内部地址。管理员 Token 仅供 aBridge 部署使用，Compose 显式清空 API/worker 中的同名环境值，不向它们提供有效管理员凭据，也不通过 Yuxi 页面编辑。应用初始化同步配置定义时清理历史保存的管理员 Token 覆盖值，不修改部署凭据。

Compose 固定发布镜像，不接受本地源码构建；初始化器不接受 `--source-dir`，`ABRIDGE_SOURCE_DIR` 不再参与部署。既有部署升级前仍需自行备份和核对 aBridge 数据版本，初始化器不会启动服务或迁移运行数据。

## 智能体使用设备

在智能体管理中打开目标智能体的“工具配置 → 插件”，选择“aBridge 设备”。模型看到插件简介，调用 `load_plugin("abridge")` 后获得设备操作说明、画面展示说明及本人获准使用的设备工具。插件中间件在后续模型请求中补充设备与沙盒的边界、异步操作等待及失败恢复约束。

aBridge 从智能体的直接 MCP 选择和会话连接器提及列表中排除。MCP 管理保留服务定义；个人设备绑定与连接授权通过“设置 → 设备管理”维护。插件配置修改在新建会话生效，已有会话沿用[配置快照](../agents/agents-config.md)，用户的设备连接保持原状。需要撤销已有会话的设备访问时，在 aBridge 撤销平台连接。插件开发入口见[部署插件开发](../agents/plugin-development.md)。

集成使用两种不同凭据：OIDC ID Token 只完成登录；Yuxi 在每次 MCP 出站前为当前有效人类用户签发最长一分钟的专用委托。个人委托不保存到 `mcp_connections`，不进入浏览器响应或模型上下文。aBridge 复核目标受众、签发方、用途及当前本地设备策略。普通外部 MCP 的个人 OAuth 和静态凭据不受影响。

在 aBridge 撤销平台连接后，重新登录不会恢复它。用户需要显式点击“重新授权”，建立新的连接；该连接也默认允许本人全部设备及全部能力，需要按需重新收窄；旧连接及旧会话保持失效。退出 Yuxi 不会自动退出 aBridge，也不会回滚已发生的设备操作。aBridge 本地浏览器会话最长十二小时；Yuxi 停用用户后拒绝新签发，已签发委托最多继续有效一分钟。

外部身份按 issuer 与稳定用户标识映射，用户名和邮箱不参与自动合并。更换 issuer 或用户标识会产生不同身份，既有本地用户及设备不会自动迁移。aBridge 的持久数据格式由外部服务拥有；旧运行数据不能仅凭 Yuxi 配置更新就认定兼容。需要保留历史设备时，应先做在线备份并在停机维护窗口显式转换、验证，不能把开发副本的调整当作生产升级。本接入不操作现有部署的数据。

## 插件配置升级

生产旧库按 [Skills 升级说明](../agents/skills-management.md#从运行依赖升级)一次升级至当前结构。旧的显式 aBridge MCP 选择或全部 MCP 选择在同一次升级中转为启用设备插件，其他配置和历史执行事实保留。新部署直接使用插件入口，不需要独立升级步骤。

## 协议验证

两边的协议验证由各自测试承担。发布验证使用官方 `v0.4.13` 对应的源码 revision `f43b7712e04ae6ad19805e842afefb5400699916`，与部署镜像版本一致；在独立目录构建后运行真实跨项目探针：

```bash
ABRIDGE_TEST_SOURCE=../aBridge \
  uv run --project backend --group test pytest \
  backend/test/integration/services/test_abridge_devices.py
```

探针还需要指向隔离 PostgreSQL 与 Redis 的 `POSTGRES_URL`、`REDIS_URL`。它创建自己的用户、Schema、aBridge 数据库和回环端口，不连接现有设备。未提供源码时该可选探针会跳过；显式指定的目录缺少构建产物则失败。发布此集成时须执行探针并检查没有跳过，跳过不能证明跨项目链路可用。
