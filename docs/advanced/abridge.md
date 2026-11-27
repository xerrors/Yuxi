# aBridge 设备接入

本页供部署管理员启用 aBridge 统一登录和个人设备访问。需要 Yuxi、Docker Compose，以及浏览器可达的两个公开来源。未启用集成时，Yuxi 的普通聊天与 aBridge 本地账户继续独立工作。

## 配置、启动与访问

先按[部署指南](deployment.md)准备 Yuxi 环境文件。生产部署将下面的 `.env` 换成 `.env.prod` 或实际使用的环境文件。生成集成配置：

```bash
uv run --project backend python scripts/init_abridge.py \
  --env-file .env \
  --yuxi-url https://yuxi.example.com \
  --abridge-origin https://bridge.example.com
```

初始化器生成独立 RSA 签名密钥、OIDC 客户端凭据和 aBridge 管理员 Token，保存到环境文件和旁边的私有 `.yuxi-identity/` 目录。重复运行保留已有凭据。目录为私有访问，密钥以 `0640` 供文件所属组只读；Compose 将该组补充给固定 UID 的 API/worker，避免宿主用户 UID 不同时无法读取。环境文件及密钥需要备份，不能提交到仓库；aBridge 仅取得公开密钥，不取得 Yuxi 登录签名密钥或 Provider 私钥。

