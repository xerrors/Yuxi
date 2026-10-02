# 当前 Project 写工具审批豁免

状态：archived
类型：feature
Owner：backend/package/yuxi/agents/tool_approval.py

## 问题

默认工具审批模式需要减少当前 Conversation 所绑定 Project Workdir 内的常规文件创建和编辑确认，同时继续要求用户确认 Project 外写入与命令执行。

## 决策

默认模式使用 `HumanInTheLoopMiddleware` 的 `when` 谓词逐次判断写工具目标。`write_file` 和 `edit_file` 的 `file_path` 等于当前 Project Workdir 或位于其子目录时不触发审批；其他路径、缺失参数和非法路径继续触发审批。`execute` 始终触发审批，`always_trust` 不装配审批中间件。

Chatbot graph 从已准备运行上下文的持久化 `workdir_relative_path` 派生 Sandbox runtime Project 根，再把它交给审批 Owner。审批判断只改变是否中断，不改变 Sandbox Backend 的文件权限。默认模式下 SubAgent 继续隐藏敏感文件工具，不因这项豁免获得新能力。

## 替代方案

- 保持所有写工具都审批：无法减少当前 Project 内高频写入的无效确认。
- 将默认模式改为完全信任：同时放行 Project 外写入和命令执行，范围超过需求。
- 修改 Sandbox Backend 写权限：改变的是执行授权而非人工审批，不能表达本次交互语义。
- 自定义一套审批中间件：LangChain HITL 已提供按工具调用参数执行的 `when` 谓词，无需复制中断与恢复协议。

## 后果

当前 Project 内通过 `write_file` 和 `edit_file` 发起的写入自动执行；相邻路径前缀、其他 Project、父目录跳转、非绝对路径、URL、反斜杠路径和缺失参数保持人工确认。`execute` 即使以当前 Project 为工作目录也继续确认。审批豁免不是文件系统隔离，Sandbox 的实际读写能力维持现状。

## 验证

- `docker compose exec api uv run --group test pytest test/unit/agents/test_tool_approval.py test/unit/agents/test_summary_graph_config.py -q`：13 passed；覆盖 Project 内写入、Project 外写入、相邻前缀、非法路径、缺失 Workdir、`execute`、`always_trust` 和 Chatbot graph 装配。
- `docker compose exec api uv run --group test pytest test/unit/agents test/unit/services/test_chat_stream_interrupt.py -q`：102 passed；覆盖 Agent 中间件和既有审批中断投影。
- `docker compose exec -e RUFF_CACHE_DIR=/tmp/ruff-cache api uv run ruff check package/yuxi/agents/tool_approval.py package/yuxi/agents/buildin/chatbot/graph.py test/unit/agents/test_tool_approval.py test/unit/agents/test_summary_graph_config.py`：通过。
- `python3 scripts/verify_engineering_contracts.py && python3 -m unittest scripts.test_verify_engineering_contracts`：通过，61 passed。
- `git diff --check`（本次文件范围）：通过。
- 独立 Reviewer：No blocking findings；指出真实 HITL/Graph 装配证据缺口后补充 Chatbot graph 装配测试。真实 worker/SSE 的审批、resume 与多工具调用 E2E：Not run，残余风险限于框架运行时组合语义。
