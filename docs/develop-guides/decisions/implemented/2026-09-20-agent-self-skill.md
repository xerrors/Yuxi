# Agent 专属技能

状态：implemented
类型：feature
Owner：backend/package/yuxi/agents/skills/service.py

`yuxi.agents.skills.service` 拥有绑定关系、权限派生和 self-skill 生命周期；`yuxi.agents.skills.runtime` 拥有运行时强制注入与预加载；`yuxi.repositories.agent_repository` 拥有 Agent 删除时的级联；`BaseContext.agent_slug` 拥有本次 Run 的 Agent 身份。

## 问题

Agent 需要一份只属于自己的 Skill 内容（例如 `mysql-reader-agent` 的 MySQL 使用规则）。普通 `Skill` 是「可共享、可安装、可被多个 Agent 使用」的资源，拥有独立 `share_config` 和扩展中心管理入口；如果 self-skill 继续伪装成普通 Skill，就需要不断叠加特殊字段和例外逻辑，并且会出现两类漂移：Agent 权限变化后 Skill 权限不同步，以及用户在普通 Skill 选择器或聊天 mention 中看到本不应独立使用的内容。

系统需要一种 Agent 专属、强制预加载、权限跟随 Agent 的 Skill，同时不引入第二套 Skill 管理能力。

## 决策

`skills` 表新增可空列 `bound_agent_id`，并以部分唯一索引保证一个 Agent 最多有一个绑定 Skill。绑定关系是数据库事实，不依赖 slug 命名约定：`bound_agent_id` 非空即 Agent 专属技能，`source_type` 仍表达 builtin/upload/remote 来源。`ResolvedSkill` 新增第四种 `source_scope = "agent_bound"`，与既有 `builtin`/`shared`/`personal` 并列。

绑定 Skill 不维护独立权限。`_resolved_agent_bound_skill` 在读取时把**绑定 Agent 的** `created_by` 和 `share_config` 装进运行时视图；这是读时投影而非持久副本，因此 Agent 修改共享范围后绑定 Skill 权限立即跟随，不存在同步任务或漂移。由于 `resolve_resource_permission` 只鸭子类型读取这两个字段，现有 `resolve_skill_permission`、`user_can_access_skill`、`user_can_manage_skill` 不为此改变。绑定 Agent 缺失时权限解析返回 `NONE`，不退化为普通 Skill 语义。

self-skill 是**按需创建**的，不是 Agent 创建的副产品：没有内容需求的 Agent 不产生空行，运行时允许 Agent 尚不存在绑定 Skill。`create_agent_self_skill` 幂等。创建之后没有独立删除入口——需要停用时把内容改成最小说明即可，删除只随 Agent 删除级联发生，因此 Agent 不会进入「专属技能丢失但自身仍在」的中间状态。`AgentRepository.delete` 在同一个事务里删除绑定 Skill 行与 Agent 行，提交失败时把内容目录放回原位，提交成功后才清理垃圾目录。

**内容管理不新建平行编辑器。** Agent 编辑页的「专属技能」分区只负责展示绑定关系并提供跳转；SKILL.md、目录结构、文件增删改、`tool_dependencies`/`mcp_dependencies`/`skill_dependencies` 全部复用既有 `SkillDetailView` 与按 slug 寻址的 Skill 接口。为此 `GET /system/skills/{slug}` 允许返回绑定 Skill（列表入口仍然排除），写操作结果按绑定 Agent 派生权限序列化。Skill 管理页对绑定 Skill 隐藏共享范围、启用状态与删除入口，与内置 Skill 的只读处理同一形态。

写路径显式拒绝针对绑定 Skill 的独立操作：`update_skill_share_config`、`update_skill_enabled`、`delete_skill` 与安装/上传入口；`_can_depend_on` 对绑定 Skill 作为父级时改用绑定 Agent 的读取范围，否则依赖范围校验会基于无意义的占位 `share_config` 计算。

对外列表默认隐藏绑定 Skill：Agent 配置的 Skill 选择器、依赖引用列表和扩展中心管理列表都以 `bound_agent_id IS NULL` 为默认条件，只有显式 `include_agent_bound` 的接口才返回。

运行时由 `BaseContext.agent_slug`（不可配置字段，由 `prepare_run_execution` 从 `run.agent_slug` 注入）定位绑定 Skill，并无条件加入 `effective_skills` 与 `preloaded_skills`。因为它不来自 `context.skills` 或 `preload_skills`，用户提交空列表无法关闭它；无权访问该 Agent 的用户即使知道 slug 也不会激活。根级 `SKILL.md` 不可读时沿用现有 `RuntimeError` 显式失败，不静默降级为无 Skill。绑定 Skill 的虚拟路径与普通共享 Skill 一致（`/home/gem/skills/<slug>`），因此照常进入按 uid 的授权投影，不新增挂载点。

不做版本、草稿、回滚、按用户灰度和长期调试模式。保存即对后续新 Run 生效，进行中的 Run 由现有 `AgentRun.manifest` 的 `content_hash` 与 `preload_content_hash` 固化。

## 替代方案

