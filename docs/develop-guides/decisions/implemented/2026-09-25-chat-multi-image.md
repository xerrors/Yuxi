# 聊天输入支持多图（≤10 张）直读

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/input_message_service.py

## 问题

聊天输入框原先一次只能携带**一张**图片，限制写在四层：前端 file input 单选、前端单值状态、请求体 `image_content: str | None`、消息构造单参数。模型侧不是瓶颈——`deepseek-flash` 单请求上限 600 张，且实测能直读图片。

三个与图片数量无关的既有事实决定了做法：

1. **多图底座已经存在**。`build_chat_input_message_from_openai_content` 保留全部 `image_url` part，外部 Agent Call API 已在生产使用；`AgentRunInputMessage.raw_message()`（`langchain_message.model_dump()`）含全部图片，普通 Web 链路的 `_build_message_metadata` 也写进 `extra_metadata.raw_message`；`restore_chat_input_message` 优先用 `raw_message` 还原，与图片数量无关。
2. **多图数据已经在持久化，因此不需要 schema 变更**。base64 今天已落两份（`messages.image_content` 列与 `extra_metadata.raw_message`），加 LangGraph checkpoint 是三份；新增列只会成为第四份而收益为零。`BUSINESS_SCHEMA_VERSION` 保持 8，运行进程仍只做相等校验。
3. **硬顶来自体积**。单图压缩上限 5MB、base64 后约 6.8MB，**3 张大图就会撞** `nginx client_max_body_size 20M`；而 dev 拓扑没有 nginx，本地测不出这一点。

事实分工：wire 契约与图片归一由 `backend/package/yuxi/services/input_message_service.py` 拥有；HTTP 模型由 `agent_router.py` 与 `agent_invocation_eval_router.py` 拥有；历史 DTO 由 `conversation_service.py` 拥有；前端逐项状态与拖拽分流由 `web/src/components/AgentInputArea.vue` 拥有；体积放行由 `docker/nginx/default.conf` 与 `normalize_image_contents` 共同拥有。本记录不反过来成为运行时事实源。

## 决策

1. **wire 形态：宽化同一个字段**。`AgentRunCreate.image_content` 与 `AgentEvalRunCreate.image_content` 由 `str | None` 改为 `str | list[str] | None`。不新增字段，避免「两个字段谁优先」与别名退役条件。`image_content` 不删：CLI（`packages/yuxi-cli/src/yuxi_cli/client.py`）与已文档化的 API-key 用户是它的现存消费者。
2. **张数与总量在同一个归一函数里闭合**。`MAX_CHAT_IMAGES = 10`、`MAX_CHAT_IMAGE_TOTAL_BYTES = 80MB` 与公开的 `normalize_image_contents` 判定张数、元素类型与总量；`build_chat_input_message` 接受单值或数组并在内部归一，两条路由只把 `ValueError` 映射为 422。归一放在构造器内，`image_content` 的现存调用方既有单值也有数组，参数名与 wire 字段同名，这样只有一个真值来源。
3. **模型输入不加新字段**。多图事实由 `langchain_message` 承载；`AgentRunInputMessage.image_content` 保留为**首图**，仅作向下兼容与旧数据兜底。单图时 parts 逐字节不变（`data:image/jpeg;base64,` 前缀、text 在前、图片按请求顺序）。
4. **历史回显走后端窄投影**。history DTO 增加 `image_contents: list[str]`（由公开 `extract_image_contents(raw_message)` 从 content parts 取内联 base64），`image_content` 原值保留；无 `raw_message` 的旧行退化为 `[image_content]`。不让前端解析 `raw_message`：那是 `HumanMessage.model_dump()` 的形状，让浏览器读它等于把 LangChain 形状升格为 wire 契约，且旧行兜底会散在每个渲染点。
5. **SSE init 事件不改**（仍「首图 + `has_image` 布尔」）。重放裁剪白名单刻意不含图片字段；发送端本地已有列表，`useAgentStreamHandler` 既有合并逻辑扩成列表即可，运行中刷新由 history 覆盖。
6. **体积三层闭合**：nginx 只对 `/api/agent/runs` 这个精确 location 放宽到 100M（其余 `/api/` 仍 20M），后端按 base64 总量校验并返回 422，前端按 `imageContent` 长度累加做预算、超限不发请求。nginx 那段的代理设置显式写全而不嵌套继承：嵌套 location 不继承父级 `proxy_pass`，缺了它会退化成静态文件服务返回 404，而其余 `proxy_*` 是否继承要逐条推敲——该端点承载 SSE，缓冲设置错了会让回复憋成一大坨再吐。
7. **前端三条入口同路**：菜单「上传图片」改为多选、粘贴收集剪贴板全部图片（事件名同步改为 `paste-images`，载荷 `File[]`）、拖拽按 `image/*` 分流（图片走 vision，其余仍走附件）。菜单只负责选文件，上传与限流统一由 `AgentInputArea` 处理。图片的 OCR 解析入口保留在「添加附件」菜单。
8. **已知缺口**：文本模型 + 用户上传图片会被 `ImageInputCompatibilityMiddleware` 的硬编码话术拒绝，因为该兜底只覆盖 `read_file` 来源的 tool 图片——`_read_file_image_paths` 依赖 `ToolMessage.additional_kwargs.read_file_path`，而用户上传的图是 `HumanMessage` 里的 data URL，没有路径。重新引入条件：把 data URL 落成工作区文件再转 tool 图片，或在 wire 层对不支持视觉的模型直接拒绝。

