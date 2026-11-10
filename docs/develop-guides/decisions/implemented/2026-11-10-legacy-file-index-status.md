# 历史文件入库状态可以重新声明

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/implementations/milvus.py

## 问题

`done` 与 `failed` 是旧版本写入的文件状态。筛选把 `done` 并入已入库、把 `failed` 并入入库失败，界面也把文件 `done` 显示为可重新入库。入库声明却只接受 `parsed`、`error_indexing` 和 `indexed`，因此历史 `done` 不能重新入库，历史 `failed` 不能重试。`done` 同时是文件夹就绪值，不能把文件夹算进可入库集合。

## 决策

### 实现方案

Milvus 入库在创建集合和条件更新之前读取文件记录。记录是文件夹时直接失败，不进入声明。非文件夹的 `done` 与 `indexed` 一样可以重新声明为入库中，`failed` 与 `error_indexing` 一样可以重试。声明集合因此包含这两个历史字面量，但文件夹 `done` 被前面的判断排除。

前端 `failed` 使用与 `error_indexing` 相同的“重试入库”主动作，并进入可入库集合。`done` 仍表示已入库、可重新入库。批量按钮仅在所有可入库的选中文件都是 `done` 或 `indexed` 时显示“批量重新入库”；只要含有 `parsed`、`error_indexing` 或 `failed`，仍显示“批量入库”。

图谱清除的事实在 `repositories/graphs.py`。`failed` 与 `error_indexing` 一样可以清除已有图谱；处理中状态和文件夹仍然拒绝。下载原件同样接受 `failed`。保存已有分块或处于 `failed` 的 Markdown 时，仍先提示需要重新入库。提交重试后，列表在 worker 领取前继续把 `failed` 视为等待状态并保持轮询。

## 替代方案

- 把历史 `done`/`failed` 迁移成 `indexed`/`error_indexing`。这会改写存量状态，也把文件夹就绪值 `done` 卷进文件迁移。
- 只在前端把 `failed` 显示成可重试。后端声明仍会拒绝，按钮没有实际效果。
- 把 `done` 放进声明集合而不先拒绝文件夹。文件夹会和历史文件一起被声明为入库中。

## 后果

文件夹不能从文件列表进入入库声明。历史失败文件可以按单文件或批量入库重试，也可以下载原件、清除已有图谱，并在保存 Markdown 前看到重新入库提示。批量按钮在混有首次入库时不改成“重新入库”。待入库全量任务仍只统计 `parsed` 与 `error_indexing`，不因这次修复把历史 `failed` 算进待处理计数。

## 验证

- 文件夹 `done` 在创建集合和条件更新之前失败，声明调用为空，状态保持 `done`。
- 文件 `done` 与 `failed` 能进入声明，声明集合同时包含这两个历史值，记录先变为入库中，然后才读取 Markdown。
- 前端策略测试覆盖 `failed` 的重试入库动作、下载、文件夹不可入库，以及批量按钮文案。图谱清除、Markdown 保存确认和提交后的等待轮询同样接受 `failed`。
- `require_clearable_file` 接受 `failed`，拒绝处理中状态和文件夹。去掉 `failed` 后，对应允许用例失败。
