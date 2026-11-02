# 自动标题只校验格式，不校验原文字词重合

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/titles.py

## 问题

自动标题通过与原消息的字词重合判断相关性，会拒绝“如何提升速度”概括为“性能优化建议”等合法标题。用户要求只保证标题合法规范，可修正的格式直接清理。

## 决策

### 实现方案

标题 service 删除原文关联校验及其参数。模型继续概括主题；候选结果清除思考段、标题前缀、外层引号及 Markdown 标题和强调标记，合并空白后检查空结果、占位标题和残留会话引用协议，再截断至 30 字，避免截断协议后绕过检查。通过格式校验的结果进入原有会话锁及写回事务。

本决定部分取代 [GUI 报告剩余缺陷](2026-10-12-gui-report-remaining-defects.md) 中的自动标题字词关联限制；标题生成、权限范围和持久化边界仍由原有 service 拥有。

## 替代方案

保留字词关联阈值或增加语义评判都会引入用户未要求的拒绝条件。完全原样写入会保留可清理的格式标记，不能满足规范标题要求。

## 后果

格式校验不判断模型概括质量。一次尝试、失败回退、手动改名优先级及历史会话不补生成的既有行为不变；不改变队列、Run、HTTP、Schema 和前端。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 无原文字词重合、单字、数字和其他语言的标题均可保存 | 语义过滤误拒绝 | titles.py | 真实 PostgreSQL 标题集成测试回读 | 旧实现运行新增 13 项用例全部因持久标题错误而失败 | Passed |
| 可修正格式规范化后持久化 | 模型输出格式 | titles.py | 真实 PostgreSQL 前缀、引号、Markdown、空白及长度用例 | 旧实现拒绝无重合候选；Markdown 和引号包装有明确预期结果 | Passed |
| 无效结果回退、一次生成和手动改名优先 | 事务写回 | titles.py / threads.py | 既有标题集成测试 | 超时、429、占位、思考段、引用协议和并发改名 | Passed |
| 引用协议跨越截断边界时仍拒绝候选 | 截断掩盖协议 | titles.py | 真实 PostgreSQL 回读输入回退标题 | 校验前截断使 `truncated_reference` 用例保存以 `@s` 结尾的候选，产生 1 项预期失败 | Passed |

- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_session_title_generation.py --confcutdir=test/integration/services -q`：46 passed；测试创建隔离 PostgreSQL Schema，在模型边界返回固定候选，重新读取数据库标题和命名标记。
- 后端全量 Ruff 检查、格式检查和 import 检查通过；修改测试文件的 Ruff 与格式检查通过。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：3520 passed、125 skipped；容器缺少仓库根文件的部署配置用例在宿主补跑 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`，207 passed。跳过项不计作产品通过证据。
- 工程契约检查及其 56 项单测、修改文档的 5 个相对链接目标检查、文档 `pnpm run build` 和 `git diff --check` 通过。
- Not run：真实 HTTP、worker、浏览器及真实模型完整链路；修复仅调整候选格式处理，沿用既有调用与写回流程，确定性集成测试不证明外部模型概括质量。
