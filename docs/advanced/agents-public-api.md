# Agents Public API

本页供外部集成开发者查找 Session 接口、输入格式、响应和恢复规则。第一次接入见[运行一次公开 Agent 对话](../intro/agents-api-quickstart.md)。部署的 `/docs#/agents-public-v1` 与 `/openapi.json` 提供生成的字段、类型、限制和示例。运行时机制见[Agent 输入队列与调度](../mechanisms/agent-request-queue.md)。

## 协议范围与差异

主路径 `/api/v1/agents/sessions` 对齐 [OpenAI Agents Sessions](https://developers.openai.com/api/docs/guides/agents-api/sessions) 的核心子集：保存的 Agent、字符串或消息数组初始输入、消息/取消事件、公开持久 Items 和 `agent.session.*` 输出。Session ID 沿用内部 Thread UUID；数据库 Session 行与 Thread 一对一，调用方始终使用公开 UUID。

创建返回 `201`，事件提交返回 `202`。创建、详情、列表元素、POST 更新、已读和归档返回相同的 `object=agent.session` 资源。核心字段包括 `id`、`agent`、Unix 秒 `created_at/last_active_at` 和工作 `status`；展示、队列和等待点位于 `yuxi`。`agent.model` 返回会话配置快照中的有效模型，创建时依次从显式值、Agent 配置和系统默认模型解析；空会话同样要求模型可用。Session 工作状态为 `idle/in_progress/requires_action/completed/failed/cancelled`。空会话为 `idle`；执行及协作等待为 `in_progress`；需要回答或审批为 `requires_action`；最近 Turn 的成功、失败、取消直接返回对应终态。后续输入是否可以接收由等待点与队列门禁决定。归档使用 `yuxi.archived`；未读使用独立的 `yuxi.unread`，已读操作不改变工作状态。

`yuxi.current_turn` 为最近轮次的概览，字段为 `id/status/current_run_id/result_run_id/waitpoint`。Session、Turn 详情和 SSE 中的工作状态使用相同语义。waitpoint 只描述等待内容；Run 的状态用于执行段详情。

`last_active_at` 来自持久接收回执、Input 消费/取消和 Run 执行时间；没有活动时使用创建时间。标题、置顶、已读和归档更新不改变该字段。创建回执单独放在 `yuxi.receipt`，普通资源读取的该字段为空。

SSE 是覆盖多轮工作的长期订阅，单轮终态后保持连接。调用方按 Turn ID 观察 `completed/failed/cancelled`，回读持久结果并主动关闭连接；不承诺官方 SDK 自动结束调用或收集最终结果。创建流先返回 `agent.session.created`，其 `session` 为同一 Session 响应。

Yuxi 要求创建与提交携带幂等键，每次仅接收一个事件，图片只接受内联 data URL。创建仅支持已保存的 `agent_id` 与 `agent.model` 覆盖；环境配置、内联完整 Agent、外部 `tool_result` 和 computer-use approval 未实现，返回 `422`。FIFO、显式 steer、人工回答/通用工具审批、回执和 Run/Input 查询、协作控制属于 Yuxi 扩展。客户端按本页协议和部署实例的 OpenAPI 集成；上游 SDK 对其他能力的支持不构成 Yuxi 的接口承诺。

```json
{
  "id": "<session-id>",
  "object": "agent.session",
  "agent": {"id": "default-chatbot", "model": "<provider:model>"},
  "created_at": 1791504000,
  "last_active_at": 1791504000,
  "status": "in_progress",
  "yuxi": {
    "title": "新的对话",
    "archived": false,
    "unread": false,
    "receipt": {
      "object": "yuxi.session.event.accepted",
      "event_id": "<receipt-id>",
      "session_id": "<session-id>",
      "input_id": "<input-id>",
      "turn_id": null,
      "run_id": null,
      "status": "accepted"
    }
  }
}
```

示例省略部分展示与队列字段，完整响应以 OpenAPI 的 `SessionResponse` 为准。`yuxi.receipt.status=accepted` 只表达该次持久接收；普通消息回执始终保留空的 Turn/Run，消费归属从 Input 查询，工作结果从目标 Turn 读取。

## 身份与作用域

登录用户用 JWT 调用 Public API；个人集成可用未绑定 APP 的 `agents` 或 `full` Key，以密钥所属用户身份访问同一产品作用域。外部应用使用绑定 `app_id` 的 `agents` API Key。服务端从 Key 决定 APP，客户端的 `X-App-Id` 不改变作用域。绑定 APP 的 Key 可提供 `X-End-User-Id`，服务端在 Key 所属用户和 APP 内解析独立终端用户；未提供时使用该 APP 的默认终端用户。后续查询和订阅使用相同 Header。JWT 与未绑定 APP 的 `agents` 或 `full` Key 不接受该 Header（包括本人 UID）；APP 终端用户不能访问产品 Thread 或 Workspace。密钥创建与权限见 [API Key 接入](./api-key-integration.md)。

所有 APP 终端用户，包括默认终端用户，都无法发现、读取或运行私有 Agent。JWT 和未绑定 APP 的个人 `agents` 或 `full` Key 可使用自己的私有 Agent；系统管理员治理其他人的私有定义使用产品管理接口。完整规则见[资源权限](../mechanisms/resource-permissions.md)。

系统的用户列表和共享候选默认只显示系统用户。管理员在用户管理中通过“用户类型”筛选显式查看 APP 终端用户或全部类型，“所属部门/实例”列显示终端用户的 `app_id`。长用户名和 ID 在列表中缩略，点击用户信息可查看完整值。

## Session、Thread、Turn、Run 与 Input

Thread 是长期对话，Turn 是一轮工作，Run 是其中一段有执行 owner 的运行。每次普通消息提交保存为独立 Input，`follow_up` 逐条 FIFO，`steer` 在消费时按接收序号合批为连续就绪前缀。线程空闲且队列未暂停时领取优先队头并创建 Turn/Run；运行中在安全边界消费 steer，在同一 Turn 创建下一 Run。回答或审批消费明确等待点，也在同一 Turn 创建下一 Run。接收响应中的 `event_id`、`input_id` 和状态只证明持久接收；工作结果通过 Turn 查询。

`/api/v1/agents/sessions` 是主协议。创建回执的关联 ID 位于 `yuxi.receipt`；事件提交回执使用 `yuxi.session.event.accepted`，保留明确的 `session_id`、`input_id`、`turn_id` 和 `run_id`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/agents`、`/api/v1/agents/{agent_id}` | 查询可见主 Agent |
| `POST`、`GET` | `/api/v1/agents/sessions` | 创建空或带首批输入的 Thread；按 APP 作用域列出 active Thread |
| `GET`、`POST` | `/api/v1/agents/sessions/{session_id}` | 读取资源；修改标题、置顶及后续输入默认配置 |
| `POST` | `/api/v1/agents/sessions/{session_id}/archive` | 无活跃 Turn 和待处理 Input 时归档，保留历史 |
| `POST`、`GET` | `/api/v1/agents/sessions/{session_id}/events` | 提交单个输入或控制事件；订阅整段 Thread |
| `GET` | `/api/v1/agents/sessions/{session_id}/queue` | 查看待处理 Input 和暂停状态 |
| `GET` | `/api/v1/agents/sessions/{session_id}/inputs/{input_id}` | 查看接收、消费和消息归属 |
| `GET` | `/api/v1/agents/sessions/{session_id}/receipt?idempotency_key=<key>` | 响应丢失后定位原提交的回执 |
| `GET` | `/api/v1/agents/sessions/{session_id}/turns` | 分页发现本会话的轮次，包含零输出轮次 |
| `GET` | `/api/v1/agents/sessions/{session_id}/turns/{turn_id}` | 查看整轮状态、等待点、Run 和明确结果 |
| `GET` | `/api/v1/agents/sessions/{session_id}/items` | 分页读取会话公开内容 |
| `GET` | `/api/v1/agents/sessions/{session_id}/turns/{turn_id}/items` | 分页读取指定轮次的公开内容 |
| `GET` | `/api/v1/agents/sessions/{session_id}/runs/{run_id}` | 查看指定执行段 |

Thread 的创建、事件提交都必须提供长度为 1–128 的 `Idempotency-Key`。同一身份、Thread 和键重复提交相同意图返回首次回执；改变命令、目标或内容返回 `409`。未知字段、批量事件或无效内容返回 `422`。

Session 列表返回 `{object:"list", data, first_id, last_id, has_more}`，默认 `order=desc`、`limit=50`，limit 为 1–100。以创建时间和 ID 稳定排序，将上一页 `last_id` 作为 `after`；改名、置顶、已读和归档不会移动下一页边界。`agent_id`、`archived` 与 `is_pinned` 筛选授权范围内的资源；`after` 必须属于当前用户和 APP，否则返回 `400`。Web 单独读取全部置顶页并合并展示，普通列表游标保持独立。

POST 更新使用 `{"agent":{"model":"<provider:model>"},"yuxi":{"title":"新标题","is_pinned":true,"tool_approval_mode":"always_trust"}}`，每个字段均可省略。模型使用 `agent.model`，消息级覆盖使用事件的 `yuxi.model`；显式 `null` 没有重置语义，返回 `422`。更新响应为同一 Session 资源。会话配置来自创建时的 Agent Context 与默认值，修改保存的 Agent 或系统默认值不会改变该会话；更新模型与审批模式影响更新后接收的新输入，已接收的队列输入、当前 Turn 和恢复执行保留原配置。

## 创建与提交消息

创建或提交消息时可传 `yuxi.mcps: ["<connector-slug>"]`，将连接器追加到当前会话。选择与创建时的 Agent Context MCP 取并集并去重，`all` 保留按当前目录解析的语义；省略或空列表不移除选择。空会话也支持该字段。连接器选择随接收事务持久化，后续输入无需重复传入，刷新页面后继续保留；不会修改保存的 Agent 或其他会话。

`yuxi.mcps` 改变会话的后续资源选择；同一请求中的 `yuxi.attachment_file_ids` 只归本次 Input，消费后绑定对应 Message，消息事件的 `yuxi.model` 也只覆盖本次 follow-up。三者通过同一请求传递，各自按所属作用域生效。

已经接收的 Input 保留原配置快照。运行中的 steer 在安全边界的新 Run 合入本批连接器选择，连续 steer 合批不丢失选择，模型和审批配置仍继承原 Run。取消 pending Input 不撤销已接收的会话选择，重试改变新增选择返回 `409`。持久选择不代表授权：工具装配与出站继续复查当前执行用户、服务开关和本人的连接；不可用连接器沿用现有过滤与警告行为。

创建可传 `agent_id`、`title`、`project_id`、`agent.model`、`tool_approval_mode`、`input`、`yuxi.attachment_file_ids`、`yuxi.mcps` 和 `stream`。`agent_id` 使用可见 Agent slug；`input` 是文本字符串或最多 20 条有序 `user` 消息，内容块支持 `input_text` 与内联 `data:image/...;base64,...` 的 `input_image`。每条消息最多 10 张图片，图片内容总量最多 80 MiB；内置 nginx 对 Thread 创建和消息事件放行 100 MiB 请求体，外层代理也需配置相应上限。附件及 `stream=true` 均要求同时提供非空输入。带输入创建先把 Thread、Input、提交级附件绑定和回执在同一数据库事务提交；有附件时再准备 Workdir 文件并提交就绪事实。只有文件就绪且队列允许领取时才在消费事务创建 Turn/Run 与正式 Message，提交后投递 worker。

```bash
curl --fail "$BASE_URL/api/v1/agents/sessions" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'X-End-User-Id: crm-user-42' \
  -H 'Idempotency-Key: crm-thread-0001' \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"default-chatbot","input":[{"role":"user","content":[{"type":"input_text","text":"你好"}]}]}'
