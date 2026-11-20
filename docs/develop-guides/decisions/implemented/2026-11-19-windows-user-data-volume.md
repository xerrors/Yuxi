# Windows Container 部署使用 Linux 用户数据卷

状态：implemented
类型：bug-fix
Owner：docker-compose.windows.yml

## 问题

Windows Docker Desktop 的宿主目录绑定无法执行个人 Skill 和 Workspace 创建目录时所需的 `RENAME_NOREPLACE`。即使文件可写，个人 Skill 确认安装仍可能在最后发布目录时失败。

## 决策

### 实现方案

提供显式启用的 Windows Compose 覆盖。API、worker、sandbox-runner 和 storage-init 通过同一个项目命名卷访问用户数据。目录发布继续由文件系统层在单次原子操作内完成，并在同名目标已存在时拒绝覆盖。

## 替代方案

先检查目录是否存在，再用普通 rename 发布，会在并发安装时留下覆盖窗口。Linux 数据卷保留已有的文件系统语义。

## 后果

已有部署切换挂载前需要停写、复制并校验用户文件。操作步骤由 [Windows 用户数据卷](../../../advanced/container-sandbox.md#windows-用户数据卷) 维护。

## 验证

`test/unit/config/test_docker_compose_windows_storage.py` 检查四个用户数据消费者都挂载同一个项目卷。通过 Compose 解析后的实际挂载核对路径，并用容器上的 Linux 卷验证目录能发布、同名发布被拒绝、原文件内容保留。
