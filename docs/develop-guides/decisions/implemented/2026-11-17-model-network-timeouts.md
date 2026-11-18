# 模型网络停顿的超时与重试反馈

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/models/chat.py

## 问题

模型连接长时间没有响应时，Run 心跳仍正常，未配置请求超时使网络重试无法及时开始。用户无法区分正常思考与连接失败后的等待。

## 决策

### 实现方案

模型装配边界为 OpenAI 兼容与 Anthropic HTTP 客户端提供连接 10 秒、写入 30 秒、读取 120 秒、连接池等待 10 秒的默认超时。显式调用参数保留优先级。Google 客户端使用独立传输栈，不将 HTTPX 参数强塞给它。

Agent 继续由 NetworkRetryMiddleware 拥有重试，SDK 重试保持关闭。600 秒预算限制重试准入，每次重试开始前重新检查；它不是健康流式生成的总时限。正在执行的一次请求仍受网络阶段超时与 Run 总时限约束。取消直接传播。

网络重试通过现有 custom 事件和公开扩展适配发出脱敏通知，前端复用通知组件显示重试次数与等待时间，按事件去重并过滤非活动 Run；它是临时进度，不新增持久业务状态。失败流的模型尝试保留独立来源身份，成功回答不拼入失败片段。持久化及工具执行继续使用已有链路。

## 替代方案

- 整个模型调用限制 30 秒：会终止仍在持续输出的长回答，拒绝。
- SDK 与 middleware 同时重试：放大请求次数和等待时间，拒绝。
- 600 秒到点强制取消所有模型请求：会中断正常长生成，保留准入预算与阶段超时的职责区分。

## 后果

读取超时测量网络无数据间隔，服务端心跳可能保持连接。内部思考超过 120 秒且不发送任何数据会被重试；阶段超时不能消除供应商或代理故障。预算不是所有模型调用的总时限，已准入的请求可以跨过 600 秒继续输出。

通知只在事件抵达时短暂显示，页面重载不恢复通知，也不把它作为业务状态。Anthropic 的 LangChain 超时字段及客户端缓存只接受标量，因此 adapter 在创建 SDK client 后设置其请求 timeout；同步、异步请求均覆盖，保留显式标量及 `None` 配置。

## 验证

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/agents/test_network_retry.py test/unit/services/test_model_selectors.py test/unit/services/test_chat_service_langfuse_stream.py -q --tb=short`：Passed，158 项；包括 SDK 请求 timeout、显式值与参数别名覆盖、显式关闭超时、取消传播、调度等待越过预算后拒绝重试、脱敏事件与协议适配。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_chat_network_timeouts.py --timeout=15 -q --tb=short`：Passed，3 项。真实 HTTP 与生产使用的 v3 流协议覆盖响应头停顿、部分输出后停顿、持续输出；使用缩短的测试读取超时，断言最终正文和不同尝试的来源身份。
- `docker compose exec -T frontend node --test test/unit/modelRetryNotification.test.js`：Passed，当前 Run 通知、相邻 Run 拒绝、事件去重。真实浏览器 DOM 另验证同一事件只显示一条通知。
- 独立暂存副本后端全量 unit：3807 passed、127 skipped；显式超时参数修复另经聚焦测试验证。独立副本前端全量 unit：1089 passed；lint 通过，构建使用 `tsc --project tsconfig.json && vite build`（复用容器依赖，绕过 pnpm 对临时目录的自动安装检查）。前端 lint/build、后端 Ruff、工程契约与文档构建通过。
- 宿主机补跑 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`：211 passed、1 failed。当前工作区锁文件的清华镜像地址违反既有官方 PyPI 检查；该锁文件不属于超时修复，不修改它来掩盖 gate 失败。
- 部署后的 PostgreSQL/worker E2E、部分工具调用断流后的执行与持久快照回读、真实供应商超时探针：Not run。验证没有重启业务服务；HTTP 重放不能替代这些证据。
