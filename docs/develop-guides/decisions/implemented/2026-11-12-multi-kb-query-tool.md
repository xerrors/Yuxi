# 决策：知识库检索工具接受多个 ID

状态：implemented
类型：feature
Owner：backend/yuxi/modules/knowledge/services/tools.py

## 问题

会话已经支持选择多个知识库，但 MCP `query_kb` 每次只能接收一个 `kb_id`。跨库问题需要模型重复调用相同工具。

## 决策

`kb_id` 接受单个字符串或非空字符串数组。字符串保留单库返回；数组去重后并行检索，按输入顺序返回 `kb_ids` 和合并的 `results`，每条命中保留自己的 `kb_id`、`file_id` 和 metadata。

### 实现方案

MCP 在现有单库输入 Schema 上扩展 ID 类型；Public HTTP 输入保持单库。Knowledge 查询服务先验证所有目标属于当前可见集合，再通过 `asyncio.gather` 并行调用现有 `retrieve`，结果按输入顺序合并。会话选择与当前用户权限的交集继续由 MCP access 服务提供。任一目标不可见时不启动检索；检索失败时等待全部请求结束，再传播输入顺序中的首个错误，不返回部分成功。调用被取消时取消所有子检索。复用各库的检索参数和排序，不增加 RRF、跨库分数比较或界面。

Knowledge 的引用适配按每条结果的 `kb_id` 关联来源，并与已规范化的输入目标和当前可见集合取交集；同名文件与分块仍按库区分，撤权后不再成为引用来源。Agent 通用引用服务继续调用该业务适配，不解释多库协议。

## 替代方案

- 模型逐库重复调用：现有能力可完成任务，但不能满足一次工具调用指定多个 ID 的要求。
- 全局修改共用 Schema：会同时扩展 Public HTTP 协议，超出请求范围。
- 跨库融合与重排：会引入新的排名语义；多个 ID 一起检索不要求此机制。
- 逐库串行检索：独立请求没有数据依赖，串行会累加各库延迟。
- `gather` 默认遇错立即传播：其他请求仍会继续，调用结束后可能残留检索；等待全部结束可复用各库已有的超时与清理。

## 后果

总返回量与并发请求数随目标库数增长；跨库拼接不表达全局相关性排序。并行消除独立请求的串行等待，但实际耗时仍取决于最慢的检索和底层资源竞争。失败不返回部分结果，模型须修正目标或重试。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 一次调用返回多个库的片段及原始来源；字符串单库兼容 | 工具参数与聚合 | MCP Schema、`services/tools.py` | 相关 unit 和真实 MCP HTTP 协议结果 | 空数组拒绝；重复 ID 只检索一次 | Passed |
| 独立库并行检索，逆序完成仍按输入合并，取消时所有子检索收尾 | 并行生命周期 | `services/tools.py` | Event 单测、真实 HTTP 双请求 Barrier | 串行实现因第二库无法启动而超时；取消后缺少子检索收尾即失败 | Passed |
| 多库输入不能绕过当前权限或会话选择 | 可见性校验 | access 服务与查询服务 | 真实 HTTP 委托调用 | 合法 ID 与库外 ID 混合整次拒绝 | Passed |
| 多库工具快照的引用按实际来源区分，过滤当前不可见或未请求的库 | 引用消费 | `mcp/references.py` | 引用 unit 与真实 HTTP 持久工具快照回读 | 跨库同名片段不去重；撤权与未请求来源不进入引用 | Passed |
| 单库 Public HTTP 协议保持原约束 | HTTP Schema | `schemas.py` | unit Schema 校验与 HTTP 请求 | 数组仍被拒绝 | Passed |

验证命令：

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：3756 passed、127 skipped。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_knowledge_tool_queries.py test/unit/routers/test_knowledge_mcp.py test/unit/services/test_knowledge_mcp_boundaries.py test/unit/toolkits/test_knowledge_mcp_client.py test/unit/routers/test_public_knowledge_tools_errors.py -q`：91 passed。多库结果、单元素数组和失败传播用例在实现前因数组被当成单个 ID 而失败；并行顺序用例在串行实现上因 Event 等待超时失败，取消用例验证两个检索都执行收尾。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_knowledge_mcp.py test/integration/api/test_knowledge_mcp_delegation.py -q`：20 passed。真实 API、认证、PostgreSQL 配置和 Dify HTTP replay 通过双请求 Barrier 验证并发，回读双库正文、来源、选择范围及即时撤权；外部 Dify 服务本身未作实时探针。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_turn_references.py test/unit/services/test_knowledge_mcp_boundaries.py -q`：55 passed。多库引用参数化用例在修复前均因 `unhashable type: 'list'` 失败。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_turn_references.py -q`：14 passed、1 skipped（既有真实模型探针未启用）。新增用例经 PostgreSQL 工具快照和真实 HTTP 验证双库引用来源。
- 后端 Ruff 检查、格式检查与 import 排序检查通过；文档 `pnpm run build` 和工程契约检查通过。

未验证范围：未运行真实模型自主选库或跨 worker 的 Agent E2E；工具执行入口与权限交集由上述真实 HTTP integration 验证。