## 替代方案

- **新增 `image_contents` 列**：被拒。多图事实在 `raw_message` 里已完备，新增列是第四份 base64 拷贝，还要付幂等 DDL 与版本推进的代价。
- **前端解析 `extra_metadata.raw_message` 取图（零后端改动）**：被拒。把 LangChain 形状升格为 wire 契约，且旧行兜底会在每个渲染点各写一遍。
- **删除 `image_content` 换成 `image_contents`**：被拒。对已文档化的 API-key 用户与 CLI 是破坏性变更。
- **新增 `paste-images` 事件而保留 `paste-image`**：被拒。唯一消费者在同一次改动内、`web/test/` 零命中，保留旧事件只会留下要靠 Reviewer 记得删的多余表面；改名让载荷类型变化可见。
- **只做计数上限、不做总量预算**：被拒。3 张 5MB 图即超 nginx 20M，计数上限在 wire 层不成立。
- **把图片改走 Files API（请求只带 file_id）**：暂不做。能从根上解决体积，但要引入第二种图片引用形态与生命周期清理，独立提案更合适。
- **聊天图片落盘到沙盒以让文本模型 OCR 兜底可用**：暂不做。需要决定落盘位置、清理 Owner 与权限边界，属 storage/sandbox 边界的独立变更。

## 后果

- 历史响应里同一条消息的 base64 出现三份：`image_content`（首图）、`image_contents`（n 张，本次新增）与 `extra_metadata.raw_message`（n 张，既有且前端不读）。本次新增那份让响应体积从约 (n+1) 份变为约 (2n+1) 份，10 张 5MB 图时该响应可达百 MB 量级。可行性上可以把 `raw_message` 的 image part 从历史投影里摘掉，但那是改动一个共享 wire 字段的内容，且未穷尽验证仓库外消费者，因此留给独立决策。
- 三份 base64 拷贝随图片数线性放大（列 + `raw_message` + LangGraph checkpoint）；`raw_message` 仍随每次 history 出网，而前端从该字段取图的路已被窄投影取代——这是既有浪费，留给后续提案收敛。
- 单轮 10 张的 token 与成本不设防（每张最多折算 1024 token），也不做前端缩放。
- 旧客户端零改动（发 `str` 仍可用，已实测 200）；**新前端打到未升级后端会 422**，因为 Pydantic 不会把 list 静默转成 str——这是刻意选择的显式失败。
- **`docker/nginx/default.conf` 是上游文件**，本次按取舍修改它，下次同步上游时该文件会冲突；且生产拓扑前面还有宿主机 nginx，其 `client_max_body_size` 默认 1M，需要在服务器上同样放行，否则请求到不了容器内这层。
- mime 声明不真：`build_chat_input_message` 仍硬编码 `data:image/jpeg;base64,`，PNG 也这么声明。实测无害（服务端按内容判断格式），且改它会波及单图 parts 的逐字节契约与已落库历史，故保持。
- 聊天框的图片行为（多图、拖拽分流）此前没有任何文档描述；本次只补了 API-key 文档，界面行为仍靠本记录与源码。

