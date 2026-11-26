# Public Agent 对话演示客户端

状态：implemented
类型：feature
Owner：packages/agent-demo/src/useDemo.js

## 问题

外部应用需要独立网页展示真实对话能力，使用绑定不同 APP 的 Key 切换凭据，在 APP 内切换终端用户，并在桌面演示手机、平板布局。

演示应用逐个上传同一批附件。首个文件还在等待时，用户可能切换 APP、终端用户或会话，继续上传会让后续文件归属新的身份或成为不可见草稿。

## 决策

### 实现方案

`packages/agent-demo` 使用 Vue、Vite 和 Node 提供独立网页。Node 只启动静态开发服务，浏览器直接调用默认 `http://localhost:5173` 的 Public API。APP、Key、终端用户、服务地址和布局偏好保存在浏览器 localStorage；对话和执行状态由服务端拥有。每个 APP 使用绑定它的独立 Key，用户通过 `X-End-User-Id` 声明。服务端的 `X-App-Id` 用于识别和校验绑定，客户端配置不授予权限。终端用户 ID 在新增和 Header 发送边界限定为可打印 ASCII 字符，显示名称支持中文；随机配置 ID 与幂等键使用局域网 HTTP 可用的 `crypto.getRandomValues`。

`src/api.js` 固定调用身份，集中请求、SSE 和带凭据的 Thread artifact 下载；`src/protocol.js` 按稳定 item ID 与逻辑 event ID 合并公开消息，恢复已提交内容块并拒绝迟到增量；`src/useDemo.js` 拥有身份与视图生命周期、回读时序、幂等输入和等待恢复。同一视图的 history、artifact 回读按请求序号接受最新结果；身份或 Thread 切换中止旧订阅并拒绝旧结果写入。网络或服务端失败保留原始幂等请求，用户手动重试。

界面加载可见 Agent 和分页 Thread，提供 IM 风格流式对话、生成中追加 `follow_up`、取消、结构化回答与审批恢复、继续暂停队列、工具过程、失败原因和文件下载。下载路径来自公开 state 或消息虚拟链接，Markdown 链接在边界解码一次，再由 API 边界编码。原始 Markdown HTML 被禁用，渲染结果经过 sanitizer。调试记录仅在内存中保留最近 80 条，Key 脱敏。

桌面使用 9:16 或 4:3 设备画布，平板会话侧栏常驻、手机侧栏按需打开；真实手机直接显示聊天。右侧调试台在宽屏常驻，窄屏使用原生 dialog 管理遮罩、键盘焦点和 Escape 关闭。

添加附件先上传为草稿，发送时携带草稿 ID。内联图片使用保留 MIME 的 data URL。每个文件开始前，上传流程检查发起批次的身份和视图，失效时结束剩余文件。切换 APP 或终端用户会中止该身份的在途上传。只切换 Thread 时，在途上传可以结束，其响应仅在发起视图仍有效时更新列表。启动、CORS 配置和附件格式见[包内说明](https://github.com/xerrors/Yuxi/blob/main/packages/agent-demo/README.md)。

## 替代方案

- 集成到产品前端：可以复用 UI，但需要产品登录与完整前端依赖，不能独立演示 API Key 接入。
- Node 转发 API：便于跨域连接，与用户要求的浏览器直接访问冲突。
- 原生 DOM 管理状态：依赖较少，对流式消息、响应式抽屉和等待交互的维护成本更高。
- 仅捕获原 API 客户端能固定发送凭据，但旧视图离开后仍可能继续上传不可见草稿。复用现有视图检查可以停止尚未发出的文件。

## 后果

Demo Origin 需要由 Yuxi 的 CORS 配置允许。默认网页端口为 5180；服务端默认开发白名单只有 5173，启动说明明确增加 Demo 来源。手机访问需要使用局域网服务地址。浏览器配置含 Key，使用者可以在调试台显式查看；请求记录不保存 Authorization Header。放弃本地待重试请求不撤回服务端已经接收的输入。

切换会话后，待上传文件可以在目标会话重新选择。已经收到的草稿由服务端保存及到期清理。

## 验证

- `cd packages/agent-demo && npm test` 延迟第一份附件并切换 APP，检查第二份旧附件没有提交，同时核对新 APP 可以独立上传自己的批次。恢复旧循环后，请求记录会出现归属新 APP 的旧附件。
- 使用仓库已有 Prettier 格式化包内 JS、Vue、CSS、HTML 和 JSON，并执行 `prettier --check`：Passed。
- `cd packages/agent-demo && npm run lint && npm test && npm run build`：Passed；13 个协议 / API 单元测试通过，构建成功。
- `cd packages/agent-demo && npm run test:browser`：Passed，15 个用例通过；浏览器使用独立 HTTP fixture 核对 DOM、布局比例、身份 Header、幂等重试、追加消息、取消、回答、审批、SSE 重连、过期 history、Markdown 中文下载路径、键盘焦点、失败原因、日志脱敏和下载文件字节；真实局域网 HTTP Origin 下不存在 `crypto.randomUUID` 时仍可启动、发送、取消和新增 APP；中文 ID 不保存或切换，中文名称配 ASCII ID 可以连接。fixture 不替代真实数据库或 worker 证据。
- 真实浏览器连接默认 5173 服务地址：Passed，独立临时 APP / Key 的目录与会话可读、身份切换清空视图、跨 APP / 用户读取返回 404，重复创建只产生同一 Thread，中文文件通过真实授权下载接口返回与 Workspace fixture 一致的字节。执行命令为 `node /tmp/yuxi-agent-demo-boundary-run.mjs`；一次性准备器使用当前测试账号创建隔离资源，完成后撤销 Key、清理测试 Thread 和文件，停用测试终端身份。
- `node /tmp/yuxi-agent-demo-live-run.mjs` 将 `test/live-smoke.mjs` 接入两个隔离 APP 的临时 Key：Passed。浏览器发送两条消息后回读真实 Public history 与 Turn，核对两轮 completed 的最终文本、`result_run_id` 与消息 Run 归属、浏览器最终正文、用户隔离和 APP 切换；结束后撤销 Key 并清理测试 Thread。真实链路依赖当前模型供应商可用性和余额，可复用命令与凭据环境见包内说明。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：Passed，64 个 gate 单元测试通过。
- `docker compose exec -T api uv run --group test pytest test/unit -m "not slow"`：未执行到 pytest，现有容器构建 editable 包时不能更新 `yuxi.egg-info` 的时间戳。使用已安装解释器执行 `docker compose exec -T api python -m pytest test/unit -m "not slow"`：Passed，2515 passed、55 skipped、7 subtests passed；跳过项不计为通过。
- `cd docs && pnpm run build` 与 `git diff --check`：Passed。
