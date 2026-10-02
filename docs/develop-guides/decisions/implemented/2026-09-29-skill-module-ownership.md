# Skill 模块按运行时、用例与仓储分层

状态：implemented
类型：architecture
Owner：backend/package/yuxi/services/skills/shared.py

## 问题

Skill 安装和管理用例曾散落在 `agents/skills` 与顶层 `services`；数据库查询也位于 Agent 目录。目录职责与架构约定不一致，调用方需要跨多个位置寻找同一业务链路。

## 决策

### 实现方案

共享、个人、草稿、远程获取、编辑和投影用例位于 `yuxi.services.skills`；Skill 数据库访问位于 `yuxi.repositories.skill_repository`。包解析、快照复制及包内 slug 改写集中在 `services/skills/package.py`，来源描述位于同目录 `resolved.py`；用例、Agent 运行时和存储迁移直接导入这些实际模块。`yuxi.agents.skills` 保留运行时 Skill 解析和内置 Skill 资源。HTTP 路由、worker、存储迁移与测试都直接导入新的语义 Owner；旧模块路径不保留兼容转发。

这次模块迁移不改变 HTTP 契约、持久数据、安装结果或运行时选择语义。远程获取的公开入口位于私有执行与解析细节之前；个人 Skill 的确认与文件操作由 `services/skills/personal.py` 集中持有，`workspace.paths` 只提供用户工作区根与校验原语。

简单共享查询由调用方直接使用 `SkillRepository`，service 不提供逐方法转发。内置来源筛选由 repository 的 SQL 查询执行，保留原有排序。service 保留组合查询、安装、授权与一致性用例。

## 替代方案

- 继续保留散落文件，仅重命名模块：改动小，但架构 Owner 仍不清楚。
- 把所有文件放进 `agents/skills`：Agent 运行时入口集中，却继续让安装、权限和 PostgreSQL 查询受 Agent 目录拥有。
- 为包格式和类型单设顶层目录：增加一个查找位置，且包快照复制本就拥有文件副作用；当前没有需要独立维护的领域包边界。

## 后果

调用方按运行时、用例、格式和数据库边界定位 Skill 代码。包格式与文件复制归属 Skill service 内部模块，`workspace` 不依赖这些模块；`services/skills/shared.py` 仍通过 Agent 内置资源目录发现随代码发布的 Skill。旧 Python 模块路径的仓库外消费者需要同步迁移。

## 验证

旧路径搜索、工程契约检查及其 63 项测试通过；完整后端 non-slow unit 为 2454 passed、58 skipped，相关真实 HTTP/PostgreSQL integration 为 7 passed，共享编辑到真实 worker 的确定性 E2E 为 1 passed。锁定版本 Ruff 的 lint、format 和 import 检查，以及前端 lint、394 项 unit、生产构建和文档构建通过。命令与未验证范围见[共享 Skill 编辑验证](./2026-09-28-shared-skill-edit.md#验证)。
