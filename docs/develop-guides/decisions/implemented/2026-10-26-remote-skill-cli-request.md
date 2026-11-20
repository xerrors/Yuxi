# 无聊天 Run 的远程 Skill 获取

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/extensions/skills/remote.py

## 问题

扩展页的远程发现和安装没有 Agent Run。容器沙盒的通用执行器要求活动 Run，因此推荐套件和远程来源在 CLI 启动前失败。镜像中的 Node/npm 还需要固定版本的 Skills CLI。

目标是恢复现有来源的发现、草稿和个人安装。非目标是改变 Agent 执行权限、增加来源类型、创建虚假 Run、执行包内脚本或放松不覆盖已有目录的发布规则。

## 决策

### 实现方案

扩展模块保留白名单、操作者身份和请求独占临时目录。Cube 继续在操作者 VM 获取；Container 通过认证 runner 的专用协议运行镜像内固定版本的 Skills CLI。runner 的 wire 边界只允许发现、搜索及下载指定 Skill；参数直接作为 argv，不接收 shell 文本或用户环境变量。

`sandbox_runner/manager.py` 拥有获取容器。每次请求只挂载自己的临时目录，使用 bridge 下载来源，不挂载应用、其他工作区、共享 Skills 或 Docker socket，不注入凭据。CLI 可能解引用来源包中的符号链接，隔离必须在 CLI 读取来源之前成立，不能只检查生成的目录。容器沿用普通用户、只读根和资源限制。同步请求的 finally 删除容器后才返回结果；调用方取消时等待当前 HTTP 调用结束。CLI 执行限时 300 秒，传输失败或 runner 崩溃时由独立六分钟期限回收遗留容器。worker 识别请求期限，不把它当成缺失 Run 的孤儿。Agent 的 Run、lease 和 Workdir 校验保持原有语义。

## 替代方案

- 为安装创建虚假的 Agent Run，或放松通用执行器校验：改变业务状态和执行边界，拒绝。
- 在 API 直接运行 CLI：来源符号链接可能在复制前被跟随，应用文件不能得到隔离，拒绝。
- 把请求借放在当前聊天容器：扩展页没有 Run，且获取进程会看到额外 Workdir 和 Skills，拒绝。

## 后果

获取请求占用 runner 的同一并发配额。确认安装继续使用现有个人 Skill 发布入口。

## 验证

- `test/unit/sandbox/test_skill_fetch.py` 验证专用请求没有 Agent Run、个人 Skills 和共享 Skills 挂载，搜索不创建个人 Skills 目录，临时文件走受限 tmpfs，正常和错误出口回收容器。
- `test/unit/services/skills/test_remote_install.py` 验证仅请求绑定沙盒 CLI，失败输出不泄露，以及调用方取消时等待 HTTP 调用结束。
- `test/e2e/test_remote_skill_install_e2e.py` 提供 API 与 runner 的外部来源探针，核对获取、草稿、安装内容与容器回收。