```

向已有 Thread 提交普通排队消息使用 `yuxi.mode=follow_up`；优先处理使用 `yuxi.mode=steer`。steer 位于所有 follow-up 前面，每次提交保留独立 Input，领取时才合批；待领取期间可以逐条取消。普通消息不接受 `yuxi.turn_id`；Input 在消费时固定 Turn/Run，接收回执不返回 mode。即使发送期间当前 Turn 已结束，steer 仍会优先开启新 Turn。

未指定 mode 时，服务在 Thread 锁内按运行中 steer、空闲 follow-up 选择；等待用户回答、审批或取消清理时拒绝普通消息；协作等待时默认为 follow-up，只进入 FIFO 队列。一次 POST 只接收一个事件，事件内可有最多 20 条有序消息。幂等重试返回同一回执与 Input ID，不重新入队；提交、转引导和取消输入的回执及 accepted SSE 始终保持 Turn/Run 为空。Input 查询在消费后返回实际 Turn/Run 归属，两类 pending Input 的 Turn/Run ID 均为空。

新 Input 在接收时冻结完整的可配置 Context，包含模型、审批模式、提示词、资源选择和执行限制。`follow_up` 的单次模型与审批覆盖随消息原子接收，仅作用于该输入。steer 不接受显式模型或审批配置；空闲调度使用合批首个 Input 的冻结配置和执行来源，运行中安全接管沿用当前 Run 配置。运行身份由 worker 注入，资源权限在准备和工具执行时校验。调度、暂停和逐条取消的详细规则见[输入队列机制](../mechanisms/agent-request-queue.md)。

```bash
curl --fail -X POST "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'X-End-User-Id: crm-user-42' \
  -H 'Idempotency-Key: crm-message-0002' \
  -H 'Content-Type: application/json' \
  -d '{"events":[{"type":"agent.session.input.message","yuxi":{"mode":"follow_up"},"input":[{"role":"user","content":[{"type":"input_text","text":"请继续"}]}]}]}'
