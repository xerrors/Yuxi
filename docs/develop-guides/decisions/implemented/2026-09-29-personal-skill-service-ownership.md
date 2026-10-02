# 个人 Skill 文件用例由专用服务拥有

状态：implemented
类型：architecture
Owner：backend/package/yuxi/services/skills/personal.py

## 问题

个人 Skill 的草稿确认位于 service，列举、安装、读取和删除却位于 `workspace/personal_skills.py`。同一来源的业务流程分散在两个模块；目录拼接又经一次性函数转发。新 Skill 模块的部分私有实现位于主要用例之前，增加了阅读负担。

## 决策

### 实现方案

个人 Skill 的确认、列举、安装、读取和删除由 `services/skills/personal.py` 统一拥有；共用包解析和复制由 `services/skills/package.py` 提供，底层文件原语复用现有 filesystem 工具。`workspace/personal_skills.py` 和只拼接目录的 `get_personal_skills_root_dir` 不再存在。个人 Skill 字节仍保存在现有 UserWorkspace 的 `agents/skills`，服务在安装、读取和删除边界校验固定用户目录及目标路径。

`personal.py` 是唯一允许直接定位 UserWorkspace 宿主根的 service。工程门禁按精确文件路径放行它对 `user_workspace_dir` 的导入，继续拒绝其他 service、repository 对宿主根的访问。新增 Skill 模块按主要公开用例、领域查询、私有实现的顺序组织；单次异步投影转发和仅提取工具 slug 的薄层函数内联到消费用例。

## 替代方案

- 个人文件操作继续放在 workspace，再由 service 转发：保持原宿主路径门禁，但业务 Owner 分裂，且需要无业务语义的转发层。
- 将个人 Skill 字节迁出 UserWorkspace：需要数据迁移与运行时挂载改动，会改变既有持久来源和用户可见路径。
- 放宽所有 service 的宿主路径访问：实现简单，却失去 UserWorkspace 的现有跨层边界。

## 后果

个人 Skill 的业务入口集中在专用 service；数据仍由当前用户的 UserWorkspace 持有。精确例外要求该服务自行维护 uid 与路径的文件边界；若未来再拆分个人文件操作，必须同步审查门禁和架构规则。

## 验证

`python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts` 通过（63 项），包括允许个人 Skill Owner 定位用户根、拒绝其他 service 获取宿主路径的负向案例。完整后端 non-slow unit 通过（2454 passed、58 skipped），共享文件与投影相关真实 HTTP/PostgreSQL integration 通过（7 passed）。命令与环境限制见[共享 Skill 编辑验证](./2026-09-28-shared-skill-edit.md#验证)；未运行完整个人 Skill E2E。
