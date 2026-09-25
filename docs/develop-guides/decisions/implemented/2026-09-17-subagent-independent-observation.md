# 子智能体派发与等待分离，页面独立观察子 Run

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/middlewares/subagent_task.py

## 问题

同步 task 把子 Run 创建和等待放在同一次工具调用中，父 checkpoint 在等待期间没有子 Run 身份。父页面从 checkpoint 读取状态，父 graph 等待多个子任务时无法独立显示先完成的任务。

## 决策

模型仅获得 subagent_start/status/await/cancel。start 立即返回并写入 subagent_runs，await 只等待已有 Run。state 提供身份，页面用现有 Run HTTP/SSE 读取当前状态，不向父流复制子 Run 生命周期。父 Run 终态级联取消、FIFO、lease、runtime cleanup 和工具恢复归属保持原有策略。

AgentRunRepository 按用户和父 Conversation 的持久关系查询子 Run；chat_service 的状态 HTTP 入口据此补齐记录，覆盖创建提交后尚未写入 checkpoint 的窗口。查询包含历史 Run，以创建时间和 ID 稳定排序。

useSubagentRuns 按 run_id 独立保存观察结果，不被父 checkpoint 的旧状态覆盖。页面切换用户、会话或停用时关闭订阅；迟到 HTTP 响应不能写入新视图。HTTP/1.1 下最多保留三个子 Run SSE，为父流和普通请求留出连接；其余活跃子 Run 每两秒回读，连接空缺后建立订阅。已有子流无事件时每十五秒核对状态，终态关闭连接，故障显示重连提示。

工具行按参数或结果的 run_id 定位侧栏运行 Tab，同一线程的不同 Run 不共用 Tab。详情从 history 的 Run 列表与消息加载指定运行，不重复读取 checkpoint；仅当前可见且页面激活的 Tab 订阅 SSE，停用时中止 HTTP/SSE，重新激活沿已有游标续接。加载失败保留已有内容并提供重试，迟到响应不得覆盖停用视图。

历史 task ToolMessage 保留展示，新装配不注册 task。历史 checkpoint 尚未执行的 task 调用由工具执行器明确报错，不隐式重放；升级前完成或取消旧版本活跃 Run。已保存的内置和自定义 Agent 提示词由管理员按[使用子智能体](../../../agents/subagents-management.md)更新，默认值变化不覆盖用户编辑。

## 替代方案

- 保留 task：继续维护两套时序和结果格式，创建身份仍晚于等待。
- start(wait=true)：只统一名称，保留阻塞派发的问题。
- 父流转发子任务全部状态：增加发布路径与双来源合并，而子 Run 已有可订阅接口。
- 删除 task 并独立订阅：采用此方案，接受串行任务可能多一轮模型调用与多个受限连接。

## 后果

子任务完成可见性不再依赖父 graph 的 values 事件。HTTP 数据库查询拥有当前状态，SSE 只负责及时触发回读。子任务超过连接预算时，状态更新有最多约两秒的轮询间隔；网络故障不承诺固定延迟。前端保留历史消息渲染，后端移除同步 executor 与专属结果包装器。

## 验证

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow'`：Passed，2152 passed、53 skipped；覆盖工具装配、立即派发、未知历史工具拒绝、状态与权限相关纯逻辑。标准不带 `--no-sync` 的命令因容器系统 site-packages 无写权限而失败，使用已安装依赖完成验证。
- `docker compose exec -T api uv run --no-sync pytest test/integration/api/test_subagent_state_recovery.py -q`：Passed，1 passed；真实 PostgreSQL/HTTP 从没有父 checkpoint 的持久关系恢复子 Run，其他用户收到 404。该测试加入 Runtime System Tests。integration/E2E 共享清理 fixture，必须串行执行，避免一个测试会话清理另一个会话的活跃测试 Run。
- `docker compose exec -T api uv run --no-sync pytest test/e2e/test_deterministic_agent_path_e2e.py -k 'subagent_worker_enforces_inherited_write_policy or subagent_end_is_observable' -q`：Passed，3 passed；确定性 replay 验证 start/await、两种审批模式、子输出消息，以及父 await/慢子任务仍 running 时快子任务已完成并可独立订阅终态。该文件由既有 Runtime System Tests 选择。
- `docker compose exec -T web pnpm run lint:check`、`docker compose exec -T web pnpm run test:unit`、`docker compose exec -T web pnpm run build`：Passed，341 项前端测试通过；观察器验证快慢任务、旧快照、同子线程新 Run、重连游标、迟到响应、清理、查询失败与八任务连接上限。
- Playwright 真实页面验证：Passed；使用相同确定性快慢子任务，DOM 一项完成、一项运行中，同时 HTTP 回读父 Run 为 running；释放慢任务后回读父子最终结果。八任务连接上限由前端单测覆盖，未执行八个真实 worker 的浏览器压力测试。
- `pnpm --dir docs run build`、`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：Passed，工程契约 unit 62 项通过；校验相对链接、decision 生命周期和 workflow 接线。真实外部模型未运行，确定性 replay 不替代 provider 行为校准。

旧能力不存在：新工具注册、prompt、内置 Skill 和 E2E fixture 不再发起 task；历史消息渲染有明确 consumer 而保留。工具集合精确断言会在重新注册 task 时失败；start 测试把任何隐式 await 变为失败。

重新引入条件：只有实测模型往返成本不可接受且能在等待前可靠交付持久身份时，才重新评估阻塞快捷工具。

本决定取代[状态面板展示](./2026-08-25-state-panel-display.md)中从父 state 直接展示子任务状态的部分，保留其按子线程收敛和历史描述回填规则。

独立 Reviewer 已复核完整 diff、生命周期、权限、连接预算、历史 consumer 和验证边界，未发现剩余必须修复的问题。

本轮详情优化验证：新增 4 项前端测试覆盖 await 参数关联、指定 Run 历史隔离、隐藏 Tab 清理/游标续接、失败重试/迟到响应；前端全量 345 项通过。Playwright 在真实 worker 快慢任务中点击等待工具打开侧栏，注入一次 history 网络失败后点击重试恢复；父子最终结果断言通过。现有日志中的子线程 state/history 返回 200、服务端耗时约 5–10ms，未复现用户偶发错误本身，不能据此断言所有卡顿均来自连接排队。

整体 Review 补充：工具行优先显示对应 Run 的最新状态，保留工具调用本身的错误；状态观察与 SSE 结束后的终态回读均传递取消信号，隐藏/切换时释放在途 HTTP。三个回归用例在修复前分别因旧 pending 覆盖 completed、状态请求未取消、终态回读未取消而失败，修复后相关 15 项通过；独立 Reviewer 复核无剩余必须修复的问题。