```

Input 查询返回原始有序 `messages`、原始 `attachment_file_ids`、现存 `attachments`、附件准备状态及消费归属；`items` 在 pending/cancelled 时为空，消费后为真实 Message。正式历史不包含排队内容。消费消息的 `received_at` 是接收时间，`created_at` 是执行投影时间；历史遵循服务端分页顺序，不能按接收时间重排。Run 详情、Turn 的 runs、历史运行元信息和生命周期 SSE 使用有序 `input_ids`；提交回执仍使用单个 `input_id`。

`yuxi.session.input.promote` 控制事件携带 `input_id`，并复用 `Idempotency-Key`，将未消费 follow-up 转为引导。已消费/取消、附件未就绪、等待回答/审批/协作或取消清理时返回 `409`。暂停队列允许调整优先级但不恢复执行；任务结束后的 pending 输入可优先开启下一轮。相同命令重试返回原回执，新的命令转换仍 pending 的 steer 视为已满足，不重复调度。客户端在成功或 `409` 后回读队列。

## 图片与文件附件

直接视觉输入使用 `{"type":"input_image","image_url":"data:image/png;base64,..."}`，保留实际 MIME 与完整 data URL。当前不支持远程图片 URL。内联图片不会自动生成 Workdir 文件；需要文件操作或 Agent OCR 工具时，把图片作为文件附件上传。

`POST /api/v1/agents/files` 接收 multipart 的 `file`，大小上限 5 MiB，返回 `201` 文件草稿资源：

```json
{"id":"0123456789abcdef0123456789abcdef","object":"file","filename":"report.pdf","bytes":1024,"mime_type":"application/pdf","created_at":1791504000,"expires_at":1791590400,"status":"draft"}
```

上传不创建 Session，也不写入 Workdir 或对话历史。草稿在 24 小时内提交；`GET /files/{file_id}` 读取草稿信息，`DELETE /files/{file_id}` 删除未提交草稿。未提交的过期原文件和派生资源在同作用域后续上传时清理。服务端管理存储地址，调用方只保存 `id`。

```bash
curl --fail "$BASE_URL/api/v1/agents/files" \
  -H "Authorization: Bearer $API_KEY" \
  -F 'file=@report.pdf'
