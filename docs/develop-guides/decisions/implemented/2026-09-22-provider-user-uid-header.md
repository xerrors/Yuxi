# Provider 请求头携带用户 UID

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/chat.py

## 问题

平台需要在 Yuxi 之外按用户统计模型用量：外部网关或供应商自行计量，Yuxi 本身不做用量账本。模型请求原本无法携带调用者身份：`model_providers.headers_json` 是供应商级静态头，对同一 provider 的所有用户相同；`load_chat_model` 只为 opencode 系 provider 注入固定的会话头。没有稳定身份头时，外部网关只能看到 API Key 粒度，无法把用量归属到 Yuxi 的用户 uid，多用户共享同一 API Key 时尤其不可分。共享 Key 场景下，未签名的 uid 头只是归因信号——任何持 Key 调用方都能改写它，不足以支撑网关侧的配额强制。

## 决策

`model_providers` 增加布尔列 `include_user_uid`，默认 `false`，schema 版本从 business 7 幂等推进到 8。开启后，`load_chat_model` 在向该供应商发起的聊天模型请求头中注入 `x-yuxi-uid`，值为当前 agent runtime context 的 `uid`。默认关闭保证既有 provider 与外部契约零变化。

请求头在 `load_chat_model` 统一注入：chatbot graph、subagent graph、context middleware 和 summary middleware 四个 agent runtime 调用点传入已有的 `context.uid`。调用方未提供 uid 或值为空时不注入，不生成随机占位值，避免把匿名流量记成真实用户；uid 含 CR/LF 等 HTTP 头非法字符时显式失败，不发出畸形请求。uid 只进请求头，不进请求体、日志和模型可见输入。

开启即带签名：`include_user_uid` 是唯一配置项，开启后 Yuxi 对 `uid=<uid>\nts=<unix 时间戳>` 计算 HMAC-SHA256，与 `x-yuxi-uid` 一起附加 `x-yuxi-uid-ts`、`x-yuxi-uid-sig`；网关按请求 Key 查密钥、校验时间戳窗口后重算比对，可检测篡改并拒绝超窗签名。当前签名没有每请求唯一 nonce，捕获到的头仍可在时间窗口内重放；这不是严格的一次性防重放机制。签名密钥只从固定的 `YUXI_UID_SIGNATURE_SECRET` 读取，不落库。保存 provider 时若开关为开启而该环境变量缺失，service 直接拒绝并给出变量名与文档指引（partial 更新按 DB 现值判定，防止漏传字段绕过）；保存后密钥又被移除时，运行期 fail-closed 兜底，绝不发出未签名请求。不提供“携带但不签名”模式：未签名头只是归因信号，任何持 Key 调用方都能改写，留着它只会诱导把不可验的身份用于配额判断。

签名密钥没有可配置项，也不复用 `JWT_SECRET_KEY` 等认证密钥：认证密钥签会话 token，UID 签名密钥要进入外部网关的安全域，复用会把“伪造用量归属”的泄露升级为“伪造任意用户登录态”并耦合轮换。签名头只含 HMAC 输出、不含密钥，但若允许配置任意环境变量名，持有 provider 配置权的人即可获得两类间接通道：按“未设置报错 vs 签名成功”探测任意环境变量是否存在，以及用持续产出的 (消息, 签名) 样本离线猜解该变量承载的密钥。固定名称让这类通道结构上不存在：没有任何用户输入的字符串会进入 `os.getenv`，不依赖校验规则的正确性，也不受绕过 service 直接写库的影响。代价是同一实例的所有已签名供应商共用一把密钥；多网关各持各钥没有当前 consumer，如需支持应在服务端以白名单开放命名空间，而不是在配置面接受自由文本。

该开关只覆盖 agent 对话链路。知识库抽取、评测、样例问题等后台系统任务没有用户身份，继续不带该头；这些路径由系统 API Key 计量，不伪装成用户流量。provider 管理接口（`ModelProviderPayload`）与前端新增/编辑供应商弹窗暴露这一个开关，供应商卡片展示当前状态。Gemini 不支持此请求头，服务端拒绝为 Gemini 开启该选项，前端也禁用对应开关。

