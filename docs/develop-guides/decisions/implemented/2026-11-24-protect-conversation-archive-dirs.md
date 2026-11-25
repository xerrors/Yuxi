# 个人空间与 Viewer 拒绝删除会话压缩留档

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/workspace/runtime_paths.py

## 问题

会话摘要和大工具结果写在 Workdir 的 `outputs/conversation_history` 与 `outputs/large_tool_results`。个人空间删除只挡住 Workdir 根和它的祖先，Viewer 只挡住根路径 `/`。这两份留档在目录内部，用户可以连同内容一起删掉。Checkpoint 只保存路径，删掉后摘要无法从原文件恢复。

## 决策

个人空间和 Viewer 都拒绝删除这两个目录及其内部路径。`outputs/tool_media`、Workdir 内的普通文件，以及整个 `outputs` 目录的既有删除行为保持不变。沙盒里的 `rm` 不在这次限制内。

### 实现方案

`is_conversation_archive_path` 识别路径组件中紧挨着的 `outputs` 与上述两个目录名。`delete_workspace_path` 和 `delete_viewer_file` 在真正删除前抛出 `InvalidOperationError`，业务码 `conversation_archive_protected`。Workdir 祖先保护仍先按原规则判断根和祖先；留档判断覆盖目录内部路径。

## 替代方案

- 只在个人空间拒绝：Viewer 仍能删掉同一目录。
- 锁住整个 `outputs`：`tool_media` 和用户放在该目录下的其他文件也不能删除。
- 同时禁止沙盒 `rm`：命令执行还有其他入口，这次没有一个能覆盖全部删除路径的边界。

## 后果

从个人空间或 Viewer 删除留档目录、其中的文件或子目录会失败，文件保持原样。删除整个 `outputs` 仍会连带清掉留档，因为该目录还包含可删除的 `tool_media` 和其他文件。名字刚好是 `outputs/<留档目录>` 的其他个人路径同样会被拒绝。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 个人空间删除留档失败且不调用删除 | 留档进入 delete_authorized_path | delete_workspace_path | test_personal_workspace_refuses_conversation_archive_but_deletes_tool_media | 去掉留档判断 | Passed |
| 同一 Workdir 的 tool_media 仍可删除 | tool_media 被一起拒绝 | delete_workspace_path | 同上 | 匹配整个 outputs | Passed |
| Viewer 删除留档失败且文件仍在 | 文件从后端存储消失 | delete_viewer_file | test_viewer_refuses_conversation_archive_and_keeps_the_file | 判断放在删除之后 | Passed |
