# Yuxi Agent Demo

独立的 Public API 对话演示客户端。桌面使用手机 9:16 或平板 4:3 画布，手机直接显示聊天界面。调试台管理 APP、Key 和终端用户，并查看公开请求与 SSE 事件。

## 启动

需要 Node.js 22.12 或以上版本，以及已启动的 Yuxi API / worker。

```bash
cd packages/agent-demo
npm ci
node server.mjs
```

打开 `http://localhost:5180`，在调试台填写绑定 APP 的 `agents` API Key，点击「保存并连接」。服务地址默认 `http://localhost:5173`，可以改为其他 Yuxi 部署地址；填写部署根地址，不包含 `/api`。APP ID 可以留空，连接后从服务端 `X-App-Id` 识别；显式填写时校验与 Key 绑定一致。多个 APP 分别保存自己的 Key，通过调试台切换。

`PORT=5181 node server.mjs` 可以修改网页端口。Node 运行 Vite 静态开发服务，浏览器直接请求 Yuxi；此启动器不配置 API 转发。`npm run build` 生成可独立托管的 `dist/` 静态文件。

## 测试托管浏览器

保存 APP Key 并在「用户」选择终端用户，然后打开调试台的「浏览器」，点击「连接沙盒浏览器」。这里会显示 `POST /api/v1/agents/managed-browser` 的 HTTP 状态和设备 ID。`202` 表示正在启动，可点击「检查连接」重试同一个身份。`200` 表示设备已就绪，可以在对话中让已接入 aBridge 的 Agent 使用该设备。切换 APP 或终端用户后，状态和请求记录会随身份重置。

## 跨域与手机访问

Yuxi 需要允许 Demo 网页的 Origin。服务端默认开发白名单只包含 5173；默认 Demo 使用 5180，需要按[部署文档的 CORS 配置](../../docs/advanced/deployment.md#跨域cors)设置允许来源并重启 API。例如保留产品前端来源，同时允许本机 Demo：

```dotenv
YUXI_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:5180,http://127.0.0.1:5180
```

使用实际手机时，打开启动器打印的局域网地址，把调试台服务地址改为电脑的局域网 Yuxi 地址，并在服务端加入该 Demo 的实际 Origin。手机上的 `localhost` 指向手机本身。

## 对话与数据

客户端自动加载可见 Agent 和当前 APP / 用户的会话列表，支持分页读取、历史恢复、流式 Markdown、运行中追加排队消息、停止、恢复回答与审批、继续暂停队列、工具过程与交付文件下载。Thread 下载请求携带当前 Key 和终端用户身份；产物路径来自公开 state 或消息中的虚拟路径链接。支持文件草稿上传和内联图片输入；产物预览仍不在范围内。添加附件不会创建 Session，发送时携带草稿 id；添加图片使用完整 data URL 并保留 MIME。文件规则见[Public API](../../docs/advanced/agents-public-api.md#图片与文件附件)。

APP、Key、用户列表、服务地址及布局偏好仅保存在当前浏览器 localStorage；对话和执行状态由 Yuxi 保存。新增本地用户后，首次调用携带 `X-End-User-Id`，服务端解析独立终端用户。用户 ID 使用可打印 ASCII 字符（例如 `customer-01`），显示名称支持中文。切换 APP / 用户会清空当前视图并中止旧请求、订阅。调试记录仅保留当前页面内最近 80 条公开事件或请求元数据，复制前脱敏 Key。

网络或服务端错误可能发生在消息已经接收之后。此时「重试」先按原键查回执，尚未发现接收记录时重放同一意图；创建重试使用原请求与键。历史初始读取最新 100 个 item，「加载更早消息」沿公开游标读取下一页。SSE 重连保留最后成功处理的 cursor，resync 回读目标 Turn 的内容与结果；没有正文增量时同样从持久结果恢复。未确认命令仅保留在当前页面内存，重新打开页面从服务端会话、轮次和内容找到已接收工作。

## 验证

```bash
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:browser
```

浏览器测试使用独立 HTTP fixture 验证协议请求、DOM 和下载文件字节，覆盖身份隔离、非法 Header 身份拒绝、幂等重试、流式恢复、等待交互和布局，并通过真实局域网 HTTP Origin 验证手机启动、发送和取消（运行环境需要局域网 IPv4）；它不证明真实模型、数据库或 worker 成功生成结果。

真实链路使用绑定测试 APP 的 Key，在环境中设置 `DEMO_API_KEY` 后执行 `npm run test:live`；`DEMO_BASE_URL` 默认 5173，`DEMO_URL` 默认 5180，`DEMO_SECOND_API_KEY` 可用于第二个测试 APP。脚本从真实网页创建并追加两条消息，回读 Public Items 和 Turn 的最终结果、附件 Input 关联与原件字节，核对 DOM、用户隔离和可选 APP 切换，最后归档创建的测试会话。凭据由调用者管理，脚本不会创建、打印或撤销 Key。建议使用独立测试 APP；模型供应商失败会使命令失败，不计为链路通过。

确定性 worker 验收可设置 `DEMO_AGENT_ID` 指定测试 Agent、`DEMO_EXPECTED_OUTPUT` 指定预期正文。`DEMO_RECOVERY_FAULTS=1` 在真实 API 接收后丢弃一次消息响应，并中断首次 SSE，随后断言回执恢复没有重复 POST。同源部署测试可用 `DEMO_ASSET_PROXY` 转发独立 Demo 页面资源，API 仍访问 `DEMO_BASE_URL` 指向的真实部署；独立跨域部署需要满足上述 CORS 配置。
