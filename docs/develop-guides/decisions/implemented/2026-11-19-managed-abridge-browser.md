# Cube 托管 aBridge 浏览器

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/plugins/abridge/managed_browser.py

## 问题

普通 Linux 需要用 Browser Device Key 启动无头浏览器。APP 终端用户需要经 Agents Public API 自动绑定自己的 Cube 浏览器，并让同一身份的 Run 能使用它。

## 决策

### 实现方案

Public API 从 APP Key 解析终端 UID。Yuxi 用受信平台 JWT 预检 aBridge 状态协议及公开 Origin，由 Cube 确认当前 VM。aBridge 适配器在登记前确认镜像安装了客户端，再传入从实例 ID 派生的安装标识和当前登记版本。aBridge 管理浏览器专用设备、票据、绑定版本、设备凭据与作用于该设备的 AgentConnection。

Cube 模板与普通 Linux 安装同一个 `abridge-client-browser`。普通 Linux 从私有输入读取 Browser Device Key。Cube 首次登记时 Yuxi 经私有 stdin 交付一次性票据，客户端兑换后调用原版扩展的绑定入口。两种路径使用同一套 Chromium 启动、Profile 与绑定确认逻辑。

Cube 负责核对用户实例代次和执行租约。aBridge 适配器用通用来宾进程接口在已运行的实例内调用镜像自带的浏览器客户端。安装路径、专用账号、一次性票据和状态回执由适配器及客户端约定。查看连接状态不唤醒 VM。

Cube 通过原生快照休眠。恢复后仍使用原有设备和浏览器资料。打开浏览器会话时，aBridge 对已离线的设备直接返回离线。短暂停后设备可能仍显示在线，aBridge 通过 WebSocket Ping/Pong 确认当前连接能响应，确认失败才限时等待同一设备以原凭据重连。已有会话上的新操作也会先确认连接。客户端在等待确认时断开请求，aBridge 会在写入会话或操作前停止处理。已经派发且结果未知的操作应先核对结果，避免重复执行。

重复启动从 aBridge 读取登记状态。有效的 v2 绑定沿用原 Device Key。安装实例变更，或当前来宾确认缺少已兑换的 v2 本地绑定时，才按 aBridge 的当前版本显式替换登记。早期模板首次转入 v2 时走相同替换流程，保留设备 ID 和现有浏览器资料。Yuxi 返回就绪前检查 APP、Key、部署设置和来宾代次，并核对本地与 aBridge 的设备、绑定版本和在线状态。APP 的 MCP 委托绑定发起 Key、当前 Run、Worker 租约及设备绑定版本。执行身份失效会中止插件加载及设备调用，单台设备离线则允许重试。

## 替代方案

- 将 Linux 浏览器加入现有 Shell / 文件客户端会共用安装入口，同时让两个能力共享升级与依赖。Cube 只需要浏览器。
- 在 Yuxi 内实现兑换和扩展驱动会独立维护一份协议及 Chromium 启动逻辑，普通 Linux 仍需第二份实现。
- 仅以远端在线状态判就绪会把已被替换的来宾连接计入当前设备。仅检查进程也会把尚未完成授权或扩展离线的浏览器计入就绪。

## 后果

部署需要相互匹配的 aBridge v2 Server、独立发行物与 Cube 模板，并配置来宾可达的 HTTPS/WSS。新 VM 使用预装新版客户端的模板，已有 VM 可以原地升级。仍装有 v1 客户端的 VM 需在切换前停用并升级。关闭 Yuxi 开关会拒绝后续 APP 登记和委托。已有浏览器按自己的设备身份与 aBridge 保持连接，重新打开可沿用原绑定。aBridge 的显式按版本撤权会作废 Key、票据与 MCP 连接。[部署与升级步骤](../../../advanced/abridge.md#cube-托管浏览器)

## 验证

- 在本机 Linux 解包、安装 Chromium，使用 Browser Device Key 绑定后运行真实 MCP 页面读取。重启后设备 ID 一致，渲染进程启用命名空间与 seccomp 沙盒。Shell / 文件设备的 Key 仍只返回自身能力。临时中断 aBridge 后保留绑定，并重新连接同一设备。
- 初期来宾探针验证了重启、关闭再开启 Yuxi 开关、aBridge 显式撤权和扩展存储丢失。释放验证节点磁盘后，完成 0.5.0 候选模板导入。最终隔离环境通过公开 APP HTTP、真实 ARQ Worker 和 aBridge WebSocket 读取 Chromium DOM，并在直接续跑、先查磁盘再续跑两种情况下沿用原 VM。该环境使用显式开启的 HTTP/WS 配置。
- `test_managed_abridge_browser.py` 额外覆盖协议拒绝、模板不兼容、票据兑换响应丢失、跨 APP、Run 租约和上游超时。aBridge 的 `identity.test.ts` 通过真实 SQLite 与 WebSocket 覆盖单次兑换、替换时的权限隔离和撤权。
- 在 MCP 初始化响应和客户端确认之间撤销发起 Key，工具发现及已发现工具的调用都拒绝继续。真实 LangGraph 回归覆盖首次加载插件时撤权，确认模型未进入下一轮。
- Cube 协议测试验证目标实例、执行身份、普通进程输出和原生休眠。aBridge 接入测试验证模板兼容性、私有票据交接、同一设备在线与当前授权。
- 个人与 APP 的真实 Cube 分别休眠 3 秒，再由磁盘查询唤醒。个人首次打开会话并读取页面成功，APP 的真实 Worker 从等待点续办后首次也读到了页面中的验收值。在 `browser_exec` 等待设备响应时断开 MCP HTTP 请求，aBridge 的 SQLite 操作表无新增记录，WebSocket 无设备操作派发，浏览器扩展日志摘要保持一致。同一连接上的对照请求返回页面中的验收值。
