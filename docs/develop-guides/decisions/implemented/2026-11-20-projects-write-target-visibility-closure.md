# projects 展示只精确隐藏会话 implicit 项目，写守卫与之对应

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/workspace/services/files.py

## 问题

个人空间列表沿用了 0.7.3 的"非 selectable 项目一律隐藏"投影：projects 顶层散文件、日期中间层、用户
自建未绑定目录、会话 implicit 项目全部不出现在列表，而上传与新建目录对这些位置不设防——用户"上传成功/
创建成功但条目凭空消失"，且在个人空间自建文件夹后立即不可见，破坏了"个人空间建目录 → 会话选择项目里挑中
绑定"这一被期望的流程（issue xerrors/Yuxi#1184）。会话弹窗创建目录并当场绑定之所以正常，仅因为绑定后路径
进入 selectable 集合。

## 决策

把隐藏面收敛为唯一有真实理由的对象——会话自动创建（implicit）Project 的子树；其余一律可见，写守卫与
展示口径一一对应：

- 展示：projects 树内（根条目除外）隐藏 implicit workdir 根及其子树、以及不含任何 selectable 项目的
  implicit 祖先日期层；selectable 绑定不再决定可见性（其子树与祖先照常可见），用户自建目录、既有黑洞
  散文件都显示，用户因此能清理历史遗留并继续创建。
- 写入：上传文件与 artifact 保存仅拒绝 implicit 子树（那里写入必然不可见）；projects 顶层与未绑定目录
  在新口径下可见，不再拒写。新建目录始终允许（绑定流程依赖）。
- 绕过通道保持：`include_unbound_project_dirs=true`（项目选择弹窗浏览未绑定/历史目录）语义不变。

### 实现方案

- `ProjectRepository.list_implicit_workdir_paths_for_user`：当前 uid 全部 implicit 行（含软删除，数据已
  不属于浏览面）的 workdir 路径。
- `_filter_project_tree_entries`：先 selectable 匹配（根/子树/祖先可见，保日期层含已绑定项目时不空壳），
  再 implicit 匹配（根/子树/纯祖先隐藏），默认可见。展示与守卫共用 `_implicit_project_root_paths`。
- `assert_projects_write_targets_visible`（upload_workspace_files 批次、save_thread_artifact_to_workspace_view
  destination 两个接入点）：对目标及其每一父目录套用与展示完全同款的可见性判定，任一级不可见即在任何
  磁盘副作用前 400 `workspace_hidden_target`；因此 linked Project 绑定进 implicit 子树后（展示转为可见）
  写入同样放行，不含 selectable 的纯 implicit 日期层直写散文件也被拒——隐藏面与守卫面严格一一对应。
  树外批量零触库。
- `write_workspace_file_content` 只覆盖既有文件，不产生新黑洞，不加守卫。

## 替代方案

- 仅拒写、展示口径不动（本 DR 上一版决策）：被后续实测推翻——个人空间自建文件夹仍"成功却消失"，用户
  期望的"创建→可见→可绑定"流程无法成立；拒写只把黑洞从文件扩展到目录体验。
- 上传/建目录时自动创建 selectable Project：被否——替用户发明项目实体，绑定语义应由会话选择动作产生。
- 全部可见（连 implicit 会话内容也显示）：被否——会话工作目录含 Agent 中间产物与附件，历史上明确不让
  用户面看到，隐藏 implicit 的原始理由仍然成立。

## 后果

- 展示口径变化：升级前落在"非 selectable 区域"的历史黑洞文件/目录从此可见，用户可自行删除或就地使用。
- projects 顶层重新可写（与默认区语义一致），不再需要"先绑定再上传"的操作顺序。
- implicit 判定依赖 projects 表行；行被硬删除（异常数据）时对应目录会转为可见——保守方向，无隐私放大
  （仍是该 uid 自己的文件）。
- 前端无需改动：可见性即列表返回；错误文案对 implicit 写入给出可行动说明。

## 验证

| 验收主张 | 语义 Owner | 直接证据 / 命令 | 负向案例 |
|---|---|---|---|
| implicit 根/子树/纯祖先日期层隐藏，用户目录、散文件、selectable 祖先可见 | _filter_project_tree_entries | test/unit/services/test_workspace_project_visibility.py（fake repo 双列表） | 谓词顺序把 default 改 False → 用户目录断言红 |
| 同层 selectable+implicit 混合时日期层保留可见 | 同上 | 同文件 test_date_layer_containing_selected_project_stays_visible | 删 selected 先判 → 红 |
| projects 顶层/未绑定目录创建+上传成功且可见（黑洞消失） | list/upload/directory 用例 | test/integration/api/test_workspace_projects_write_guard_api.py::test_user_directories_and_files_in_projects_are_visible（真实 HTTP+DB） | 恢复"非 selectable 拒写"旧守卫 → 400 断言红 |
| implicit 子树与纯 implicit 日期层写文件拒绝且不落盘；绑定进 implicit 的目录转为可写（与展示同口径） | assert_projects_write_targets_visible + _projects_candidate_visible | test/integration/api/test_workspace_projects_write_guard_api.py::test_upload_into_implicit_project_rejected；test/unit/services/test_artifact_service.py::test_save_artifact_rejects_hidden_projects_destination；test/unit/services/test_workspace_project_visibility.py 守卫三用例 | 守卫改单级判定或去掉 selectable 豁免 → 对应断言红 |
| 绑定为 linked Project 后上传可见（既有流程） | repositories/projects.py + 展示 | 同文件 test_upload_into_bound_project_visible | 谓词/过滤器误伤 selectable → 红 |
| 树外批量零触库 | assert_projects_write_targets_visible | test_projects_write_targets_skip_database_when_no_projects_targets | 去掉短路 → patch 爆炸断言红 |
| 弹窗 include_unbound 浏览语义不变 | list_workspace_tree | test_project_picker_tree_keeps_unbound_project_directories | 误接守卫 → 红 |

### 服务层模拟与人工界面验收步骤

1. 归因方法：对每个候选位置用真实服务函数（create_project_record / upload_workspace_files /
   list_workspace_tree）在 api 容器内模拟"创建→落盘→列表"，以接口返回集合差异定位黑洞；再用
   `find workspace/projects -maxdepth 1 -type d` 与 projects 表 workdir_path 对比，找出无行孤儿目录
   （0.7.3 升级残留的空日期层，是"显示一堆日期"的来源，确认 0 文件后删除）。
2. 验收数据构造（uid 对应账号登录）：projects 顶层散文件、自建目录+文件、linked selectable 绑定项目+文件、
   以及一条带假 workdir 行（selection_status='implicit'）的模拟会话目录。
3. 预期界面：前三类均立即可见且可在会话"选择项目"弹窗中选到；模拟会话目录与其日期层整体不出现；
   向 implicit 子树直接调上传接口返回 400 workspace_hidden_target 且不落盘。
4. 验收后清理：删除假 projects 行与对应目录。

已知限制：①写守卫对目标父链逐级判定，展示过滤器按条目自身路径判定——直接以
`GET /tree?path=<纯 implicit 祖先日期层>` 列目录时，其中的用户目录/散文件会显示而写守卫仍拒写；
方向 fail-closed、不产生黑洞，正常界面导航不可达该形态。②`search_workspace_files` 从不套 projects
可见性投影（存量缺口，本次未回归修复），implicit 内文件名可被搜出并可经 /file 直读；暴露面收敛属后续决策。

重新引入条件：若会话 implicit 目录需要向用户暴露（如"会话存储"产品化），须先定义清理与配额语义，再
评估移除 implicit 隐藏。
