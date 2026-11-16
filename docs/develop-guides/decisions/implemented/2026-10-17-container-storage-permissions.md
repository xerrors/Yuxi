# 镜像权限与统一存储初始化

状态：implemented
类型：simplification
Owner：docker/storage-init.sh

## 问题

宿主源码为 600/700 时，镜像中的非 root API 可能无法读取配置与源码。API 可写目录依赖部署者手工准备，而 Milvus 使用独立权限初始化器，安装路径存在重复职责和遗漏风险。

只修复 Milvus 挂载根无法处理 root 持有的历史嵌套目录。Milvus 以 UID/GID 999 运行，创建 `data/cache/.../growing_mmap` 时会因无写权限退出，导致依赖向量库的 API 与 worker 初始化失败。

## 决策

### 实现方案

默认应用镜像的项目文件在构建时明确 root 属主和权限：目录 0755、配置与源码 0644、入口脚本 0755。API 与 worker 保持 UID/GID 1000；上游软件和依赖包的权限保留其安装器契约，不对系统目录统一递归 chmod。

开发和生产 Compose 使用同一个轻量 storage-init 镜像及脚本，仅挂载五个受管目录。API 的三个目录设置为 1000:1000、0700，Milvus 两个目录设置为 999:999、0755；Milvus 数据树递归恢复为 999:999，同时保留已有文件内容和模式，API 数据树仍只调整挂载根。API、worker、Milvus 与 app-init 等待该进程成功退出。Schema 与共享应用内容由[应用初始化决策](2026-10-18-app-initialization.md)拥有。生产教程移除手工准备步骤，CI 与离线镜像导出同步构建统一初始化器。

## 替代方案

- 维持手工命令与 Milvus 专用容器：同一权限职责分散且人工步骤易漏。
- 只在 init.sh 处理：直接运行 Compose 会绕过准备步骤。
- 服务入口以 root 递归修权限再切用户：每次启动扫描历史数据，并扩大业务进程权限与启动成本。
- 全部改成 named volume：不能消除镜像文件权限问题，也改变 Cube 的宿主路径共享和现有备份契约。

## 后果

宿主源码 bind mount 会覆盖镜像权限，开发 checkout 的可读性仍由宿主管理。首次启动或恢复备份时，统一初始化会扫描 Milvus 两个数据树并恢复 UID/GID 999；该扫描可能增加启动耗时，但不修改文件内容和模式。API 的三个数据根仍只调整根目录。五个挂载根接受固定数字身份与模式，初始化失败阻止依赖服务启动。

## 验证

旧能力不存在：开发与生产 Compose 不再注册 milvus-storage-init，当前部署教程不再要求手工 chown API 目录。

重新引入条件：只有独立服务出现无法由统一初始化器表达的生命周期或存储身份要求，才重新评估专用步骤。

- `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q`：133 passed；包含两套 Compose 的目录映射、镜像隔离和完成依赖，缺失或非阻塞依赖的负控。
- `python -m unittest scripts.ci.test_container_permissions`：真实 Docker 使用宿主 600/700 输入构建，UID 1000 回读 root 持有的项目文件并确认不可写；恢复不可读权限的负控出现 Permission denied。Milvus UID 999 在修复前无法写入历史嵌套目录，统一初始化器重复运行后可创建子目录并读取原文件；嵌套目录和文件恢复为 999:999，同时保留模式与字节内容。只读挂载由 Docker inspect 证明 RW=false，并使初始化失败。合成冲突宿主环境变量不会改变测试 project 或状态目录。
- `docker compose exec -T api uv run --group test pytest test/unit -m 'not slow'`：2720 passed、91 skipped、7 subtests passed。容器缺少仓库根文件的配置检查由宿主 config 全集补充。
- Production 的 API、Web、MinIO、storage-init 四个镜像构建成功并更新服务，两个初始化进程均退出 0。API UID 1000 可读 root 持有的 0644 源码，三个数据根为 1000:1000、0700；Milvus UID 999、两个数据根为 999:999、0755。Web 的 HTML 与 13 个 JS/CSS 资源通过 80 端口读取成功。
- 真实 Production 登录与 readiness 通过；既有 Schema、完成 Turn/Run 和文件字节保持原值。知识库 vector、keyword、hybrid 检索均返回目标来源。新模型会话经 worker 与 Cube 完成文件写入和读回，数据库回读确认顶层会话、Turn/result Run/输出消息关联及精确输出，宿主工作区文件字节符合请求。
- 工程契约、66 项检查器单测、相对链接及 docs build、Ruff、shell 语法和 `git diff --check` 通过。

验证不覆盖 Windows 原生 PowerShell 执行、GPU OCR 镜像、原生 Cube 节点与 rootless/userns/NFS 特殊映射。
