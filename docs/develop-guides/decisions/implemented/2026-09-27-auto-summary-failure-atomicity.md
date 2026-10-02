# 自动摘要失败保持 checkpoint 原子性

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/middlewares/summary.py

## 问题

自动摘要把模型异常转换为 `Error generating summary: ...` 文本，并在历史文件写入失败时只记录警告。主模型随后会把错误文本当作对话历史继续运行，checkpoint 也可能保存无法回读原文的 `_summarization_event`。异步路径并发执行历史落盘和摘要调用，落盘失败时仍可能产生无用的摘要请求。

## 决策

自动摘要采用 fail-closed 边界。历史文件写入失败、摘要模型抛出异常或摘要内容为空时，当前模型调用直接失败，不调用主模型，也不构造新的 `_summarization_event`。PostgreSQL 中的聊天消息和 checkpoint 中已有的摘要事件保持不变。

### 实现方案

同步路径先保存待摘要历史并校验文件路径，再调用摘要模型和校验非空结果。异步路径按相同顺序串行执行，只有两个步骤都成功后才构造模型消息和 checkpoint update。外围 `wrap_model_call` / `awrap_model_call` 继续负责发送 `failed` 压缩事件并传播原异常。主动压缩已有的严格失败行为不变。

本决定只处理自动压缩失败原子性，不改变动态模型窗口阈值、按 token 保留消息、摘要 prompt、usage 统计或 85% 手动压缩提示。

## 替代方案

- 保留警告并继续主模型调用：会把不可恢复或错误的摘要视图当成有效历史，拒绝采用。
- 历史落盘和摘要继续并发，在汇总结果后统一失败：可以阻止 checkpoint 更新，但落盘失败时仍会产生无效的摘要调用。
- 失败后回退到未压缩历史调用主模型：上下文已经达到压缩条件或发生 overflow，回退不能保证请求可执行，并会掩盖压缩失败。

## 后果

自动压缩失败会使当前 Run 进入既有错误通道，调用方可以观察真实失败原因；成功路径仍保存可恢复历史、生成摘要并更新 checkpoint。异步摘要失去一次与文件写入并行的延迟优化，以换取明确的先决条件和避免无效模型调用。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 历史落盘失败不调用摘要模型或主模型 | 警告后继续并发布 event | summary.py | summary middleware unit | 同步和异步历史写入返回 `disk full` | Passed |
| 摘要异常或空内容不进入模型历史 | 错误文本或空摘要进入 checkpoint | summary.py | summary middleware unit | 同步和异步摘要分别抛错、返回空白 | Passed |
| 成功、overflow 和主动压缩路径保持原行为 | fail-closed 误伤正常调用 | summary.py | `test_summary_middleware.py` 全文件 35 passed | 既有成功与主动压缩用例 | Passed |

最小回归命令：`docker compose exec -T api uv run --no-sync --no-dev pytest test/unit/middlewares/test_summary_middleware.py -q`。本地隔离依赖环境执行同一测试文件通过；完整 Compose gate 和真实模型 integration 由 PR 验证记录说明。
