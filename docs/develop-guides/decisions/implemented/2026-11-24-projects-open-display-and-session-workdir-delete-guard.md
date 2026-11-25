# projects 全量展示实际文件，删除整目录保护会话 Workdir

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/workspace/services/files.py

## 问题

个人空间列表按"未归属 selectable Project 即隐藏"投影，上传、新建目录、保存产物成功后条目凭空消失
（issue xerrors/Yuxi#1184）。上一轮按"细化隐藏面 + 写守卫"收敛，维护者裁决改为产品口径：个人空间就是
用户文件的真实视图，`projects/` 下目录与文件一律显示，包括会话自动创建的 Workdir；项目是否可选、是否
绑定继续由项目选择器接口负责。放开展示后必须补上被旧隐藏间接掩盖的破坏面：用户删除日期目录时现有接口
会递归删光会话文件，而 Project 绑定与 Session 引用仍然存活，会话随即指向不存在的目录。

## 决策

1. 展示：删除 `_filter_project_tree_entries` 及其 selectable/implicit 双列表查询，projects 树直接返回目录
   实际内容；随隐藏规则一同移除服务端参数及前端透传与上一稿的写守卫 `assert_projects_write_targets_visible`（黑洞不再成立，守卫
   失去存在理由）。上传、新建目录、保存产物成功后必然可见。
2. 删除保护（[P1]）：`delete_workspace_path` 在任何磁盘副作用前查询"仍被未删除会话绑定的 active Project"
   的 workdir 路径集合；目标等于其中某路径、或为其祖先时拒绝整次删除并说明原因（400
   `workspace_session_workdir_protected`）。判定只走路径祖先关系：删除 Workdir 内单个文件、已软删 Project
   或已删会话（status=deleted）的残留目录不受影响；`Session.status` 为 NULL 的历史行按"仍引用"处理
   （fail-closed）。

### 实现方案

- `files.list_workspace_tree` 直接返回实际目录条目。项目选择器使用普通工作区目录请求。
- `ProjectRepository.list_referenced_workdir_paths_for_user`：projects JOIN sessions，谓词
  `Project.status='active' AND (Session.status IS NULL OR Session.status != 'deleted')`，
  `distinct()` 收敛多会话共用同一 linked workdir。
- `files.delete_workspace_path(*, path, current_user, db)`：根目录检查后、任何磁盘副作用前取引用集合，
  目标等于某 workdir 或位于其祖先链（`PurePosixPath.parents`）即 400
  `workspace_session_workdir_protected`；路由注入 `get_db`。
- `artifacts.save_thread_artifact_to_workspace_view`、`upload_workspace_files`：回退写守卫接入点，恢复
  原签名；上一稿的 `assert_projects_write_targets_visible` 与 implicit 查询整体删除。

## 替代方案

- 细化隐藏 + 写守卫（本 PR 上一稿）：被维护者产品裁决替换——隐藏投影本身不成立，守卫只是给黑洞打补丁。
- 删除目录时级联删除 Project 与会话：被否——静默销毁会话历史与文件，破坏面比拒绝大得多。
- 允许整删、事后由恢复扫描清绑定：被否——会话文件不可逆丢失，且绑定悬空窗口内 run 会对着空目录执行。

## 后果

- 会话 Workdir（Agent 中间产物、附件）从此对用户可见可删（单文件粒度），这是维护者的明确产品意图；
  历史"非 selectable 一律隐藏"的隐私假设由本决策取代。
- 含活跃会话 Workdir 的日期目录整体删除会被拒绝，用户需先删除对应项目或会话；错误信息给出可行动路径。
- 删除保护要求 `delete_workspace_path` 触库；非 projects 路径同样先查一次引用集合（单查询，uid 索引）。
- 检查与 rmtree 之间未持咨询锁：并发新建 implicit Workdir 落在正在删除的日期层内是窄竞态窗口，
  与既有删除链路同等暴露，后续批随会话删除能力一并收敛。

## 验证

| 验收主张 | 语义 Owner | 直接证据 / 命令 | 负向案例 |
|---|---|---|---|
| projects 下会话 Workdir、日期层、散文件全部逐级可见可写 | list_workspace_tree | test/integration/api/test_workspace_projects_display_delete_guard_api.py::test_session_workdir_visible_and_ancestor_delete_rejected（真实 HTTP+DB 取 workdir） | 重新接入隐藏过滤 → workdir/日期层断言红 |
| 上传进会话 Workdir 成功且不阻塞已分配目录使用 | upload_workspace_files | 同用例 from-user 上传 200 并出现在深层列表 | 恢复写守卫 → 400 断言红 |
| 目标等于被引用 Workdir 或其祖先时整次拒绝、文件保留 | delete_workspace_path | 同用例日期层删除 400 `workspace_session_workdir_protected` 且 loose 文件仍在；test/unit/services/test_workspace_session_workdir_delete_guard.py 三级祖先矩阵 | 去掉祖先分支 → 红 |
| 删除 Workdir 内单个文件不受保护误伤 | delete_workspace_path 祖先判定 | unit test_delete_file_inside_workdir_is_not_blocked / test_delete_allows_unrelated_targets 含子目录与旁支 | 谓词改字符串前缀（startswith）→ 红 |
| Project 软删后残留目录可整删 | list_referenced_workdir_paths_for_user | test_linked_workdir_delete_rejected_until_project_removed（绑定期拒绝 → DELETE /api/projects → 整删 200） | 谓词去掉 Project.status 过滤 → 红 |
| 引用集合含 NULL status 会话（fail-closed） | repositories/projects.py or_ 条件 | unit 谓词经真实 PG 的上述集成用例覆盖会话默认 active 路径；NULL 行为由 or_ 分支静态保证 | 删 or_ NULL 分支后手工置 NULL 行 → 目录可删（记录于此，不作 CI 断言） |

### 服务层模拟与人工界面验收步骤

1. 账号 A 建会话（implicit Workdir）+ 日期层上传散文件；账号 B 登录不应看到 A 的任何条目（引用集合按
   uid 过滤，删除保护同样只约束本 uid）。
2. 界面预期：A 的 projects 根能看到当日日期层，进入可见会话目录与散文件；对日期层点"删除"弹出 400 文案；
   进入会话目录删除单个文件成功；删除该会话绑定的项目后日期层可整体删除。
3. 验收后清理测试账号目录。

重新引入条件：若产品再次要求隐藏会话产物面，隐藏投影与写守卫须成对恢复，并先定义已落盘黑洞文件的清理
语义；删除保护独立保留。
