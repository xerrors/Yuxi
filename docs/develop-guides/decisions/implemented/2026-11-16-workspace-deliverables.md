# 对话交付文件自动归集到我的成果

状态：implemented
类型：feature
Owner：backend/yuxi/modules/workspace/services/deliverables.py

## 问题

网盘的“我的成果”需要一个可靠的数据入口。对话正式交付的文档、图片、视频、音频等文件必须自动形成用户可读取的独立副本，来源对话归档或源文件变化不能影响副本；中间文件、上传附件和历史对话不在范围内。

## 决策

现有 `present_artifacts` 作为最终文件发布入口。它在受信运行上下文中调用 Workspace 成果用例，校验用户、Thread、Turn、Run、worker lease 和源文件授权后复制普通文件；所有副本准备完成并再次确认执行仍有效后，才在 PostgreSQL 写入成果记录并返回工具成功。`run_id + operation_id + source_path` 是唯一发布身份，重放同一工具调用复用已有记录。

成果记录、查询和认证读取由 Workspace 领域拥有。副本位于用户数据根下独立于普通可编辑 Workspace 的 `/deliverables` 目录，并通过 `Workspace.user_data` 复用 no-follow 文件边界。成果列表按当前 Web 用户分页、搜索、类型筛选并返回来源对话；预览和下载只读取保存副本，音视频由前端通过认证请求获取 Blob。

新增 `workspace_deliverables` 属于 business Schema，通过显式、事务化、可重跑的升级入口从 `business=3/knowledge=3` 提升到 `business=4/knowledge=3`；API、worker 启动不隐式建表。前端网盘保留现有页签，正式提供我的成果列表、空态、错误重试、预览、下载和来源对话入口。

## 替代方案

- 继续手动保存：无法满足交付后自动归集。
- 直接列出工作区文件或引用原路径：会混入附件和临时文件，且不能保留独立副本。
- 将成果元数据嵌入工具审计 output：文件目录会依赖审计结构及其生命周期，权限、分页和查询边界也不属于审计 Owner。
- Run 终态扫描累计 artifacts：不能可靠区分当前 Run 与历史结果，并推迟已交付文件的保存。

## 后果

成果拥有独立字节和独立权限查询，源文件被修改、删除、替换为软链接、项目变更或会话归档都不改变已保存结果。多文件发布在数据库登记前失败会删除本次已创建的副本；进程在文件落盘与数据库提交之间崩溃仍可能留下未登记副本，后续清理需要由运维处理。发布能力依赖 Agent 正式调用 `present_artifacts`，不会扫描任意沙盒文件或回填历史结果。

## 验证

- `test/integration/services/test_workspace_deliverables_schema.py`：5 passed，验证显式升级、幂等重跑、不支持版本和未版本化表拒绝。
- `test/integration/api/test_workspace_deliverables_api.py`：7 passed，验证真实 HTTP/PostgreSQL、并发幂等与输家副本清理、数量上限、跨用户与 APP 隔离、Web API Key 拒绝、搜索筛选分页、撤权、软链接 no-follow、源文件删除和副本丢失错误。
- `test/e2e/test_workspace_deliverables_e2e.py`：1 passed，验证真实 worker、模型重放、三类文件、源文件修改/删除、后续 Run、归档后下载和字节独立性。
- `test/unit/services/test_agent_artifacts_state.py` 及相关 middleware unit：通过；前端 `test/unit/deliverables.test.js`：4 passed；前端 lint、build：通过。
- `ruff check`、`ruff format --check`、导入排序检查和 `git diff --check`：通过。工程契约脚本已在 Linux 容器中通过；Windows 本地 unittest 的 symlink 负例受创建符号链接权限限制，未将该环境结果视为代码通过。
