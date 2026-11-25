# aBridge 统一登录、个人设备与工具展示

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/plugins/abridge/service.py

## 问题

用户需要用 Yuxi 账户连接个人 aBridge 设备，并直接阅读工具结果。共享管理员凭证不能表达个人权限；另建账户、复制 Agent Key 会割裂登录、设置与执行。

## 决策

### 实现方案

aBridge 使用 [Yuxi OIDC Provider](2026-11-14-oidc-provider.md) 登录，按 issuer 与稳定 sub 建立身份，不按同名账户合并。首次统一登录创建默认访问本人全部设备及全部能力的平台连接，用户可在连接设置中收窄。重复登录不恢复撤销或重置已保存策略。显式重新授权创建新的默认全部授权连接。设置页和部署文档说明该默认值。

aBridge 能力发现同时要求 OIDC 已启用、有效实例地址和公开来源；地址沿用 Options 的数据库覆盖与环境回退，仅开启通用 OIDC 不会启用设备连接器。Extensions 适配器拥有设备读取与 MCP 认证装配：每次出站确认运行用户，在设备适配模块中复用 Identity 的有效用户校验与签名，签发最多一分钟的平台委托；aBridge 校验用途、签名、issuer、audience 和有效期，再与本地设备策略取交集。登录 ID Token 不用于设备执行，个人连接表不保存长期凭据，通用 MCP 不解释设备协议。

设置展示当前用户设备、远端平台和管理入口；超管通过既有 Options 机制编辑内部通信地址。地址变更后旧工具对象在出站前拒绝使用过期配置。首次读取使用骨架，刷新保留列表，失败清空旧结果并提供重试。平台图标由共享映射提供，未知平台使用通用图标，不按设备名称猜测。

Compose 拥有服务、持久目录和只读签名密钥装配，区分内部通信地址与公开浏览器地址；私网 backchannel 不改变公开 issuer。私钥以 0640 权限和文件所属补充组供 API/worker 读取。集成不可用时明确报错，不阻塞普通聊天。配置与操作见 [aBridge 接入参考](../../../advanced/abridge.md)。

session 的 [回执解析](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/model/abridgeToolResult.js)与 [AbridgeTool](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/ui/tools/renderers/AbridgeTool.vue) 复用现有工具卡片、图片查看器和状态投影，展示设备、连接能力、桌面/浏览器动作、命令输出与设备文件。连接能力取当前会话回执，缺失时才用请求参数；不从相邻工具猜测设备或结果。文件正文仅在完成且回执为 executed 时按纯文本展示，远端路径不链接到 Yuxi 工作区。失败、拒绝和结果未知不投影为成功；命令查询区分处理中、结束与输出过期。参数和完整回执保留折叠入口，无执行回执的错误保留默认原因。基础卡片失败时保留工具图标，并通过明确的状态文字区分成功、失败和结果未知。

浏览器工具按 aBridge 的 MCP 回执展示。读取文本或表格时保留范围、分页及截断提示，等待条件经确认后显示满足，脚本显示返回值、日志与完成步骤。操作查询与取消按同一回执区分结果未知、已请求取消和已取消。旧会话中的 `command_status` 和 `command_cancel` 仍使用原有展示。脚本代码只进入参数详情。部署版本见 [aBridge 0.5.2 发布镜像与启动](2026-11-25-abridge-0-5-2.md)。

配置、协议展示与提示的职责收敛见 [aBridge 简化决定](2026-11-17-abridge-simplification.md)。

个人 Cube 安装浏览器时使用同一稳定账户标识登记普通设备。设备列表与智能体继续使用该账户已有的平台授权，设备和能力的选择由 aBridge 管理。[个人 Cube 浏览器登记](./2026-11-20-personal-cube-browser.md)

聊天输入框打开 `@` 时，通过设备管理使用的 `GET /api/devices` 读取本人授权设备。选中后将设备 ID 和当时的显示名写入普通消息正文。同名设备由 ID 区分，历史消息可直接显示原名称。运行时沿用当前会话的插件配置和 aBridge 授权。

## 替代方案

- 个人静态 Agent Key 或 Device Flow 不能同时解决账户来源与统一登录；直接复用 Yuxi 登录 JWT 会扩大凭据用途。
- 仅使用外部 IdP 不能覆盖 Yuxi 本地账户；自动同名合并会混淆身份。默认空授权需要修改外部服务，采用其发布版本的默认全部授权并明确告知用户，避免 Yuxi 维护平行权限逻辑。
- 通用 JSON 难以阅读工具结果；逐工具建组件会重复协议解析；复用工作区文件渲染器会混淆设备路径归属，因此扩展单一 aBridge 渲染器。

## 后果

Yuxi 成为身份信任根，签发能力失陷可冒充用户；设备授权、暂停与撤销仍由 aBridge 拥有。已签发委托最多继续有效一分钟，退出浏览器不等于取消设备任务。issuer/sub 变化会产生不同身份，既有本地账户与设备不自动迁移。图标与结果展示不新增设备操作或权限，原始回执保留核对入口。

## 验证

前端浏览器用例在真实聊天页面选择两个同名设备中的一个，核对发送请求中的设备 ID 和刷新后历史消息显示的名称。[设备引用用例](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/deviceMentions.js)

[委托 integration](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/services/test_abridge_delegation.py) 与 [跨项目协议测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/services/test_abridge_devices.py) 覆盖真实登录、用户设备隔离、授权撤销与恢复、委托用途和 HTTP 权限；后者需要显式提供已构建的 aBridge 源码，默认跳过不代表联调通过。

[设备单测](https://github.com/xerrors/Yuxi/blob/main/backend/test/unit/services/test_abridge.py)、[设置组件测试](https://github.com/xerrors/Yuxi/blob/main/frontend/test/unit/deviceSettings.test.js)及 [工具组件测试](https://github.com/xerrors/Yuxi/blob/main/frontend/test/unit/toolRendering.test.js) 覆盖配置边界、平台保留、加载与失败状态、回执解析和正文转义。失败/拒绝/未知回执携带貌似成功的结果、误导性设备名称及未知工具构成负向案例。

[工具浏览器脚本](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/abridgeBrowserRendering.js)与 [设置浏览器脚本](https://github.com/xerrors/Yuxi/blob/main/frontend/test/browser/deviceSettings.js) 使用合成回执或 HTTP 响应验证真实组件、深浅主题及 1440/1024/768px；它们不证明真实设备执行。未对在线设备执行键鼠、命令或文件写入，也未验证现有数据的 Schema 转换。