## 验证

- `backend/test/unit/services/test_input_message_service.py`（新增 11 条）：归一对非法类型、超量、超总量显式失败；无图仍是 `text`；**单图 parts 逐字节不变**；单值与单元素数组结果一致；**10 张全部进入 `raw_message` 且顺序保持**；历史投影只取内联图片、跳过外部链接；纯文本投影为空；旧单值行仍能还原出一张。
- `backend/test/unit/services/test_conversation_history_images.py`（新增 4 条）：多图历史行按顺序给出全部图片且单值字段保留；旧单值行退化为一张；纯文本行不产生图片；投影是字符串数组，形状由后端收窄。
- `web/test/unit/multimodal_image_limits.test.js`（新增 6 条）：拖拽分流（混合/纯图片/纯文档/缺 type 不误判）；base64 总量累加；上限常量与后端约定一致。
- 真实 HTTP：11 张 → **422** 且 `detail` 含张数，`messages` 行数未变（拒绝落在写库之前）；单值字符串 → **200**（旧客户端兼容）。
- 真实页面（已登录开发环境，真实 `deepseek-flash`）：菜单一次选 2 张 → 2 张预览；发送请求体 `image_content` 是长度 2 的数组，模型回复「第一张图上的文字是：IMG-A / 第二张图上的文字是：IMG-B」；刷新后历史渲染 2 张且接口返回 `image_contents` 为 2 个字符串、`image_content` 仍是首图、响应里没有 `raw_message` 的 part 形状（该字段本身仍在 `extra_metadata` 里随历史出网，见「后果」）；拖入图片进图片通道（无附件卡、无附件弹窗）、拖入 PDF 打开附件弹窗且图片数不变；11 张只收 10 张并弹出「最多添加 10 张图片，超出的未添加」。
- nginx：真实 nginx 容器加载本配置验证作用域——30MB 打 `/api/agent/runs` 被放行并代理到后端，30MB 打 `/api/chat/image/upload` 返回 413，110MB 打目标端点返回 413，其它 API 与静态资源不受影响。
- 回归：`pytest test/unit -m "not slow"` 2582 passed（其中 `test/unit/plugins/test_milvus_kb.py::test_query_filters_orphaned_chunks_from_search_results` 失败，已在**移除本改动**的干净树上复现同一条失败，属既有问题）；`pnpm run test:unit` 424 passed；`lint:check`、`docs build`、`verify_engineering_contracts.py` 通过。
- 独立审查（不继承开发上下文的 Reviewer，读源码 + 实跑）：复核了单图 parts 逐字节不变、10 张顺序保持、旧单值回落、Pydantic 宽化行为、nginx 作用域四条与嵌套 `proxy_pass` 那条注释，并复现了全部门禁数字。它发现的必须修项是**发送载荷的键失效**：发送端从 `{ image }` 改为 `{ images }` 后，`handleSendOrStop` 仍读 `payload?.image`，运行中只带图片发送会被判成「没有新输入」而走取消分支。已修（`payload?.images?.length`）。**危害等级未确认**：试图在真实页面复现「取消运行 + 静默丢图」时，该状态下的 Enter 被 `isSendButtonDisabled` 的其它项挡住（该项不止审查引用的那一项），因此没能实到那个结局；键失效本身可由改动前后的配对关系确认。
- 同轮按审查修掉的其余项：评估端点的网关口径写进 api-key 文档；前端边界单测从常量自比改为真谓词 `isWithinBase64Budget` / `remainingImageSlots` 并做了变异验证（把 `<=` 改成 `<` 后两条用例转红）；浏览器脚本的用户消息定位从「第一条带 image_contents 的消息」改为按 `type === 'human'`，运行中只带图发送那条断言放宽为稳定不变量（不取消运行且图片不静默消失），并把输入框定位改为兼容空对话页的 contenteditable。
- 未验证：`web/test/browser/chatMultiImage.js`（新增脚本，逐条对应上面手工步骤，尚未以 playwright-cli 形式跑通）；宿主 nginx 的放行属运维步骤，本机无从验证；单轮 10 张大图的端到端成本未测。