```

创建与后续消息统一使用提交级 `yuxi.attachment_file_ids`，每次提交最多 20 个。两种提交都校验用户、APP、有效期与已有归属；同一文件重复绑定明确拒绝。接收时绑定 `input_id`，消费时把本次全部文件绑定该 Input 最后一条用户 Message；多 Input 同 Run 消费时分别绑定自己的末条消息。附件属于整个提交，内联图片仍属于各自消息。旧的 `input[].yuxi.attachment_file_ids` 返回 `422`，不做兼容转换。附件顺序参与幂等意图，改变列表返回 `409`。

```json
{"agent_id":"default-chatbot","input":[{"role":"user","content":[{"type":"input_text","text":"请总结报告"}]}],"yuxi":{"attachment_file_ids":["0123456789abcdef0123456789abcdef"]}}
```

```json
{"events":[{"type":"agent.session.input.message","input":[{"role":"user","content":[{"type":"input_text","text":"请总结报告"}]}],"yuxi":{"mode":"follow_up","attachment_file_ids":["0123456789abcdef0123456789abcdef"]}}]}
```

接收回执证明 Input 与来源已经持久化。准备失败仍返回已接收回执，`GET /sessions/{session_id}/inputs/{input_id}` 和 `/queue` 用 `attachment_status=preparing`、`attachment_error` 表达未就绪；恢复扫描调用同一准备函数补全 Input 与目标文件。回执读取和同幂等键重放只返回接收事实，不补文件或派发。未就绪输入不能执行，也不会被后续 FIFO 输入跳过。准备完成提交后状态为 `ready`，清理 MinIO 原文件和私有预解析临时内容，正式内容由 Workdir 拥有；清理失败由现有恢复循环重试。无附件输入同样返回 `ready`。

提交后通过 `GET /sessions/{session_id}/attachments` 回读正式文件引用、`input_id` 和 artifact URL；draft 读、删、预解析接口不再接受该文件（`409`）。文件归授权 Workdir 管理，使用相同 Project 的 Session 可通过文件 API/工具发现它。模型上下文只列本次输入与此前已消费输入的附件，不提前列出后续排队或已取消 Input 的文件。取消排队输入保留已接收文件，归档会话或软删除 Project 后仍恢复尚未完成的文件提交，但不启动新 Run；显式删除使用 `/sessions/{session_id}/attachments/{file_id}`，文件提交未完成、待消费引用或运行期间拒绝删除。

删除成功会移除附件记录和文件，历史不保留占位；Input 的原始 `attachment_file_ids` 不变，不承诺已删除文件可回放。取消只取消执行，不释放附件为草稿，也不隔离它在共享工作目录中的工具可见性。

产品手动 OCR 预解析与图片缩略图处理分别使用 JWT 私有 `/api/agent/files/{file_id}/parse`、`/api/agent/images`，所有 API Key 都不能调用。Public 上传与发送不需要预解析、解析引擎或对象路径。配置了 `ocr_parse_file` 的 Agent 仍可按需解析 Workdir 文件，详见[运行时文件机制](../mechanisms/agent-runtime.md)。旧 Public 临时上传、confirm、parse 和 images 入口已移除。

## 错误与重试

错误保留 FastAPI 的 `{detail: ...}` 结构，校验错误 detail 可为数组。`401` 检查凭据，`403` 检查 Key 权限/终端身份，`404` 表示资源不存在或当前作用域不可见，`409` 表示幂等意图冲突或状态不允许，`422` 表示输入/配置/游标无效。超时或丢失响应使用原幂等键重试同一意图；修改意图必须生成新键。各接口的生成文档列出适用错误与处理方向。

## 断线恢复

创建响应丢失时，重放相同 `POST /sessions` 请求、身份和幂等键，得到原 Session 与 `yuxi.receipt`。已有 Session 的提交响应丢失时，先用原键查询回执：

```bash
curl --fail --get "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/receipt" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'X-End-User-Id: crm-user-42' \
  --data-urlencode 'idempotency_key=crm-message-0002'