签名时间戳只能在发送边界现算：`load_chat_model` 只解析并校验 uid（开关、空值、头非法字符、签名密钥是否就位），把 uid 存到模型适配器实例上；时间戳与 HMAC 在每次出站请求构造时重新生成，经 `extra_headers` 下发，覆盖流式、非流式和网络重试后的下一轮请求。模型实例由主 Agent 和摘要长期复用——长工具调用后的下一轮模型请求、晚触发的摘要、`NetworkRetryMiddleware` 600 秒预算内的重试都复用同一实例——而网关按 ±300 秒窗口验签，构图时一次签名会让这些后续请求必然携带过期签名被拒。uid 校验留在构图期，密钥就位校验同时保留在构图期与发送期：保存后密钥又被移除（例如只重建了部分容器）时，发送边界显式失败，不降级为未签名头。

## 替代方案

- 只扩展静态 `headers_json`：零新字段，但 uid 是每请求动态值，静态头无法表达，拒绝。
- 在请求体注入 uid：部分供应商会拒绝未知字段，且请求体会被更多中间层记录；`x-opencode-session` 已证明请求头是可行通道，选头。
- 所有请求无条件注入 uid：后台任务没有真实用户 uid，会产生随机 UUID 伪装成用户身份的假数据，拒绝；由 provider 级开关显式开启且仅在调用方传入 uid 时注入。
- 复用 `JWT_SECRET_KEY` 做签名密钥：零新增配置，但泄露面从计量升级到认证绕过，且轮换相互耦合，拒绝。
- provider 可配置密钥 Env 变量名（即使加命名空间白名单）：唯一好处是多网关各持各钥，但把自由文本喂给 `os.getenv` 需要长期依赖校验正确性（直写库可绕），且该场景没有当前 consumer，拒绝；固定名称让攻击路径结构上消失。
- 双开关（“携带用户 ID”与“签名”分离）：看似给“只记录不验签”的使用方留了路，但未签名头不可验、只能用于报表，留着它会诱导把弱身份用于配额判断，还多出一个需要互相校验的配置项；拒绝，携带即签名。
- 未签名裸头模式：同上，且需要独立文档说明信任边界，拒绝。
- 在 `load_chat_model` 一次生成签名并写入 `default_headers`：实现最短，但签名时间戳被冻结在构图时刻，而模型实例被主 Agent 和摘要长期复用，±300 秒窗口之后的所有后续请求（长工具调用后的下一轮、晚触发摘要、网络重试）都会带过期签名被网关拒绝；拒绝，改为每次发送边界现算。
- 在 Yuxi 内建用量账本：属于另一个问题域，本次明确不做，用量计量由外部网关控制。

## 后果