升级既有部署时，先备份 aBridge 数据卷并核对实际镜像和数据库。0.5.2 可初始化空库或启动兼容的 SQLite schema 6 数据库。0.4.13 的 schema 3 和使用旧试用登记表的数据库需要先在副本上完成并验证离线转换。允许重新绑定设备时，可按[升级已有登记和 Cube](#升级已有登记和-cube)使用新卷，保留旧卷用于回退。检查环境变量和 Compose 覆盖文件是否仍固定旧镜像。

```bash
docker compose -f docker-compose.yml -f docker-compose.abridge.yml up -d --wait
```

Container 沙盒按原部署方式附加 `--profile container`；生产环境将基础文件改为 `docker-compose.prod.yml`，并使用对应 `--env-file`。可选 aBridge 文件不替代基础拓扑。[Compose](https://github.com/xerrors/Yuxi/blob/main/docker-compose.abridge.yml) 固定官方 aBridge 0.5.2 多架构镜像摘要，沿用镜像的 Server 启动入口，将状态存入独立命名卷，并默认只在回环地址发布端口 `18765`。反向代理需要将 aBridge 的公开来源转发到该端口，并支持设备 WebSocket。调整宿主端口使用 `YUXI_ABRIDGE_PORT`。默认端口可用 `curl -fsS http://127.0.0.1:18765/health` 核对运行版本。

公开来源是浏览器和设备使用的地址。生产 Yuxi 的 OIDC 回调使用 HTTPS。初始化器只接受回环地址上的 HTTP 来源。隔离部署已配置 HTTP 来源时，需在 Yuxi 与 aBridge 显式设置 `ABRIDGE_ALLOW_INSECURE_HTTP=true`。aBridge 的 OIDC backchannel 在 Compose 中映射到 `http://api:5050`，只改变服务器请求的传输地址，issuer、浏览器跳转和令牌受众仍使用公开来源。使用浏览器可以访问的公开地址。

登录 Yuxi 后，在设置 → 设备管理中进入 aBridge。首次统一登录自动建立普通 aBridge 用户，再按 aBridge 原有流程绑定电脑或浏览器。首次统一登录创建的平台连接默认允许访问本人全部设备及全部能力（屏幕、输入、命令、文件、浏览器）。需要限制范围时，在 aBridge 的连接设置中选择指定设备和能力；Yuxi 不另设授权副本。返回 Yuxi 刷新后，设备页只显示该平台连接获准访问的设备；Agent 使用相同用户的权限。离线和本机暂停不代表权限被解除。

超级管理员可在同一设置页编辑后端通信的服务 URL；全局用户和设备概况请进入 aBridge 查看。公开来源由部署配置 `ABRIDGE_PUBLIC_ORIGIN` 固定，缺失时设备入口与 MCP 均不可用，不回退到内部地址。管理员 Token 仅供 aBridge 部署使用，Compose 显式清空 API/worker 中的同名环境值，不向它们提供有效管理员凭据，也不通过 Yuxi 页面编辑。应用初始化同步配置定义时清理历史保存的管理员 Token 覆盖值，不修改部署凭据。

使用自建镜像时，先构建镜像，再通过 `ABRIDGE_IMAGE` 指定镜像引用。

## 智能体使用设备

在智能体管理中打开目标智能体的“工具配置 → 插件”，选择“aBridge 设备”。模型看到插件简介，调用 `load_plugin("abridge")` 后获得设备操作说明、画面展示说明及本人获准使用的设备工具。插件中间件在后续模型请求中补充设备与沙盒的边界、异步操作等待及失败恢复约束。

aBridge 从智能体的直接 MCP 选择和会话连接器提及列表中排除。MCP 管理保留服务定义；个人设备绑定与连接授权通过“设置 → 设备管理”维护。插件配置修改在新建会话生效，已有会话沿用[配置快照](../agents/agents-config.md)，用户的设备连接保持原状。需要撤销已有会话的设备访问时，在 aBridge 撤销平台连接。插件开发入口见[部署插件开发](../agents/plugin-development.md)。

集成使用两种不同凭据：OIDC ID Token 只完成登录；Yuxi 在每次 MCP 出站前为当前有效人类用户签发最长一分钟的专用委托。个人委托不保存到 `mcp_connections`，不进入浏览器响应或模型上下文。aBridge 复核目标受众、签发方、用途及当前本地设备策略。普通外部 MCP 的个人 OAuth 和静态凭据不受影响。

在 aBridge 撤销平台连接后，重新登录不会恢复它。用户需要显式点击“重新授权”，建立新的连接；该连接也默认允许本人全部设备及全部能力，需要按需重新收窄；旧连接及旧会话保持失效。退出 Yuxi 不会自动退出 aBridge，也不会回滚已发生的设备操作。aBridge 本地浏览器会话最长十二小时；Yuxi 停用用户后拒绝新签发，已签发委托最多继续有效一分钟。

外部身份按 issuer 与稳定用户标识映射，用户名和邮箱不参与自动合并。更换 issuer 或用户标识会产生不同身份，既有本地用户及设备不会自动迁移。aBridge 的持久数据格式由外部服务拥有；旧运行数据不能仅凭 Yuxi 配置更新就认定兼容。需要保留历史设备时，应先做在线备份并在停机维护窗口显式转换、验证，不能把开发副本的调整当作生产升级。本接入不操作现有部署的数据。

## Cube 托管浏览器

在「设置 → 运行环境」点击「连接沙盒浏览器」。首次连接会准备本人的 Cube，并把其中的浏览器登记到本人的 aBridge 账号。回到「设置 → 设备管理」，点击「刷新设备」即可在当前授权范围内看到它。启用了 aBridge 插件的智能体通过同一个设备列表使用浏览器。Cube 休眠时，列表里的设备会显示离线。在运行环境点击「重新连接」可唤醒它。在 aBridge 重新生成设备凭据后，也从这里连接原来的浏览器。

如果你在 aBridge 只授权了指定设备，请在那里把新设备加入授权范围。撤销过平台授权的账号，需要在 aBridge 显式重新授权。

如果创建环境失败，请管理员核对 Cube 节点空间和模板。故障排除后，在运行环境的“环境操作”菜单中选择“重建环境”，核对清除范围后确认。

外部 APP 通过 [Agents Public API](agents-public-api.md#启动托管浏览器)启动所属终端用户的浏览器。部署需要以下条件。

1. 使用 Compose 默认的 aBridge 0.5.2 镜像，设置 `ABRIDGE_MANAGED_BROWSER_ENABLED=true`，通过上文的 Compose 命令启动。Server 提供 APP 和个人浏览器登记，数据库使用 SQLite schema 6。
2. 从 [aBridge v0.5.2 Release](https://github.com/xerrors/aBridge/releases/tag/v0.5.2) 下载 `abridge-client-browser-0.5.2-linux.tar.gz`，按 Release 的 `SHA256SUMS` 校验后解包。在 Cube 节点用解包后的 `abridge-client-browser` 作为镜像构建上下文。模板基础镜像需带与发行物相同版本的 Playwright 和 Chromium。

   ```bash
   docker build -f docker/cube/Dockerfile.managed-browser \
     --build-arg CUBE_USER_IMAGE=127.0.0.1:5000/yuxi-user:0.1 \
     --build-context abridge-browser-client=/path/to/abridge-client-browser \
     -t yuxi-user-managed-browser:local docker/cube
   ```

   用 [Cube 节点](sandbox-operations.md#原生执行节点)的本地镜像仓库登记新版模板。

   ```bash
   docker tag yuxi-user-managed-browser:local 127.0.0.1:5000/yuxi-user-managed-browser:local
   docker push 127.0.0.1:5000/yuxi-user-managed-browser:local
   cubemastercli template create-from-image \
     --image http://127.0.0.1:5000/yuxi-user-managed-browser:local \
     --alias yuxi-user-managed-browser --cpu 1000 --memory 2048 \
     --writable-layer-size 2G --expose-port 49983 --probe 49983 --probe-path /health
   ```

   按[沙盒配置与运维](sandbox-operations.md#原生执行节点)为 API 和 Worker 配置同一 Cube 节点，设置 `SANDBOX_BACKEND=cube`，并将 `CUBE_TEMPLATE_ID` 设为新模板别名。新模板只用于新建 VM。Yuxi 读取实际 VM 的 `service-v2` 标记。已有 VM 缺少客户端时按下节原地升级。浏览器 Profile 属于当前 VM 的专用账户，暂停恢复或重启会沿用它。
3. 从 Cube 验证 `ABRIDGE_PUBLIC_ORIGIN` 的 HTTPS 和 WebSocket 可达。aBridge 客户端使用该地址兑换票据和建立浏览器连接。Cube 默认拒绝访问私网地址。API 和 Worker 从已配置的内部服务 URL 调用登记和 MCP。隔离测试环境如需使用 HTTP，Yuxi 和 aBridge 都需设置 `ABRIDGE_ALLOW_INSECURE_HTTP=true`。
4. APP 接入由超级管理员在「设置 → 设备管理 → 实例配置」启用 `allow_shadow_managed_browser`。个人浏览器登记使用登录用户的稳定身份，由 aBridge 同一账号下的普通平台连接决定可见设备和可用能力。Yuxi 准备 VM 前检查 aBridge 只读协议和来源，准备后通过私有 stdin 将一次性票据交给来宾客户端。就绪要求扩展已经连通，且 aBridge 确认同一设备和登记版本在线。

关闭开关后，Yuxi 拒绝新的 APP 登记和委托。已经签发的委托最长一分钟到期，设备仍按 aBridge 的登记与授权运行。要立即作废该设备的密钥、一次性票据及受限连接，请在 aBridge 撤权。

### 升级已有登记和 Cube

在维护窗口暂停接收新任务，排空正在执行的任务后停止 API、Worker 和 aBridge，再切换 aBridge 数据卷。

本地部署允许丢弃 aBridge 数据时，可在原 Compose 项目中用覆盖文件将 `volumes.abridge-data.name` 指向新的空卷，保留原卷和原配置以便回退。Yuxi 继续使用原 PostgreSQL 和 Cube VM。普通设备需要重新绑定，APP 与 Web 沙盒浏览器也需重新登记，空库会签发新的设备 ID 和密钥。已保存旧 v2 绑定的 VM 需先按下文处理本地绑定。

需要保留历史设备、密钥或操作记录的部署，应先在备份副本上验证目标 aBridge 版本支持的升级路径，再切换服务。aBridge 的数据库格式和迁移由 aBridge 维护。

旧 VM 保持原工作区和虚拟机身份。客户端升级工具位于仓库的 `scripts/abridge/upgrade-managed-browser.py`。管理员通过已有的可信来宾管理通道，用 root 在空闲 VM 中运行它，传入受信发行物的 SHA-256 和原浏览器使用的规范公开根地址，省略默认端口和末尾斜杠。先停止浏览器专用账号的进程。普通模板需要能下载固定 Playwright 版本对应的 Chromium。

```bash
/usr/local/bin/python3 -I upgrade-managed-browser.py "/path/to/abridge-client-browser-${BROWSER_VERSION}-linux.tar.gz" \
  --sha256 "$BROWSER_RELEASE_SHA256" --public-origin "$ABRIDGE_PUBLIC_ORIGIN"
```

工具将已有浏览器资料复制到同一专用账号的新目录。原先安装过客户端的 VM 会保留 `/opt/yuxi-managed-browser.before-upgrade`。旧试用快照带有运行中的 Chromium 时，先唤醒 VM 并停止旧客户端，再切换。保留 aBridge 登记的试用版 APP 首次按新协议连接时，在原设备 ID 上重新兑换托管浏览器凭据。

若明确使用空库，已安装 v2 的 VM 可在可信来宾管理通道运行以下停止命令。确认退出码为零、状态为 `bound` 或 `unbound`，待专用账号的浏览器进程退出后，按原属主和权限备份完整状态目录。

```bash
runuser -u managed-browser -- /usr/local/bin/python3 -I /opt/yuxi-managed-browser/abridge-client-browser stop
```

默认状态目录为 `/var/lib/yuxi-managed-browser/.local/state/abridge-client-browser`。已确认的旧 v2 绑定保存在 `binding.json`。确认新服务的公开地址正确且原登记已弃用后，在来宾管理通道移走这个文件，保留 Chromium 的 `profile/`。随后用原 Web 账号或 APP 终端身份重新连接。新服务签发新凭据，客户端在同一 Profile 中绑定新设备。遇到未经计划的绑定冲突时，应先核对所连服务和数据库。

放流前，用待发布的 API、Worker、前端和 aBridge 在隔离环境验证两条路径。在新模板创建的 VM 中，从 Web 连接沙盒浏览器，在 aBridge 本人的设备与 Yuxi 刷新后的设备列表核对同一设备 ID，再用普通对话读回网页内容。在原地升级的试用 VM 中，从原 APP 对话读回网页内容。若保留了 aBridge 数据，还要核对设备 ID 与升级前一致，并用普通授权访问一台既有设备。

回退时先停止客户端，在同一维护窗口恢复原应用版本、模板配置和 aBridge 数据目录。重新绑定会覆盖扩展中的设备密钥，回退到原 v2 登记时要一并恢复备份中的完整状态目录，包括 `binding.json` 和 `profile/`。对升级过的 VM 恢复旧客户端安装，首次安装的则移走新安装，然后再启动服务。

### 恢复 APP 终端运行环境

`sandbox_create_failed` 表示 Cube 未能完成创建。`sandbox_unavailable` 表示已有失败记录，需要部署管理员检查。先恢复节点资源，再在「系统管理 → 用户管理」筛选 APP 终端用户，核对 APP、终端标识和完整 UID。重建会清理来宾主目录中的文件与软件，工作区和技能保留。确认目标、清除范围和无在途任务后，在 Yuxi 部署目录运行以下命令。成功推进到新代次后，针对旧代次的恢复请求会被拒绝。

```bash
docker compose exec -T api uv run --no-sync python - '<终端 UID>' <<'PY'
import asyncio
import sys

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.agents.services.sandbox import get_user_sandbox, rebuild_user_sandbox
from yuxi.modules.identity.repositories.users import UserRepository

async def recover(uid):
    load_models()
    pg_manager.initialize()
    try:
        user = await UserRepository().get_by_uid(uid)
        if user is None or user.is_deleted or user.user_kind != "end_user":
            raise SystemExit("请核对当前部署的 APP 终端 UID")
        status = await get_user_sandbox(uid)
        if not status.get("rebuild_required") or status["busy"]:
            raise SystemExit("当前环境未处于可重建的故障状态")
        result = await rebuild_user_sandbox(uid, failed_generation=status["generation"])
        print(result["state"], result["generation"])
    finally:
        await pg_manager.close()

asyncio.run(recover(sys.argv[1]))
PY
```

环境恢复后，APP 使用原 APP Key 和终端标识重新调用托管浏览器接口。

## 插件配置升级

正式 0.8.0 的标准清单只提供知识库与 DeepWiki。升级保留这两项连接的既有选择。若部署自行登记了 aBridge，先核对旧清单并审阅受影响配置，设备插件的额外能力须显式启用。操作见 [Skill 与 MCP 升级说明](../agents/skills-management.md#从运行依赖升级)。

## 协议验证

默认部署使用官方 `v0.5.2`，对应源码 revision `6dcaf1e69a6dd85a8e21e4ca4db271fa1ae33a48`。在独立目录构建该版本后，验证普通设备授权、既有配置升级和个人 Cube 浏览器登记。

```bash
ABRIDGE_TEST_SOURCE=../aBridge \
  uv run --project backend --group test pytest backend/test/integration/services/test_abridge_devices.py
```

探针还需要指向隔离 PostgreSQL 与 Redis 的 `POSTGRES_URL`、`REDIS_URL`。它创建自己的用户、Schema、aBridge 数据库和回环端口，不连接现有设备。未提供源码时该可选探针会跳过；显式指定的目录缺少构建产物则失败。发布此集成时须执行探针并检查没有跳过，跳过不能证明跨项目链路可用。