```

回执 `404` 只表示该次查询尚未发现已提交记录；原请求仍可能在途。重放原意图与原键会由同一幂等边界串行处理。查询超时保留原命令与键，不生成另一条提交。接收后用 `input_id` 查询 Input；pending 等待消费，cancelled 表达排队取消，consumed 的 `turn_id/run_id` 指向固定执行归属。控制回执直接指向原控制目标。

观察目标 Turn 的持久状态；完成后读取该 Turn 的 `yuxi.output`，归属由 `result_run_id` 决定。Session 的 current_turn 是当前活动概览，排队输入可能属于另一轮；相邻 Turn 的完成不能结算当前提交。`in_progress` 继续观察，包括协作等待；`requires_action` 按 waitpoint 提交回答或审批。

SSE 断开后保留最后一条已成功处理事件的 `id`，通过 `Last-Event-ID` 重连。收到 resync 时暂停事件应用，分页读取目标 Turn 的 Items 和 Turn 资源，合并成功后推进游标。HTTP 200、EOF、正文 done 和 Run settled 都不能代替目标 Turn 终态。事件已过期或未收到任何 delta 时，仍可从持久 Turn 输出取得结果。重新打开客户端可从 Session、Turn 列表和 Items 找到持久工作。

## 等待、取消与队列

Turn 等待用户回答或审批时普通消息被拒绝，协作等待时 follow-up 只进入 FIFO 队列。Turn 资源的 `yuxi.waitpoint` 提供 `id`、`kind` 和应回答的问题或应决策的工具调用。恢复事件必须提供 `turn_id`、`waitpoint_id`，并按等待点完整提交 `answer` 或 `approval` 响应；旧等待点或重复改变意图返回 `409`。

```json
{"events":[{"type":"yuxi.session.input.resume","turn_id":"<turn-id>","waitpoint_id":"<waitpoint-id>","response":{"type":"answer","answers":[{"question_id":"<question-id>","answer":"确认"}]}}]}
```

官方取消事件为 `agent.session.input.cancel`，可用 `yuxi.turn_id`、`yuxi.expected_run_id` 固定目标。省略目标时，事务内选择当前 Turn；幂等重试仍取消首次选定的 Turn。取消使当前 Turn 收敛，有 pending 输入时暂停保留的队列，空队列不设置暂停；执行和必要清理完成后直接结束取消，等待点先清理 checkpoint。`yuxi.session.input.continue` 在清理完成后显式解除暂停并领取原优先队头。用户向暂停会话发送新的消息时，解除目标队列暂停并优先单独执行这条消息，之后恢复旧队列；附件未就绪时旧队列继续等待，旧 steer 不与恢复消息合批。幂等重放不再次恢复队列。`yuxi.session.input.cancel_input` 只移除指定的待处理 Input，不冒充尚未创建的 Turn。归档拒绝活跃 Turn 或待处理 Input；归档后的详情与历史仍可读。

## 读取与事件

Session、Turn 和 Items 列表均返回 `{object:"list", data, first_id, last_id, has_more}`，使用 `after/limit/order`。Session 默认每页 50 条，Turn 与 Items 默认 20 条，limit 为 1–100。默认 `order=desc`，传上一页 `last_id` 为 `after` 读取更早内容；`order=asc` 从最早内容开始。同一消息的多个公开 item 可分在不同页。Items 的游标必须属于当前 Session，指定 Turn 的 Items 游标还必须属于该 Turn；否则返回 `400`。排队和已取消的未消费 Input 从 `/queue` 或 `/inputs/{input_id}` 读取，只有消费后的正式 Message 才进入 Items；内部 prompt、checkpoint 和未登记的助手审计不进入 Items。

Items 页面另含 `yuxi.runs`，仅返回当前页内容引用的轻量 Run，使用 `id` 标识执行段，提供状态、时间和错误。Turn 列表是摘要，包含核心状态和 `yuxi.current_run_id/result_run_id/waitpoint`；完整执行段、用量和输出通过指定 Turn 的详情读取。数据库先按作用域、游标和页大小选择公开内容，查询不会先装载全部历史再截页。

`GET /sessions/{session_id}` 返回 Session 资源，`yuxi` 包含 `current_turn`、`queue_paused` 和 `queued_input_count`。`/turns/{turn_id}` 返回 `object=agent.session.turn`，核心字段与 SSE 事件中的 `turn` 使用同一投影：`id`、`session_id`、Agent、状态、Unix 秒时间、错误和用量。`started_at` 表达本轮首次执行，恢复 Run 不重置它。核心状态为 `queued/in_progress/requires_action/completed/failed/cancelled`；取消清理期间保持 `in_progress`。waitpoint 提供等待内容，内部 Turn/Run 状态不用于修正公开工作状态。核心 `usage` 为 null，Yuxi 用量统计位于 `yuxi.usage`。

Turn 详情的 `yuxi` 包含 `current_run_id`、`result_run_id`、`waitpoint`、`cancellation_pending`、`runs` 和 `output`。`cancellation_pending=true` 表示本轮已接受取消且仍在停止或清理，适用于 Run 保持 `interrupted` 的等待取消；客户端据此恢复停止反馈，终态后该值为 false。结果只来自 `yuxi.result_run_id` 指向的本轮 Run；模型正文结束、`interrupted` 和 `yielded` 均不表示 Turn 完成。`/turns/{turn_id}/items` 复用相同分页与公开投影。`/state` 提供运行状态，内容统一从 Items 读取。普通用户可读取已经展示的工具参数、结果和执行状态；内部 prompt、checkpoint 和完整审计不进入普通历史，审计仍仅允许超级管理员 JWT。

`GET /sessions/{session_id}/events` 订阅整个 Thread，每条 SSE `data` 就是一个公开事件，`event` 等于其 `type`，`id` 是订阅 cursor。`event_id` 标识逻辑事件，Redis 重放保持稳定；它与 cursor 分开。`session_id` 是真实 Thread ID，`yuxi.run_id` 是业务执行段。协作成员通过自己的 Thread 入口读取历史和事件；父页面通过 `/state` 的 `agent_state.cooperation` 读取树内持久状态。

| 内容 | 公开事件 |
| --- | --- |
| 正文 | `agent.session.turn.item.added/done`、`content_part.added/done`、`output_text.delta/done` |
| 实际工具 | `function_call` 与 `function_call_output` item，通过 `call_id` 关联；完整参数和执行完成分别通知 |
| 业务整轮 | `agent.session.turn.created/in_progress/completed/failed/cancelled`，主体来自已提交 Turn |
| 能力受限 | `yuxi.session.turn.capability_limited`，只有通用提示，不包含无权资源元信息 |
| 原始推理 | `yuxi.session.turn.reasoning.delta/done`，关联对应 message item，不表达 reasoning summary |
| 人工等待 | `yuxi.session.turn.waiting`，等待点绑定真实 Turn/Run |
| 执行段、状态、恢复 | `yuxi.session.run.*`、`yuxi.session.turn.state`、`yuxi.session.turn.context_compression`、`yuxi.session.resync` |

客户端按 `item_id`、`output_index`、`content_index` 合并，delta 追加、done 完整值替换；终态 item 拒绝迟到的增量。只有 Turn 的 `result_run_id` 对应输出使用 `phase=final_answer`，其他输出为 commentary。工具不会因名称被伪装为 OpenAI 托管搜索、命令或 MCP，也不生成上游未提供的参数增量。

活动 message 的 `yuxi.completed_content_indices` 与 `completed_reasoning_indices` 表示已持久化的块完成边界。resync 时用这些块的完整值补回漏收的 done，保留其他块尚未持久化的本地增量，忽略已完成块的迟到 delta，并清除旧展示平滑缓存。取消时由当前有效执行 Owner 保存已展示的部分正文，随后以 incomplete item 回读。

`Last-Event-ID` 使用 v2 cursor 续订。Redis 增量过期时发送 `yuxi.session.resync`，客户端缓冲新事件、重读 items 和快照，再按稳定 ID 合并。已完成正文与工具结果先持久化再发送完成边界；进行中的内容依赖短期 Redis 重放，不承诺逐 token 落库。

协作成员拥有独立 Thread、Turn、Run、等待点和结果，全部使用相同公开协议。父轮次完成、失败或取消均保留后代工作；普通取消固定指定 Turn。“停止全部”通过 POST `/sessions/{session_id}/events` 提交 `yuxi.session.tree.stop`，停止所有成员在途 Turn 与队列消费，先向各成员提交取消请求再清理等待点；`yuxi.session.tree.continue` 在在途工作收敛后重新消费保留队列，已取消轮次不恢复。全树在途工作及清理结束后，用户新消息可解除停止门禁并只恢复目标会话，其他成员旧待办继续暂停。这两种控制输入本身不作为 SSE 输出事件发布，结果通过持久树状态和各 Turn 观察。整树共享实际沙盒与 Project Workdir，各 Run 的 lease 和 heartbeat 独立。工具与用户操作见[会话协作](../agents/session-cooperation.md)。

该协议使用 Redis 事件格式 v2 和 cursor v2。数据库使用[部署参考](./deployment.md#_3-数据库与文件目录)定义的当前空库基线，app-init 在写入前拒绝其他版本；已有数据库由运维显式处置。旧创建请求顶层 `attachment_file_ids` 和逐消息 `input[].yuxi.attachment_file_ids` 不再支持，统一使用提交级 `yuxi.attachment_file_ids`；事件没有双格式消费。
