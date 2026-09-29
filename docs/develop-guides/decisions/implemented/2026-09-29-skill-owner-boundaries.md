# Skill 来源与文件一致性边界

状态：implemented
类型：architecture
Owner：backend/package/yuxi/services/skills/projection.py

## 问题

共享 Skill、个人 Skill、安装草稿和用户投影曾由单个 service 混合处理。共享文件编辑引入行锁和修订值后，读取、创建、删除、导出与 artifact 仍可能走不同的来源和锁协议；运行时通过通用列表函数的布尔参数选择锁语义。调用者无法从入口判断授权、文件可见性和事务的责任边界。

## 决策

### 实现方案

个人 Skill 的草稿确认、列举、安装、读取与删除由 `backend/package/yuxi/services/skills/personal.py` 负责，用户工作区根由 `workspace.paths` 提供。个人安装沿用包内 slug，不查询或分配共享数据库 slug。共享 Skill 的业务索引和安装保留在 `backend/package/yuxi/services/skills/shared.py`；包格式解析位于 `backend/package/yuxi/services/skills/package.py`，上传与远程暂存位于 `backend/package/yuxi/services/skills/draft.py`，草稿读取与筛选位于 `backend/package/yuxi/services/skills/draft.py`。

共享文件入口由 `backend/package/yuxi/services/skills/edit.py` 持有：先锁定共享数据库行并重查权限，再从可信根逐层 no-follow 打开目录和普通文件。编辑使用修订值，发布文件后提交索引；提交失败恢复旧文件。创建失败撤回新节点，删除在提交前把节点移到暂存区以便恢复。Artifact 下载只使用已授权的共享行定位文件，不经个人同名覆盖。

投影授权快照、跨进程锁和目录发布由本记录的 Owner 负责；运行时单独锁定已选择的共享 Skill 及其依赖。展示列表不接受影响授权或锁行为的布尔参数。HTTP 路由仍只编排对应 service，repository 持有可见性查询和行锁查询。

## 替代方案

- 只调整单个 service 的函数排序：改动少，但文件来源和事务责任仍混合。
- 建立统一 Skill provider 框架：可统一调用形态，却把个人工作区与共享数据库伪装为相同的持久化模型。

## 后果

个人与共享 Skill 保持同名覆盖的运行时语义，但安装命名互不影响。共享文件读取、运行时快照和投影刷新使用各自明确的锁入口。PostgreSQL 与文件系统无法组成原子事务；进程在文件替换和数据库提交之间崩溃的窗口仍是已知限制，需要后续恢复机制才能消除。

## 验证

完整后端 non-slow unit 通过（2454 passed、58 skipped），相关真实 HTTP/PostgreSQL integration 通过（7 passed），共享编辑到真实 worker 的确定性 E2E 通过（1 passed），覆盖修订值、共享行锁、投影回读、artifact 来源及 Run 清单。工程契约检查及其 63 项测试、锁定版本 Ruff 与补丁检查通过。命令与未验证范围见[共享 Skill 编辑验证](./2026-09-28-shared-skill-edit.md#验证)；这些证据不覆盖文件与数据库发布之间的进程崩溃窗口。
