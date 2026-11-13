# 决策：统一检索测试与片段原文读取

状态：implemented
类型：feature
Owner：backend/yuxi/modules/knowledge/services/retrieval.py

## 问题

多库工具与管理侧检索测试需要共用结果规范化、路径过滤与保存参数，避免同一查询在测试和 Agent 中产生不同的来源身份。测试页面默认使用保存配置，显式单次覆盖仍需保留。

来源片段的原文入口需要保持所属库、文件与行号。远端文件 ID 不代表本地文件，本地文件的全文读取能力也不代表普通用户可以调用管理侧内容接口。

## 决策

### 实现方案

检索覆盖服务保留既有参数声明与文件范围校验，改为调用与 Agent 相同的 `retrieve`，将标准结果中的片段列表交给管理接口。管理接口保持列表及 `result` 外层约定，省略 `meta` 等同空覆盖。前端检索测试把标准片段投影到已有渲染字段，保留分数、高亮、文件身份和原文行号。文档明确 `meta` 仅用于单次测试覆盖，不修改保存配置。

Knowledge 的片段弹窗提供“查看原文”，经父列表打开所属库和文件，并传入片段行号。本地库管理员保留 `FileDetailModal` 的完整文件操作；普通用户和外部库使用既有 `ExternalDocumentReader`，避免普通用户调用管理侧内容接口。文件级入口也按相同能力选择。`supports_document_read=false` 或缺少身份时保留片段预览，不展示原文操作；Turn 来源投影保留当前文档能力。缺少行号时从文档开头读取，不伪造定位。

## 替代方案

- 仅更换函数名称而保留 `aquery` 旁路：不能统一模型结果规范化。
- 改变 Public HTTP 协议或新增多库测试端点：现有单库测试与多库工具已经满足本次任务。
- 为每个来源维护另一套文件预览：现有按行阅读器已经支持本地和外部正文。
- 同时加入父子分块、标签、配置版本或 RRF：这些改变数据或排名语义，与入口与原文读取缺口独立。

## 后果

Web 检索列表保留工具结构中的来源身份，并在顶层保留已有的分数与高亮字段。外部库没有行号时只能从头阅读；提供方当前权限仍由后端每次读取复核。未加入独立排名与持久化机制。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 相同查询默认得到与 MCP 相同的片段；合法覆盖只影响本次 | HTTP 检索测试 | `services/retrieval.py` | unit、真实 HTTP 对比 | 恢复 `aquery` 旁路后标准身份缺失；非法覆盖仍被拒绝 | Passed |
| 点击多库片段读取实际所属库、文件与行号 | 来源原文 | Knowledge 结果组件 | 前端 unit、真实浏览器 DOM 与请求 | 同名跨库不串用；缺少 ID 或禁用读取能力时不发请求 | Passed |

验证命令与边界：

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：3756 passed、127 skipped。
- `docker compose exec -T -e TEST_BASE_URL=http://localhost:5051 api uv run --no-sync --group test pytest test/integration/api/test_knowledge_mcp.py test/integration/api/test_knowledge_mcp_delegation.py -q`：20 passed。临时 API 使用相同应用装配和真实 PostgreSQL，避免影响有活跃 Run 的主实例。验证相同默认查询的标准结果、临时覆盖后恢复保存配置、越界覆盖 422 和多库权限。临时实例在测试后关闭。
- `docker compose exec -T -e TEST_BASE_URL=http://localhost:5050 api uv run --no-sync --group test pytest test/integration/api/test_knowledge_mcp.py -k queries_multiple -q`：1 passed，主实例回读多库与管理侧检索结果。
- `docker compose exec -T frontend pnpm run test:unit`：1069 passed；`pnpm run lint:check` 与 `pnpm run build` 通过。
- `node --test test/unit/knowledgeReadingState.test.js test/unit/knowledgeQueryResult.test.js test/unit/kb_result_groups.test.js`：24 passed，覆盖零分、高亮、跨库来源、读取能力和管理员/普通用户的展示路径。
- `playwright-cli run-code --filename=frontend/test/browser/knowledgeSourceReading.js` 与 `knowledgeQueryReading.js`：Passed。真实 Vite 页面配合确定性 HTTP 夹具，回读不同库的请求 ID、offset、DOM 高亮、错误重试与无行号降级；验证本地管理员完整详情、普通用户只读请求，以及浅/深色和 768/1024/1440 宽度。

未验证范围：真实模型自主选库与外部服务实时漂移未作探针；浏览器 HTTP 夹具不替代后端权限与持久化验证。