外部多了三个请求头契约 `x-yuxi-uid`、`x-yuxi-uid-ts`、`x-yuxi-uid-sig`，网关需同步识别；开关默认关闭使未升级消费者无感。uid 与签名会随请求离开 Yuxi 边界到达供应商，仅在管理员显式开启的 provider 上发生。签名引入密钥分发、按 Key 映射与轮换的运维成本，并要求两边严守 `uid=<uid>\nts=<ts>` 规范化格式；格式变更等于双边发布。签名密钥固定读取 `YUXI_UID_SIGNATURE_SECRET`，多网关场景下同一实例共用一把密钥，运维需知悉该边界；API 与 worker 两个进程都需要该变量，保存期校验只看得到 API 进程，worker 缺失时由运行期 fail-closed 暴露。签名改为每次请求现算后，`models/chat.py` 顶层导入 `langchain_anthropic` 与 `langchain_google_genai`（原为函数内惰性导入，但两者都已被 deepagents 预加载，惰性没有省到导入成本），使 anthropic 家族也能在同一边界注入。Gemini 不支持该请求头，服务端拒绝在 Gemini provider 上启用，管理界面会禁用开关。该开关不覆盖知识库抽取、评测等后台模型调用，这些路径的用量仍按系统 Key 计量。新增 provider 字段改变了 business schema 版本与 `ModelProvider` 序列化形状，管理 API 消费方会多看到一个布尔字段。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 开关开启且调用方传入 uid 时，聊天请求头包含 `x-yuxi-uid` 且值等于该 uid | 开关形同虚设，或写入错误/空值 | `backend/package/yuxi/models/chat.py` | `pytest test/unit/services/test_model_selectors.py -k uid`：MockTransport 捕获流式与非流式出站请求头 | 恢复“不注入”后 `test_load_chat_model_user_uid_header_follows_opt_in` 与 `test_user_uid_header_reaches_real_stream_and_regular_requests` 失败 | Passed |
| 开关关闭、uid 为空或调用方未传 uid 时不注入该头 | 关闭的 provider 也带头，泄露用户身份 | `backend/package/yuxi/models/chat.py` | 同一测试文件内三条负向断言 | 移除开关或空值条件后负向用例失败 | Passed |
| 含 CR/LF 等头注入字符的 uid 显式失败，不发出畸形请求 | uid 含换行导致请求畸形或被注入伪造头 | `backend/package/yuxi/models/chat.py` | `test_load_chat_model_rejects_header_unsafe_uid` | 去掉字符校验后该测试失败 | Passed |
| 开启后追加时间戳与 HMAC-SHA256 签名，且签名可被独立重算复现 | 签名与约定格式不一致，网关无法验签 | `backend/package/yuxi/models/chat.py` | `pytest test/unit/services/test_model_selectors.py -k uid`：`test_load_chat_model_signs_user_uid_header`、`test_user_uid_header_reaches_real_stream_and_regular_requests` 断言三头与按 `uid=<uid>\nts=<ts>` 重算的签名 | 修改规范化消息或头名后重算失配 | Passed |
| 同一模型实例跨越验签窗口后再次发送时重新生成时间戳与签名，覆盖流式、非流式与重试 | 长任务后续请求带过期签名被网关拒绝 | `models/chat.py` 的 `_get_request_payload` 注入 `extra_headers` | `test_user_uid_signature_refreshed_after_window_expiry`（MockTransport 连发两次、时钟 +301 秒）、`test_anthropic_model_signs_user_uid_at_every_send_boundary`（anthropic 家族载荷层）、`test_anthropic_model_without_uid_switch_sends_no_extra_headers` | 去掉 `_attach_user_uid_headers` 后断言"第二次时间戳与签名均刷新"、"anthropic 载荷含新签名"的用例失败 | Passed |
| 签名密钥环境变量缺失时运行期显式失败，不降级为未签名 | 密钥被移除后静默发裸头，网关收到可伪造身份 | `backend/package/yuxi/models/chat.py` | `test_load_chat_model_rejects_signing_without_secret`（构图期）、`test_user_uid_signing_fails_closed_when_secret_removed_after_load`（构图后删除密钥的发送期） | 删除构图期或发送期密钥校验后对应测试失败 | Passed |
| 签名绑定 uid 与时间戳，任一侧篡改即失配 | 签名不覆盖 uid，攻击者可改写 uid 仍通过验签 | 规范化消息契约 | `test_user_uid_signature_binds_uid_and_timestamp`（网关侧重算比对） | 篡改 uid 或 ts 后 `hmac.compare_digest` 为 False | Passed |
| 保存期校验：开关开启而密钥缺失时创建与更新均被拒绝并给出指引；partial 更新按 DB 现值判定 | 漏传开关字段绕过校验，保存后请求才失败 | `providers/service.py` | `test_create_provider_config_rejects_uid_header_without_signature_secret`、`test_update_provider_config_checks_effective_uid_header_flag`、`test_provider_uid_header_save_passes_when_signature_secret_present` | 去掉保存期校验或改用 payload 现值后测试失败 | Passed |
| provider 创建、编辑接口可读写 `include_user_uid`，默认关闭且只接受 JSON 布尔值；Gemini 与缺少密钥的设置被拒绝 | 字符串或数字被 HTTP schema 强制转换为 true，或不支持的配置静默保存 | `providers/service.py`、`model_provider_router.py` | `test/integration/api/test_model_provider_uid_header.py`：ASGI HTTP + PostgreSQL 往返 | 非布尔值得到 422、Gemini 与缺少密钥得到 400，且未创建 provider | Passed |
| 签名密钥只读固定环境变量，配置面不存在任何环境变量名字段 | 重新引入可配密钥 Env，借自由文本恢复探测/猜解其他环境变量的通道 | `yuxi/models/providers/cache.py` 的 `USER_UID_SIGNATURE_SECRET_ENV` 常量、`providers/service.py` 字段集合 | 负向符号搜索：`grep -rn uid_signature_secret_env backend web docs` 无结果 | 该字符串重新出现在配置面或 `os.getenv` 参数中即失败 | Passed |
| 开关随模型缓存跨进程透传，旧缓存缺省关闭 | Redis 旧缓存缺字段导致 KeyError 或误开 | `providers/cache.py` | `pytest test/unit/services/test_model_cache.py`：rebuild→save→load 往返与缺省值用例 | 旧缓存 JSON 缺字段时加载不报错且按关闭处理 | Passed |
| business schema v7 幂等迁移新增列并记录版本 8 | 版本门禁错误拒绝已支持的升级源，或版本记录先于 DDL | `storage/postgres/manager.py`、`storage_migration.py` | `test_supported_legacy_business_schema_is_converged_and_versioned_as_current[7]`；真实 PostgreSQL 删除列后重放 `ensure_business_schema` | 不接受未知版本；列恢复之前不记录版本 | Passed |
| 前端可切换开关并随保存发送，卡片展示状态，Gemini 时禁用开关 | 开关不持久化、编辑回显丢失或 Gemini 显示为可用 | `web/src/components/model-management/ModelProviderManagePanel.vue` | `pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`；Playwright mocked API 页面检查新增弹窗 | 删除表单字段或 payload 字段后页面与保存行为变化 | Passed |
| agent runtime 四个调用点把 context.uid 传给 load_chat_model | uid 断链，头永远缺值 | chatbot/subagent graph、context/summary middleware | `pytest test/unit/agents/test_summary_graph_config.py`、`test_builtin_discovery.py` 断言 uid 穿透 | 去掉任一调用点 uid 传参后对应测试失败 | Passed |

## 风险

`x-yuxi-uid` 与签名头属于对外新增请求头契约，外部网关需要同步识别；开关默认关闭使未升级消费者无感。uid 与签名会随请求离开 Yuxi 边界到达供应商，仅在管理员显式开启的 provider 上发生。签名防止不知道密钥的 API Key 持有者任意改写 UID，但只限制捕获签名的重用时长，不能消除窗口内重放；也防不住本就持有密钥的 Yuxi 管理员。若业务要求严格防重放，协议还需加入每请求唯一 nonce/请求标识并由网关原子去重。要彻底按用户强制配额，网关应以 API Key→用户映射为主、签名 uid 为校验。后台链路（知识库抽取、评测、样例问题）不携带该头，其用量仍按系统 Key 计量；如未来需要用户级归属，应在对应 service 显式传入身份，而不是默认注入。

Playwright 通过 Vite 页面检查了新增弹窗；由于开发 API 当时不健康，页面请求使用 mock，且当前新增弹窗的供应商类型列表没有 Gemini，未在浏览器里验证 Gemini 禁用态。签名密钥通过 API/worker 环境变量配置，不由前端输入；字段创建、更新、回读与拒绝路径由 ASGI HTTP + PostgreSQL 集成测试覆盖。
