# 个人空间展示实际工作区文件

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/workspace/services/files.py

## 问题

按 Project 是否可选过滤个人空间目录树，导致用户上传文件、新建目录或保存产物后无法在列表中找到它们（issue xerrors/Yuxi#1184）。

## 决策

### 实现方案

个人空间目录树直接读取实际文件，包括会话自动创建的 Workdir。Project 是否可选由独立的 Project 接口决定。目录删除规则见[目录删除与会话继续执行](2026-11-27-agent-task-workdir.md)。

## 替代方案

按 Project 分类隐藏目录，同时拦截进入隐藏区的写入。个人空间需要展示用户实际写入的文件，隐藏投影和额外写入限制会阻断这一用途。

## 后果

用户可以在个人空间查看自己保存的文件和会话产生的目录。Project 选择器仍以可选 Project 记录为准。

## 验证

HTTP 集成用例逐级读取目录，上传并回读普通文件和已绑定 Workdir 内的文件。
