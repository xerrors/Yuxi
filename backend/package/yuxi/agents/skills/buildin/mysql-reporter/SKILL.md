---
name: mysql reporter
slug: mysql-reporter
description: "生成 MySQL 查询报表。当用户需要查询 MySQL 数据库并以报表形式展示结果时使用此技能，包括：统计销售数据、分析用户行为、生成业务报表、查询业务指标等。"
version: "2026.09.17"
---

# MySQL 报表技能

根据用户的指令，通过终端脚本访问 MySQL 数据库，根据查询结果构建报表。

## 操作流程

1. 理解用户的指令，明确报表的需求和目标
2. 通过 terminal 进入技能目录：`cd /home/gem/skills/mysql-reporter`
3. 使用 `uv run scripts/list_tables.py` 查看可用表；如果脚本提示缺少 MySQL 配置，按“环境变量缺失处理”回复用户
4. 必要时用 `uv run scripts/describe_table.py --table 表名` 查看表结构
5. 生成正确且高效的只读 SQL，通过 `uv run scripts/query.py --sql "SQL语句" --timeout 60` 执行查询并获取结果
6. 使用 Markdown 表格呈现查询结果，说明关键指标、变化与结论

## 环境变量缺失处理

脚本只读取 Agent 沙盒中的环境变量，不读取后端 `.env` 或 Docker Compose 变量。必填变量包括：

- `MYSQL_HOST`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

可选变量包括：

- `MYSQL_PORT`：默认 `3306`
- `MYSQL_DATABASE_DESCRIPTION`：数据库业务说明，用于辅助理解表和指标含义

如果执行脚本时出现 `MySQL configuration missing required key`，不要继续猜测连接信息或编造报表。应明确告诉用户：需要在个人设置中的「沙盒环境变量」里配置缺失的 `MYSQL_*` 变量；保存后仅对新建沙盒生效，需要重新发起任务或新建会话后再执行。

## 关键约束

- 生成的 SQL 查询必须正确且高效，避免全表扫描
- MySQL 操作必须通过本技能 `scripts/` 下的 CLI 脚本执行，不要调用平台内置 MySQL tools
- 不要在报表或错误说明中输出 `MYSQL_PASSWORD` 等敏感环境变量的值，只能说明缺少哪些变量名
- 只返回报表相关的结论，不要返回原始 SQL 查询语句

## 允许的工具

- terminal：执行 `scripts/list_tables.py`、`scripts/describe_table.py`、`scripts/query.py`
- 网络检索工具：必要时补充背景信息