- 新建 `agent_self_skills` 表：需要复制第二套内容目录管理、frontmatter 解析、依赖校验、文件树编辑、投影和运行时解析。个人 Skill「不入库」已经证明代价：没有 DB 身份、没有外键、没有权限查询、没有投影控制。
- 把绑定关系编码进 slug（`{agent_slug}-self-skill`）：slug 可重命名，无法作为稳定绑定依据；且每个列表路径都要重新实现命名约定。
- 复制一份 Agent 的 `share_config` 到 Skill 行：需要 Agent 共享变更时的同步钩子，否则必然漂移；且写入路径仍要防止用户单独修改。
- self-skill 提供独立删除入口：删除只会让 Agent 失去自己的资产并进入残缺状态，而「改成空说明」已能满足停用需求；独立删除还会多一条需要级联保护的破坏性路径。
- 为 self-skill 新建平行内容编辑器：`SkillDetailView` 已拥有文件树、文件 CRUD 与三类依赖编辑，且这些接口本就按 slug 寻址、权限已按绑定 Agent 派生。平行编辑器只能覆盖 SKILL.md，会持续落后于普通 Skill 的管理能力，并制造第二套需要同步演进的表面。
- self-skill 随 Agent 创建自动落库：把文件系统副作用耦合进持久化路径，并为没有内容需求的 Agent 产生空行。
- self-skill 只做预注入文本、不进 uid 投影：self-skill 若包含脚本或参考文件，模型在沙盒内读不到；且需要为它单独处理虚拟路径契约。

## 后果

`/home/gem/skills` 是当前用户全部已授权 Skill 的只读投影，因此用户在运行 Agent B 时，目录列表中可能出现 Agent A 的 self-skill。隐私边界由授权层保证（只能看到可访问 Agent 的 self-skill），不由沙盒列目录保证；这是选择「不新增挂载点」的明确代价。Agent 共享范围变更后，投影要到该用户下一次 Run 初始化才刷新（`sync_agent_context_skills` 每次 Run 都会执行），授权本身仍在执行前解析。

每个绑定 Skill 与普通预加载 Skill 一样增加每轮模型输入，并从首轮暴露其依赖工具；内容 Owner 应控制体量。管理面多出一个需要记住的过滤条件：新增任何 Skill 列表入口都必须默认排除绑定 Skill，否则会重新暴露本应隐藏的内容。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| 绑定依据是 `bound_agent_id` 而非 slug；独立共享、启停、删除被拒且数据库未变 | `docker compose exec api uv run --group test pytest test/unit/agents/skills/test_agent_self_skill.py test/integration/api/test_agent_self_skill.py -q` | Passed；覆盖 share-config/enabled/delete 三个 guard、self-skill 无独立删除入口（405）与删除 Agent 的级联 |
| 绑定 Skill 权限完全派生自 Agent，Agent 共享范围变化后立即同步；绑定 Agent 缺失 fail-closed | `docker compose exec api uv run --group test pytest test/unit/agents/skills/test_agent_self_skill.py -q` | Passed；覆盖 owner=MANAGE、授权用户=READ、未授权=NONE、缺失 Agent=NONE |
| 配置选择器、依赖列表、扩展中心管理列表默认不含绑定 Skill，且列表非空以避免恒真断言 | `docker compose exec api uv run --group test pytest test/integration/api/test_agent_self_skill.py test/integration/api/test_agent_config_resource_authorization.py -q` | Passed |
| 绑定 Skill 复用既有 Skill 管理面：按 slug 查询、文件树、文件 CRUD、依赖编辑均可用 | `docker compose exec api uv run --group test pytest test/integration/api/test_agent_self_skill.py -q` | Passed；`GET /system/skills/{slug}` 返回绑定 Skill 且 `can_manage` 派生正确 |
| `skills=[]` 时绑定 Skill 仍强制生效并预加载完整内容；无 Agent 权限不激活；`SKILL.md` 不可读显式失败 | `docker compose exec api uv run --group test pytest test/unit/agents/skills/test_skill_runtime.py -q` | Passed |
| 绑定 Skill 作为父级声明依赖时使用 Agent 读取范围，普通 Skill 行为不回退 | `docker compose exec api uv run --group test pytest test/unit/agents/skills/test_agent_self_skill.py -q` | Passed |
| 真实 PostgreSQL 下的绑定、权限派生、级联删除与投影刷新 | `docker compose exec api uv run --group test pytest test/integration -q` | Passed for skill/agent 相关用例；4 个失败与 6 个 error 在 stash 后的干净基线上复现，与本次改动无关 |
| 受影响 Python 文件满足静态检查与格式约束 | `docker compose exec api uv run --directory backend ruff check <受影响文件>`；`ruff format --check <受影响文件>` | Passed；仅存 1 个既有 E501（`test/unit/toolkits/test_ocr_parse_file_tool.py`），已在基线上复现 |
| 前端编辑器不要求管理者猜测绑定 slug，保存链路无控制台错误 | `docker compose exec web pnpm run lint:check`；`pnpm run test:unit`；`pnpm run build`；真实页面打开「专属技能」空态与已存在态 | Passed：351 tests；lint 与 build 通过；预填模板携带正确 slug，空态与已存在态都显示 slug 约束提示 |
| Agent 编辑页跳转到既有 Skill 管理页，且绑定 Skill 在該页隐藏共享/启停/删除 | 真实页面：智能体编辑 → 「专属技能」→ 「打开 Skill 管理」→ 技能详情代码管理与配置两个 tab | Passed：SKILL.md 内容、依赖编辑可用；「保存范围」按钮隐藏、启用开关禁用、删除按钮隐藏、只读说明就位；控制台 0 error |
| 工程契约保持可验证 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts` | Passed：112 decisions / 5 workflows / 4 agents files / 161 docs / 26 routers / 250 web sources；62 contract tests |
