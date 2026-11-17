# 运行一次公开 Agent 对话

本教程供外部应用开发者使用 cURL 完成创建、接收、读取结果和继续对话。先由管理员配置可用聊天模型与 Agent，并创建绑定 APP 的 `agents` API Key。身份和权限规则见[API Key 接入](../advanced/api-key-integration.md)。示例使用 `jq` 读取 JSON。

## 准备调用身份

将部署地址、Key 和 APP 内的终端用户标识放入进程环境变量。以下示例始终携带相同终端身份。

```bash
export BASE_URL='https://yuxi.example.com'
export API_KEY='<api-key>'
export END_USER_ID='integration-user-42'
curl --fail-with-body "$BASE_URL/api/v1/agents" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID"
```

确认响应 `data` 中存在要使用的 Agent `id`，将其填入下一步的 `agent_id`。

## 创建并接收初始输入

每个逻辑创建请求生成并保存一个幂等键。网络超时重试时复用同一键和请求体。

```bash
CREATE_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
CREATED=$(curl --fail-with-body "$BASE_URL/api/v1/agents/sessions" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID" \
  -H "Idempotency-Key: $CREATE_KEY" -H 'Content-Type: application/json' \
  -d '{"agent_id":"default-chatbot","input":"用一句话介绍你的能力"}')
SESSION_ID=$(printf '%s' "$CREATED" | jq -r '.id')
INPUT_ID=$(printf '%s' "$CREATED" | jq -r '.yuxi.receipt.input_id')
printf '%s\n' "$CREATED" | jq .
```

观察 HTTP `201`、`object=agent.session` 和非空的 Input ID。接收成功后工作异步执行。普通消息创建回执的 `yuxi.receipt.turn_id/run_id` 始终为空。

## 定位本轮并读取结果

用 Input 查询消费归属，直到 `turn_id` 非空。下面每次执行读取当前持久状态。

```bash
INPUT=$(curl --fail-with-body "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/inputs/$INPUT_ID" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID")
TURN_ID=$(printf '%s' "$INPUT" | jq -r '.turn_id // empty')
printf '%s\n' "$INPUT" | jq .
```

取得 Turn ID 后，重复读取 Turn，直到状态为 `completed`、`failed` 或 `cancelled`；等待回答或审批时按[等待点协议](../advanced/agents-public-api.md#等待、取消与队列)提交结构化响应。

```bash
curl --fail-with-body "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/turns/$TURN_ID" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID" | jq .
curl --fail-with-body "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/items?order=asc&limit=100" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID" | jq .
```

`in_progress` 继续等待，协作等待同样如此；`requires_action` 按 `yuxi.waitpoint` 回答或审批。`completed` 时检查 Turn 的 `yuxi.result_run_id` 和 `yuxi.output`。Items 的 `data` 展示持久消息及工具过程，`phase=final_answer` 标识该 Turn 明确结果对应的助手正文。若 `has_more=true`，将 `last_id` 作为下一页 `after`。

## 继续对话与观察增量

新消息生成新的幂等键。显式 `follow_up` 按 FIFO 排队。

```bash
MESSAGE_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl --fail-with-body "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID" \
  -H "Idempotency-Key: $MESSAGE_KEY" -H 'Content-Type: application/json' \
  -d '{"events":[{"type":"agent.session.input.message","input":[{"role":"user","content":[{"type":"input_text","text":"再详细解释一下"}]}],"yuxi":{"mode":"follow_up"}}]}'
```

HTTP `202` 返回输入回执，复用前面的 Input → Turn 查询步骤读取本次结果。实时展示可在另一个终端、提交前订阅：

```bash
curl --no-buffer --fail-with-body "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID"
```

SSE 的 `data` 是事件 JSON，`id` 是恢复 cursor。连接覆盖多轮工作。观察目标 Turn 终态、回读结果后主动关闭。断线重连携带 `Last-Event-ID`；收到 `yuxi.session.resync` 时分页回读目标 Turn 的 Items 与详情，合并成功后再推进游标。完整限制与差异见[公开 API 参考](../advanced/agents-public-api.md)。

## 恢复丢失的提交响应

已有 Session 的消息响应丢失时，用保存的 MESSAGE_KEY 读取原回执，取出 INPUT_ID 后继续前面的定位步骤。

```bash
RECEIPT=$(curl --fail-with-body --get "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/receipt" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID" \
  --data-urlencode "idempotency_key=$MESSAGE_KEY")
INPUT_ID=$(printf '%s' "$RECEIPT" | jq -r '.input_id')
printf '%s\n' "$RECEIPT" | jq .
```

创建响应丢失时重放相同创建请求和 CREATE_KEY；回执查询返回 404 时重放相同消息和 MESSAGE_KEY。每次观察都沿 Input 的消费归属读取目标 Turn。列表提供更早轮次和内容的发现入口，最终结果始终读取对应 Turn 的 `yuxi.output`。

## 随消息提交附件或图片

发送前上传文件草稿，得到 FILE_ID；上传不会建立会话或写入 Workdir。

```bash
FILE_ID=$(curl --fail-with-body "$BASE_URL/api/v1/agents/files" \
  -H "Authorization: Bearer $API_KEY" -H "X-End-User-Id: $END_USER_ID" \
  -F 'file=@report.pdf' | jq -r '.id')
```

创建时将 FILE_ID 放入请求的 `yuxi.attachment_file_ids`；后续消息放入对应事件的同名字段。创建请求体例如：

```json
{
  "agent_id": "default-chatbot",
  "input": "总结这份报告",
  "yuxi": {"attachment_file_ids": ["<file-id>"]}
}
```

后续消息事件的 `yuxi` 为 `{"mode":"follow_up","attachment_file_ids":["<file-id>"]}`。沿原 Input 查看附件准备状态和消费归属，准备未就绪时不生成正式消息。准备失败保留同一次接收事实，按[附件协议](../advanced/agents-public-api.md#图片与文件附件)恢复，不用新幂等键重复提交文件。

直接视觉输入在消息 `content` 中加入 `{"type":"input_image","image_url":"data:image/png;base64,..."}`，保留实际 MIME 与完整 URL。
