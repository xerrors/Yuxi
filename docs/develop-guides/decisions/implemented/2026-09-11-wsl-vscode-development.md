# Windows VS Code 通过 WSL 运行开发环境

状态：implemented
类型：process
Owner：docker-compose.wsl.yml

## 问题

仓库位于 Windows 文件系统时，默认 Compose 将 PostgreSQL 数据目录绑定到 Windows 路径。PostgreSQL 初始化需要修改目录权限，Windows 挂载不提供相同的 chmod/chown 语义，导致容器持续重启。Windows 自动换行还可能使容器入口脚本无法执行。

## 决策

保留默认 Compose 作为开发拓扑，通过 `docker-compose.wsl.yml` 仅将 PostgreSQL 数据目录替换为 Docker 命名卷。仓库跟踪一组 VS Code 任务，从 Windows 调用 WSL Ubuntu 中的 Docker Compose。`.gitattributes` 固定 shell 脚本使用 LF。前端 `test/` 目录单独声明 Node.js 全局变量，使固定基线中的 Node 单元测试可由 ESLint 正确检查。

该覆盖不改变 API、worker、前端和知识服务的运行方式，也不复制源码或创建另一套应用配置。

## 替代方案

- 将整个仓库复制到 WSL 文件系统：能够提供完整 Linux 语义，但会产生第二份源码，不符合当前单工作区开发方式。
- 在 Windows 原生安装所有依赖：需要维护与上游 Docker 拓扑不同的运行方式。
- 继续把 PostgreSQL 数据写入 Windows 挂载目录：初始化权限失败，不能作为可用方案。

## 后果

- Windows VS Code 可编辑原目录，容器继续通过挂载获得热重载。
- PostgreSQL 开发数据保存在 Docker 命名卷中，不直接出现在源码目录。
- 仓库内任务通过 `wsl.exe --cd` 使用 VS Code 当前工作区，但仍默认使用名为 `Ubuntu` 的发行版；其他发行版需要在个人配置中调整。
- 停止或重建应用不能使用会删除命名卷的命令，除非明确要清空开发数据。

## 验证

- `docker compose -f docker-compose.yml -f docker-compose.wsl.yml config --quiet`
- 使用覆盖文件启动后，PostgreSQL 健康检查通过，storage-migrator 以退出码 0 完成。
- `/api/system/ready` 返回 `status=ready` 且 `degraded=false`。
- `http://localhost:5173` 返回 HTTP 200。
- `.vscode/tasks.json` 可被 JSON 解析，且启动、状态、日志和停止任务均显式使用相同的两个 Compose 文件。
