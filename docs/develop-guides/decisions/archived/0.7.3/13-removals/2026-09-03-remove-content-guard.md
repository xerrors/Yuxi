# 移除内容审查能力

状态：archived
类型：simplification
Owner：backend/package/yuxi/services/chat_service.py

## 问题

内容审查曾在聊天主链路前后增加关键词与可选 LLM 判断，同时暴露三项系统配置、独立关键词文件、前端设置和专用错误展示。这套能力扩大每次 Agent 运行的模型可见输入路径、延迟和维护表面；当前产品不再提供该能力，需要完整移除，避免只隐藏配置却继续执行审查，或只删除运行逻辑却留下无效配置和文档。

## 决策

Agent 聊天服务不再执行输入、流式输出或完整输出内容审查，也不再产生内容审查专用错误。内容审查实现、关键词文件及专属测试不存在。

系统配置不再声明、返回或接受内容审查字段；Web 设置不展示相关开关和模型选择器，消息组件不保留内容审查专用错误文案，正式文档与导航不再介绍该能力。本决定在内容审查参考页及其稳定路径上部分取代[面向读者的文档写作与维护](../01-engineering-trust-and-docs/2026-08-26-human-centered-documentation.md)；运行时事实分别由聊天服务、系统配置字段、Web 组件和文档导航拥有，本记录不替代这些 Owner。

历史 PostgreSQL `system_options.value` JSON 中可能存在的旧键不执行数据迁移。配置读取只投影当前字段，配置更新拒绝未知字段但保留 JSON 中既有未知键，因此旧值不被读取、返回或修改，也没有运行时 consumer。

## 替代方案

- 保留现状：继续承担关键词误判、额外模型调用和跨前后端配置维护，不符合删除目标。
- 仅关闭默认值或隐藏 Web 配置：API 或历史数据仍可能重新启用运行逻辑，不构成完整移除。
- 保留关键词检查、只删除 LLM 检查：仍保留内容审查能力和错误协议，不符合完整移除。
- 完整移除：删除所有当前 consumer 和配置面；未来若重新引入，按新的产品需求重新设计并验证。

## 后果

Yuxi 不再在自身聊天链路拦截关键词或调用独立 LLM 判断内容。需要内容安全策略的部署方必须在模型供应商、网关或其他明确边界承担该策略。

已持久化的旧配置键可以留在 JSON 中，但不可见、不可更新且没有 consumer；未来若重新使用同名字段，必须显式处理这些历史值，不能让旧值静默恢复能力。历史消息若带有旧错误类型，会通过通用错误回退展示；本变更不重写历史消息。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| Agent 聊天不再执行内容审查，普通流式异常仍保存已生成内容与 trace | `docker compose exec -T api uv run --group test pytest test/unit -m "not slow" -q` | Passed：1698 passed，44 skipped |
| shipping API、worker、SSE 与 PostgreSQL 仍形成同一 Run 的完整因果链 | 启动 CI 确定性 replay 后运行 `docker compose exec -T api uv run --group test pytest test/e2e/test_deterministic_agent_path_e2e.py::test_deterministic_agent_path_reaches_persisted_result -m e2e -q` | Passed：1 test |
| 退役系统配置字段不被读取或更新，其他配置更新保留其原始持久值 | `docker compose exec -T api uv run --group test pytest test/integration/api/test_system_router_api.py::test_retired_system_config_field_is_hidden_rejected_and_preserved -q` | Passed：1 test |
| Web 设置不展示内容审查配置且前端可交付 | `pnpm run lint:check`；`pnpm run test:unit`；`pnpm run build`；Playwright 登录后回读基本设置 DOM 和浏览器 console | Passed：193 unit tests；旧设置文本与开关均不存在，console errors 为 0 |
| 正式文档与导航可构建 | 临时移开本地忽略目录 `docs/vibe/` 后运行 `pnpm run build` | Passed；仅有既有构建 warnings |
| 工程契约与 verifier 自测有效 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts` | Passed：61 tests |
| 当前运行路径、配置、Web、测试和正式文档没有旧能力符号或专属文件 | `rg` 搜索旧类名、配置键、错误类型、关键词文件名及用户文案；文件存在性检查 | Passed：当前树零命中，专属文件均不存在 |

旧能力不存在：当前源码、配置、Web、测试、正式文档和导航中不存在内容审查实现、调用、专属错误协议、三个旧配置字段、关键词文件或对应测试；历史 changelog 与旧 implemented decision 仅保留当时事实。

重新引入条件：出现明确的内容安全产品需求、可问责的策略 Owner、误判与延迟目标、权限和审计边界，以及覆盖输入、流式输出、完整输出和真实配置装配的风险相称证据后，以新提案重新引入。
