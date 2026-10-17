# 评估运行取消与查询失败语义

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/eval/service.py

## 问题

RAG 评估的 Task 取消、超时和异常都由同一个失败钩子收敛成 `failed`，评估页无法区分用户停止和真实错误；取消入口只存在于管理员任务中心，普通知识库管理者没有对应业务操作。检索链路可能把底层查询错误降级成空列表，评估会把故障算成零命中。运行中的 Run 还能被直接删除，任务可能继续消耗资源或写入已删除的关联数据。

## 决策

### 实现方案

评估提交在 `retrieval_config` 中强制写入 `raise_on_error=true`；`KnowledgeBaseManager.aquery` 只把开关作为显式关键字参数传给执行器，不捕获配置读取、执行器创建或底层查询错误。评估链路开启时由真实异常 Owner 上抛：Dify 配置缺失或两次请求均失败、Notion 连接/查询失败会中断评估并进入既有失败钩子，Milvus 主检索原本就上抛。Dify 的兼容重试成功仍返回结果，不把成功重试当故障。`fail_rag_evaluation_task` 和 `list_runs` 的读时投影按 Task 的 `cancel_requested` / `cancelled` 状态把 Run 收敛为 `cancelled`，其余失败仍是 `failed`，取消保留已完成题目数量。评估路由新增 `POST /api/evaluation/databases/{kb_id}/runs/{run_id}/cancel`，使用知识库管理权限定位活跃 Durable Task 后调用通用取消；running Run 的删除被拒绝，前端补取消按钮、取消中和 `cancelled` 展示。

Dataset 删除由 `EvaluationService.delete_dataset` 在 service 层保护：先复用生成元数据同步逻辑，把关联 Durable Task 的终态投影到 Dataset，再拒绝 `build_metadata.status` 为 `pending` 或 `running` 的删除并提示先取消生成任务。排队中的生成任务也受保护，因为它仍可启动并写入数据集；终态同步避免已结束任务的陈旧 running 元数据误拦删除。删除操作不联动取消任务，路由沿用 `ValueError` 到 HTTP 400 的转换。

## 替代方案

把所有任务失败都保持成 `failed` 最小，但会让用户主动停止和系统错误共用一个含义。把检索失败记录成逐项 `retrieval_status` 可以保留更多结果，但会扩大评估表、指标汇总和 UI 契约，当前不需要。删除 running Run 时静默取消任务看起来更顺手，但会把破坏性操作和长耗时任务终止隐式耦合。

## 后果

用户取消保留部分评估进度并显示“已取消”；检索故障会让整条评估失败，不再伪装成低分结果。知识库管理权限可以取消自己的评估，不再必须进入全局任务中心。running Run 必须先取消评估任务并等待终态再删除；pending / running Dataset 必须先取消生成任务并等待终态再删除。共享查询层保留既有的失败传播语义；默认静默降级只属于 Dify 和 Notion 执行器，Milvus 与共享层故障继续由普通查询路由返回 `failed`。

Dataset 删除保护是 service 层的状态检查，检查与删除不在同一加锁事务中，并发恢复生成与删除的互斥未验证。缺少 task_id 且找不到活跃任务的生成元数据沿用既有同步逻辑，保持 pending / running 并拒绝删除。

## 验证

| 验证 | 结果 |
|---|---|
| `backend/.venv/Scripts/python.exe -m pytest test/unit/knowledge/test_evaluation_task_cancellation.py test/unit/knowledge/test_knowledge_manager_aquery.py test/unit/knowledge/eval/test_service_generation.py test/unit/knowledge/eval/test_evaluator.py test/unit/services/test_tasker_behavior.py` | 既有验证记录：38 passed，仅覆盖 Run 取消与查询失败变更，不包含新增 Dataset 删除保护用例 |
| `python -B -m pytest backend/test/unit/knowledge/eval/test_service_generation.py`（本机补充验证 Dataset 删除保护用例） | Not run：系统 Python 3.11 缺少 pytest，无法在本机执行 |
| `python -B -m pytest backend/test/unit/knowledge/test_knowledge_manager_aquery.py`（执行器 fail-fast 回归） | Not run：本机系统 Python 3.11 缺少 pytest，且没有 backend 虚拟环境、uv 和 Docker；新增用例覆盖 manager 失败传播与透传、Dify 请求失败的开启/关闭语义及 Notion 上抛与默认降级路径 |
| `python -B scripts/verify_engineering_contracts.py` | √ 工程契约检查通过 |
| `python -B -m unittest scripts.test_verify_engineering_contracts` | 63 项中 62 项成功、1 项错误；符号链接隔离用例因 Windows WinError 1314 无创建符号链接权限失败，沙盒外重跑结果相同 |
| Dataset 删除保护真实 HTTP / PostgreSQL / worker 验证、文档构建 | Not run：本机缺少对应运行环境与工具，尚未验证真实删除结果和取消后删除链路 |
| `git diff --check` | 通过；Windows 仅提示既有 CRLF 转换警告 |
| `python -B -m py_compile`（manager、Dify、Notion、Milvus 及查询回归测试） | √ 使用临时 Python 缓存目录通过 |
