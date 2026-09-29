# 共享宿主侧 no-follow 目录遍历

状态：archived
类型：simplification
Owner：backend/package/yuxi/utils/paths.py

## 问题

Workdir、UserWorkspace 文件访问和个人 Skill 曾分别实现逐层 `dir_fd`、`O_NOFOLLOW` 打开和按需 `mkdir`。这些实现拥有同一个 fd 生命周期与路径安全算法，却在 fd 清理、symlink 识别和异常翻译上产生漂移。远端 Sandbox 脚本与独立 sandbox-provisioner 镜像不能导入 `yuxi` 包，不属于宿主共享边界。

## 决策

`yuxi.utils.paths.open_directory_fd` 是宿主进程中逐层打开目录的共享实现。函数从可信路径或调用方已经固定的目录 fd 开始，逐层打开已校验的单路径组件；`create=True` 时以 owner-only 的 `0o700` 创建缺失目录。函数返回由调用方关闭的 fd；传入目录 fd 时先复制，不接管原 fd。

共享函数拥有 fd 生命周期、目录创建和底层错误分类。Linux 可能把 symlink 与普通非目录组件都报告为 `ENOTDIR`，函数在父目录 fd 仍有效时以 no-follow `stat` 识别 symlink，并将其规范为 `OSError(ELOOP)`；真实非目录组件保留 `ENOTDIR`。Workdir、Workspace 与 Skills 的薄 wrapper 将底层 errno 翻译为各边界已有的 `ValueError`、`PermissionError` 或原始 `NotADirectoryError`。

远端 Sandbox 脚本、远端文件操作脚本与独立 provisioner 逻辑保持自包含，不导入 `yuxi`。宿主和 Sandbox 数据面统一身份后的权限契约见[统一 Workspace 运行身份并删除权限补丁](./2026-08-20-unified-workspace-runtime-identity.md)。

## 替代方案

- 保留各模块实现：拒绝。安全算法继续复制会让 fd 清理、errno 和创建语义进一步漂移。
- 在共享函数中统一业务异常：拒绝。不同边界已有不同的可观察错误与 HTTP 映射，共享工具不拥有业务消息。
- 同时抽取远端脚本和 provisioner：拒绝。它们运行在不能导入 `yuxi` 的进程或镜像中，自包含 stdlib 脚本属于当前部署边界。
- 同时改变权限：拒绝。权限对齐属于独立部署身份决策，由统一运行身份决定拥有。

## 后果

- Workdir、Workspace 和个人 Skill 复用同一套 fd/no-follow 遍历、创建与失败清理。
- 调用方继续选择可信 anchor、校验路径组件并拥有业务异常翻译；共享函数不成为授权或路径规范化边界。
- 调用方传入的目录 fd 在成功和失败后都保持有效；返回 fd 由调用方关闭。
- 共享函数规范化 symlink 的底层 errno，因此调用方不需要在父 fd 已关闭后重新解析路径。
- 远端脚本和独立 provisioner 仍有局部重复；该重复由运行时边界决定，不形成应用包内第二套共享 API。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 宿主侧目录遍历复用同一个 fd/no-follow 实现 | 仍保留可独立漂移的循环 | `yuxi.utils.paths` 与三个调用模块 | 源码负向搜索；相关 unit `187 passed` | 旧私有循环仍直接执行逐层 `mkdir + openat` | Passed |
| 共享函数在成功和失败时维持明确 fd 所有权 | 返回 fd 泄漏或误关调用方 fd | `open_directory_fd` | `test/unit/utils/test_paths.py`：`5 passed` | 中途打开失败、传入已有 fd | Passed |
| symlink 与真实非目录组件保留底层分类和业务边界异常 | 两者均为 `ENOTDIR` 导致 symlink 被映射为 404 | shared helper 与各业务 wrapper | 全量非慢 unit：`1371 passed, 34 skipped`；Viewer HTTP integration：`3 passed`；Workdir integration：`3 passed`；deterministic Workdir/runtime E2E：`1 passed` | symlink 组件返回 403/`PermissionError`；普通文件组件返回 404/`NotADirectoryError` | Passed |
| 独立运行时脚本保持自包含，目录权限由统一身份契约拥有 | 共享 helper 重构使独立镜像反向依赖 `yuxi`，或权限没有明确 Owner | 各调用点、独立脚本与统一运行身份决定 | diff、源码检查及 Sandbox/Skills/Workspace unit | 独立镜像导入 `yuxi`；重新出现跨 UID world-writable 补丁 | Passed；权限契约已由后续决定收敛 |

旧能力不存在：宿主 `yuxi` 包内的 Workdir、Workspace 和个人 Skill 不再保留独立的逐层创建/打开循环；各业务 wrapper 只保留 anchor 选择和异常翻译。远端执行脚本与独立 provisioner 明确排除。

重新引入条件：只有新的调用方具有不同的 fd 所有权、创建原子性或底层路径解析要求，且共享函数无法在不改变既有消费方契约的情况下表达时，才允许新增独立遍历实现。
