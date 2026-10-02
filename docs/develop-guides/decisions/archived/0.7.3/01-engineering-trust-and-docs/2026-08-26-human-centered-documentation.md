# 面向读者的文档写作与维护

状态：archived
类型：process
Owner：docs/develop-guides/documentation-guidelines.md

## 问题

Yuxi 的正式文档覆盖部署、知识库、Agent 和工程治理，但页面职责和叙述深度不够稳定。入口页有宣传性铺垫，参考页夹杂操作步骤，机制页又把实现细节重复到多个页面；命令、页面名称和配置说明也需要回到当前源码与 Compose 核对。

## 决策

Yuxi 采用四类面向读者的文档：教程帮助读者按顺序得到结果，操作指南解决一个具体问题，参考页提供可查找的当前事实，解释页说明机制、Owner、边界和取舍。现有目录作为发布路径保留，但维护中的页面按单一读者任务组织，并在必要时拆分为独立页面。

文档规则由 `docs/develop-guides/documentation-guidelines.md` 拥有：开头说明读者、前置条件和完成标准；使用主动、具体、现在时；命令和操作写出可观察结果与失败处理；首次出现的技术术语给出解释；示例使用占位值；重要事实链接到源码、配置、Schema、Compose 或测试 Owner。`Passed`、`Inspected`、`Not run` 和 `Inferred` 分别表示已核对的证据强度，构建、HTTP 200、日志关键词和 Agent 自述不能单独证明业务结果。

本次文档分层曾将内容审查、网页搜索和服务端口拆为独立参考页，将 Agent 配置与 Agent 运行时、后端开发分开，将知识库管理拆为文档 API 与导图/图谱页面。内容审查能力及其参考页随后由[移除内容审查能力](../13-removals/2026-09-03-remove-content-guard.md)决定删除；其他机制页保留状态、权限、失败恢复、源码和测试入口。版本记录和决策历史保留历史职责，不为了统一语气改写历史事实。

文档站点使用严格的站内链接检查，只保留对本机示例地址的必要忽略规则。页面移动或拆分必须同步更新导航、入站链接和稳定的源码入口。

## 替代方案

- 只修正错别字：改动更小，但无法解决页面职责混杂、重复和读者找不到入口的问题。
- 全量重建目录并改动公开 URL：结构更彻底，但会破坏已有链接，当前证据也不足以证明需要新的发布路径。
- 依赖自动生成文档或外部 Wiki：可以帮助发现缺口，但不能替代 Yuxi 对权限、持久化、命令和失败语义的源码核对。

## 后果

- 维护者需要在修改代码、配置、API、状态、权限或命令时更新对应的事实 Owner。
- 文档会减少重复和硬译式表达，但机制页仍保留安全、失败、恢复和验证边界，不以删字换取表面简洁。
- 仍受当前产品能力支持的新页面和拆分页保留稳定公开路径；内容审查参考页是由后续删除决定明确取代的例外。
- 文档构建只能证明 Markdown、导航和链接有效；运行时语义仍需由源码、测试、真实链路和独立 Reviewer 共同判断。
- 外部资料只用于学习组织和表达方式。当前采用 Diátaxis 与 Write the Docs 的公开建议，不引入双语同步、自动生成目录或新的文档类型系统。

## 验证

| 验收主张 | 直接证据 / 命令 | 当前结果 |
|---|---|---|
| 维护中的正式页面按读者任务分层，重复主题已拆分并加入导航 | 逐页检查当前文档、`docs/.vitepress/config.mts`、入口页和交叉链接；独立 Reviewer 复核完整 diff | `Inspected` |
| 文档、Compose 和配置示例没有已知的本地断链或锚点错误 | `cd docs && pnpm run build`；正式 Markdown 相对链接与生成 HTML 锚点检查 | `Passed` |
| 工程契约、决策生命周期和指令预算保持有效 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts`（61 tests） | `Passed` |
| 文档改动不包含空白错误 | `git diff --check` | `Passed` |
| 运行时事实已经按风险回到源码、Compose、配置和现有测试核对 | 对照 Agent、知识库、沙盒、OCR、OIDC、API Key、Langfuse、路由和相关 unit；没有改变运行时代码 | `Inspected` |
| 相关后端纯逻辑回归没有被文档/配置引用误导 | `docker compose exec -T api uv run --group test pytest test/unit -m "not slow"`（1541 passed, 40 skipped） | `Passed` |

真实 PostgreSQL、HTTP、worker、SSE、浏览器、OIDC、外部模型/搜索、生产升级回滚和 Kubernetes PVC 链路不在本次文档变更的验证范围；缺少 `.env.prod` 和稳定的完整 Compose 环境时，不把这些结果写成通过。
